"""Regression checks that Agent trace success matches real tool results."""

import asyncio
import os
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent import graph
from app.agent.tools_registry import RiskLevel, ToolDefinition
from app.mcp_tools.process_tools import ToolResult


class AutoApprovalManager:
    def register_pending(self, request_id, session_id, tool_name, tool_args, risk_level, description, future):
        future.set_result(True)

    def remove_pending(self, request_id):
        return None


class RejectingApprovalManager:
    def register_pending(self, request_id, session_id, tool_name, tool_args, risk_level, description, future):
        future.set_result(False)

    def remove_pending(self, request_id):
        return None


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


def _is_event(
    event: dict,
    *,
    phase: str | None = None,
    event_type: str | None = None,
    source: str | None = None,
) -> bool:
    if phase is not None and event.get("phase") != phase:
        return False
    if event_type is not None and event.get("event_type") != event_type:
        return False
    if source is not None and event.get("source") != source:
        return False
    return True


def test_rejected_write_approval_finishes_turn_without_retry() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0
        write_tool_executed = False

        original_call_llm = graph.call_llm
        original_get_tool = graph.tools_registry.get_tool
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log
        original_assess_impact = graph.assess_impact
        from app.websocket import approval as approval_module
        original_approval_manager = approval_module.approval_manager

        def fake_move_file(source: str, destination: str) -> ToolResult:
            nonlocal write_tool_executed
            write_tool_executed = True
            return ToolResult(success=True, data=f"moved {source} to {destination}")

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "tool-1",
                        "name": "move_file",
                        "arguments": {
                            "source": "/tmp/opsguard-manual-test/sample-copy.txt",
                            "destination": "/tmp/opsguard-manual-test/sample-moved.txt",
                        },
                    }
                ],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        def fake_get_tool(name: str):
            if name == "move_file":
                return ToolDefinition(
                    name=name,
                    description="Move file",
                    parameters={"type": "object", "properties": {}},
                    function=fake_move_file,
                    risk_level=RiskLevel.WRITE,
                    category="file",
                    display_name="移动/重命名文件",
                )
            return original_get_tool(name)

        async def fake_assess_impact(*args, **kwargs):
            return "影响评估"

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.tools_registry.get_tool = fake_get_tool
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
            graph.assess_impact = fake_assess_impact
            approval_module.approval_manager = RejectingApprovalManager()

            result = await graph.reasoning_node({
                "session_id": "approval-rejected",
                "user_message": "把 sample-copy.txt 改名为 sample-moved.txt",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert llm_calls == 1
            assert write_tool_executed is False
            assert result["final_response"].startswith("操作已取消")
            assert "未执行" in result["final_response"]
            assert result.get("is_blocked") is False
            assert any(
                _is_event(event, phase="approval_response", event_type="failure")
                for event in events
            )
            assert not any(_is_event(event, phase="execution") for event in events)
            assert not any(
                _is_event(
                    event,
                    phase="response",
                    event_type="failure",
                    source="read_tool_truthfulness_guard",
                )
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_tool = original_get_tool
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log
            graph.assess_impact = original_assess_impact
            approval_module.approval_manager = original_approval_manager

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


def test_read_tool_success_with_completion_word_is_not_write_blocked() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0

        original_call_llm = graph.call_llm
        original_get_tool = graph.tools_registry.get_tool
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log
        original_record_tool_execution = graph.record_tool_execution

        def fake_get_service_status(service: str) -> ToolResult:
            return ToolResult(success=True, data="Active: active (running)")

        async def fake_record_tool_execution(**kwargs) -> None:
            return None

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            if llm_calls == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {"id": "tool-1", "name": "get_service_status", "arguments": {"service": "nginx"}}
                    ],
                }
            return {"content": "nginx 服务状态已确认：active (running)。", "tool_calls": []}

        async def capture_event(event: dict) -> None:
            events.append(event)

        def fake_get_tool(name: str):
            if name == "get_service_status":
                return ToolDefinition(
                    name=name,
                    description="Get service status",
                    parameters={"type": "object", "properties": {}},
                    function=fake_get_service_status,
                    risk_level=RiskLevel.READ,
                    category="service",
                )
            return original_get_tool(name)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.tools_registry.get_tool = fake_get_tool
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
            graph.record_tool_execution = fake_record_tool_execution

            result = await graph.reasoning_node({
                "session_id": "read-success-not-write-blocked",
                "user_message": "查看 nginx 当前状态",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert result["final_response"] == "nginx 服务状态已确认：active (running)。"
            assert result.get("is_blocked") is False
            assert not any(
                event["phase"] == "response"
                and event["event_type"] == "failure"
                and event.get("source") == "write_completion_guard"
                for event in events
            )
            assert any(
                event["phase"] == "response"
                and event["event_type"] == "success"
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_tool = original_get_tool
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log
            graph.record_tool_execution = original_record_tool_execution

    asyncio.run(scenario())


def test_tool_plan_without_execution_gets_fresh_service_preflight() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0
        executed_tools: list[str] = []
        captured_messages: list[dict] = []

        original_call_llm = graph.call_llm
        original_get_tool = graph.tools_registry.get_tool
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log
        original_record_tool_execution = graph.record_tool_execution

        def fake_get_service_status(service: str) -> ToolResult:
            executed_tools.append("get_service_status")
            return ToolResult(success=True, data=f"{service}: Active: active (running)")

        async def fake_record_tool_execution(**kwargs) -> None:
            return None

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls, captured_messages
            llm_calls += 1
            captured_messages = list(messages)
            assert any(msg.get("role") == "tool" and "active (running)" in msg.get("content", "") for msg in messages)
            return {"content": "nginx 服务运行正常。", "tool_calls": []}

        async def capture_event(event: dict) -> None:
            events.append(event)

        def fake_get_tool(name: str):
            if name == "get_service_status":
                return ToolDefinition(
                    name=name,
                    description="Get service status",
                    parameters={"type": "object", "properties": {}},
                    function=fake_get_service_status,
                    risk_level=RiskLevel.READ,
                    category="service",
                    display_name="服务状态",
                )
            return original_get_tool(name)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_tool = fake_get_tool
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
            graph.record_tool_execution = fake_record_tool_execution

            result = await graph.reasoning_node({
                "session_id": "plan-without-execution-blocked",
                "user_message": "查看 nginx 当前状态",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert llm_calls == 1
            assert executed_tools == ["get_service_status"]
            assert result.get("is_blocked") is False
            assert result["final_response"] == "nginx 服务运行正常。"
            assert any(msg.get("role") == "tool" for msg in captured_messages)
            assert any(
                event["phase"] == "execution"
                and event["event_type"] == "success"
                and event.get("source") == "get_service_status"
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_tool = original_get_tool
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log
            graph.record_tool_execution = original_record_tool_execution

    asyncio.run(scenario())


def test_write_tool_success_can_report_tool_result_without_read_verification() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0

        original_call_llm = graph.call_llm
        original_get_tool = graph.tools_registry.get_tool
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log
        original_record_tool_execution = graph.record_tool_execution
        from app.websocket import approval as approval_module
        original_approval_manager = approval_module.approval_manager

        def fake_write_file(filepath: str, content: str, append: bool = False) -> ToolResult:
            return ToolResult(success=True, data=f"已追加: {filepath} ({len(content)} bytes)")

        async def fake_record_tool_execution(**kwargs) -> None:
            return None

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            if llm_calls == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "name": "write_file",
                            "arguments": {
                                "filepath": "/tmp/opsguard-manual-test/sample.txt",
                                "content": "hello-from-opsguard",
                                "append": True,
                            },
                        }
                    ],
                }
            return {
                "content": "写操作已执行：已追加到 `/tmp/opsguard-manual-test/sample.txt`（19 bytes）。尚未读取文件做额外验证。",
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        def fake_get_tool(name: str):
            if name == "write_file":
                return ToolDefinition(
                    name=name,
                    description="Write file",
                    parameters={"type": "object", "properties": {}},
                    function=fake_write_file,
                    risk_level=RiskLevel.WRITE,
                    category="file",
                )
            return original_get_tool(name)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.tools_registry.get_tool = fake_get_tool
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
            graph.record_tool_execution = fake_record_tool_execution
            approval_module.approval_manager = AutoApprovalManager()

            result = await graph.reasoning_node({
                "session_id": "write-result-no-read-ok",
                "user_message": "在 /tmp/opsguard-manual-test/sample.txt 追加 hello-from-opsguard",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert result["final_response"].startswith("写操作已执行")
            assert result.get("is_blocked") is False
            assert not any(
                _is_event(
                    event,
                    phase="response",
                    event_type="failure",
                    source="read_tool_truthfulness_guard",
                )
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_tool = original_get_tool
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log
            graph.record_tool_execution = original_record_tool_execution
            approval_module.approval_manager = original_approval_manager

    asyncio.run(scenario())


def test_write_tool_success_without_read_blocks_extra_verification_claim() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0

        original_call_llm = graph.call_llm
        original_get_tool = graph.tools_registry.get_tool
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log
        original_record_tool_execution = graph.record_tool_execution
        from app.websocket import approval as approval_module
        original_approval_manager = approval_module.approval_manager

        def fake_copy_file(source: str, destination: str) -> ToolResult:
            return ToolResult(success=True, data=f"已复制: {source} → {destination}")

        async def fake_record_tool_execution(**kwargs) -> None:
            return None

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            if llm_calls == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "name": "copy_file",
                            "arguments": {
                                "source": "/tmp/opsguard-manual-test/sample.txt",
                                "destination": "/tmp/opsguard-manual-test/sample-copy.txt",
                            },
                        }
                    ],
                }
            return {
                "content": "复制已完成，目标文件内容与源文件完全一致，文件大小 77 字节。",
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        def fake_get_tool(name: str):
            if name == "copy_file":
                return ToolDefinition(
                    name=name,
                    description="Copy file",
                    parameters={"type": "object", "properties": {}},
                    function=fake_copy_file,
                    risk_level=RiskLevel.WRITE,
                    category="file",
                )
            return original_get_tool(name)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.tools_registry.get_tool = fake_get_tool
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
            graph.record_tool_execution = fake_record_tool_execution
            approval_module.approval_manager = AutoApprovalManager()

            result = await graph.reasoning_node({
                "session_id": "write-extra-verification-blocked",
                "user_message": "复制 sample.txt 到 sample-copy.txt",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert llm_calls == 3
            assert result.get("is_blocked") is True
            assert result["final_response"].startswith("本次写操作已执行，但不能确认后续验证结论")
            assert "完全一致" not in result["final_response"]
            assert any(
                _is_event(
                    event,
                    phase="response",
                    event_type="failure",
                    source="read_tool_truthfulness_guard",
                )
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_tool = original_get_tool
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log
            graph.record_tool_execution = original_record_tool_execution
            approval_module.approval_manager = original_approval_manager

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


def test_history_recall_can_reference_previous_tool_evidence() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        captured_user_content = ""

        original_call_llm = graph.call_llm
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log
        original_get_recent = graph.get_recent_tool_executions

        async def fake_get_recent(session_id, *, limit=12, successful_only=False):
            return [
                {
                    "tool_name": "system_overview",
                    "tool_args": {},
                    "status": "success",
                    "result_summary": "memory used 88.2%, load average 2.1",
                    "error": "",
                    "timestamp": "2026-05-27T14:44:38",
                },
                {
                    "tool_name": "health_check",
                    "tool_args": {},
                    "status": "success",
                    "result_summary": "status warning, memory pressure detected",
                    "error": "",
                    "timestamp": "2026-05-27T14:44:39",
                },
                {
                    "tool_name": "list_processes",
                    "tool_args": {"sort_by": "memory", "limit": 20},
                    "status": "success",
                    "result_summary": "top process vscode-server",
                    "error": "",
                    "timestamp": "2026-05-27T14:44:40",
                },
            ]

        async def fake_call_llm(messages, tools=None):
            nonlocal captured_user_content
            captured_user_content = messages[-1]["content"]
            return {
                "content": (
                    "刚刚检测到的最大风险是内存压力偏高。"
                    "上一轮 `system_overview` 显示内存使用 88.2%，"
                    "`health_check` 给出 warning，`list_processes` 显示主要占用来自 vscode-server。"
                ),
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
            graph.get_recent_tool_executions = fake_get_recent

            result = await graph.reasoning_node({
                "session_id": "history-recall-evidence",
                "user_message": "刚刚检测到的最大风险是什么？",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert result["final_response"].startswith("刚刚检测到的最大风险")
            assert "历史工具执行证据" in captured_user_content
            assert not any(
                event["phase"] == "response"
                and event["event_type"] == "failure"
                and event.get("source") == "read_tool_truthfulness_guard"
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log
            graph.get_recent_tool_executions = original_get_recent

    asyncio.run(scenario())


def test_read_tool_explanation_without_execution_is_allowed() -> None:
    async def scenario() -> None:
        events: list[dict] = []

        original_call_llm = graph.call_llm
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log

        async def fake_call_llm(messages, tools=None):
            return {
                "content": (
                    "这只是操作含义说明，尚未执行 `read_file`。\n\n"
                    "`read_file(filepath)` 用于读取指定文本文件内容；如果需要确认文件是否存在，"
                    "我需要先调用只读工具获取真实结果。"
                ),
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

            result = await graph.reasoning_node({
                "session_id": "read-tool-explanation",
                "user_message": "请解释读取 /tmp/not-exist-opsguard 这个操作的含义",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert result["final_response"].startswith("这只是操作含义说明")
            assert not any(
                event["phase"] == "response"
                and event["event_type"] == "failure"
                and event.get("source") == "read_tool_truthfulness_guard"
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def test_bare_read_request_uses_fresh_file_preflight() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0
        executed_tools: list[str] = []

        original_call_llm = graph.call_llm
        original_get_tool = graph.tools_registry.get_tool
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log
        original_record_tool_execution = graph.record_tool_execution

        def fake_read_file(filepath: str, max_bytes: int = 65536) -> ToolResult:
            executed_tools.append("read_file")
            return ToolResult(success=False, data="", error="文件不存在: /tmp/not-exist-opsguard")

        async def fake_record_tool_execution(**kwargs) -> None:
            return None

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            assert any(msg.get("role") == "tool" and "文件不存在" in msg.get("content", "") for msg in messages)
            return {
                "content": "结论：本轮 read_file 返回文件不存在，因此无法读取 `/tmp/not-exist-opsguard`。",
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        def fake_get_tool(name: str):
            if name == "read_file":
                return ToolDefinition(
                    name=name,
                    description="Read file",
                    parameters={"type": "object", "properties": {}},
                    function=fake_read_file,
                    risk_level=RiskLevel.READ,
                    category="file",
                    display_name="读取文件",
                )
            return original_get_tool(name)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_tool = fake_get_tool
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
            graph.record_tool_execution = fake_record_tool_execution

            result = await graph.reasoning_node({
                "session_id": "bare-read-fabricated",
                "user_message": "读取 /tmp/not-exist-opsguard",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert llm_calls == 1
            assert executed_tools == ["read_file"]
            assert result.get("is_blocked") is False
            assert result["final_response"].startswith("结论：本轮 read_file 返回文件不存在")
            assert any(
                event["phase"] == "execution"
                and event["event_type"] == "failure"
                and event.get("source") == "read_file"
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_tool = original_get_tool
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log
            graph.record_tool_execution = original_record_tool_execution

    asyncio.run(scenario())


def test_followup_append_completion_without_write_tool_is_blocked() -> None:
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
                "content": "结论：已成功添加 hello! 中国软件杯! 到 `/tmp/opsguard-manual-test/sample.txt`，当前文件共 3 行。",
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

            result = await graph.reasoning_node({
                "session_id": "followup-append-fake-success",
                "user_message": "继续追加hello! 中国软件杯!",
                "send_to_client": capture_event,
                "messages": [
                    {"role": "user", "content": "在 /tmp/opsguard-manual-test/sample.txt 写入 hello_kiki"},
                    {"role": "assistant", "content": "已通过工具写入 hello_kiki。"},
                ],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert llm_calls == 2
            assert result["final_response"].startswith("本次没有执行该写操作")
            assert "已成功添加" not in result["final_response"]
            assert any(
                event["phase"] == "response"
                and event["event_type"] == "failure"
                and event.get("source") == "write_completion_guard"
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def test_verification_claim_without_read_tool_is_blocked() -> None:
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
                "content": "结论：验证成功！`/tmp/opsguard-manual-test/sample.txt` 当前内容完整、准确，共 3 行，54 字节。",
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

            result = await graph.reasoning_node({
                "session_id": "verification-fake-success",
                "user_message": "执行验证操作",
                "send_to_client": capture_event,
                "messages": [
                    {"role": "user", "content": "继续追加hello! 中国软件杯!"},
                    {"role": "assistant", "content": "已成功添加 hello! 中国软件杯!。"},
                ],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert llm_calls == 2
            assert result["final_response"].startswith("本次不能确认这些检查已经真实执行")
            assert "验证成功" not in result["final_response"]
            assert any(
                event["phase"] == "response"
                and event["event_type"] == "failure"
                and event.get("source") == "read_tool_truthfulness_guard"
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log

    asyncio.run(scenario())


def test_fresh_status_request_executes_read_preflight_before_llm_reply() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0
        executed_tools: list[str] = []
        captured_messages: list[dict] = []

        original_call_llm = graph.call_llm
        original_get_tool = graph.tools_registry.get_tool
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log
        original_record_tool_execution = graph.record_tool_execution

        def fake_system_overview() -> ToolResult:
            executed_tools.append("system_overview")
            return ToolResult(success=True, data={"load_avg": {"1min": "0.42"}, "memory": "used 81%"})

        def fake_health_check() -> ToolResult:
            executed_tools.append("health_check")
            return ToolResult(success=True, data={"status": "warning", "issues": []})

        def fake_get_recent_errors(lines: int = 30) -> ToolResult:
            executed_tools.append("get_recent_errors")
            return ToolResult(success=True, data="May 28 11:00 app[1]: sample current error")

        def fake_get_failed_services() -> ToolResult:
            executed_tools.append("get_failed_services")
            return ToolResult(success=True, data="0 loaded units listed.")

        async def fake_record_tool_execution(**kwargs) -> None:
            return None

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls, captured_messages
            llm_calls += 1
            captured_messages = list(messages)
            assert any(msg.get("role") == "tool" and "sample current error" in msg.get("content", "") for msg in messages)
            return {
                "content": "本轮已基于实时证据预检结果分析：系统状态 warning，最近错误日志包含 sample current error。",
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        def fake_get_tool(name: str):
            tools = {
                "system_overview": (fake_system_overview, "system"),
                "health_check": (fake_health_check, "system"),
                "get_recent_errors": (fake_get_recent_errors, "log"),
                "get_failed_services": (fake_get_failed_services, "service"),
            }
            if name in tools:
                func, category = tools[name]
                return ToolDefinition(
                    name=name,
                    description=name,
                    parameters={"type": "object", "properties": {}},
                    function=func,
                    risk_level=RiskLevel.READ,
                    category=category,
                    display_name=name,
                )
            return original_get_tool(name)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.tools_registry.get_tool = fake_get_tool
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
            graph.record_tool_execution = fake_record_tool_execution

            result = await graph.reasoning_node({
                "session_id": "fresh-status-preflight",
                "user_message": "帮我分析系统最近状态和错误日志",
                "send_to_client": capture_event,
                "messages": [
                    {"role": "user", "content": "帮我查看系统整体状态"},
                    {"role": "assistant", "content": "上一轮 health_check 返回 critical。"},
                ],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert llm_calls == 1
            assert set(executed_tools) == {
                "system_overview",
                "health_check",
                "get_recent_errors",
                "get_failed_services",
            }
            assert result["final_response"].startswith("本轮已基于实时证据预检结果")
            assert result.get("is_blocked") is False
            assert sum(1 for msg in captured_messages if msg.get("role") == "tool") == 4
            assert any(
                event["phase"] == "execution"
                and event["event_type"] == "success"
                and event.get("source") == "get_recent_errors"
                for event in events
            )
            assert not any(
                event["phase"] == "response"
                and event["event_type"] == "failure"
                and event.get("source") in {"read_tool_truthfulness_guard", "write_completion_guard"}
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_tool = original_get_tool
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log
            graph.record_tool_execution = original_record_tool_execution

    asyncio.run(scenario())


def test_fresh_listening_ports_request_executes_preflight_once() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0
        executed_tools: list[str] = []

        original_call_llm = graph.call_llm
        original_get_tool = graph.tools_registry.get_tool
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_log = graph.audit_logger.log
        original_record_tool_execution = graph.record_tool_execution

        def fake_get_listening_ports() -> ToolResult:
            executed_tools.append("get_listening_ports")
            return ToolResult(success=True, data="LISTEN 0 128 0.0.0.0:22 users:((\"sshd\",pid=123,fd=3))")

        async def fake_record_tool_execution(**kwargs) -> None:
            return None

        async def fake_call_llm(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            assert any(msg.get("role") == "tool" and "0.0.0.0:22" in msg.get("content", "") for msg in messages)
            return {
                "content": "当前监听端口来自本轮 get_listening_ports 结果：22/tcp 由 sshd 监听。",
                "tool_calls": [],
            }

        async def capture_event(event: dict) -> None:
            events.append(event)

        def fake_get_tool(name: str):
            if name == "get_listening_ports":
                return ToolDefinition(
                    name=name,
                    description="Get listening ports",
                    parameters={"type": "object", "properties": {}},
                    function=fake_get_listening_ports,
                    risk_level=RiskLevel.READ,
                    category="network",
                    display_name="监听端口",
                )
            return original_get_tool(name)

        try:
            graph.call_llm = fake_call_llm
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.tools_registry.get_tool = fake_get_tool
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
            graph.record_tool_execution = fake_record_tool_execution

            result = await graph.reasoning_node({
                "session_id": "fresh-listening-ports",
                "user_message": "列出当前监听端口",
                "send_to_client": capture_event,
                "messages": [
                    {"role": "user", "content": "列出当前监听端口"},
                    {"role": "assistant", "content": "历史上 8080 端口在监听。"},
                ],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
            })

            assert llm_calls == 1
            assert executed_tools == ["get_listening_ports"]
            assert result["final_response"].startswith("当前监听端口来自本轮")
            assert result.get("is_blocked") is False
            assert any(
                event["phase"] == "execution"
                and event["event_type"] == "success"
                and event.get("source") == "get_listening_ports"
                for event in events
            )
        finally:
            graph.call_llm = original_call_llm
            graph.tools_registry.get_tool = original_get_tool
            graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
            graph.audit_logger.log = original_log
            graph.record_tool_execution = original_record_tool_execution

    asyncio.run(scenario())


def main() -> None:
    test_tool_result_failure_is_traced_as_failure()
    test_rejected_write_approval_finishes_turn_without_retry()
    test_read_only_status_request_blocks_write_tool_choice()
    test_read_tool_success_with_completion_word_is_not_write_blocked()
    test_tool_plan_without_execution_gets_fresh_service_preflight()
    test_write_tool_success_can_report_tool_result_without_read_verification()
    test_write_tool_success_without_read_blocks_extra_verification_claim()
    test_repeated_write_completion_claim_without_tool_is_blocked()
    test_file_rename_completion_claim_without_tool_is_blocked()
    test_path_first_append_completion_claim_without_tool_is_blocked()
    test_planning_trace_evidence_is_chinese()
    test_read_tool_claim_without_execution_is_blocked()
    test_history_recall_can_reference_previous_tool_evidence()
    test_read_tool_explanation_without_execution_is_allowed()
    test_bare_read_request_uses_fresh_file_preflight()
    test_followup_append_completion_without_write_tool_is_blocked()
    test_verification_claim_without_read_tool_is_blocked()
    test_fresh_status_request_executes_read_preflight_before_llm_reply()
    test_fresh_listening_ports_request_executes_preflight_once()
    print("agent trace truthfulness regression OK")


if __name__ == "__main__":
    main()
