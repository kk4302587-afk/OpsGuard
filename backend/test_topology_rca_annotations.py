"""Regression checks for topology RCA annotations from trace evidence."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import aiosqlite

os.chdir(Path(__file__).parent)

from app.api import topology
from app.incidents import store as incident_store


async def _seed_incident_events(db_path: str, session_id: str) -> str:
    incident_id = await incident_store.create_incident(
        session_id=session_id,
        problem_statement="Diagnose nginx failure",
        source="test",
        db_path=db_path,
    )
    events = [
        {
            "phase": "execution",
            "event_type": "success",
            "title": "service status",
            "detail": "nginx inactive",
            "evidence": {
                "claim": "get_service_status executed against nginx",
                "evidence_type": "command",
                "source": "get_service_status",
                "observed": "nginx.service inactive",
                "confidence": "high",
                "execution_state": "executed",
            },
            "metadata": {"tool_name": "get_service_status", "tool_args": {"service": "nginx"}},
        },
        {
            "phase": "execution",
            "event_type": "success",
            "title": "listening ports",
            "detail": "port scan",
            "evidence": {
                "claim": "get_listening_ports returned nginx listener",
                "evidence_type": "command",
                "source": "get_listening_ports",
                "observed": 'LISTEN 0 511 0.0.0.0:80 users:(("nginx",pid=123,fd=6))',
                "confidence": "high",
                "execution_state": "executed",
            },
            "metadata": {"tool_name": "get_listening_ports", "tool_args": {}},
        },
        {
            "phase": "recent_changes",
            "event_type": "success",
            "title": "recent changes",
            "detail": "config changed",
            "evidence": {
                "claim": "Recent change check returned one nginx config change",
                "evidence_type": "command",
                "source": "get_recent_changes",
                "observed": json.dumps({"changes": [{"target": "/etc/nginx/nginx.conf"}]}),
                "confidence": "high",
                "execution_state": "executed",
            },
            "metadata": {"tool_name": "get_recent_changes", "tool_args": {"window_hours": 24}},
        },
        {
            "phase": "planning",
            "event_type": "start",
            "title": "planning only",
            "detail": "LLM plans a check",
            "evidence": {
                "claim": "LLM inferred next step",
                "evidence_type": "user input",
                "source": "LLM",
                "observed": "maybe nginx",
                "confidence": "medium",
                "execution_state": "inferred",
            },
            "metadata": {},
        },
    ]
    for event in events:
        await incident_store.record_incident_event(
            incident_id=incident_id,
            session_id=session_id,
            phase=event["phase"],
            event_type=event["event_type"],
            title=event["title"],
            detail=event["detail"],
            evidence=event["evidence"],
            metadata=event["metadata"],
            db_path=db_path,
        )
    return incident_id


def test_topology_annotations_from_incident_evidence() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            session_id = "topology-session"
            original_db = topology.get_knowledge_db_path
            original_incident_db = incident_store.get_knowledge_db_path
            try:
                topology.get_knowledge_db_path = lambda: db_path
                incident_store.get_knowledge_db_path = lambda: db_path
                await _seed_incident_events(db_path, session_id)
                annotations = await topology.build_topology_annotations(session_id)
            finally:
                topology.get_knowledge_db_path = original_db
                incident_store.get_knowledge_db_path = original_incident_db

        targets = {item["target_id"]: item for item in annotations}
        assert "svc_nginx" in targets
        assert "port_80" in targets
        assert "proc_123" in targets
        assert "conf_nginx" in targets
        assert all(item["execution_state"] in {"executed", "failed"} for item in annotations)
        assert not any(item["source"] == "LLM" for item in annotations)

    asyncio.run(scenario())


def test_topology_latest_scope_uses_only_newest_incident() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            session_id = "topology-latest-session"
            original_db = topology.get_knowledge_db_path
            original_incident_db = incident_store.get_knowledge_db_path
            try:
                topology.get_knowledge_db_path = lambda: db_path
                incident_store.get_knowledge_db_path = lambda: db_path

                old_incident_id = await incident_store.create_incident(
                    session_id=session_id,
                    problem_statement="Old nginx check",
                    source="test",
                    db_path=db_path,
                )
                await incident_store.record_incident_event(
                    incident_id=old_incident_id,
                    session_id=session_id,
                    phase="execution",
                    event_type="success",
                    title="old service status",
                    detail="nginx inactive",
                    evidence={
                        "claim": "old nginx check",
                        "evidence_type": "command",
                        "source": "get_service_status",
                        "observed": "nginx inactive",
                        "confidence": "high",
                        "execution_state": "executed",
                    },
                    metadata={"tool_name": "get_service_status", "tool_args": {"service": "nginx"}},
                    timestamp="2026-05-20T00:00:00",
                    db_path=db_path,
                )
                await incident_store.finalize_incident(
                    incident_id=old_incident_id,
                    final_summary="old done",
                    db_path=db_path,
                )

                new_incident_id = await incident_store.create_incident(
                    session_id=session_id,
                    problem_statement="New redis check",
                    source="test",
                    db_path=db_path,
                )
                await incident_store.record_incident_event(
                    incident_id=new_incident_id,
                    session_id=session_id,
                    phase="execution",
                    event_type="success",
                    title="new service status",
                    detail="redis inactive",
                    evidence={
                        "claim": "new redis check",
                        "evidence_type": "command",
                        "source": "get_service_status",
                        "observed": "redis inactive",
                        "confidence": "high",
                        "execution_state": "executed",
                    },
                    metadata={"tool_name": "get_service_status", "tool_args": {"service": "redis"}},
                    timestamp="2026-05-21T00:00:00",
                    db_path=db_path,
                )

                latest = await topology.build_topology_annotations(session_id, scope="latest")
                whole_session = await topology.build_topology_annotations(session_id, scope="session")
            finally:
                topology.get_knowledge_db_path = original_db
                incident_store.get_knowledge_db_path = original_incident_db

        latest_targets = {item["target_id"] for item in latest}
        session_targets = {item["target_id"] for item in whole_session}
        assert "svc_redis" in latest_targets
        assert "svc_nginx" not in latest_targets
        assert {"svc_redis", "svc_nginx"}.issubset(session_targets)

    asyncio.run(scenario())


def test_apply_annotations_adds_highlights_and_inferred_edges() -> None:
    graph = {
        "nodes": [
            {"id": "svc_nginx", "name": "nginx", "category": "service", "highlight": False},
            {"id": "port_80", "name": ":80", "category": "port", "highlight": False},
        ],
        "edges": [],
        "categories": [
            {"name": "service", "itemStyle": {"color": "#00d4aa"}},
            {"name": "port", "itemStyle": {"color": "#e5c07b"}},
            {"name": "config", "itemStyle": {"color": "#c678dd"}},
            {"name": "process", "itemStyle": {"color": "#61afef"}},
        ],
    }
    annotations = [
        {
            "target_id": "svc_nginx",
            "target_type": "service",
            "rca_role": "affected",
            "evidence_summary": "nginx inactive",
            "source": "get_service_status",
            "phase": "execution",
            "event_type": "success",
            "execution_state": "executed",
            "inferred": False,
        },
        {
            "target_id": "conf_nginx",
            "target_type": "config",
            "rca_role": "suspected_root_cause",
            "evidence_summary": "/etc/nginx/nginx.conf changed",
            "source": "get_recent_changes",
            "phase": "recent_changes",
            "event_type": "success",
            "execution_state": "executed",
            "inferred": False,
        },
    ]

    topology._apply_annotations(graph, annotations)

    node_by_id = {node["id"]: node for node in graph["nodes"]}
    assert node_by_id["svc_nginx"]["highlight"] is True
    assert node_by_id["svc_nginx"]["rca_role"] == "affected"
    assert node_by_id["conf_nginx"]["highlight"] is True
    assert node_by_id["conf_nginx"]["rca_role"] == "suspected_root_cause"
    assert any(edge["relation"] == "evidence_link" and edge["inferred"] for edge in graph["edges"])


def main() -> None:
    test_topology_annotations_from_incident_evidence()
    test_topology_latest_scope_uses_only_newest_incident()
    test_apply_annotations_adds_highlights_and_inferred_edges()
    print("topology RCA annotations regression OK")


if __name__ == "__main__":
    main()
