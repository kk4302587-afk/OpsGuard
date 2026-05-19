"""Regression checks for structured incident-memory knowledge entries."""

import asyncio
import os
import tempfile
from pathlib import Path

import aiosqlite

os.chdir(Path(__file__).parent)

from app.agent import graph
from app.knowledge import store as store_module
from app.knowledge.store import ensure_knowledge_schema, knowledge_store


async def _create_legacy_knowledge_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                problem_signature TEXT NOT NULL,
                diagnosis_path TEXT NOT NULL,
                solution TEXT NOT NULL,
                tools_used TEXT,
                success_count INTEGER DEFAULT 1,
                last_used DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()


def test_knowledge_schema_migrates_legacy_table() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            await _create_legacy_knowledge_db(db_path)
            async with aiosqlite.connect(db_path) as db:
                await ensure_knowledge_schema(db)
                cursor = await db.execute("PRAGMA table_info(knowledge_entries)")
                columns = {row[1] for row in await cursor.fetchall()}

        assert "root_cause" in columns
        assert "evidence" in columns
        assert "non_applicability_conditions" in columns
        assert "source_incident_id" in columns

    asyncio.run(scenario())


def test_structured_incident_memory_is_saved_and_retrieved() -> None:
    async def scenario() -> None:
        original_get_path = store_module.get_knowledge_db_path
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            store_module.get_knowledge_db_path = lambda: db_path
            try:
                async with aiosqlite.connect(db_path) as db:
                    await ensure_knowledge_schema(db)
                    await db.commit()

                await knowledge_store.save_resolution(
                    problem_signature="nginx 502 after config change",
                    diagnosis_path="checked nginx status, logs, config syntax",
                    solution="fixed upstream config and reloaded nginx",
                    tools_used=["get_service_status", "get_service_logs", "check_config_syntax"],
                    incident_memory={
                        "symptoms": ["HTTP 502", "nginx active but upstream failed"],
                        "root_cause": "upstream target was wrong after config edit",
                        "evidence": ["journal showed connect() failed", "nginx -t passed after fix"],
                        "successful_actions": ["write_file corrected upstream", "restart_service nginx"],
                        "failed_attempts": ["restart before config fix did not resolve 502"],
                        "validation_method": "curl returned 200 and nginx status active",
                        "applicability_conditions": ["nginx reverse proxy", "502 after config change"],
                        "non_applicability_conditions": [],
                        "source_incident_id": "incident-123",
                        "confidence": "high",
                    },
                )

                results = await knowledge_store.search("nginx 502 upstream config", limit=3)
            finally:
                store_module.get_knowledge_db_path = original_get_path

        assert results
        entry = results[0]
        assert entry["root_cause"] == "upstream target was wrong after config edit"
        assert "HTTP 502" in entry["symptoms"]
        assert entry["validation_method"] == "curl returned 200 and nginx status active"
        assert entry["safe_to_reuse"] is True
        assert "structured root cause" in entry["match_reason"] or "shared terms" in entry["match_reason"]

    asyncio.run(scenario())


def test_legacy_entries_remain_searchable_after_migration() -> None:
    async def scenario() -> None:
        original_get_path = store_module.get_knowledge_db_path
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            await _create_legacy_knowledge_db(db_path)
            store_module.get_knowledge_db_path = lambda: db_path
            try:
                await knowledge_store.save_resolution(
                    problem_signature="restart nginx service",
                    diagnosis_path="called restart_service",
                    solution="nginx restarted",
                    tools_used=["restart_service"],
                )
                results = await knowledge_store.search("restart nginx", limit=3)
            finally:
                store_module.get_knowledge_db_path = original_get_path

        assert results
        assert results[0]["problem_signature"] == "restart nginx service"
        assert results[0]["root_cause"] is None
        assert results[0]["evidence"] == []
        assert results[0]["safe_to_reuse"] is False

    asyncio.run(scenario())


def test_agent_knowledge_trace_shows_incident_memory() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        original_search = graph.knowledge_store.search

        async def fake_search(query: str, limit: int = 3):
            return [
                {
                    "problem_signature": "nginx 502 after config change",
                    "solution": "fix upstream and reload",
                    "match_score": 0.91,
                    "match_reason": "shared terms: nginx, 502; has evidence",
                    "root_cause": "wrong upstream target",
                    "evidence": ["journal showed connect() failed"],
                    "successful_actions": ["write_file corrected upstream"],
                    "failed_attempts": [],
                    "validation_method": "curl returned 200",
                    "applicability_conditions": ["nginx reverse proxy"],
                    "non_applicability_conditions": [],
                    "safe_to_reuse": True,
                }
            ]

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.knowledge_store.search = fake_search
            result = await graph.knowledge_retrieval_node(
                {
                    "session_id": "memory-trace",
                    "user_message": "nginx 502",
                    "send_to_client": capture_event,
                }
            )
        finally:
            graph.knowledge_store.search = original_search

        assert "wrong upstream target" in result["knowledge_hint"]
        assert "写操作仍需重新检查和审批" in result["knowledge_hint"]
        success_events = [
            event for event in events
            if event.get("phase") == "knowledge_retrieval" and event.get("event_type") == "success"
        ]
        assert success_events
        assert "root_cause: wrong upstream target" in success_events[0]["content"]
        assert success_events[0]["execution_state"] == "executed"

    asyncio.run(scenario())


def main() -> None:
    test_knowledge_schema_migrates_legacy_table()
    test_structured_incident_memory_is_saved_and_retrieved()
    test_legacy_entries_remain_searchable_after_migration()
    test_agent_knowledge_trace_shows_incident_memory()
    print("incident memory knowledge regression OK")


if __name__ == "__main__":
    main()
