"""Persistent tool execution ledger for evidence-aware replies."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import aiosqlite
from loguru import logger

from app.database import get_knowledge_db_path
from app.agent.trace_evidence import compact_observed


async def ensure_tool_execution_schema(db: aiosqlite.Connection) -> None:
    """Create the tool execution ledger table if needed."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            incident_id TEXT,
            call_id TEXT,
            tool_name TEXT NOT NULL,
            tool_args TEXT,
            risk_level TEXT,
            status TEXT NOT NULL,
            result_summary TEXT,
            error TEXT,
            timestamp TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_executions_session_time ON tool_executions(session_id, timestamp)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_executions_call_id ON tool_executions(call_id)"
    )


async def record_tool_execution(
    *,
    session_id: str,
    incident_id: str = "",
    call_id: str = "",
    tool_name: str,
    tool_args: dict[str, Any],
    risk_level: str,
    status: str,
    result: Any = None,
    error: str | None = None,
) -> None:
    """Persist one real tool execution attempt.

    This ledger is the durable source of truth used by follow-up turns. It is
    intentionally separate from chat messages because assistant prose is not
    reliable evidence that a tool actually ran.
    """
    try:
        result_dict = _result_to_dict(result)
        result_summary = compact_observed(
            result_dict.get("data", result_dict) if isinstance(result_dict, dict) else result_dict,
            max_chars=700,
        )
        error_text = error
        if not error_text and isinstance(result_dict, dict):
            error_text = result_dict.get("error")

        async with aiosqlite.connect(get_knowledge_db_path()) as db:
            await ensure_tool_execution_schema(db)
            await db.execute(
                """
                INSERT INTO tool_executions
                    (session_id, incident_id, call_id, tool_name, tool_args, risk_level,
                     status, result_summary, error, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    incident_id,
                    call_id,
                    tool_name,
                    json.dumps(tool_args or {}, ensure_ascii=False, default=str),
                    risk_level,
                    status,
                    result_summary,
                    compact_observed(error_text, max_chars=500) if error_text else None,
                    datetime.now().isoformat(),
                ),
            )
            await db.commit()
    except Exception as exc:
        logger.warning(f"Failed to record tool execution ledger row: {exc}")


async def get_recent_tool_executions(
    session_id: str,
    *,
    limit: int = 12,
    successful_only: bool = False,
) -> list[dict[str, Any]]:
    """Return recent tool execution evidence for a session."""
    where = "WHERE session_id = ?"
    params: list[Any] = [session_id]
    if successful_only:
        where += " AND status = 'success'"

    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_tool_execution_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""
            SELECT id, session_id, incident_id, call_id, tool_name, tool_args,
                   risk_level, status, result_summary, error, timestamp
            FROM tool_executions
            {where}
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()

    return [_row_to_execution(row) for row in rows]


def format_recent_tool_evidence(executions: list[dict[str, Any]]) -> str:
    """Build a compact prompt block from recent tool execution evidence."""
    if not executions:
        return ""

    lines = [
        "",
        "## 历史工具执行证据",
        "以下记录来自后端工具执行账本，可用于回答“刚刚/上一轮/之前”的追问。",
        "如果用户要求“重新检查/当前/现在”，仍需重新调用工具获取新证据。",
    ]
    for item in executions:
        status = "成功" if item["status"] == "success" else "失败"
        args_text = compact_observed(item.get("tool_args") or {}, max_chars=120)
        observed = item.get("error") or item.get("result_summary") or ""
        lines.append(
            f"- [{item['timestamp']}] {item['tool_name']}({args_text}) {status}: "
            f"{compact_observed(observed, max_chars=220)}"
        )
    return "\n".join(lines) + "\n"


def _result_to_dict(result: Any) -> Any:
    if hasattr(result, "__dict__"):
        return dict(result.__dict__)
    return result


def _row_to_execution(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "incident_id": row["incident_id"] or "",
        "call_id": row["call_id"] or "",
        "tool_name": row["tool_name"],
        "tool_args": _json_loads(row["tool_args"], {}),
        "risk_level": row["risk_level"] or "",
        "status": row["status"],
        "result_summary": row["result_summary"] or "",
        "error": row["error"] or "",
        "timestamp": row["timestamp"],
    }


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default
