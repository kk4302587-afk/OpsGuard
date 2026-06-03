"""Regression checks for Context Management 2.0."""

import asyncio
import os
import tempfile
from pathlib import Path

import aiosqlite

os.chdir(Path(__file__).parent)

from app.agent import context_manager
from app.agent.context_manager import build_context_package, split_recent_conversation
from app.agent.tool_execution_store import record_tool_execution


def _long_history(turns: int = 30) -> list[dict]:
    messages: list[dict] = []
    for idx in range(turns):
        messages.append({"role": "user", "content": f"第 {idx} 轮用户请求，检查 nginx 状态"})
        messages.append({"role": "assistant", "content": f"第 {idx} 轮助手回复，previous observation {idx}"})
    return messages


def test_long_session_keeps_bounded_recent_conversation() -> None:
    recent, older = split_recent_conversation(_long_history(30))

    assert len(recent) == 16
    assert len(older) == 44
    assert recent[0]["content"].startswith("第 22 轮")
    assert recent[-1]["content"].startswith("第 29 轮")


def test_context_package_labels_sources_and_summarizes_older_turns() -> None:
    async def scenario() -> None:
        original_get_path = context_manager.get_knowledge_db_path
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            context_manager.get_knowledge_db_path = lambda: db_path
            try:
                package = await build_context_package(
                    session_id="ctx-labels",
                    user_message="现在 nginx 状态如何？",
                    conversation_history=_long_history(30),
                    knowledge_hint="## 历史经验参考\nnginx 曾因配置错误失败",
                    recent_changes_hint="## 近期变更证据\n/etc/nginx/nginx.conf 修改过",
                    multimodal_hint="## 多模态输入识别结果\n截图疑似 nginx failed",
                    fresh_evidence_hint="## 本轮实时证据要求\n- get_service_status",
                )

                prompt = package.messages[-1]["content"]
                assert len(package.messages) == 17
                assert "第 0 轮用户请求" not in prompt
                assert "第 29 轮助手回复" in prompt
                assert "【previous_turn 会话摘要】" in prompt
                assert "【historical_memory】" in prompt
                assert "【current_turn】" in prompt
                assert "【multimodal_recognition】" in prompt
                assert "【inferred】" in prompt

                async with aiosqlite.connect(db_path) as db:
                    cursor = await db.execute(
                        "SELECT summary, covered_message_count FROM session_context_summaries WHERE session_id = ?",
                        ("ctx-labels",),
                    )
                    row = await cursor.fetchone()
                assert row is not None
                assert row[1] == 44
            finally:
                context_manager.get_knowledge_db_path = original_get_path

    asyncio.run(scenario())


def test_history_recall_can_inject_prior_tool_ledger() -> None:
    async def scenario() -> None:
        original_get_path = context_manager.get_knowledge_db_path
        import app.agent.tool_execution_store as tool_store

        original_tool_store_get_path = tool_store.get_knowledge_db_path
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            context_manager.get_knowledge_db_path = lambda: db_path
            tool_store.get_knowledge_db_path = lambda: db_path
            try:
                await record_tool_execution(
                    session_id="ctx-ledger",
                    call_id="call_nginx_status",
                    tool_name="get_service_status",
                    tool_args={"service": "nginx"},
                    risk_level="read",
                    status="success",
                    result={"success": True, "data": {"service": "nginx", "active": True}},
                    execution_state="executed",
                )
                package = await build_context_package(
                    session_id="ctx-ledger",
                    user_message="刚才执行了什么？",
                    conversation_history=[],
                    include_recent_tool_ledger=True,
                )

                prompt = package.messages[-1]["content"]
                assert "历史工具执行证据" in prompt
                assert "get_service_status" in prompt
                assert "previous_turn" in prompt
            finally:
                context_manager.get_knowledge_db_path = original_get_path
                tool_store.get_knowledge_db_path = original_tool_store_get_path

    asyncio.run(scenario())


def main() -> None:
    test_long_session_keeps_bounded_recent_conversation()
    test_context_package_labels_sources_and_summarizes_older_turns()
    test_history_recall_can_inject_prior_tool_ledger()
    print("context management regression OK")


if __name__ == "__main__":
    main()
