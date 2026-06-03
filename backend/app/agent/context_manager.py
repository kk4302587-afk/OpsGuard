"""Context layering and budgeting for long Agent sessions.

The Agent should see enough recent conversation to stay coherent, while older
turns are compressed into a labelled session summary. Raw durable evidence
remains in trace/tool-ledger storage and is referenced through compact blocks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

import aiosqlite
from loguru import logger

from app.agent.tool_execution_store import (
    format_recent_tool_evidence,
    get_recent_tool_executions,
)
from app.database import get_knowledge_db_path


ContextLLMCall = Callable[[list[dict[str, str]]], Awaitable[dict[str, Any]]]


STATE_LABELS = {
    "current_turn",
    "previous_turn",
    "historical_memory",
    "inferred",
    "user_claim",
    "multimodal_recognition",
}

RECENT_TURN_COUNT = 8
SUMMARY_TRIGGER_MESSAGES = 12
SUMMARY_SOURCE_CHAR_LIMIT = 9000
SESSION_SUMMARY_CHAR_LIMIT = 2200


@dataclass
class ContextLayer:
    name: str
    state_label: str
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextPackage:
    messages: list[dict[str, str]]
    layers: list[ContextLayer]
    session_summary: str = ""
    recent_tool_ledger_hint: str = ""
    fresh_evidence_requirements: str = ""

    def layer_block(self) -> str:
        lines = [
            "## 上下文分层说明",
            "所有注入上下文都带有来源标签；只有 current_turn 工具结果可以描述为当前事实。",
        ]
        for layer in self.layers:
            if not layer.content:
                continue
            lines.append(f"\n### [{layer.state_label}] {layer.name}")
            lines.append(layer.content)
        return "\n".join(lines).strip()


async def ensure_context_schema(db: aiosqlite.Connection) -> None:
    """Create session summary storage used by Context Management 2.0."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS session_context_summaries (
            session_id TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            covered_message_count INTEGER DEFAULT 0,
            covered_until TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )


async def build_context_package(
    *,
    session_id: str,
    user_message: str,
    conversation_history: list[dict[str, Any]],
    knowledge_hint: str = "",
    recent_changes_hint: str = "",
    multimodal_hint: str = "",
    fresh_evidence_hint: str = "",
    include_recent_tool_ledger: bool = False,
    llm_call: ContextLLMCall | None = None,
) -> ContextPackage:
    """Build bounded, source-labelled context for one Agent turn."""
    recent_messages, older_messages = split_recent_conversation(conversation_history)
    session_summary = await get_or_update_session_summary(
        session_id=session_id,
        older_messages=older_messages,
        llm_call=llm_call,
    )
    recent_tool_ledger_hint = ""
    if include_recent_tool_ledger:
        recent_tool_ledger_hint = await _load_recent_tool_ledger_hint(session_id)

    layers = [
        ContextLayer(
            name="current_user_request",
            state_label="user_claim",
            content=strip_to_budget(user_message, 1200),
        ),
        ContextLayer(
            name="recent_conversation",
            state_label="previous_turn",
            content=format_recent_conversation(recent_messages),
            metadata={"message_count": len(recent_messages)},
        ),
        ContextLayer(
            name="session_summary",
            state_label="previous_turn",
            content=session_summary,
            metadata={"older_message_count": len(older_messages)},
        ),
        ContextLayer(
            name="historical_memory",
            state_label="historical_memory",
            content=label_existing_block(knowledge_hint, "historical_memory"),
        ),
        ContextLayer(
            name="recent_tool_ledger",
            state_label="previous_turn",
            content=label_existing_block(recent_tool_ledger_hint, "previous_turn"),
        ),
        ContextLayer(
            name="fresh_evidence_requirements",
            state_label="inferred",
            content=label_existing_block(fresh_evidence_hint, "inferred"),
        ),
        ContextLayer(
            name="multimodal_recognition",
            state_label="multimodal_recognition",
            content=label_existing_block(multimodal_hint, "multimodal_recognition"),
        ),
        ContextLayer(
            name="recent_changes",
            state_label="current_turn",
            content=label_existing_block(recent_changes_hint, "current_turn"),
        ),
    ]

    context_block = ContextPackage(
        messages=recent_messages,
        layers=layers,
        session_summary=session_summary,
        recent_tool_ledger_hint=recent_tool_ledger_hint,
        fresh_evidence_requirements=fresh_evidence_hint,
    ).layer_block()

    bounded_messages = list(recent_messages)
    user_content = user_message
    if context_block:
        user_content = f"{user_message}\n\n{context_block}"
    bounded_messages.append({"role": "user", "content": user_content})
    return ContextPackage(
        messages=bounded_messages,
        layers=layers,
        session_summary=session_summary,
        recent_tool_ledger_hint=recent_tool_ledger_hint,
        fresh_evidence_requirements=fresh_evidence_hint,
    )


def split_recent_conversation(
    conversation_history: list[dict[str, Any]],
    *,
    recent_turns: int = RECENT_TURN_COUNT,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Keep the most recent user/assistant turns verbatim."""
    normalized = [
        {"role": str(msg.get("role") or ""), "content": str(msg.get("content") or "")}
        for msg in conversation_history
        if msg.get("role") in {"user", "assistant"} and msg.get("content")
    ]
    keep_messages = max(2, recent_turns * 2)
    if len(normalized) <= keep_messages:
        return normalized, []
    return normalized[-keep_messages:], normalized[:-keep_messages]


async def get_or_update_session_summary(
    *,
    session_id: str,
    older_messages: list[dict[str, str]],
    llm_call: ContextLLMCall | None = None,
) -> str:
    """Return a rolling summary for conversation outside the recent window."""
    if not older_messages:
        return await _load_session_summary(session_id)

    existing = await _load_summary_row(session_id)
    if existing and int(existing.get("covered_message_count") or 0) >= len(older_messages):
        return str(existing.get("summary") or "")

    summary = await summarize_older_conversation(
        older_messages=older_messages,
        previous_summary=str(existing.get("summary") or "") if existing else "",
        llm_call=llm_call,
    )
    await _save_session_summary(
        session_id=session_id,
        summary=summary,
        covered_message_count=len(older_messages),
        covered_until=_covered_until(older_messages),
    )
    return summary


async def summarize_older_conversation(
    *,
    older_messages: list[dict[str, str]],
    previous_summary: str = "",
    llm_call: ContextLLMCall | None = None,
) -> str:
    """Summarize older turns, falling back to deterministic compaction."""
    if len(older_messages) < SUMMARY_TRIGGER_MESSAGES or llm_call is None:
        return deterministic_session_summary(older_messages, previous_summary=previous_summary)

    source = format_messages_for_summary(older_messages, max_chars=SUMMARY_SOURCE_CHAR_LIMIT)
    prompt = [
        {
            "role": "system",
            "content": (
                "你是 OpsGuard 会话上下文摘要器。只总结较早对话，不要生成运维结论。"
                "保留用户目标、已执行工具、明确结论、未解决问题、重要路径/服务名。"
                "所有内容都必须表述为 previous_turn，不要写成当前状态。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"已有摘要：\n{previous_summary or '无'}\n\n"
                f"较早对话：\n{source}\n\n"
                "请输出 1200 字以内中文摘要。"
            ),
        },
    ]
    try:
        response = await llm_call(prompt)
        text = str(response.get("content") or "").strip()
        if text:
            return strip_to_budget(text, SESSION_SUMMARY_CHAR_LIMIT)
    except Exception as exc:
        logger.warning(f"Session summary LLM compaction failed: {exc}")
    return deterministic_session_summary(older_messages, previous_summary=previous_summary)


def deterministic_session_summary(
    older_messages: list[dict[str, str]],
    *,
    previous_summary: str = "",
) -> str:
    lines = []
    if previous_summary:
        lines.append(strip_to_budget(previous_summary, 900))
    lines.append("【previous_turn 会话摘要】以下是较早对话的压缩摘要，不能当作当前系统状态。")
    for msg in older_messages[-SUMMARY_TRIGGER_MESSAGES:]:
        role = "用户" if msg.get("role") == "user" else "助手"
        content = str(msg.get("content") or "").replace("\n", " ")
        lines.append(f"- {role}: {strip_to_budget(content, 180)}")
    return strip_to_budget("\n".join(lines), SESSION_SUMMARY_CHAR_LIMIT)


def format_recent_conversation(messages: list[dict[str, str]]) -> str:
    if not messages:
        return ""
    lines = ["最近对话原文，仅用于理解上下文；其中系统状态一律是 previous_turn。"]
    for msg in messages:
        role = "用户" if msg.get("role") == "user" else "助手"
        content = str(msg.get("content") or "").replace("\n", " ")
        lines.append(f"- {role}: {strip_to_budget(content, 260)}")
    return "\n".join(lines)


def format_messages_for_summary(messages: list[dict[str, str]], *, max_chars: int) -> str:
    lines = []
    for msg in messages:
        role = "用户" if msg.get("role") == "user" else "助手"
        lines.append(f"[{role}] {str(msg.get('content') or '')}")
    return strip_to_budget("\n".join(lines), max_chars)


def label_existing_block(block: str, state_label: str) -> str:
    block = (block or "").strip()
    if not block:
        return ""
    if state_label not in STATE_LABELS:
        state_label = "inferred"
    return f"【{state_label}】\n{block}"


def strip_to_budget(text: str, max_chars: int) -> str:
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n...（已按上下文预算截断）"


async def _load_recent_tool_ledger_hint(session_id: str) -> str:
    try:
        executions = await get_recent_tool_executions(session_id, limit=12)
        return format_recent_tool_evidence(executions)
    except Exception as exc:
        logger.warning(f"Recent tool evidence lookup failed: {exc}")
        return ""


async def _load_session_summary(session_id: str) -> str:
    row = await _load_summary_row(session_id)
    return str(row.get("summary") or "") if row else ""


async def _load_summary_row(session_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_context_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT session_id, summary, covered_message_count, covered_until, updated_at
            FROM session_context_summaries WHERE session_id = ?
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def _save_session_summary(
    *,
    session_id: str,
    summary: str,
    covered_message_count: int,
    covered_until: str,
) -> None:
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_context_schema(db)
        await db.execute(
            """
            INSERT INTO session_context_summaries
                (session_id, summary, covered_message_count, covered_until, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                summary = excluded.summary,
                covered_message_count = excluded.covered_message_count,
                covered_until = excluded.covered_until,
                updated_at = excluded.updated_at
            """,
            (
                session_id,
                summary,
                covered_message_count,
                covered_until,
                datetime.now().isoformat(),
            ),
        )
        await db.commit()


def _covered_until(messages: list[dict[str, str]]) -> str:
    if not messages:
        return ""
    last = messages[-1]
    timestamp = last.get("timestamp")
    if timestamp:
        return str(timestamp)
    return json.dumps(last, ensure_ascii=False)[:300]
