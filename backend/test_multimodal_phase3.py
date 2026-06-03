"""Regression checks for Phase 3 multimodal evidence/report knowledge flow."""

import asyncio
import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent import graph
from app.agent.final_response import make_tool_ledger_entry
from app.incidents import store as incident_store
from app.mcp_tools.process_tools import ToolResult


def test_multimodal_incident_report_evidence_pairs_with_real_tools() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "knowledge.db")
            incident_id = await incident_store.create_incident(
                session_id="s1",
                problem_statement="分析截图中的 nginx 502",
                source="agent",
                db_path=db_path,
            )
            await incident_store.record_incident_event(
                incident_id=incident_id,
                session_id="s1",
                phase="image_recognition",
                event_type="success",
                title="图片识别完成",
                detail="图片识别完成\n摘要：截图中出现 nginx 502",
                evidence={
                    "claim": "图片内容已识别为用户提供证据，尚未执行系统操作",
                    "evidence_type": "user input",
                    "source": "aliyun_dashscope",
                    "observed": {"summary": "截图中出现 nginx 502"},
                    "confidence": "medium",
                    "execution_state": "inferred",
                },
                metadata={
                    "multimodal": {
                        "input_type": "image",
                        "summary": "截图中出现 nginx 502",
                        "entities": {"services": ["nginx"], "error_codes": ["502"]},
                        "confidence": "medium",
                    }
                },
                db_path=db_path,
            )
            await incident_store.record_incident_event(
                incident_id=incident_id,
                session_id="s1",
                phase="execution",
                event_type="success",
                title="服务日志执行完成",
                detail="执行成功: 服务日志",
                evidence={
                    "claim": "服务日志 已对 nginx 执行完成",
                    "evidence_type": "command",
                    "source": "get_service_logs",
                    "observed": "upstream returned 502",
                    "confidence": "high",
                    "execution_state": "executed",
                },
                db_path=db_path,
            )

            stats = await incident_store.get_recent_multimodal_evidence(
                since="2000-01-01T00:00:00",
                db_path=db_path,
            )

            assert stats["total"] == 1
            assert stats["images"] == 1
            assert stats["items"][0]["entities"]["error_codes"] == ["502"]
            assert stats["items"][0]["verification"][0]["source"] == "get_service_logs"
            assert stats["items"][0]["verification"][0]["execution_state"] == "executed"

    asyncio.run(scenario())


def test_multimodal_context_is_auxiliary_knowledge_only_after_tool_execution() -> None:
    async def scenario() -> None:
        saved: list[dict] = []
        traces: list[dict] = []

        async def fake_extract_resolution_summary(messages, final_response):
            return {
                "problem": "诊断 nginx 502",
                "diagnosis": "查看截图线索后读取服务日志",
                "solution": "根据日志定位上游错误",
                "symptoms": ["HTTP 502"],
                "root_cause": "",
                "evidence": ["真实工具返回 nginx 日志"],
                "successful_actions": ["读取 nginx 日志"],
                "failed_attempts": [],
                "validation_method": "重新读取日志确认错误消失",
                "applicability_conditions": ["nginx 返回 502"],
                "non_applicability_conditions": [],
                "confidence": "medium",
            }

        class FakeKnowledgeStore:
            async def save_resolution(self, **kwargs):
                saved.append(kwargs)

        async def fake_send_to_client(message: dict):
            traces.append(message)

        original_extract = graph._extract_resolution_summary
        original_store = graph.knowledge_store
        graph._extract_resolution_summary = fake_extract_resolution_summary
        graph.knowledge_store = FakeKnowledgeStore()
        try:
            await graph.knowledge_save_node({
                "session_id": "s1",
                "incident_id": "incident-1",
                "user_message": "看截图",
                "final_response": "已基于真实日志给出建议",
                "messages": [{"role": "tool", "content": "nginx log: 502"}],
                "current_turn_tool_ledger": [
                    make_tool_ledger_entry(
                        call_id="call-nginx-logs",
                        tool_name="get_service_logs",
                        tool_args={"service": "nginx"},
                        risk_level="read",
                        status="success",
                        result=ToolResult(success=True, data="nginx log: 502"),
                        execution_state="executed",
                        approval_granted=False,
                    )
                ],
                "multimodal_context": [
                    {
                        "input_type": "image",
                        "summary": "截图中出现 nginx 502",
                        "entities": {"services": ["nginx"], "error_codes": ["502"]},
                        "confidence": "medium",
                    }
                ],
                "current_turn_tool_count": 1,
                "send_to_client": fake_send_to_client,
            })

            assert len(saved) == 1
            memory = saved[0]["incident_memory"]
            assert memory["source_incident_id"] == "incident-1"
            assert "image_recognition" in memory["source_modalities"]
            assert "real_tool_execution" in memory["source_modalities"]
            assert memory["multimodal_evidence"][0]["summary"] == "截图中出现 nginx 502"
            assert any("多模态识别结果仅作为辅助上下文" in item for item in memory["applicability_conditions"])

            saved.clear()
            await graph.knowledge_save_node({
                "session_id": "s1",
                "incident_id": "incident-2",
                "user_message": "只上传图片",
                "final_response": "识别完成",
                "messages": [{"role": "tool", "content": "历史工具结果，不属于本轮"}],
                "multimodal_context": [{"input_type": "image", "summary": "nginx failed"}],
                "current_turn_tool_count": 0,
                "send_to_client": fake_send_to_client,
            })
            assert saved == []
        finally:
            graph._extract_resolution_summary = original_extract
            graph.knowledge_store = original_store

    asyncio.run(scenario())


def main() -> None:
    test_multimodal_incident_report_evidence_pairs_with_real_tools()
    test_multimodal_context_is_auxiliary_knowledge_only_after_tool_execution()
    print("multimodal phase3 regression OK")


if __name__ == "__main__":
    main()
