"""Fault correlation topology API.

Provides data for the fault correlation graph visualization.
Builds relationships: process → port → service → log → config
Cross-platform: works on both Linux and Windows via psutil.

Supports both static snapshots and dynamic updates during diagnosis.
"""

import psutil
import platform
import json
import re
from typing import Any

import aiosqlite
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.database import get_audit_db_path, get_knowledge_db_path
from app.incidents.store import ensure_incident_schema

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


class TopologyAnnotation(BaseModel):
    """RCA annotation derived from real trace or incident evidence."""
    target_id: str
    target_type: str  # service, port, process, config, host, unknown
    rca_role: str  # affected, suspected_root_cause, downstream_impact, evidence
    evidence_summary: str
    source: str
    phase: str
    event_type: str
    execution_state: str
    inferred: bool = False


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
async def get_topology_with_diagnosis(
    session_id: str,
    scope: str = Query(default="session", pattern="^(latest|session)$"),
):
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

    annotations = await build_topology_annotations(session_id, scope=scope)
    _apply_annotations(base, annotations)

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


async def build_topology_annotations(session_id: str, scope: str = "session") -> list[dict]:
    """Build topology RCA annotations from persisted trace/incident evidence."""
    events = await _load_incident_trace_events(session_id, latest_only=(scope == "latest"))
    if not events and scope != "latest":
        events = await _load_audit_trace_events(session_id)

    annotations: list[TopologyAnnotation] = []
    seen: set[tuple[str, str, str, str]] = set()
    for event in events:
        for annotation in _annotations_from_event(event):
            key = (
                annotation.target_id,
                annotation.source,
                annotation.execution_state,
                annotation.evidence_summary,
            )
            if key in seen:
                continue
            seen.add(key)
            annotations.append(annotation)
    return [annotation.dict() for annotation in annotations]


async def _load_incident_trace_events(session_id: str, latest_only: bool = False) -> list[dict]:
    """Load incident events for a session, preserving evidence if available."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_incident_schema(db)
        db.row_factory = aiosqlite.Row
        latest_incident_id = None
        if latest_only:
            incident_cursor = await db.execute(
                """
                SELECT id
                FROM incidents
                WHERE session_id = ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            )
            incident_row = await incident_cursor.fetchone()
            latest_incident_id = incident_row["id"] if incident_row else None
            if not latest_incident_id:
                return []

        where_clause = "session_id = ?"
        params: tuple[Any, ...] = (session_id,)
        if latest_incident_id:
            where_clause = "session_id = ? AND incident_id = ?"
            params = (session_id, latest_incident_id)

        cursor = await db.execute(
            f"""
            SELECT event_type, phase, title, detail, evidence, metadata, timestamp
            FROM incident_events
            WHERE {where_clause}
            ORDER BY timestamp ASC, id ASC
            LIMIT 300
            """,
            params,
        )
        rows = await cursor.fetchall()
    return [_row_event(row) for row in rows]


async def _load_audit_trace_events(session_id: str) -> list[dict]:
    """Fallback to audit trace rows when no incident timeline exists."""
    async with aiosqlite.connect(get_audit_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT phase, event_type, content, metadata, timestamp
            FROM audit_logs
            WHERE session_id = ?
            ORDER BY timestamp ASC
            LIMIT 300
            """,
            (session_id,),
        )
        rows = await cursor.fetchall()
    return [_row_event(row) for row in rows]


def _annotations_from_event(event: dict) -> list[TopologyAnnotation]:
    """Extract node annotations from one trace or incident event."""
    evidence = event.get("evidence") if isinstance(event.get("evidence"), dict) else {}
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    source = str(evidence.get("source") or metadata.get("tool_name") or metadata.get("source") or event.get("source") or "")
    execution_state = str(evidence.get("execution_state") or event.get("execution_state") or "")
    if execution_state and execution_state not in {"executed", "failed"}:
        return []

    observed = _compact(
        evidence.get("observed")
        or event.get("detail")
        or event.get("content")
        or event.get("title")
        or ""
    )
    tool_args = metadata.get("tool_args") if isinstance(metadata.get("tool_args"), dict) else metadata.get("args")
    if not isinstance(tool_args, dict):
        tool_args = {}

    phase = str(event.get("phase") or "")
    event_type = str(event.get("event_type") or "")
    failed = execution_state == "failed" or event_type == "failure"
    annotations: list[TopologyAnnotation] = []
    seen_annotations: set[tuple[str, str, str, str]] = set()

    def add(target_id: str, target_type: str, role: str, summary: str, inferred: bool = False) -> None:
        if not target_id:
            return
        summary_text = _compact(summary)
        key = (target_id, target_type, role, summary_text)
        if key in seen_annotations:
            return
        seen_annotations.add(key)
        annotations.append(TopologyAnnotation(
            target_id=target_id,
            target_type=target_type,
            rca_role=role,
            evidence_summary=summary_text,
            source=source or "trace",
            phase=phase,
            event_type=event_type,
            execution_state=execution_state or "executed",
            inferred=inferred,
        ))

    service = _normalize_service(str(tool_args.get("service") or ""))
    filepath = str(tool_args.get("filepath") or tool_args.get("path") or "")

    if source in {"get_service_status", "get_service_logs"} and service:
        add(f"svc_{service}", "service", "affected" if failed else "evidence", observed)

    if source in {"read_config_file", "check_config_syntax", "diff_config"} and filepath:
        config_id = _config_id(filepath)
        role = "suspected_root_cause" if failed else "evidence"
        add(config_id, "config", role, observed, inferred=False)
        svc = _service_from_path(filepath)
        if svc:
            add(f"svc_{svc}", "service", "affected" if failed else "evidence", observed, inferred=True)

    if source == "get_recent_changes":
        for path in _extract_config_paths(observed):
            add(_config_id(path), "config", "suspected_root_cause", observed, inferred=False)
            svc = _service_from_path(path)
            if svc:
                add(f"svc_{svc}", "service", "suspected_root_cause", observed, inferred=True)

    if source in {"get_listening_ports", "check_port"}:
        explicit_port = tool_args.get("port")
        if explicit_port not in (None, ""):
            add(f"port_{explicit_port}", "port", "downstream_impact", observed)
        for port in _extract_ports(observed):
            add(f"port_{port}", "port", "downstream_impact", observed)
        for pid, name in _extract_processes(observed):
            target_id = f"proc_{pid}" if pid else f"proc_{_safe_id(name)}"
            add(target_id, "process", "downstream_impact", observed)

    if source in {"get_process_detail", "list_processes"}:
        explicit_pid = tool_args.get("pid")
        if explicit_pid not in (None, ""):
            add(f"proc_{explicit_pid}", "process", "evidence", observed)
        for pid, name in _extract_processes(observed):
            target_id = f"proc_{pid}" if pid else f"proc_{_safe_id(name)}"
            add(target_id, "process", "evidence", observed)

    return annotations


def _apply_annotations(graph: dict, annotations: list[dict]) -> None:
    """Merge annotations into topology nodes, adding missing evidence nodes."""
    graph["annotations"] = annotations
    node_by_id = {node["id"]: node for node in graph["nodes"]}
    for annotation in annotations:
        target_id = annotation["target_id"]
        node = node_by_id.get(target_id)
        if not node:
            node = {
                "id": target_id,
                "name": _name_from_target(target_id, annotation["target_type"]),
                "category": annotation["target_type"] if annotation["target_type"] in _category_names(graph) else "service",
                "value": "",
                "highlight": False,
            }
            graph["nodes"].append(node)
            node_by_id[target_id] = node

        node.setdefault("annotations", []).append(annotation)
        node["highlight"] = True
        node["rca_role"] = _stronger_role(node.get("rca_role"), annotation["rca_role"])

    _add_annotation_edges(graph, annotations, node_by_id)


def _add_annotation_edges(graph: dict, annotations: list[dict], node_by_id: dict[str, dict]) -> None:
    """Add evidence relationships between annotated service/config/port/process nodes."""
    existing = {
        (edge.get("source"), edge.get("target"), edge.get("relation"))
        for edge in graph["edges"]
    }
    services = [a for a in annotations if a["target_type"] == "service"]
    others = [a for a in annotations if a["target_type"] in {"config", "port", "process"}]
    for service in services:
        for other in others:
            source = service["target_id"]
            target = other["target_id"]
            if source == target or source not in node_by_id or target not in node_by_id:
                continue
            relation = "evidence_link"
            key = (source, target, relation)
            if key in existing:
                continue
            existing.add(key)
            graph["edges"].append({
                "source": source,
                "target": target,
                "relation": relation,
                "inferred": True,
                "annotations": [service, other],
            })


def _row_event(row: aiosqlite.Row) -> dict:
    item = dict(row)
    item["content"] = item.get("content") or item.get("detail") or item.get("title") or ""
    item["metadata"] = _json_loads(item.get("metadata"), {})
    item["evidence"] = _json_loads(item.get("evidence"), None)
    if not item["evidence"] and isinstance(item["metadata"], dict):
        item["evidence"] = item["metadata"].get("evidence")
    return item


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _compact(value: Any, max_chars: int = 500) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value or "")
    text = " ".join(text.split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _normalize_service(service: str) -> str:
    return service.strip().replace(".service", "")


def _config_id(path: str) -> str:
    service = _service_from_path(path)
    return f"conf_{service}" if service else f"conf_{_safe_id(path)}"


def _service_from_path(path: str) -> str:
    lowered = path.lower()
    for service in ("nginx", "sshd", "mysql", "redis", "apache"):
        if service in lowered:
            return service
    return ""


def _extract_config_paths(text: str) -> list[str]:
    patterns = [
        r"(/etc/[A-Za-z0-9_./-]+\.conf)",
        r"([A-Za-z]:\\\\[A-Za-z0-9_.\\\\-]+\.conf)",
    ]
    paths: list[str] = []
    for pattern in patterns:
        paths.extend(re.findall(pattern, text))
    return list(dict.fromkeys(paths))


def _extract_ports(text: str) -> list[str]:
    ports = re.findall(r"(?<![\w.])(?:0\.0\.0\.0:|127\.0\.0\.1:|\[?::\]?:|:)(\d{2,5})(?!\d)", text)
    return list(dict.fromkeys(ports))


def _extract_processes(text: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for name, pid in re.findall(r'"([^"]+)",pid=(\d+)', text):
        results.append((pid, name))
    for pid, name in re.findall(r"\bPID[:= ]+(\d+).*?\b([A-Za-z][A-Za-z0-9_.-]+)", text):
        results.append((pid, name))
    if not results:
        for name in ("nginx", "apache", "redis", "mysql", "sshd"):
            if name in text.lower():
                results.append(("", name))
    return list(dict.fromkeys(results))


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    return safe.strip("_").lower()[:80] or "unknown"


def _name_from_target(target_id: str, target_type: str) -> str:
    if target_type == "service" and target_id.startswith("svc_"):
        return target_id[4:]
    if target_type == "config" and target_id.startswith("conf_"):
        return f"{target_id[5:]}.conf"
    if target_type == "port" and target_id.startswith("port_"):
        return f":{target_id[5:]}"
    if target_type == "process" and target_id.startswith("proc_"):
        return target_id[5:]
    return target_id


def _category_names(graph: dict) -> set[str]:
    return {category["name"] for category in graph.get("categories", [])}


def _stronger_role(current: str | None, candidate: str) -> str:
    order = {
        "suspected_root_cause": 4,
        "affected": 3,
        "downstream_impact": 2,
        "evidence": 1,
        None: 0,
    }
    return candidate if order.get(candidate, 0) > order.get(current, 0) else (current or candidate)
