"""Session management API endpoints."""

import uuid
import json
from pathlib import Path
from datetime import datetime

import aiosqlite
from fastapi import APIRouter, HTTPException, Query

from app.agent.tool_execution_store import ensure_tool_execution_schema
from app.database import get_knowledge_db_path

router = APIRouter()

_VIEWABLE_WRITE_TARGETS = {
    "write_file": "filepath",
    "create_file": "filepath",
    "copy_file": "destination",
    "move_file": "destination",
}


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
        if messages:
            attachment_map = await _load_message_attachments(db, [message["id"] for message in messages])
            for message in messages:
                message["attachments"] = attachment_map.get(message["id"], [])
    return {"messages": messages}


@router.post("/{session_id}/messages")
async def save_message(session_id: str, message: dict):
    """Save a message to a session."""
    attachments = _coerce_attachment_refs(message.get("attachments"))
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute(
            "INSERT OR REPLACE INTO messages (id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (message["id"], session_id, message["role"], message["content"], message["timestamp"]),
        )
        if attachments:
            ids = [item["id"] for item in attachments if item.get("id")]
            placeholders = ",".join("?" for _ in ids)
            await db.execute(
                f"""UPDATE message_attachments
                SET session_id = ?, message_id = ?
                WHERE id IN ({placeholders})""",
                (session_id, message["id"], *ids),
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


def _coerce_attachment_refs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    refs: list[dict] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        attachment_id = item.get("id")
        input_type = item.get("type") or item.get("input_type")
        if isinstance(attachment_id, str) and input_type in {"image", "audio"}:
            refs.append({"id": attachment_id, "type": input_type})
    return refs


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


@router.get("/{session_id}/written-file-content")
async def get_written_file_content(
    session_id: str,
    path: str = Query(..., min_length=1),
    max_bytes: int = Query(65536, ge=1, le=1024 * 1024),
):
    """Read current content for a file successfully written in this session."""
    requested_path = _validated_absolute_file_path(path)
    write_record = await _find_session_write_target(session_id, requested_path)
    if not write_record:
        raise HTTPException(
            status_code=403,
            detail="当前会话没有成功写入过该文件，不能直接查看。",
        )

    content = _read_current_file_content(requested_path, max_bytes)
    return {
        **content,
        "write": write_record,
    }


async def _load_audit_trace(session_id: str) -> list[dict]:
    """Load legacy/coarse audit rows as trace events."""
    from app.database import get_audit_db_path

    async with aiosqlite.connect(get_audit_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, timestamp, phase, event_type, content, metadata
            FROM audit_logs WHERE session_id = ?
            ORDER BY timestamp ASC LIMIT 200""",
            (session_id,),
        )
        rows = await cursor.fetchall()
        trace = []
        for row in rows:
            metadata = json.loads(row["metadata"]) if row["metadata"] else None
            event = {
                "id": f"audit:{row['id']}",
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
            "id": f"incident:{row['id']}",
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
    seen: set[tuple[str, str, str, str, str]] = set()
    ordered: list[dict] = []
    for event in sorted(events, key=lambda item: (item.get("timestamp") or "", item.get("id") or "")):
        # Keep repeated fixed-text phases from later turns. Only collapse exact
        # duplicate rows from multiple persistence sources at the same timestamp.
        key = (
            str(event.get("timestamp") or ""),
            str(event.get("phase") or ""),
            str(event.get("event_type") or ""),
            str(event.get("content") or ""),
            str(event.get("source") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        ordered.append(event)
    return ordered


def _validated_absolute_file_path(value: str) -> Path:
    if "\x00" in value:
        raise HTTPException(status_code=400, detail="文件路径包含非法字符。")

    path = Path(value)
    if not path.is_absolute():
        raise HTTPException(status_code=400, detail="仅支持查看绝对路径文件。")
    return path


async def _find_session_write_target(session_id: str, requested_path: Path) -> dict | None:
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_tool_execution_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, tool_name, tool_args, timestamp
            FROM tool_executions
            WHERE session_id = ?
              AND status = 'success'
              AND is_write = 1
              AND approval_granted = 1
            ORDER BY timestamp DESC, id DESC
            LIMIT 100
            """,
            (session_id,),
        )
        rows = await cursor.fetchall()

    for row in rows:
        tool_name = row["tool_name"] or ""
        target_arg = _VIEWABLE_WRITE_TARGETS.get(tool_name)
        if not target_arg:
            continue
        tool_args = _json_loads(row["tool_args"], {})
        if not isinstance(tool_args, dict):
            continue
        target = tool_args.get(target_arg)
        if not isinstance(target, str) or not target:
            continue
        if _same_file_target(target, requested_path):
            return {
                "tool_name": tool_name,
                "timestamp": row["timestamp"],
                "target": str(requested_path),
            }
    return None


def _same_file_target(recorded_path: str, requested_path: Path) -> bool:
    if "\x00" in recorded_path:
        return False

    candidate = Path(recorded_path)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate)

    if str(candidate) == str(requested_path):
        return True

    try:
        return candidate.resolve(strict=False) == requested_path.resolve(strict=False)
    except OSError:
        return False


def _read_current_file_content(path: Path, max_bytes: int) -> dict:
    try:
        if path.is_symlink():
            raise HTTPException(status_code=400, detail="目标是符号链接，暂不支持直接查看。")
        if not path.exists():
            raise HTTPException(status_code=404, detail="文件当前不存在。")
        if path.is_dir():
            raise HTTPException(status_code=400, detail="目标是目录，不能按文件内容查看。")
        if not path.is_file():
            raise HTTPException(status_code=400, detail="目标不是普通文件。")

        safe_max = max(1, min(int(max_bytes or 65536), 1024 * 1024))
        stat = path.stat()
        with open(path, "rb") as f:
            raw = f.read(safe_max)
        return {
            "path": str(path),
            "size": stat.st_size,
            "max_bytes": safe_max,
            "truncated": stat.st_size > safe_max,
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "content": raw.decode("utf-8", errors="replace"),
        }
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"读取文件失败：{exc}") from exc


def _json_loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


async def _load_message_attachments(db: aiosqlite.Connection, message_ids: list[str]) -> dict[str, list[dict]]:
    if not message_ids:
        return {}
    placeholders = ",".join("?" for _ in message_ids)
    cursor = await db.execute(
        f"""SELECT id, message_id, input_type, filename, content_type, size, sha256
        FROM message_attachments
        WHERE message_id IN ({placeholders})
        ORDER BY created_at ASC""",
        message_ids,
    )
    rows = await cursor.fetchall()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        item = {
            "id": row["id"],
            "type": row["input_type"],
            "filename": row["filename"],
            "content_type": row["content_type"],
            "size": row["size"],
            "sha256": row["sha256"],
            "previewUrl": f"/api/multimodal/attachments/{row['id']}",
        }
        grouped.setdefault(row["message_id"], []).append(item)
    return grouped
