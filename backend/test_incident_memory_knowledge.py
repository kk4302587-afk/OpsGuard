"""Regression checks for structured incident-memory knowledge entries."""

import asyncio
import os
import tempfile
from pathlib import Path

import aiosqlite

os.chdir(Path(__file__).parent)

from app.agent import graph
from app.api import knowledge as knowledge_api
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


def test_hybrid_search_returns_evidence_refs_and_fresh_checks() -> None:
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
                    problem_signature="nginx 502 upstream unavailable",
                    diagnosis_path="checked nginx status and app-api port",
                    solution="started app-api and nginx recovered",
                    tools_used=["get_service_status", "check_port_listening"],
                    incident_memory={
                        "symptoms": ["HTTP 502", "upstream connection refused"],
                        "root_cause": "app-api inactive",
                        "evidence": ["app-api inactive"],
                        "evidence_refs": [
                            {
                                "type": "tool_call",
                                "call_id": "call_123",
                                "summary": "app-api inactive",
                            }
                        ],
                        "tool_call_ids": ["call_123"],
                        "source_session_id": "session-1",
                        "source_incident_id": "incident-1",
                        "entities": {
                            "services": ["nginx", "app-api"],
                            "ports": [80, 8080],
                            "paths": ["/etc/nginx/nginx.conf"],
                        },
                        "validation_method": "curl health endpoint returned 200",
                        "applicability_conditions": ["same nginx upstream topology"],
                        "confidence": "high",
                    },
                )

                results = await knowledge_store.search(
                    "502 app-api 8080 upstream",
                    limit=3,
                    filters={"service": "nginx"},
                )
            finally:
                store_module.get_knowledge_db_path = original_get_path

        assert results
        entry = results[0]
        assert entry["evidence_refs"][0]["call_id"] == "call_123"
        assert entry["source_session_id"] == "session-1"
        assert "score_breakdown" in entry
        assert "fts5_keyword" in entry["retrieval_sources"] or "structured_semantic" in entry["retrieval_sources"]
        assert any("app-api" in check or "8080" in check for check in entry["recommended_fresh_checks"])

    asyncio.run(scenario())


def test_missing_validation_is_low_confidence() -> None:
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
                    problem_signature="redis connection errors",
                    diagnosis_path="checked redis logs",
                    solution="suspected redis restart helped",
                    tools_used=["get_service_logs"],
                    incident_memory={
                        "symptoms": ["connection refused"],
                        "evidence": ["redis log had refused connections"],
                        "validation_method": "",
                        "confidence": "high",
                    },
                )

                results = await knowledge_store.search("redis connection refused", limit=3)
            finally:
                store_module.get_knowledge_db_path = original_get_path

        assert results
        assert results[0]["validation_status"] == "missing"
        assert results[0]["confidence"] == "low"
        assert results[0]["safe_to_reuse"] is False

    asyncio.run(scenario())


def test_knowledge_search_api_passes_structured_filters() -> None:
    async def scenario() -> None:
        captured: dict = {}
        original_search = knowledge_api.knowledge_store.search

        async def fake_search(query: str, limit: int = 5, filters: dict | None = None):
            captured["query"] = query
            captured["limit"] = limit
            captured["filters"] = filters
            return [{"problem_signature": "nginx 502"}]

        try:
            knowledge_api.knowledge_store.search = fake_search
            result = await knowledge_api.search_knowledge(
                q="nginx 502",
                service="nginx",
                host="web-1",
                path="/etc/nginx/nginx.conf",
                port="80",
                incident_type="service_connectivity",
                source_modality="real_tool_execution",
                confidence=["high", "medium"],
                min_success_count=2,
                max_age_days=30,
                limit=7,
            )
        finally:
            knowledge_api.knowledge_store.search = original_search

        assert result["entries"][0]["problem_signature"] == "nginx 502"
        assert captured["query"] == "nginx 502"
        assert captured["limit"] == 7
        assert captured["filters"] == {
            "service": "nginx",
            "host": "web-1",
            "path": "/etc/nginx/nginx.conf",
            "port": "80",
            "incident_type": "service_connectivity",
            "source_modality": "real_tool_execution",
            "confidence": ["high", "medium"],
            "min_success_count": 2,
            "max_age_days": 30,
        }

    asyncio.run(scenario())


def test_knowledge_search_api_omits_query_default_sentinels() -> None:
    async def scenario() -> None:
        captured: dict = {}
        original_search = knowledge_api.knowledge_store.search

        async def fake_search(query: str, limit: int = 5, filters: dict | None = None):
            captured["query"] = query
            captured["limit"] = limit
            captured["filters"] = filters
            return [{"problem_signature": "nginx 502"}]

        try:
            knowledge_api.knowledge_store.search = fake_search
            result = await knowledge_api.search_knowledge(q="nginx 502")
        finally:
            knowledge_api.knowledge_store.search = original_search

        assert result["entries"][0]["problem_signature"] == "nginx 502"
        assert captured["filters"] is None

    asyncio.run(scenario())


def test_reviewed_and_deprecated_knowledge_lifecycle() -> None:
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
                    problem_signature="nginx stale bad memory",
                    diagnosis_path="checked nginx",
                    solution="old solution",
                    tools_used=["get_service_status"],
                    incident_memory={
                        "symptoms": ["nginx failed"],
                        "evidence": ["old evidence"],
                        "validation_method": "nginx active",
                        "applicability_conditions": ["nginx"],
                    },
                )
                results = await knowledge_store.search("nginx stale bad memory", limit=3)
                assert results
                entry_id = results[0]["id"]

                reviewed = await knowledge_api.update_knowledge_review(
                    entry_id,
                    knowledge_api.KnowledgeReviewUpdate(review_status="reviewed", owner="platform"),
                )
                assert reviewed["entry"]["review_status"] == "reviewed"
                assert reviewed["entry"]["owner"] == "platform"

                deprecated = await knowledge_api.update_knowledge_review(
                    entry_id,
                    knowledge_api.KnowledgeReviewUpdate(review_status="deprecated"),
                )
                assert deprecated["entry"]["review_status"] == "deprecated"
                assert deprecated["entry"]["staleness_status"] == "deprecated"

                assert await knowledge_store.search("nginx stale bad memory", limit=3) == []
                included = await knowledge_store.search(
                    "nginx stale bad memory",
                    limit=3,
                    filters={"include_deprecated": True},
                )
                assert included
                assert included[0]["review_status"] == "deprecated"
            finally:
                store_module.get_knowledge_db_path = original_get_path

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
        assert "不是当前系统事实" in result["knowledge_hint"]
        assert "不得写成当前已确认状态" in result["knowledge_hint"]
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
    test_hybrid_search_returns_evidence_refs_and_fresh_checks()
    test_missing_validation_is_low_confidence()
    test_knowledge_search_api_passes_structured_filters()
    test_reviewed_and_deprecated_knowledge_lifecycle()
    test_legacy_entries_remain_searchable_after_migration()
    test_agent_knowledge_trace_shows_incident_memory()
    print("incident memory knowledge regression OK")


if __name__ == "__main__":
    main()
