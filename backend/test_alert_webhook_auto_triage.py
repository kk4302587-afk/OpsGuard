"""Regression checks for alert webhook read-only auto-triage."""

import asyncio
import os
import tempfile
from pathlib import Path

import aiosqlite

os.chdir(Path(__file__).parent)

from app.alerts import triage
from app.agent.tools_registry import RiskLevel, ToolDefinition
from app.incidents import store as incident_store
from app.mcp_tools.process_tools import ToolResult


async def _init_temp_db(knowledge_db: str, audit_db: str) -> None:
    async with aiosqlite.connect(knowledge_db) as db:
        await db.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
            """
        )
        await incident_store.ensure_incident_schema(db)
        await db.commit()

    async with aiosqlite.connect(audit_db) as db:
        await db.execute(
            """
            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                phase TEXT NOT NULL,
                event_type TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT
            )
            """
        )
        await db.commit()


def _fake_tool(name: str, calls: list[tuple[str, dict]]):
    def _record(**kwargs):
        calls.append((name, kwargs))
        return ToolResult(success=True, data={"tool": name, "args": kwargs})

    read_tools = {
        "get_service_status": "service",
        "get_service_logs": "service",
        "get_listening_ports": "network",
        "get_disk_usage": "disk",
        "get_recent_changes": "recent_changes",
        "prometheus_range_query": "observability",
        "loki_range_query": "observability",
    }
    if name in read_tools:
        return ToolDefinition(
            name=name,
            description=f"Fake {name}",
            parameters={"type": "object", "properties": {}},
            function=_record,
            risk_level=RiskLevel.READ,
            category=read_tools[name],
        )
    if name == "restart_service":
        return ToolDefinition(
            name=name,
            description="Fake restart",
            parameters={"type": "object", "properties": {}},
            function=_record,
            risk_level=RiskLevel.WRITE,
            category="service",
        )
    return None


def test_service_down_webhook_creates_session_incident_and_read_only_trace() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            knowledge_db = str(Path(tmpdir) / "knowledge.db")
            audit_db = str(Path(tmpdir) / "audit.db")
            await _init_temp_db(knowledge_db, audit_db)
            calls: list[tuple[str, dict]] = []

            original_knowledge_path = triage.get_knowledge_db_path
            original_audit_path = triage.get_audit_db_path
            original_incident_path = incident_store.get_knowledge_db_path
            original_get_tool = triage.tools_registry.get_tool
            try:
                triage.get_knowledge_db_path = lambda: knowledge_db
                triage.get_audit_db_path = lambda: audit_db
                incident_store.get_knowledge_db_path = lambda: knowledge_db
                triage.tools_registry.get_tool = lambda name: _fake_tool(name, calls)

                result = await triage.run_alert_auto_triage(
                    {
                        "alerts": [
                            {
                                "status": "firing",
                                "labels": {
                                    "alertname": "ServiceDown",
                                    "service": "nginx",
                                    "severity": "critical",
                                    "instance": "vm-1",
                                },
                                "annotations": {"description": "nginx is not responding"},
                            }
                        ]
                    }
                )

                session_id = result["session_id"]
                incident_id = result["incident_id"]
                events = await incident_store.get_incident_events(incident_id, db_path=knowledge_db)
                async with aiosqlite.connect(audit_db) as db:
                    cursor = await db.execute(
                        "SELECT phase, event_type, metadata FROM audit_logs WHERE session_id = ?",
                        (session_id,),
                    )
                    audit_rows = await cursor.fetchall()
            finally:
                triage.get_knowledge_db_path = original_knowledge_path
                triage.get_audit_db_path = original_audit_path
                incident_store.get_knowledge_db_path = original_incident_path
                triage.tools_registry.get_tool = original_get_tool

        called_names = [name for name, _ in calls]
        assert result["template"] == "service_down"
        assert called_names == [
            "prometheus_range_query",
            "loki_range_query",
            "get_service_status",
            "get_service_logs",
            "get_listening_ports",
            "get_recent_changes",
        ]
        assert result["report"]
        assert "诊断追踪" not in result["report"]
        assert len(events) >= 6
        execution_events = [event for event in events if event["phase"] == "execution"]
        assert execution_events
        assert all(event["evidence"]["execution_state"] == "executed" for event in execution_events)
        assert audit_rows

    asyncio.run(scenario())


def test_disk_webhook_uses_disk_template_and_records_failures_truthfully() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            knowledge_db = str(Path(tmpdir) / "knowledge.db")
            audit_db = str(Path(tmpdir) / "audit.db")
            await _init_temp_db(knowledge_db, audit_db)

            def fake_get_tool(name: str):
                if name == "get_disk_usage":
                    return ToolDefinition(
                        name=name,
                        description="Fake disk",
                        parameters={"type": "object", "properties": {}},
                        function=lambda path="/": ToolResult(success=False, data="", error="df unavailable"),
                        risk_level=RiskLevel.READ,
                        category="disk",
                    )
                if name == "get_recent_changes":
                    return ToolDefinition(
                        name=name,
                        description="Fake changes",
                        parameters={"type": "object", "properties": {}},
                        function=lambda **kwargs: ToolResult(success=True, data={"changes": []}),
                        risk_level=RiskLevel.READ,
                        category="recent_changes",
                    )
                return None

            original_knowledge_path = triage.get_knowledge_db_path
            original_audit_path = triage.get_audit_db_path
            original_incident_path = incident_store.get_knowledge_db_path
            original_get_tool = triage.tools_registry.get_tool
            try:
                triage.get_knowledge_db_path = lambda: knowledge_db
                triage.get_audit_db_path = lambda: audit_db
                incident_store.get_knowledge_db_path = lambda: knowledge_db
                triage.tools_registry.get_tool = fake_get_tool

                result = await triage.run_alert_auto_triage(
                    {
                        "alertname": "HighDiskUsage",
                        "mountpoint": "/var",
                        "severity": "warning",
                        "description": "filesystem usage above threshold",
                    }
                )
                events = await incident_store.get_incident_events(
                    result["incident_id"],
                    db_path=knowledge_db,
                )
            finally:
                triage.get_knowledge_db_path = original_knowledge_path
                triage.get_audit_db_path = original_audit_path
                incident_store.get_knowledge_db_path = original_incident_path
                triage.tools_registry.get_tool = original_get_tool

        failed = [
            event for event in events
            if event["phase"] == "execution" and event["event_type"] == "failure"
        ]
        disk_check = next(check for check in result["checks"] if check["tool_name"] == "get_disk_usage")
        assert result["template"] == "high_disk_usage"
        assert disk_check["status"] == "failed"
        assert "df unavailable" in disk_check["summary"]
        assert failed
        disk_failures = [event for event in failed if event["evidence"]["source"] == "get_disk_usage"]
        assert disk_failures
        assert disk_failures[0]["evidence"]["execution_state"] == "failed"
        assert "df unavailable" in disk_failures[0]["evidence"]["failure_reason"]

    asyncio.run(scenario())


def test_webhook_auto_triage_blocks_non_read_steps() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            knowledge_db = str(Path(tmpdir) / "knowledge.db")
            audit_db = str(Path(tmpdir) / "audit.db")
            await _init_temp_db(knowledge_db, audit_db)
            calls: list[tuple[str, dict]] = []
            step = triage.TriageStep("restart_service", {"service": "nginx"}, "Should not run")

            original_audit_path = triage.get_audit_db_path
            original_incident_path = incident_store.get_knowledge_db_path
            original_get_tool = triage.tools_registry.get_tool
            try:
                triage.get_audit_db_path = lambda: audit_db
                incident_store.get_knowledge_db_path = lambda: knowledge_db
                triage.tools_registry.get_tool = lambda name: _fake_tool(name, calls)
                incident_id = await incident_store.create_incident(
                    session_id="non-read-session",
                    problem_statement="test",
                    source="test",
                    db_path=knowledge_db,
                )
                result = await triage._execute_step("non-read-session", incident_id, step)
            finally:
                triage.get_audit_db_path = original_audit_path
                incident_store.get_knowledge_db_path = original_incident_path
                triage.tools_registry.get_tool = original_get_tool

        assert result["status"] == "skipped"
        assert not calls
        assert result["evidence"]["execution_state"] == "skipped"
        assert "非只读" in result["summary"]

    asyncio.run(scenario())


def test_alertmanager_payload_enriches_observability_and_dashboard_trace() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            knowledge_db = str(Path(tmpdir) / "knowledge.db")
            audit_db = str(Path(tmpdir) / "audit.db")
            await _init_temp_db(knowledge_db, audit_db)
            calls: list[tuple[str, dict]] = []

            original_knowledge_path = triage.get_knowledge_db_path
            original_audit_path = triage.get_audit_db_path
            original_incident_path = incident_store.get_knowledge_db_path
            original_get_tool = triage.tools_registry.get_tool
            try:
                triage.get_knowledge_db_path = lambda: knowledge_db
                triage.get_audit_db_path = lambda: audit_db
                incident_store.get_knowledge_db_path = lambda: knowledge_db
                triage.tools_registry.get_tool = lambda name: _fake_tool(name, calls)

                result = await triage.run_alert_auto_triage(
                    {
                        "receiver": "opsguard",
                        "alerts": [
                            {
                                "status": "firing",
                                "labels": {
                                    "alertname": "HighNginx5xxRate",
                                    "service": "nginx",
                                    "severity": "critical",
                                    "instance": "vm-1:9113",
                                },
                                "annotations": {
                                    "summary": "nginx 5xx rate too high",
                                    "dashboard": "https://grafana.example/d/nginx",
                                    "prometheus_query": 'rate(nginx_http_requests_total{status=~"5.."}[5m])',
                                    "loki_query": '{service="nginx"} |= "502"',
                                },
                            }
                        ],
                    }
                )
                events = await incident_store.get_incident_events(result["incident_id"], db_path=knowledge_db)
            finally:
                triage.get_knowledge_db_path = original_knowledge_path
                triage.get_audit_db_path = original_audit_path
                incident_store.get_knowledge_db_path = original_incident_path
                triage.tools_registry.get_tool = original_get_tool

        called_names = [name for name, _ in calls]
        assert called_names[:2] == ["prometheus_range_query", "loki_range_query"]
        assert result["alert"]["dashboard_url"] == "https://grafana.example/d/nginx"
        assert "Grafana" in result["report"]
        evidence_types = [
            event["evidence"]["evidence_type"]
            for event in events
            if event.get("evidence")
        ]
        assert "alert" in evidence_types
        assert "dashboard_link" in evidence_types
        assert "metric" in evidence_types
        assert "log" in evidence_types

    asyncio.run(scenario())


def main() -> None:
    test_service_down_webhook_creates_session_incident_and_read_only_trace()
    test_disk_webhook_uses_disk_template_and_records_failures_truthfully()
    test_webhook_auto_triage_blocks_non_read_steps()
    test_alertmanager_payload_enriches_observability_and_dashboard_trace()
    print("alert webhook auto-triage regression OK")


if __name__ == "__main__":
    main()
