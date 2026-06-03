"""Regression checks for verbatim document output."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent import graph  # noqa: E402
from app.agent.tools_registry import RiskLevel, ToolDefinition, tools_registry  # noqa: E402
from app.mcp_tools.file_tools import read_document  # noqa: E402
from app.mcp_tools.process_tools import ToolResult  # noqa: E402


async def _noop_log(*args, **kwargs):
    return None


async def _noop_record_tool_execution(**kwargs):
    return None


def test_read_document_returns_verbatim_render_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "doc.md"
        target.write_text("# 标题\n\n原始内容\n", encoding="utf-8")

        result = read_document(str(target))

        assert result.success is True
        assert isinstance(result.data, dict)
        assert result.data["render_mode"] == "verbatim"
        assert result.data["content"] == "# 标题\n\n原始内容\n"


def test_registry_exposes_read_document_as_read_only() -> None:
    tool = tools_registry.get_tool("read_document")

    assert tool is not None
    assert tool.risk_level == RiskLevel.READ


def test_verbatim_document_request_uses_read_document_plan() -> None:
    plan = graph._fresh_read_tool_plan("请原样输出 /tmp/example.md 的文档内容，不要总结")

    assert any(item["tool_name"] == "read_document" and item["tool_args"]["filepath"] == "/tmp/example.md" for item in plan)
    assert not any(item["tool_name"] == "read_file" for item in plan)


def test_verbatim_document_request_trims_attached_chinese_intent_word() -> None:
    plan = graph._fresh_read_tool_plan("输出 /tmp/opsguard-manual-test/sample.txt内容")

    assert any(
        item["tool_name"] == "read_document"
        and item["tool_args"]["filepath"] == "/tmp/opsguard-manual-test/sample.txt"
        for item in plan
    )


def test_agent_renders_read_document_content_without_structured_summary() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "doc.txt"
            content = "第一行\n第二行：保持原文\n"
            target.write_text(content, encoding="utf-8")
            events: list[dict] = []

            async def fake_reasoning_call(messages, tools=None):
                return {"content": "草稿不应成为最终摘要", "tool_calls": []}

            def fake_get_tool(name: str):
                if name == "read_document":
                    return ToolDefinition(
                        name=name,
                        description="Read document",
                        parameters={"type": "object", "properties": {}},
                        function=read_document,
                        risk_level=RiskLevel.READ,
                        category="file",
                        display_name="输出文档原文",
                    )
                return None

            async def fake_execute_tool(tool_name, tool_args, tool_def=None):
                assert tool_name == "read_document"
                return read_document(**tool_args)

            async def fail_structured_final_reply(**kwargs):
                raise AssertionError("Verbatim document output should not use structured final summary")

            async def capture_event(event: dict) -> None:
                events.append(event)

            originals = {
                "call_llm": graph.call_llm,
                "get_tool": graph.tools_registry.get_tool,
                "get_all_tools": graph.tools_registry.get_all_tools_for_llm,
                "audit_log": graph.audit_logger.log,
                "record_tool_execution": graph.record_tool_execution,
                "execute_tool": graph.execute_tool,
                "generate_structured_final_reply": graph.generate_structured_final_reply,
            }
            try:
                graph.call_llm = fake_reasoning_call
                graph.tools_registry.get_tool = fake_get_tool
                graph.tools_registry.get_all_tools_for_llm = lambda: []
                graph.audit_logger.log = _noop_log
                graph.record_tool_execution = _noop_record_tool_execution
                graph.execute_tool = fake_execute_tool
                graph.generate_structured_final_reply = fail_structured_final_reply

                result = await graph.reasoning_node({
                    "session_id": "verbatim-document",
                    "incident_id": "",
                    "user_message": f"请原样输出 {target} 的文档内容，不要总结",
                    "send_to_client": capture_event,
                    "messages": [],
                    "risk_warning": "",
                    "knowledge_hint": "",
                    "recent_changes_hint": "",
                    "multimodal_hint": "",
                    "multimodal_context": [],
                })

                final_response = result["final_response"]
                assert "**文档内容**" in final_response
                assert f"**文档内容**：{target}" in final_response
                assert "```text" in final_response
                assert content.strip() in final_response
                assert "关键证据" not in final_response
                assert any(event.get("source") == "read_document" for event in events)
            finally:
                graph.call_llm = originals["call_llm"]
                graph.tools_registry.get_tool = originals["get_tool"]
                graph.tools_registry.get_all_tools_for_llm = originals["get_all_tools"]
                graph.audit_logger.log = originals["audit_log"]
                graph.record_tool_execution = originals["record_tool_execution"]
                graph.execute_tool = originals["execute_tool"]
                graph.generate_structured_final_reply = originals["generate_structured_final_reply"]

    asyncio.run(scenario())


if __name__ == "__main__":
    test_read_document_returns_verbatim_render_metadata()
    test_registry_exposes_read_document_as_read_only()
    test_verbatim_document_request_uses_read_document_plan()
    test_verbatim_document_request_trims_attached_chinese_intent_word()
    test_agent_renders_read_document_content_without_structured_summary()
    print("document verbatim output regression OK")
