"""Regression checks for real knowledge-base retrieval.

The tests use a temporary SQLite database and do not touch the host system.
"""

import asyncio
import os
import tempfile
from pathlib import Path

import aiosqlite

os.chdir(Path(__file__).parent)

from app.agent import graph
from app.knowledge import store as store_module
from app.knowledge.store import KnowledgeSearchError, knowledge_store


async def _create_knowledge_db(db_path: str) -> None:
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


def test_chinese_no_space_request_matches_saved_nginx_resolution() -> None:
    async def scenario() -> None:
        original_get_path = store_module.get_knowledge_db_path
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            await _create_knowledge_db(db_path)
            store_module.get_knowledge_db_path = lambda: db_path
            try:
                await knowledge_store.save_resolution(
                    problem_signature="重启Nginx服务",
                    diagnosis_path="调用 restart_service 并验证服务状态",
                    solution="nginx 服务已成功重启",
                    tools_used=["restart_service"],
                )

                results = await knowledge_store.search("帮我重启nginx", limit=3)

                assert results
                assert results[0]["problem_signature"] == "重启Nginx服务"
                assert results[0]["match_score"] >= 0.34
            finally:
                store_module.get_knowledge_db_path = original_get_path

    asyncio.run(scenario())


def test_english_request_matches_saved_chinese_resolution() -> None:
    async def scenario() -> None:
        original_get_path = store_module.get_knowledge_db_path
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            await _create_knowledge_db(db_path)
            store_module.get_knowledge_db_path = lambda: db_path
            try:
                await knowledge_store.save_resolution(
                    problem_signature="重启Nginx服务",
                    diagnosis_path="调用 restart_service",
                    solution="restart nginx service completed",
                    tools_used=["restart_service"],
                )

                results = await knowledge_store.search("restart nginx service", limit=3)

                assert results
                assert results[0]["problem_signature"] == "重启Nginx服务"
            finally:
                store_module.get_knowledge_db_path = original_get_path

    asyncio.run(scenario())


def test_unrelated_query_returns_empty_list() -> None:
    async def scenario() -> None:
        original_get_path = store_module.get_knowledge_db_path
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            await _create_knowledge_db(db_path)
            store_module.get_knowledge_db_path = lambda: db_path
            try:
                await knowledge_store.save_resolution(
                    problem_signature="重启Nginx服务",
                    diagnosis_path="调用 restart_service",
                    solution="nginx 服务已成功重启",
                    tools_used=["restart_service"],
                )

                results = await knowledge_store.search("检查磁盘 inode 使用率", limit=3)

                assert results == []
            finally:
                store_module.get_knowledge_db_path = original_get_path

    asyncio.run(scenario())


def test_search_failure_is_not_reported_as_no_history() -> None:
    async def scenario() -> None:
        events = []
        original_search = graph.knowledge_store.search
        original_log = graph.audit_logger.log

        async def failing_search(query: str, limit: int = 5) -> list[dict]:
            raise KnowledgeSearchError("database unavailable")

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.knowledge_store.search = failing_search
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
            result = await graph.knowledge_retrieval_node({
                "session_id": "knowledge-failure",
                "user_message": "帮我重启nginx",
                "send_to_client": capture_event,
            })

            assert result == {"knowledge_hint": ""}
            assert any(
                event["phase"] == "knowledge_retrieval"
                and event["event_type"] == "failure"
                and "知识检索失败" in event["content"]
                for event in events
            )
            assert not any(event.get("content") == "无相关历史经验" for event in events)
        finally:
            graph.knowledge_store.search = original_search
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def test_empty_search_trace_evidence_is_chinese() -> None:
    async def scenario() -> None:
        events = []
        original_search = graph.knowledge_store.search
        original_log = graph.audit_logger.log

        async def empty_search(query: str, limit: int = 5) -> list[dict]:
            return []

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.knowledge_store.search = empty_search
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
            result = await graph.knowledge_retrieval_node({
                "session_id": "knowledge-empty",
                "user_message": "你好",
                "send_to_client": capture_event,
            })

            assert result == {"knowledge_hint": ""}
            success_events = [
                event for event in events
                if event["phase"] == "knowledge_retrieval" and event["event_type"] == "success"
            ]
            assert any(event["content"] == "无相关历史经验" for event in success_events)
            assert any(event.get("observed") == "检索完成，未找到匹配经验" for event in success_events)
            assert not any("search completed" in event.get("observed", "") for event in success_events)
        finally:
            graph.knowledge_store.search = original_search
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def main() -> None:
    test_chinese_no_space_request_matches_saved_nginx_resolution()
    test_english_request_matches_saved_chinese_resolution()
    test_unrelated_query_returns_empty_list()
    test_search_failure_is_not_reported_as_no_history()
    test_empty_search_trace_evidence_is_chinese()
    print("knowledge retrieval regression OK")


if __name__ == "__main__":
    main()
