"""Regression checks for replaying trace after session/page switches."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import aiosqlite

os.chdir(Path(__file__).parent)

from app import database
from app.api import sessions
from app.incidents import store as incident_store


async def _create_audit_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                phase TEXT NOT NULL,
                event_type TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()


def test_session_trace_replays_incident_timeline_events() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            knowledge_db = str(Path(tmpdir) / "knowledge.db")
            audit_db = str(Path(tmpdir) / "audit.db")
            session_id = "trace-replay"
            await _create_audit_db(audit_db)

            incident_id = await incident_store.create_incident(
                session_id=session_id,
                problem_statement="Check nginx",
                source="test",
                db_path=knowledge_db,
            )
            await incident_store.record_incident_from_message(
                incident_id=incident_id,
                session_id=session_id,
                db_path=knowledge_db,
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
            async with aiosqlite.connect(audit_db) as db:
                await db.execute(
                    """
                    INSERT INTO audit_logs
                        (session_id, timestamp, phase, event_type, content, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        "2026-05-20T01:02:03",
                        "planning",
                        "start",
                        "开始推理",
                        json.dumps({"source": "audit"}),
                    ),
                )
                await db.commit()

            original_knowledge_path = sessions.get_knowledge_db_path
            original_audit_path = database.get_audit_db_path
            try:
                sessions.get_knowledge_db_path = lambda: knowledge_db
                database.get_audit_db_path = lambda: audit_db
                result = await sessions.get_session_trace(session_id)
            finally:
                sessions.get_knowledge_db_path = original_knowledge_path
                database.get_audit_db_path = original_audit_path

        trace = result["trace"]
        phases = [event["phase"] for event in trace]
        execution = [event for event in trace if event["phase"] == "execution"][0]

        assert "input_received" in phases
        assert "planning" in phases
        assert execution["content"] == "Executed get_service_status"
        assert execution["execution_state"] == "executed"
        assert execution["source"] == "get_service_status"

    asyncio.run(scenario())


def test_trace_replay_keeps_repeated_turn_events() -> None:
    events = [
        {
            "id": "incident:first",
            "timestamp": "2026-05-25T05:24:35.000000",
            "phase": "knowledge_retrieval",
            "event_type": "start",
            "content": "检索历史经验...",
        },
        {
            "id": "incident:second",
            "timestamp": "2026-05-25T05:25:35.000000",
            "phase": "knowledge_retrieval",
            "event_type": "start",
            "content": "检索历史经验...",
        },
        {
            "id": "audit:duplicate",
            "timestamp": "2026-05-25T05:25:35.000000",
            "phase": "knowledge_retrieval",
            "event_type": "start",
            "content": "检索历史经验...",
        },
    ]

    trace = sessions._dedupe_and_sort_trace(events)

    assert len(trace) == 2
    assert [event["timestamp"] for event in trace] == [
        "2026-05-25T05:24:35.000000",
        "2026-05-25T05:25:35.000000",
    ]


def main() -> None:
    test_session_trace_replays_incident_timeline_events()
    test_trace_replay_keeps_repeated_turn_events()
    print("session trace replay regression OK")


if __name__ == "__main__":
    main()
