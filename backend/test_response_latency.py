"""Regression checks for response delivery not waiting on post-processing."""

import asyncio
import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent import graph
from app.agent.tools_registry import RiskLevel, ToolDefinition
from app.mcp_tools.process_tools import ToolResult


def test_run_agent_returns_before_knowledge_save_finishes() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        extract_started = asyncio.Event()
        release_extract = asyncio.Event()

        original_get_path = graph.incident_store.get_knowledge_db_path
        original_call_llm = graph.call_llm
        original_search = graph.knowledge_store.search
        original_log = graph.audit_logger.log
        original_get_tool = graph.tools_registry.get_tool
        original_get_all_tools = graph.tools_registry.get_all_tools_for_llm
        original_extract = graph._extract_resolution_summary

        async def fake_call_llm(messages, tools=None):
            if not any(message.get("role") == "tool" for message in messages):
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": "call-dummy",
                        "name": "dummy_read",
                        "arguments": {},
                    }],
                }
            return {"content": "检查完成。", "tool_calls": []}

        async def fake_search(query: str, limit: int = 3):
            return []

        async def fake_extract(messages, final_response):
            extract_started.set()
            await release_extract.wait()
            return None

        def fake_get_tool(name: str):
            if name == "dummy_read":
                return ToolDefinition(
                    name="dummy_read",
                    description="dummy read",
                    parameters={"type": "object", "properties": {}},
                    function=lambda: ToolResult(success=True, data="ok"),
                    risk_level=RiskLevel.READ,
                    category="test",
                    display_name="测试读取",
                )
            return original_get_tool(name)

        async def capture_event(event: dict) -> None:
            events.append(event)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            try:
                graph.incident_store.get_knowledge_db_path = lambda: db_path
                graph.call_llm = fake_call_llm
                graph.knowledge_store.search = fake_search
                graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)
                graph.tools_registry.get_tool = fake_get_tool
                graph.tools_registry.get_all_tools_for_llm = lambda: [{
                    "name": "dummy_read",
                    "description": "dummy read",
                    "parameters": {"type": "object", "properties": {}},
                }]
                graph._extract_resolution_summary = fake_extract

                response = await asyncio.wait_for(
                    graph.run_agent(
                        session_id="response-latency",
                        user_message="检查一下状态",
                        conversation_history=[],
                        send_to_client=capture_event,
                    ),
                    timeout=1,
                )
                assert "检查完成" in response
                await asyncio.wait_for(extract_started.wait(), timeout=1)
                release_extract.set()
                await asyncio.sleep(0)
            finally:
                graph.incident_store.get_knowledge_db_path = original_get_path
                graph.call_llm = original_call_llm
                graph.knowledge_store.search = original_search
                graph.audit_logger.log = original_log
                graph.tools_registry.get_tool = original_get_tool
                graph.tools_registry.get_all_tools_for_llm = original_get_all_tools
                graph._extract_resolution_summary = original_extract

        assert any(event.get("phase") == "response" for event in events)

    asyncio.run(scenario())


def main() -> None:
    test_run_agent_returns_before_knowledge_save_finishes()
    print("response latency regression OK")


if __name__ == "__main__":
    main()
