"""Regression checks for Runbook governance metadata and validation."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import aiosqlite

os.chdir(Path(__file__).parent)

from app.agent import runbook_executor
from app.agent.runbook_governance import (
    compute_staleness,
    ensure_runbook_schema,
    record_runbook_result,
    save_or_update_runbook,
    validate_runbook,
)
from app.agent.tools_registry import RiskLevel, ToolDefinition
from app.mcp_tools.process_tools import ToolResult


async def _create_legacy_runbook(db_path: str, runbook_id: str, steps: list[dict]) -> None:
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
                "governance-test",
                "legacy",
                "governance",
                json.dumps(steps, ensure_ascii=False),
                "2026-05-19T00:00:00",
            ),
        )
        await db.commit()


def test_schema_versioning_and_bookkeeping() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            steps = [{"tool_name": "get_directory_size", "tool_args": {"path": tmpdir}}]
            await _create_legacy_runbook(db_path, "rb-governance", steps)

            async with aiosqlite.connect(db_path) as db:
                await ensure_runbook_schema(db)
                db.row_factory = aiosqlite.Row
                cursor = await db.execute("SELECT * FROM runbooks WHERE id = ?", ("rb-governance",))
                row = await cursor.fetchone()
                assert row["version"] == 1
                assert row["success_count"] == 0
                assert row["failure_count"] == 0

                _, updated = await save_or_update_runbook(
                    db,
                    name="governance-test",
                    description="updated",
                    trigger_pattern="governance",
                    steps=steps,
                    session_id="session-1",
                )
                assert updated is True

                cursor = await db.execute("SELECT version, updated_from_session_id FROM runbooks WHERE id = ?", ("rb-governance",))
                row = await cursor.fetchone()
                assert row["version"] == 2
                assert row["updated_from_session_id"] == "session-1"

                await record_runbook_result(db, runbook_id="rb-governance", succeeded=True)
                await record_runbook_result(db, runbook_id="rb-governance", succeeded=False, failure_reason="step 2 failed: boom")
                cursor = await db.execute(
                    "SELECT run_count, success_count, failure_count, last_failure_reason, staleness_status FROM runbooks WHERE id = ?",
                    ("rb-governance",),
                )
                row = await cursor.fetchone()
                assert row["run_count"] == 2
                assert row["success_count"] == 1
                assert row["failure_count"] == 1
                assert row["last_failure_reason"] == "step 2 failed: boom"
                assert row["staleness_status"] == "warning"

    asyncio.run(scenario())


def test_staleness_and_validation_are_truthful() -> None:
    async def scenario() -> None:
        import app.database as database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            missing_steps = [{"tool_name": "missing_tool", "tool_args": {}}]
            assert compute_staleness({"steps": missing_steps}) == "stale"

            target = str(Path(tmpdir) / "does-not-exist.txt")
            steps = [{"tool_name": "delete_file", "tool_args": {"filepath": target}}]
            await _create_legacy_runbook(db_path, "rb-validate", steps)

            original_get_path = database.get_knowledge_db_path
            try:
                database.get_knowledge_db_path = lambda: db_path
                result = await validate_runbook("rb-validate")
            finally:
                database.get_knowledge_db_path = original_get_path

            assert result["status"] == "invalid"
            assert any("target path missing" in issue["message"] for issue in result["issues"])

    asyncio.run(scenario())


def test_executor_updates_governance_stats() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        original_get_path = runbook_executor.get_knowledge_db_path
        original_get_tool = runbook_executor.tools_registry.get_tool
        original_log = runbook_executor.audit_logger.log

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            steps = [{"tool_name": "get_directory_size", "tool_args": {"path": tmpdir}}]
            await _create_legacy_runbook(db_path, "rb-exec", steps)

            def fake_get_tool(name: str):
                if name == "get_directory_size":
                    return ToolDefinition(
                        name=name,
                        description="size",
                        parameters={"type": "object", "properties": {}},
                        function=lambda path: ToolResult(success=True, data="1M\t/tmp"),
                        risk_level=RiskLevel.READ,
                        category="disk",
                    )
                return None

            async def capture_event(event: dict) -> None:
                events.append(event)

            try:
                runbook_executor.get_knowledge_db_path = lambda: db_path
                runbook_executor.tools_registry.get_tool = fake_get_tool
                runbook_executor.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
                summary = await runbook_executor.execute_runbook("session-exec", "rb-exec", capture_event)
            finally:
                runbook_executor.get_knowledge_db_path = original_get_path
                runbook_executor.tools_registry.get_tool = original_get_tool
                runbook_executor.audit_logger.log = original_log

            assert "执行概览" in summary
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT run_count, success_count, failure_count, staleness_status FROM runbooks WHERE id = ?",
                    ("rb-exec",),
                )
                row = await cursor.fetchone()
            assert row["run_count"] == 1
            assert row["success_count"] == 1
            assert row["failure_count"] == 0
            assert row["staleness_status"] == "fresh"

    asyncio.run(scenario())


def main() -> None:
    test_schema_versioning_and_bookkeeping()
    test_staleness_and_validation_are_truthful()
    test_executor_updates_governance_stats()
    print("runbook governance regression OK")


if __name__ == "__main__":
    main()
