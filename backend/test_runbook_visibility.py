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


async def _create_service_status_runbook_db(db_path: str, runbook_id: str) -> None:
    steps = [
        {
            "tool_name": "get_service_status",
            "tool_args": {"service": "nginx"},
            "description": "查看 nginx 服务当前运行状态",
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
                "查看 nginx 服务当前运行状态",
                "自动生成",
                "查看 nginx 状态",
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
        original_hybrid = runbook_executor.settings.runbook.hybrid_final_summary

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
                runbook_executor.settings.runbook.hybrid_final_summary = False

                summary = await runbook_executor.execute_runbook(
                    "session-visible",
                    runbook_id,
                    capture_event,
                )
            finally:
                runbook_executor.get_knowledge_db_path = original_get_path
                runbook_executor.tools_registry.get_tool = original_get_tool
                runbook_executor.audit_logger.log = original_log
                runbook_executor.settings.runbook.hybrid_final_summary = original_hybrid

        contents = "\n".join(event.get("content", "") for event in events)
        assert events[0]["phase"] == "input_received"
        assert events[0]["content"] == "执行 Runbook「清理 /tmp 临时文件」"
        assert "执行计划" in contents
        assert "统计目录 /tmp 的占用大小" in contents
        assert "查找 /tmp 下超过 10M 的大文件" in contents
        assert "工具：目录大小（get_directory_size）" in contents
        assert "参数：路径=/tmp" in contents
        assert "调用工具: 目录大小（get_directory_size）" in contents
        assert "执行参数: 路径=/tmp" in contents
        assert "目标对象: /tmp" in contents
        assert "技术细节:" not in contents
        assert "结果摘要: 目录占用: 128M" in contents
        assert "结果摘要: 找到 1 个候选大文件" in contents
        execution_events = [
            event for event in events
            if event.get("phase") == "execution" and event.get("event_type") == "success"
        ]
        assert execution_events
        assert all(event.get("execution_state") == "executed" for event in execution_events)
        assert all(event.get("source") in {"目录大小", "查找大文件"} for event in execution_events)
        assert "执行概览" in summary
        assert "执行明细" in summary
        assert "工具：目录大小" in summary
        assert "参数：路径=/tmp" in summary
        assert "系统影响: 本次只执行读取/检查步骤，没有修改系统。" in summary
        assert "下一步建议" in summary

    asyncio.run(scenario())


def test_runbook_summary_highlights_service_status() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        original_get_path = runbook_executor.get_knowledge_db_path
        original_get_tool = runbook_executor.tools_registry.get_tool
        original_log = runbook_executor.audit_logger.log
        original_call_llm = runbook_executor.call_llm
        original_hybrid = runbook_executor.settings.runbook.hybrid_final_summary

        service_output = """
● nginx.service - The nginx HTTP and reverse proxy server
     Loaded: loaded (/usr/lib/systemd/system/nginx.service; enabled; preset: disabled)
     Active: active (running) since Sun 2026-07-05 14:58:00 CST; 5min ago
"""

        def fake_get_tool(name: str):
            if name == "get_service_status":
                return ToolDefinition(
                    name=name,
                    description="查看服务状态",
                    parameters={"type": "object", "properties": {}},
                    function=lambda service: ToolResult(success=True, data=service_output),
                    risk_level=RiskLevel.READ,
                    category="service",
                    display_name="服务状态",
                )
            return None

        async def capture_event(event: dict) -> None:
            events.append(event)

        async def fake_call_llm(messages, tools=None):
            return {
                "content": (
                    "**结论**：Runbook「查看 nginx 服务当前运行状态」执行完成，"
                    "**nginx 当前状态为运行中**，本次没有修改系统。\n\n"
                    "**关键结论**\n"
                    "- nginx 当前状态：运行中（Active=active (running)，Loaded=loaded，开机状态=enabled）\n\n"
                    "**执行概览**\n"
                    "- 共 1 步，成功 1 步；全部为只读检查。\n"
                    "- 系统影响：仅检查，未修改系统。\n\n"
                    "**执行明细**\n"
                    "1. 服务状态：读取 nginx 的 systemd 状态成功。\n\n"
                    "**下一步建议**：nginx 当前运行正常，无需处理。"
                )
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            runbook_id = "rb-nginx-status"
            await _create_service_status_runbook_db(db_path, runbook_id)

            try:
                runbook_executor.get_knowledge_db_path = lambda: db_path
                runbook_executor.tools_registry.get_tool = fake_get_tool
                runbook_executor.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
                runbook_executor.call_llm = fake_call_llm
                runbook_executor.settings.runbook.hybrid_final_summary = True

                summary = await runbook_executor.execute_runbook(
                    "session-nginx-status",
                    runbook_id,
                    capture_event,
                    user_message="执行 Runbook「查看 nginx 服务当前运行状态」",
                )
            finally:
                runbook_executor.get_knowledge_db_path = original_get_path
                runbook_executor.tools_registry.get_tool = original_get_tool
                runbook_executor.audit_logger.log = original_log
                runbook_executor.call_llm = original_call_llm
                runbook_executor.settings.runbook.hybrid_final_summary = original_hybrid

        assert events[0]["phase"] == "input_received"
        assert "执行 Runbook「查看 nginx 服务当前运行状态」" in events[0]["content"]
        assert "关键结论" in summary
        assert "Runbook「查看 nginx 服务当前运行状态」执行完成" in summary
        assert "nginx 当前状态：运行中" in summary
        assert "Active=active (running)" in summary
        assert "Loaded=loaded" in summary
        assert "开机状态=enabled" in summary
        assert summary.index("关键结论") < summary.index("执行明细")
        assert any(event.get("source") == "runbook_hybrid_summary" for event in events)

    asyncio.run(scenario())


def test_runbook_hybrid_summary_falls_back_when_llm_overclaims() -> None:
    async def scenario() -> None:
        original_call_llm = runbook_executor.call_llm
        original_hybrid = runbook_executor.settings.runbook.hybrid_final_summary

        async def fake_call_llm(messages, tools=None):
            return {
                "content": (
                    "**结论**：Runbook「查看 nginx 服务当前运行状态」执行完成，我已重启 nginx。\n\n"
                    "**执行概览**\n- 已重启服务。"
                )
            }

        try:
            runbook_executor.call_llm = fake_call_llm
            runbook_executor.settings.runbook.hybrid_final_summary = True
            deterministic = (
                "Runbook「查看 nginx 服务当前运行状态」执行完成\n\n"
                "执行概览：\n- 共 1 步，成功 1 步。\n"
                "- 系统影响: 本次只执行读取/检查步骤，没有修改系统。\n\n"
                "关键结论：\n- nginx 当前状态：运行中（Active=active (running)，Loaded=loaded，开机状态=enabled）\n\n"
                "执行明细：\n1. [成功] 检查服务 nginx 的状态\n"
                "   工具：服务状态\n"
                "   参数：服务=nginx\n"
                "   结果：Active: active (running)"
            )
            summary = await runbook_executor._hybrid_final_summary(
                runbook_name="查看 nginx 服务当前运行状态",
                plan_steps=[{"risk_level": RiskLevel.READ}],
                executed=[
                    {
                        "step": 1,
                        "tool": "get_service_status",
                        "display_name": "服务状态",
                        "action": "检查服务 nginx 的状态",
                        "risk": "只读检查",
                        "risk_level": RiskLevel.READ,
                        "success": True,
                        "summary": "Active: active (running)",
                        "target": "nginx",
                        "service_status": {
                            "service": "nginx",
                            "active": "active",
                            "substate": "running",
                            "loaded": "loaded",
                            "unit_file_state": "enabled",
                        },
                    }
                ],
                failed_step=None,
                abort_reason=None,
                deterministic_summary=deterministic,
            )
        finally:
            runbook_executor.call_llm = original_call_llm
            runbook_executor.settings.runbook.hybrid_final_summary = original_hybrid

        assert summary == deterministic
        assert "我已重启 nginx" not in summary

    asyncio.run(scenario())


def test_runbook_result_summaries_do_not_dump_raw_json() -> None:
    system_summary = runbook_executor._summarize_result(
        "system_overview",
        {
            "success": True,
            "data": {
                "uptime": "2d 11h",
                "load_avg": {"1min": "2.14", "5min": "1.60", "15min": "1.14"},
                "memory": {"percent": "92%"},
                "disk_root": "27% /",
            },
        },
    )
    health_summary = runbook_executor._summarize_result(
        "health_check",
        {
            "success": True,
            "data": {
                "status": "critical",
                "issues": [
                    {"type": "disk", "severity": "high", "detail": "/run/media/root is 100% full"},
                    {"type": "memory", "severity": "high", "detail": "Memory usage: 92%"},
                ],
            },
        },
    )

    assert "运行时间 2d 11h" in system_summary
    assert "负载 2.14/1.60/1.14" in system_summary
    assert "{" not in system_summary
    assert "健康状态 critical，发现 2 个问题" in health_summary
    assert "{" not in health_summary


def main() -> None:
    test_runbook_replay_streams_plan_step_summaries_and_report()
    test_runbook_summary_highlights_service_status()
    test_runbook_hybrid_summary_falls_back_when_llm_overclaims()
    test_runbook_result_summaries_do_not_dump_raw_json()
    print("runbook visibility regression OK")


if __name__ == "__main__":
    main()
