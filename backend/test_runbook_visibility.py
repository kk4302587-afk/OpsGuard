"""Regression checks for human-readable Runbook replay output."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import aiosqlite

os.chdir(Path(__file__).parent)

from app.agent import runbook_executor
from app.agent.tools_registry import RiskLevel, ToolDefinition
from app.mcp_tools.process_tools import ToolResult


async def _create_runbook_db(db_path: str, runbook_id: str) -> None:
    steps = [
        {
            "tool_name": "get_directory_size",
            "tool_args": {"path": "/tmp"},
            "description": "获取目录总大小",
            "risk_level": "read",
        },
        {
            "tool_name": "find_large_files",
            "tool_args": {"path": "/tmp", "min_size": "10M", "limit": 10},
            "description": "查找大文件",
            "risk_level": "read",
        },
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
                "清理 /tmp 临时文件",
                "自动生成",
                "清理临时文件",
                json.dumps(steps, ensure_ascii=False),
                "2026-05-19T00:00:00",
            ),
        )
        await db.commit()


def test_runbook_replay_streams_plan_step_summaries_and_report() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        original_get_path = runbook_executor.get_knowledge_db_path
        original_get_tool = runbook_executor.tools_registry.get_tool
        original_log = runbook_executor.audit_logger.log

        def fake_get_tool(name: str):
            if name == "get_directory_size":
                return ToolDefinition(
                    name=name,
                    description="获取目录总大小",
                    parameters={"type": "object", "properties": {}},
                    function=lambda path: ToolResult(success=True, data="128M\t/tmp"),
                    risk_level=RiskLevel.READ,
                    category="disk",
                    display_name="目录大小",
                )
            if name == "find_large_files":
                return ToolDefinition(
                    name=name,
                    description="查找大文件",
                    parameters={"type": "object", "properties": {}},
                    function=lambda path, min_size="10M", limit=10: ToolResult(
                        success=True,
                        data={"files": ["/tmp/a.log 32M"], "count": 1},
                    ),
                    risk_level=RiskLevel.READ,
                    category="disk",
                    display_name="查找大文件",
                )
            return None

        async def capture_event(event: dict) -> None:
            events.append(event)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            runbook_id = "rb-visible"
            await _create_runbook_db(db_path, runbook_id)

            try:
                runbook_executor.get_knowledge_db_path = lambda: db_path
                runbook_executor.tools_registry.get_tool = fake_get_tool
                runbook_executor.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

                summary = await runbook_executor.execute_runbook(
                    "session-visible",
                    runbook_id,
                    capture_event,
                )
            finally:
                runbook_executor.get_knowledge_db_path = original_get_path
                runbook_executor.tools_registry.get_tool = original_get_tool
                runbook_executor.audit_logger.log = original_log

        contents = "\n".join(event.get("content", "") for event in events)
        assert "执行计划" in contents
        assert "统计目录 /tmp 的占用大小" in contents
        assert "查找 /tmp 下超过 10M 的大文件" in contents
        assert "技术细节: get_directory_size" in contents
        assert "结果摘要: 目录占用: 128M" in contents
        assert "结果摘要: 找到 1 个候选大文件" in contents
        assert "执行概览" in summary
        assert "系统影响: 本次只执行读取/检查步骤，没有修改系统。" in summary
        assert "下一步建议" in summary

    asyncio.run(scenario())


def main() -> None:
    test_runbook_replay_streams_plan_step_summaries_and_report()
    print("runbook visibility regression OK")


if __name__ == "__main__":
    main()
