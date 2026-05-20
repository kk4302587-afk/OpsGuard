"""Session management API endpoints."""

import uuid
import json
from datetime import datetime

import aiosqlite
from fastapi import APIRouter

from app.database import get_knowledge_db_path

router = APIRouter()


@router.get("/")
async def list_sessions():
    """List all chat sessions."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, title, created_at, updated_at, status FROM sessions ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        sessions = [
            {"id": row["id"], "title": row["title"], "created_at": row["created_at"], "updated_at": row["updated_at"], "status": row["status"]}
            for row in rows
        ]
    return {"sessions": sessions}


@router.post("/")
async def create_session():
    """Create a new chat session."""
    session_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, "新会话", now, now),
        )
        await db.commit()

    return {"id": session_id, "title": "新会话", "created_at": now}


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    """Delete a chat session."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.commit()
    return {"status": "deleted"}


@router.get("/{session_id}/messages")
async def get_session_messages(session_id: str):
    """Get all messages for a session."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, role, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
        messages = [dict(row) for row in rows]
    return {"messages": messages}


@router.post("/{session_id}/messages")
async def save_message(session_id: str, message: dict):
    """Save a message to a session."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute(
            "INSERT OR REPLACE INTO messages (id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (message["id"], session_id, message["role"], message["content"], message["timestamp"]),
        )
        # Update session title from first user message
        if message["role"] == "user":
            cursor = await db.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'",
                (session_id,),
            )
            count = (await cursor.fetchone())[0]
            if count <= 1:
                title = message["content"][:30] + ("..." if len(message["content"]) > 30 else "")
                await db.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (title, datetime.now().isoformat(), session_id),
                )
        else:
            await db.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), session_id),
            )
        await db.commit()
    return {"status": "saved"}


@router.get("/{session_id}/trace")
async def get_session_trace(session_id: str):
    """Get the reasoning trace for a session.

    Audit rows contain coarse checkpoints. Incident timeline rows are recorded
    from the exact trace payloads emitted during Agent/Runbook execution, so
    they let the UI recover the live reasoning process after a reconnect.
    """
    trace = await _load_incident_trace(session_id)
    trace.extend(await _load_audit_trace(session_id))
    return {"trace": _dedupe_and_sort_trace(trace)}


async def _load_audit_trace(session_id: str) -> list[dict]:
    """Load legacy/coarse audit rows as trace events."""
    from app.database import get_audit_db_path

    async with aiosqlite.connect(get_audit_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT timestamp, phase, event_type, content, metadata
            FROM audit_logs WHERE session_id = ?
            ORDER BY timestamp ASC LIMIT 200""",
            (session_id,),
        )
        rows = await cursor.fetchall()
        trace = []
        for row in rows:
            metadata = json.loads(row["metadata"]) if row["metadata"] else None
            event = {
                "timestamp": row["timestamp"],
                "phase": row["phase"],
                "event_type": row["event_type"],
                "content": row["content"],
                "metadata": metadata,
            }
            if isinstance(metadata, dict) and isinstance(metadata.get("evidence"), dict):
                event.update(metadata["evidence"])
            trace.append(event)
    return trace


async def _load_incident_trace(session_id: str) -> list[dict]:
    """Load trace events captured from incident timeline storage."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, timestamp, phase, event_type, title, detail, evidence, metadata
            FROM incident_events WHERE session_id = ?
            ORDER BY timestamp ASC, id ASC LIMIT 500""",
            (session_id,),
        )
        rows = await cursor.fetchall()

    trace: list[dict] = []
    for row in rows:
        metadata = _json_loads(row["metadata"], {})
        evidence = _json_loads(row["evidence"], None)
        phase = row["phase"]
        event = {
            "timestamp": row["timestamp"],
            "phase": "input_received" if phase == "problem_statement" else phase,
            "event_type": row["event_type"],
            "content": row["detail"] or row["title"],
            "metadata": metadata,
        }
        if isinstance(evidence, dict):
            event.update(evidence)
            event.setdefault("metadata", {}).setdefault("evidence", evidence)
        trace.append(event)
    return trace


def _dedupe_and_sort_trace(events: list[dict]) -> list[dict]:
    """Return stable chronological trace events without exact duplicates."""
    seen: set[tuple[str, str, str]] = set()
    ordered: list[dict] = []
    for event in sorted(events, key=lambda item: item.get("timestamp") or ""):
        key = (
            str(event.get("phase") or ""),
            str(event.get("event_type") or ""),
            str(event.get("content") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        ordered.append(event)
    return ordered


def _json_loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default
