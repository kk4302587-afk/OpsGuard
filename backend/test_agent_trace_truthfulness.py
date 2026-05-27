"""Regression checks that Agent trace success matches real tool results."""

import asyncio
import os
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent import graph
from app.agent.tools_registry import RiskLevel, ToolDefinition
from app.mcp_tools.process_tools import ToolResult


def test_tool_result_failure_is_traced_as_failure() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0

        original_call_llm = graph.call_llm
        original_get_tool = graph.tools_registry.get_tool
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log

        def fake_tool() -> ToolResult:
            return ToolResult(success=False, data="", error="real command failed")

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            if llm_calls == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {"id": "tool-1", "name": "fake_check", "arguments": {}}
                    ],
                }
            return {"content": "工具返回失败，未完成检查。", "tool_calls": []}

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.tools_registry.get_tool = lambda name: ToolDefinition(
                name="fake_check",
                description="fake check",
                parameters={"type": "object", "properties": {}},
                function=fake_tool,
                risk_level=RiskLevel.READ,
                category="test",
            )
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

            result = await graph.reasoning_node({
                "session_id": "trace-truth",
                "user_message": "检查一下",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
            })

            assert result["final_response"] == "工具返回失败，未完成检查。"
            assert any(
                event["phase"] == "execution"
                and event["event_type"] == "failure"
                and "real command failed" in event["content"]
                and event.get("execution_state") == "failed"
                and event.get("source") == "fake_check"
                and "real command failed" in event.get("failure_reason", "")
                for event in events
            )
            assert not any(
                event["phase"] == "execution"
                and event["event_type"] == "success"
                and "fake_check" in event["content"]
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_tool = original_get_tool
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def test_read_only_status_request_blocks_write_tool_choice() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0
        write_tool_executed = False

        original_call_llm = graph.call_llm
        original_get_tool = graph.tools_registry.get_tool
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log

        def fake_restart_service(service: str) -> ToolResult:
            nonlocal write_tool_executed
            write_tool_executed = True
            return ToolResult(success=True, data=f"Service {service} restarted")

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            if llm_calls == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {"id": "tool-1", "name": "restart_service", "arguments": {"service": "nginx"}}
                    ],
                }
            return {"content": "本轮只查询状态，未执行启动或重启。", "tool_calls": []}

        async def capture_event(event: dict) -> None:
            events.append(event)

        def fake_get_tool(name: str):
            if name == "restart_service":
                return ToolDefinition(
                    name=name,
                    description="Restart service",
                    parameters={"type": "object", "properties": {}},
                    function=fake_restart_service,
                    risk_level=RiskLevel.WRITE,
                    category="service",
                )
            return original_get_tool(name)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.tools_registry.get_tool = fake_get_tool
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

            result = await graph.reasoning_node({
                "session_id": "read-only-block",
                "user_message": "查看 nginx 当前状态",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
            })

            assert result["final_response"] == "本轮只查询状态，未执行启动或重启。"
            assert write_tool_executed is False
            assert any(
                event["phase"] == "tool_call"
                and event["event_type"] == "blocked"
                and event.get("source") == "read_only_intent_guard"
                and event.get("execution_state") == "skipped"
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_tool = original_get_tool
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def test_repeated_write_completion_claim_without_tool_is_blocked() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0

        original_call_llm = graph.call_llm
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            return {
                "content": "nginx.service 已停止，操作已完成。",
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

            result = await graph.reasoning_node({
                "session_id": "write-claim-block",
                "user_message": "帮我关闭 nginx",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
            })

            assert llm_calls == 2
            assert result["final_response"].startswith("本次没有执行该写操作")
            assert "已停止" not in result["final_response"]
            assert any(
                event["phase"] == "response"
                and event["event_type"] == "failure"
                and event.get("source") == "write_completion_guard"
                and event.get("failure_reason") == "重试后仍没有调用写操作或破坏性工具"
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def test_file_rename_completion_claim_without_tool_is_blocked() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0

        original_call_llm = graph.call_llm
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            return {
                "content": "结论：已成功将 `/tmp/sample-copy.txt` 重命名为 `/tmp/sample-moved.txt`。",
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

            result = await graph.reasoning_node({
                "session_id": "rename-claim-block",
                "user_message": "把 sample-copy.txt 改名为 sample-moved.txt",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert llm_calls == 2
            assert result["final_response"].startswith("本次没有执行该写操作")
            assert "重命名为" not in result["final_response"]
            assert any(
                event["phase"] == "response"
                and event["event_type"] == "failure"
                and event.get("source") == "write_completion_guard"
                for event in events
            )
            assert not any(event["phase"] == "execution" and event["event_type"] == "success" for event in events)
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def test_path_first_append_completion_claim_without_tool_is_blocked() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0

        original_call_llm = graph.call_llm
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            return {
                "content": "结论：已成功向 `/tmp/opsguard-manual-test/sample.txt` 添加 `10086`。",
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

            result = await graph.reasoning_node({
                "session_id": "path-append-claim-block",
                "user_message": "/tmp/opsguard-manual-test/sample.txt 追加 10086",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert llm_calls == 2
            assert result["final_response"].startswith("本次没有执行该写操作")
            assert "10086" not in result["final_response"]
            assert any(
                event["phase"] == "response"
                and event["event_type"] == "failure"
                and event.get("source") == "write_completion_guard"
                for event in events
            )
            assert not any(event["phase"] == "execution" and event["event_type"] == "success" for event in events)
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def test_planning_trace_evidence_is_chinese() -> None:
    async def scenario() -> None:
        events: list[dict] = []

        original_call_llm = graph.call_llm
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log

        async def fake_call_llm(messages, tools=None):
            return {"content": "你好，我可以帮你检查系统状态。", "tool_calls": []}

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

            await graph.reasoning_node({
                "session_id": "planning-chinese",
                "user_message": "你好",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            planning_events = [
                event for event in events
                if event["phase"] == "planning" and event["event_type"] == "start"
            ]
            assert planning_events
            assert planning_events[0].get("claim") == "智能体正在规划下一步检查或操作"
            assert "The agent is planning" not in planning_events[0].get("claim", "")
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def test_read_tool_claim_without_execution_is_blocked() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0

        original_call_llm = graph.call_llm
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            return {
                "content": "结论：`check_port(22)` 返回 sshd 正在监听，`get_service_status(\"sshd\")` 显示 active。",
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

            result = await graph.reasoning_node({
                "session_id": "read-tool-claim-block",
                "user_message": "查看 22 端口是谁在监听，并分析关联进程",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert llm_calls == 2
            assert result["final_response"].startswith("本次不能确认这些检查已经真实执行")
            assert "check_port(22) 返回" not in result["final_response"]
            assert any(
                event["phase"] == "response"
                and event["event_type"] == "failure"
                and event.get("source") == "read_tool_truthfulness_guard"
                and "check_port" in event.get("observed", "")
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def main() -> None:
    test_tool_result_failure_is_traced_as_failure()
    test_read_only_status_request_blocks_write_tool_choice()
    test_repeated_write_completion_claim_without_tool_is_blocked()
    test_file_rename_completion_claim_without_tool_is_blocked()
    test_path_first_append_completion_claim_without_tool_is_blocked()
    test_planning_trace_evidence_is_chinese()
    test_read_tool_claim_without_execution_is_blocked()
    print("agent trace truthfulness regression OK")


if __name__ == "__main__":
    main()
