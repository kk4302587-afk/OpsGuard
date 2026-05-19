"""Fault correlation topology API.

Provides data for the fault correlation graph visualization.
Builds relationships: process → port → service → log → config
Cross-platform: works on both Linux and Windows via psutil.

Supports both static snapshots and dynamic updates during diagnosis.
"""

import psutil
import platform
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class DynamicNode(BaseModel):
    """A node to add dynamically during diagnosis."""
    id: str
    name: str
    category: str  # process, port, service, config, log, remote
    value: str = ""
    highlight: bool = False  # Whether this node is related to the fault


class DynamicEdge(BaseModel):
    """An edge to add dynamically during diagnosis."""
    source: str
    target: str
    relation: str
    inferred: bool = False


class DynamicUpdate(BaseModel):
    """A batch of dynamic updates to the topology graph."""
    nodes: list[DynamicNode] = []
    edges: list[DynamicEdge] = []
    fault_node_ids: list[str] = []  # Nodes to highlight as fault-related


# In-memory store for dynamic updates per session
_session_updates: dict[str, DynamicUpdate] = {}


@router.get("/graph")
async def get_topology_graph():
    """Build a topology graph of system entities and their relationships."""
    nodes = []
    edges = []
    seen_nodes = set()
    seen_edges = set()
    connections = []

    def add_node(node_id: str, name: str, category: str, value: str = ""):
        if node_id not in seen_nodes:
            seen_nodes.add(node_id)
            nodes.append({
                "id": node_id,
                "name": name,
                "category": category,
                "value": value,
                "highlight": False,
            })

    def add_edge(source: str, target: str, relation: str, inferred: bool = False):
        edge_key = f"{source}->{target}:{relation}"
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            edges.append({"source": source, "target": target, "relation": relation, "inferred": inferred})

    # Collect listening processes and their ports
    try:
        connections = psutil.net_connections(kind='inet')
        for conn in connections:
            if conn.status == 'LISTEN' and conn.pid:
                try:
                    proc = psutil.Process(conn.pid)
                    proc_name = proc.name()
                    port = conn.laddr.port

                    proc_id = f"proc_{conn.pid}"
                    port_id = f"port_{port}"

                    cpu = proc.cpu_percent(interval=0)
                    mem = proc.memory_percent()

                    add_node(proc_id, f"{proc_name} (PID:{conn.pid})", "process", f"CPU: {cpu:.1f}% MEM: {mem:.1f}%")
                    add_node(port_id, f":{port}", "port")
                    add_edge(proc_id, port_id, "listens_on")

                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
    except (psutil.AccessDenied, OSError):
        pass

    # Collect established connections
    try:
        established = [c for c in connections if c.status == 'ESTABLISHED' and c.pid and c.raddr]
        remote_hosts = {}
        for conn in established:
            remote = f"{conn.raddr.ip}:{conn.raddr.port}"
            if remote not in remote_hosts:
                remote_hosts[remote] = {"count": 0, "pids": set()}
            remote_hosts[remote]["count"] += 1
            remote_hosts[remote]["pids"].add(conn.pid)

        for remote, info in list(remote_hosts.items())[:10]:
            if info["count"] >= 2:
                remote_id = f"remote_{remote.replace('.', '_').replace(':', '_')}"
                add_node(remote_id, remote, "remote", f"{info['count']} connections")
                for pid in list(info["pids"])[:3]:
                    proc_id = f"proc_{pid}"
                    if proc_id in seen_nodes:
                        add_edge(proc_id, remote_id, "connects_to")
    except (psutil.AccessDenied, OSError):
        pass

    # Linux services
    if platform.system() == "Linux":
        try:
            import subprocess
            result = subprocess.run(
                ["systemctl", "list-units", "--type=service", "--state=running", "--no-pager", "--plain", "--no-legend"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n")[:15]:
                    parts = line.split()
                    if parts:
                        svc_name = parts[0].replace(".service", "")
                        svc_id = f"svc_{svc_name}"
                        add_node(svc_id, svc_name, "service")
                        # Match service to process by checking if process name starts with service name
                        for node in list(nodes):
                            if node["category"] == "process":
                                proc_name_lower = node["name"].split(" ")[0].lower()  # e.g. "nginx" from "nginx (PID:123)"
                                if proc_name_lower == svc_name.lower() or proc_name_lower.startswith(svc_name.lower()):
                                    add_edge(svc_id, node["id"], "manages", inferred=True)
        except (FileNotFoundError, Exception):
            pass

        # Config files
        import os
        config_files = [
            ("/etc/nginx/nginx.conf", "nginx"),
            ("/etc/ssh/sshd_config", "sshd"),
            ("/etc/mysql/my.cnf", "mysql"),
            ("/etc/redis/redis.conf", "redis"),
        ]
        for conf_path, svc_name in config_files:
            if os.path.exists(conf_path):
                conf_id = f"conf_{svc_name}"
                add_node(conf_id, conf_path.split("/")[-1], "config")
                svc_id = f"svc_{svc_name}"
                if svc_id in seen_nodes:
                    add_edge(svc_id, conf_id, "configured_by", inferred=True)

    # Categories
    categories = [
        {"name": "process", "itemStyle": {"color": "#61afef"}},
        {"name": "port", "itemStyle": {"color": "#e5c07b"}},
        {"name": "service", "itemStyle": {"color": "#00d4aa"}},
        {"name": "config", "itemStyle": {"color": "#c678dd"}},
        {"name": "remote", "itemStyle": {"color": "#e06c75"}},
        {"name": "log", "itemStyle": {"color": "#d19a66"}},
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "categories": categories,
    }


@router.get("/graph/{session_id}")
async def get_topology_with_diagnosis(session_id: str):
    """Get topology graph merged with dynamic diagnosis findings for a session."""
    base = await get_topology_graph()

    # Merge dynamic updates if any
    updates = _session_updates.get(session_id)
    if updates:
        existing_ids = {n["id"] for n in base["nodes"]}
        for node in updates.nodes:
            if node.id not in existing_ids:
                base["nodes"].append({
                    "id": node.id,
                    "name": node.name,
                    "category": node.category,
                    "value": node.value,
                    "highlight": node.highlight,
                })
                existing_ids.add(node.id)

        for edge in updates.edges:
            base["edges"].append({
                "source": edge.source,
                "target": edge.target,
                "relation": edge.relation,
                "inferred": edge.inferred,
            })

        # Mark fault nodes
        for node in base["nodes"]:
            if node["id"] in updates.fault_node_ids:
                node["highlight"] = True

    return base


@router.post("/graph/{session_id}/update")
async def add_dynamic_update(session_id: str, update: DynamicUpdate):
    """Add dynamic nodes/edges discovered during diagnosis.

    Called by the Agent engine when it discovers relationships during analysis.
    """
    if session_id not in _session_updates:
        _session_updates[session_id] = DynamicUpdate()

    existing = _session_updates[session_id]
    existing.nodes.extend(update.nodes)
    existing.edges.extend(update.edges)
    existing.fault_node_ids.extend(update.fault_node_ids)

    return {"status": "updated", "total_nodes": len(existing.nodes), "total_edges": len(existing.edges)}


@router.delete("/graph/{session_id}")
async def clear_dynamic_updates(session_id: str):
    """Clear dynamic updates for a session."""
    _session_updates.pop(session_id, None)
    return {"status": "cleared"}


def add_diagnosis_finding(session_id: str, nodes: list[dict], edges: list[dict], fault_ids: list[str] = None):
    """Helper function called from Agent graph to add findings.

    This is the internal API used by the Agent during diagnosis.
    """
    if session_id not in _session_updates:
        _session_updates[session_id] = DynamicUpdate()

    existing = _session_updates[session_id]
    for n in nodes:
        existing.nodes.append(DynamicNode(**n))
    for e in edges:
        existing.edges.append(DynamicEdge(**e))
    if fault_ids:
        existing.fault_node_ids.extend(fault_ids)
