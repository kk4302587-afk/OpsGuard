"""Regression checks for incident timeline persistence."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import aiosqlite

os.chdir(Path(__file__).parent)

from app.agent import graph, runbook_executor
from app.agent.tools_registry import RiskLevel, ToolDefinition
from app.incidents import store as incident_store
from app.mcp_tools.process_tools import ToolResult


async def _create_runbook_db(db_path: str, runbook_id: str) -> None:
    steps = [
        {
            "tool_name": "get_service_status",
            "tool_args": {"service": "nginx"},
            "description": "Check service status",
            "risk_level": "read",
        }
    ]
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE runbooks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                trigger_pattern TEXT,
                steps TEXT NOT NULL,
                run_count INTEGER DEFAULT 0,
                last_run TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "INSERT INTO runbooks (id, name, description, trigger_pattern, steps, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                runbook_id,
                "Check nginx",
                "Read-only service check",
                "nginx status",
                json.dumps(steps),
                "2026-05-19T00:00:00",
            ),
        )
        await db.commit()


def test_incident_store_records_real_trace_evidence() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            incident_id = await incident_store.create_incident(
                session_id="session-incident",
                problem_statement="Check nginx status",
                source="test",
                db_path=db_path,
            )
            await incident_store.record_incident_from_message(
                incident_id=incident_id,
                session_id="session-incident",
                db_path=db_path,
                message={
                    "type": "trace",
                    "phase": "execution",
                    "event_type": "success",
                    "content": "Executed get_service_status",
                    "claim": "get_service_status executed against nginx",
                    "evidence_type": "command",
                    "source": "get_service_status",
                    "observed": "active",
                    "confidence": "high",
                    "execution_state": "executed",
                },
            )
            await incident_store.record_incident_from_message(
                incident_id=incident_id,
                session_id="session-incident",
                db_path=db_path,
                message={
                    "type": "approval_request",
                    "command": "restart_service({\"service\":\"nginx\"})",
                    "description": "Restart service",
                    "impact": "No reliable automated rollback",
                },
            )
            await incident_store.finalize_incident(
                incident_id=incident_id,
                final_summary="nginx is active",
                status="resolved",
                db_path=db_path,
            )

            incidents = await incident_store.get_incidents(
                session_id="session-incident",
                db_path=db_path,
            )
            events = await incident_store.get_incident_events(
                incident_id,
                db_path=db_path,
            )
            response = await incident_store.append_incident_reference(
                "done",
                incident_id,
                db_path=db_path,
            )

        assert len(incidents) == 1
        assert incidents[0]["status"] == "resolved"
        assert len(events) == 3
        execution = [event for event in events if event["phase"] == "execution"][0]
        assert execution["evidence"]["execution_state"] == "executed"
        assert execution["evidence"]["source"] == "get_service_status"
        approval = [event for event in events if event["phase"] == "approval_request"][0]
        assert approval["evidence"]["execution_state"] == "skipped"
        assert f"/api/incidents/{incident_id}/events" in response

    asyncio.run(scenario())


def test_agent_run_creates_incident_and_appends_reference() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        original_get_path = graph.incident_store.get_knowledge_db_path
        original_call_llm = graph.call_llm
        original_search = graph.knowledge_store.search
        original_log = graph.audit_logger.log

        async def fake_call_llm(messages, tools=None):
            return {"content": "I checked the available context. No write action was executed.", "tool_calls": []}

        async def fake_search(query: str, limit: int = 3):
            return []

        async def capture_event(event: dict) -> None:
            events.append(event)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            try:
                graph.incident_store.get_knowledge_db_path = lambda: db_path
                graph.call_llm = fake_call_llm
                graph.knowledge_store.search = fake_search
                graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

                response = await graph.run_agent(
                    session_id="agent-incident",
                    user_message="Check nginx status",
                    conversation_history=[],
                    send_to_client=capture_event,
                )
                incidents = await incident_store.get_incidents(
                    session_id="agent-incident",
                    db_path=db_path,
                )
                incident_events = await incident_store.get_incident_events(
                    incidents[0]["id"],
                    db_path=db_path,
                )
            finally:
                graph.incident_store.get_knowledge_db_path = original_get_path
                graph.call_llm = original_call_llm
                graph.knowledge_store.search = original_search
                graph.audit_logger.log = original_log

        assert "Incident timeline" in response
        assert len(incidents) == 1
        assert incidents[0]["status"] == "resolved"
        assert any(event["phase"] == "knowledge_retrieval" for event in incident_events)
        assert any(event["phase"] == "response" for event in incident_events)
        assert events

    asyncio.run(scenario())


def test_runbook_execution_creates_incident_from_real_step_events() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        original_get_path = runbook_executor.get_knowledge_db_path
        original_get_tool = runbook_executor.tools_registry.get_tool
        original_log = runbook_executor.audit_logger.log

        def fake_get_tool(name: str):
            if name == "get_service_status":
                return ToolDefinition(
                    name=name,
                    description="Get service status",
                    parameters={"type": "object", "properties": {}},
                    function=lambda service: ToolResult(success=True, data={"service": service, "status": "active"}),
                    risk_level=RiskLevel.READ,
                    category="service",
                    display_name="Get service status",
                )
            return None

        async def capture_event(event: dict) -> None:
            events.append(event)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            runbook_id = "rb-incident"
            await _create_runbook_db(db_path, runbook_id)
            try:
                runbook_executor.get_knowledge_db_path = lambda: db_path
                runbook_executor.tools_registry.get_tool = fake_get_tool
                runbook_executor.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

                summary = await runbook_executor.execute_runbook(
                    "runbook-incident",
                    runbook_id,
                    capture_event,
                )
                incidents = await incident_store.get_incidents(
                    session_id="runbook-incident",
                    db_path=db_path,
                )
                incident_events = await incident_store.get_incident_events(
                    incidents[0]["id"],
                    db_path=db_path,
                )
            finally:
                runbook_executor.get_knowledge_db_path = original_get_path
                runbook_executor.tools_registry.get_tool = original_get_tool
                runbook_executor.audit_logger.log = original_log

        assert "Incident timeline" in summary
        assert len(incidents) == 1
        assert incidents[0]["status"] == "resolved"
        execution = [event for event in incident_events if event["phase"] == "execution"]
        assert execution
        assert execution[0]["evidence"]["execution_state"] == "executed"
        assert any(event["phase"] == "planning" for event in incident_events)
        assert events

    asyncio.run(scenario())


def main() -> None:
    test_incident_store_records_real_trace_evidence()
    test_agent_run_creates_incident_and_appends_reference()
    test_runbook_execution_creates_incident_from_real_step_events()
    print("incident timeline regression OK")


if __name__ == "__main__":
    main()
