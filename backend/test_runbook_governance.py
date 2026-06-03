"""Regression checks for Runbook governance metadata and validation."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import aiosqlite

os.chdir(Path(__file__).parent)

from app.agent import runbook_executor
from app.agent.runbook_preflight import preflight_runbook
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


def test_runbook_preflight_extracts_variables_and_renders_steps() -> None:
    async def scenario() -> None:
        runbook = {
            "name": "service status",
            "staleness_status": "fresh",
            "variables": [{"name": "service", "type": "service", "required": True}],
            "steps": [
                {"tool_name": "get_service_status", "tool_args": {"service": "{{service}}"}},
            ],
        }
        original = runbook_executor.tools_registry.get_tool
        try:
            result = await preflight_runbook(runbook, "检查 nginx 服务状态")
        finally:
            runbook_executor.tools_registry.get_tool = original

        assert result["extracted_variables"]["service"] == "nginx"
        assert result["rendered_steps"][0]["tool_args"]["service"] == "nginx"
        assert result["status"] in {"applicable", "not_applicable", "uncertain"}

    asyncio.run(scenario())


def test_executor_renders_template_steps_before_execution() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        original_get_path = runbook_executor.get_knowledge_db_path
        original_get_tool = runbook_executor.tools_registry.get_tool
        original_execute = runbook_executor.execute_tool
        original_log = runbook_executor.audit_logger.log

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            steps = [{"tool_name": "read_file", "tool_args": {"filepath": "{{filepath}}"}}]
            target = Path(tmpdir) / "sample.txt"
            target.write_text("hello", encoding="utf-8")
            await _create_legacy_runbook(db_path, "rb-template", steps)

            async with aiosqlite.connect(db_path) as db:
                await ensure_runbook_schema(db)
                await db.execute(
                    "UPDATE runbooks SET variables = ? WHERE id = ?",
                    (json.dumps([{"name": "filepath", "type": "filepath", "required": True}], ensure_ascii=False), "rb-template"),
                )
                await db.commit()

            captured_args: list[dict] = []

            def fake_get_tool(name: str):
                if name == "read_file":
                    return ToolDefinition(
                        name=name,
                        description="read",
                        parameters={"type": "object", "properties": {}},
                        function=lambda filepath: ToolResult(success=True, data={"size": 5, "truncated": False}),
                        risk_level=RiskLevel.READ,
                        category="file",
                        display_name="读取文件",
                    )
                return None

            async def fake_execute_tool(tool_name, tool_args, tool_def=None):
                captured_args.append(tool_args)
                return ToolResult(success=True, data={"size": 5, "truncated": False})

            async def capture_event(event: dict) -> None:
                events.append(event)

            try:
                runbook_executor.get_knowledge_db_path = lambda: db_path
                runbook_executor.tools_registry.get_tool = fake_get_tool
                runbook_executor.execute_tool = fake_execute_tool
                runbook_executor.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
                summary = await runbook_executor.execute_runbook(
                    "session-template",
                    "rb-template",
                    capture_event,
                    user_message=f"读取 {target} 的内容",
                )
            finally:
                runbook_executor.get_knowledge_db_path = original_get_path
                runbook_executor.tools_registry.get_tool = original_get_tool
                runbook_executor.execute_tool = original_execute
                runbook_executor.audit_logger.log = original_log

            assert "执行概览" in summary
            assert captured_args == [{"filepath": str(target)}]

    asyncio.run(scenario())


def test_preflight_executes_readonly_preconditions() -> None:
    async def scenario() -> None:
        original_get_tool = runbook_executor.tools_registry.get_tool
        original_preflight_get_tool = __import__(
            "app.agent.runbook_preflight",
            fromlist=["tools_registry"],
        ).tools_registry.get_tool
        original_execute = __import__(
            "app.agent.runbook_preflight",
            fromlist=["execute_tool"],
        ).execute_tool

        def fake_get_tool(name: str):
            if name == "check_file_info":
                return ToolDefinition(
                    name=name,
                    description="info",
                    parameters={"type": "object", "properties": {}},
                    function=lambda filepath: ToolResult(success=True, data={"exists": True}),
                    risk_level=RiskLevel.READ,
                    category="file",
                )
            return original_get_tool(name)

        async def fake_execute_tool(tool_name, tool_args, tool_def=None):
            return ToolResult(success=True, data={"exists": True, "status": "ok"})

        preflight_module = __import__("app.agent.runbook_preflight", fromlist=["execute_tool", "tools_registry"])
        try:
            preflight_module.tools_registry.get_tool = fake_get_tool
            preflight_module.execute_tool = fake_execute_tool
            result = await preflight_runbook({
                "name": "precondition",
                "staleness_status": "fresh",
                "steps": [{"tool_name": "check_file_info", "tool_args": {"filepath": __file__}}],
                "preconditions": [{
                    "description": "文件必须可读",
                    "tool_name": "check_file_info",
                    "tool_args": {"filepath": __file__},
                    "expect": {"success": True, "status": "ok"},
                }],
            }, "")
        finally:
            preflight_module.tools_registry.get_tool = original_preflight_get_tool
            preflight_module.execute_tool = original_execute

        assert result["status"] == "applicable"
        assert result["preconditions_summary"]["counts"]["passed"] == 1
        assert any(check.get("kind") == "precondition" and check["status"] == "passed" for check in result["checks"])

    asyncio.run(scenario())


def test_executor_runs_named_failure_branch() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        original_get_path = runbook_executor.get_knowledge_db_path
        original_get_tool = runbook_executor.tools_registry.get_tool
        original_execute = runbook_executor.execute_tool
        original_log = runbook_executor.audit_logger.log

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            failing_target = Path(tmpdir) / "exists-but-tool-fails.txt"
            failing_target.write_text("x", encoding="utf-8")
            steps = [{
                "tool_name": "check_file_info",
                "tool_args": {"filepath": str(failing_target)},
                "on_failure": {"branch": "diagnose_missing"},
            }]
            await _create_legacy_runbook(db_path, "rb-branch", steps)
            async with aiosqlite.connect(db_path) as db:
                await ensure_runbook_schema(db)
                await db.execute(
                    "UPDATE runbooks SET failure_branches = ? WHERE id = ?",
                    (
                        json.dumps([{
                            "name": "diagnose_missing",
                            "steps": [{"tool_name": "list_directory", "tool_args": {"dirpath": tmpdir}}],
                        }], ensure_ascii=False),
                        "rb-branch",
                    ),
                )
                await db.commit()

            def fake_get_tool(name: str):
                if name in {"check_file_info", "list_directory"}:
                    return ToolDefinition(
                        name=name,
                        description=name,
                        parameters={"type": "object", "properties": {}},
                        function=lambda **kwargs: ToolResult(success=True, data={}),
                        risk_level=RiskLevel.READ,
                        category="file",
                        display_name=name,
                    )
                return None

            async def fake_execute_tool(tool_name, tool_args, tool_def=None):
                if tool_name == "check_file_info":
                    return ToolResult(success=False, data="", error="missing")
                return ToolResult(success=True, data={"count": 1, "items": ["fallback"]})

            async def capture_event(event: dict) -> None:
                events.append(event)

            try:
                runbook_executor.get_knowledge_db_path = lambda: db_path
                runbook_executor.tools_registry.get_tool = fake_get_tool
                runbook_executor.execute_tool = fake_execute_tool
                runbook_executor.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
                summary = await runbook_executor.execute_runbook("session-branch", "rb-branch", capture_event)
            finally:
                runbook_executor.get_knowledge_db_path = original_get_path
                runbook_executor.tools_registry.get_tool = original_get_tool
                runbook_executor.execute_tool = original_execute
                runbook_executor.audit_logger.log = original_log

        assert "执行概览" in summary
        assert "list_directory" in summary
        assert any("失败分支" in str(event.get("content", "")) for event in events)

    asyncio.run(scenario())


def test_executor_treats_invalid_validation_result_as_branch_failure() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        original_get_path = runbook_executor.get_knowledge_db_path
        original_get_tool = runbook_executor.tools_registry.get_tool
        original_execute = runbook_executor.execute_tool
        original_log = runbook_executor.audit_logger.log

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            bad_config = Path(tmpdir) / "bad-nginx.conf"
            bad_config.write_text("this is not valid nginx config", encoding="utf-8")
            steps = [{
                "tool_name": "check_config_syntax",
                "tool_args": {"filepath": str(bad_config)},
                "on_failure": {"branch": "list_parent"},
            }]
            await _create_legacy_runbook(db_path, "rb-invalid-config", steps)
            async with aiosqlite.connect(db_path) as db:
                await ensure_runbook_schema(db)
                await db.execute(
                    "UPDATE runbooks SET failure_branches = ? WHERE id = ?",
                    (
                        json.dumps([{
                            "name": "list_parent",
                            "steps": [{"tool_name": "list_directory", "tool_args": {"dirpath": tmpdir}}],
                        }], ensure_ascii=False),
                        "rb-invalid-config",
                    ),
                )
                await db.commit()

            def fake_get_tool(name: str):
                if name in {"check_config_syntax", "list_directory"}:
                    return ToolDefinition(
                        name=name,
                        description=name,
                        parameters={"type": "object", "properties": {}},
                        function=lambda **kwargs: ToolResult(success=True, data={}),
                        risk_level=RiskLevel.READ,
                        category="config" if name == "check_config_syntax" else "file",
                        display_name=name,
                    )
                return None

            async def fake_execute_tool(tool_name, tool_args, tool_def=None):
                if tool_name == "check_config_syntax":
                    return ToolResult(success=True, data={"checked": True, "valid": False, "errors": "syntax error"})
                return ToolResult(success=True, data={"count": 1, "items": ["fallback"]})

            async def capture_event(event: dict) -> None:
                events.append(event)

            try:
                runbook_executor.get_knowledge_db_path = lambda: db_path
                runbook_executor.tools_registry.get_tool = fake_get_tool
                runbook_executor.execute_tool = fake_execute_tool
                runbook_executor.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
                summary = await runbook_executor.execute_runbook("session-invalid-config", "rb-invalid-config", capture_event)
            finally:
                runbook_executor.get_knowledge_db_path = original_get_path
                runbook_executor.tools_registry.get_tool = original_get_tool
                runbook_executor.execute_tool = original_execute
                runbook_executor.audit_logger.log = original_log

        assert "执行概览" in summary
        assert "check_config_syntax" in summary
        assert "list_directory" in summary
        assert any("失败分支" in str(event.get("content", "")) for event in events)

    asyncio.run(scenario())


def main() -> None:
    test_schema_versioning_and_bookkeeping()
    test_staleness_and_validation_are_truthful()
    test_executor_updates_governance_stats()
    test_runbook_preflight_extracts_variables_and_renders_steps()
    test_executor_renders_template_steps_before_execution()
    test_preflight_executes_readonly_preconditions()
    test_executor_runs_named_failure_branch()
    test_executor_treats_invalid_validation_result_as_branch_failure()
    print("runbook governance regression OK")


if __name__ == "__main__":
    main()
