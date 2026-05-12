"""Audit logger for complete reasoning trace.

Records every step: input → safety check → plan → tool call → execute → verify → respond.
Supports the ThoughtChain visualization in the frontend.
"""

import json
from datetime import datetime
from enum import Enum

import aiosqlite
from loguru import logger

from app.database import get_audit_db_path


class AuditPhase(str, Enum):
    """Phases in the Agent reasoning pipeline."""
    INPUT_RECEIVED = "input_received"
    SAFETY_CHECK = "safety_check"
    KNOWLEDGE_RETRIEVAL = "knowledge_retrieval"
    PLANNING = "planning"
    TOOL_CALL = "tool_call"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESPONSE = "approval_response"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    RESPONSE = "response"
    ERROR = "error"
    KNOWLEDGE_SAVE = "knowledge_save"


class AuditEventType(str, Enum):
    """Types of audit events."""
    START = "start"
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"
    PENDING = "pending"


class AuditLogger:
    """Records complete reasoning traces for audit and visualization."""

    async def log(
        self,
        session_id: str,
        phase: AuditPhase,
        event_type: AuditEventType,
        content: str,
        metadata: dict | None = None,
    ):
        """Write an audit log entry."""
        timestamp = datetime.now().isoformat()

        try:
            async with aiosqlite.connect(get_audit_db_path()) as db:
                await db.execute(
                    """INSERT INTO audit_logs (session_id, timestamp, phase, event_type, content, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        timestamp,
                        phase.value,
                        event_type.value,
                        content,
                        json.dumps(metadata, ensure_ascii=False) if metadata else None,
                    ),
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

    async def get_trace(self, session_id: str, limit: int = 100) -> list[dict]:
        """Retrieve the reasoning trace for a session."""
        try:
            async with aiosqlite.connect(get_audit_db_path()) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    """SELECT id, timestamp, phase, event_type, content, metadata
                    FROM audit_logs WHERE session_id = ?
                    ORDER BY timestamp ASC LIMIT ?""",
                    (session_id, limit),
                )
                rows = await cursor.fetchall()
                return [
                    {
                        "id": row["id"],
                        "timestamp": row["timestamp"],
                        "phase": row["phase"],
                        "event_type": row["event_type"],
                        "content": row["content"],
                        "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"Failed to retrieve audit trace: {e}")
            return []


# Global audit logger instance
audit_logger = AuditLogger()
