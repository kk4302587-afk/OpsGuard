"""Integration checks for ledger-backed final replies.

These tests intentionally keep the final-response LLM call real. Tool
execution is isolated with small test tools so the suite does not mutate the
host while still proving that the final Markdown is rendered from backend
ledger facts, not from model free text or keyword guards.
"""

import asyncio
import os
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent import graph  # noqa: E402
from app.agent.tools_registry import RiskLevel, ToolDefinition  # noqa: E402
from app.mcp_tools.process_tools import ToolResult  # noqa: E402


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


async def _noop_log(*args, **kwargs):
    return None


async def _noop_record_tool_execution(**kwargs):
    return None


def _is_structured_final_reply_call(messages) -> bool:
    return bool(
        messages
        and isinstance(messages[0], dict)
        and "最终回复结构化器" in str(messages[0].get("content", ""))
    )


def _patch_common(fake_get_tool):
    return {
        "call_llm": graph.call_llm,
        "get_tool": graph.tools_registry.get_tool,
        "get_all_tools": graph.tools_registry.get_all_tools_for_llm,
        "audit_log": graph.audit_logger.log,
        "record_tool_execution": graph.record_tool_execution,
        "execute_tool": graph.execute_tool,
    }


def _restore_common(originals):
    graph.call_llm = originals["call_llm"]
    graph.tools_registry.get_tool = originals["get_tool"]
    graph.tools_registry.get_all_tools_for_llm = originals["get_all_tools"]
    graph.audit_logger.log = originals["audit_log"]
    graph.record_tool_execution = originals["record_tool_execution"]
    graph.execute_tool = originals["execute_tool"]


def test_read_result_final_reply_is_rendered_from_call_id_ledger() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0

        original_call_llm = graph.call_llm

        async def fake_reasoning_call(messages, tools=None):
            nonlocal llm_calls
            if _is_structured_final_reply_call(messages):
                return await original_call_llm(messages, tools=tools)
            llm_calls += 1
            if llm_calls == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {"id": "call_read_1", "name": "get_service_status", "arguments": {"service": "nginx"}}
                    ],
                }
            return {
                "content": "自由文本草稿：nginx 当前为 inactive (dead)，建议启动但不要执行。",
                "tool_calls": [],
            }

        def fake_get_service_status(service: str) -> ToolResult:
            return ToolResult(success=True, data="Active: inactive (dead)")

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
            if name == "start_service":
                return ToolDefinition(
                    name=name,
                    description="Start service",
                    parameters={"type": "object", "properties": {}},
                    function=lambda service: ToolResult(success=True, data="not used"),
                    risk_level=RiskLevel.WRITE,
                    category="service",
                    display_name="启动服务",
                )
            return None

        async def capture_event(event: dict) -> None:
            events.append(event)

        originals = _patch_common(fake_get_tool)
        try:
            graph.call_llm = fake_reasoning_call
            graph.tools_registry.get_tool = fake_get_tool
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = _noop_log
            graph.record_tool_execution = _noop_record_tool_execution

            result = await graph.reasoning_node({
                "session_id": "structured-read-ledger",
                "incident_id": "",
                "user_message": "查看 nginx 当前状态，如果没运行只给建议不要执行",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
                "multimodal_hint": "",
                "multimodal_context": [],
            })

            assert result.get("is_blocked") is False, result["final_response"]
            assert "`" in result["final_response"]
            assert "/get_service_status" in result["final_response"]
            assert "inactive" in result["final_response"]
            assert "尚未执行" in result["final_response"]
            assert "已执行成功" not in result["final_response"]
            assert any(event.get("source") == "structured_final_response_guard" for event in events)
        finally:
            _restore_common(originals)

    asyncio.run(scenario())


def test_successful_approved_write_is_rendered_as_executed_from_ledger() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        llm_calls = 0

        original_call_llm = graph.call_llm
        from app.websocket import approval as approval_module
        original_approval_manager = approval_module.approval_manager
        original_assess_impact = graph.assess_impact

        async def fake_reasoning_call(messages, tools=None):
            nonlocal llm_calls
            if _is_structured_final_reply_call(messages):
                return await original_call_llm(messages, tools=tools)
            llm_calls += 1
            if llm_calls == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {"id": "call_write_1", "name": "start_service", "arguments": {"service": "nginx"}}
                    ],
                }
            return {"content": "自由文本草稿：nginx 已启动。", "tool_calls": []}

        def fake_start_service(service: str) -> ToolResult:
            return ToolResult(success=True, data=f"Service {service} started")

        def fake_get_tool(name: str):
            if name == "start_service":
                return ToolDefinition(
                    name=name,
                    description="Start service",
                    parameters={"type": "object", "properties": {}},
                    function=fake_start_service,
                    risk_level=RiskLevel.WRITE,
                    category="service",
                    display_name="启动服务",
                )
            return None

        async def capture_event(event: dict) -> None:
            events.append(event)

        originals = _patch_common(fake_get_tool)
        try:
            graph.call_llm = fake_reasoning_call
            graph.tools_registry.get_tool = fake_get_tool
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = _noop_log
            graph.record_tool_execution = _noop_record_tool_execution
            graph.assess_impact = lambda *args, **kwargs: asyncio.sleep(0, result="测试影响评估")
            approval_module.approval_manager = AutoApprovalManager()

            result = await graph.reasoning_node({
                "session_id": "structured-write-ledger",
                "incident_id": "",
                "user_message": "启动 nginx",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
                "multimodal_hint": "",
                "multimodal_context": [],
            })

            assert result.get("is_blocked") is False, result["final_response"]
            assert "`call_write_1`/start_service 已执行成功" in result["final_response"]
            assert "审批：已通过" in result["final_response"]
            assert any(event.get("source") == "structured_final_response_guard" for event in events)
        finally:
            graph.assess_impact = original_assess_impact
            approval_module.approval_manager = original_approval_manager
            _restore_common(originals)

    asyncio.run(scenario())


def test_rejected_write_is_not_rendered_as_executed_even_if_draft_says_done() -> None:
    async def scenario() -> None:
        llm_calls = 0

        async def fake_reasoning_call(messages, tools=None):
            nonlocal llm_calls
            llm_calls += 1
            return {
                "content": "",
                "tool_calls": [
                    {"id": "call_write_rejected", "name": "start_service", "arguments": {"service": "nginx"}}
                ],
            }

        def fake_start_service(service: str) -> ToolResult:
            raise AssertionError("Rejected write tool must not execute")

        def fake_get_tool(name: str):
            if name == "start_service":
                return ToolDefinition(
                    name=name,
                    description="Start service",
                    parameters={"type": "object", "properties": {}},
                    function=fake_start_service,
                    risk_level=RiskLevel.WRITE,
                    category="service",
                    display_name="启动服务",
                )
            return None

        async def capture_event(event: dict) -> None:
            return None

        from app.websocket import approval as approval_module
        original_approval_manager = approval_module.approval_manager
        original_assess_impact = graph.assess_impact
        originals = _patch_common(fake_get_tool)
        try:
            graph.call_llm = fake_reasoning_call
            graph.tools_registry.get_tool = fake_get_tool
            graph.tools_registry.get_all_tools_for_llm = lambda: []
            graph.audit_logger.log = _noop_log
            graph.record_tool_execution = _noop_record_tool_execution
            graph.assess_impact = lambda *args, **kwargs: asyncio.sleep(0, result="测试影响评估")
            approval_module.approval_manager = RejectingApprovalManager()

            result = await graph.reasoning_node({
                "session_id": "structured-rejected-write",
                "incident_id": "",
                "user_message": "启动 nginx",
                "send_to_client": capture_event,
                "messages": [],
                "risk_warning": "",
                "knowledge_hint": "",
                "recent_changes_hint": "",
                "multimodal_hint": "",
                "multimodal_context": [],
            })

            assert result.get("is_blocked") is False
            assert result["final_response"].startswith("操作已取消")
            assert "未执行" in result["final_response"]
        finally:
            graph.assess_impact = original_assess_impact
            approval_module.approval_manager = original_approval_manager
            _restore_common(originals)

    asyncio.run(scenario())
