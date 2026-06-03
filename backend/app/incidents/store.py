"""Persistent incident timeline storage.

Incident events are derived from real trace, approval, and final response
payloads. This module must not invent execution outcomes.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import aiosqlite

from app.database import get_knowledge_db_path


INCIDENT_STATUSES = {"open", "resolved", "failed"}

_EVIDENCE_KEYS = {
    "claim",
    "evidence_type",
    "source",
    "observed",
    "confidence",
    "execution_state",
    "failure_reason",
    "next_check",
}


async def ensure_incident_schema(db: aiosqlite.Connection) -> None:
    """Create incident timeline tables and indexes if they do not exist."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            problem_statement TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            final_summary TEXT,
            followups TEXT,
            metadata TEXT
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS incident_events (
            id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            phase TEXT NOT NULL,
            title TEXT NOT NULL,
            detail TEXT,
            evidence TEXT,
            metadata TEXT,
            FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE CASCADE
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_incidents_session ON incidents(session_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_incidents_updated ON incidents(updated_at)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_incident_events_incident ON incident_events(incident_id, timestamp)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_incident_events_session ON incident_events(session_id)"
    )


async def create_incident(
    *,
    session_id: str,
    problem_statement: str,
    source: str,
    metadata: dict | None = None,
    db_path: str | None = None,
) -> str:
    """Create an open incident and record the initial problem statement."""
    incident_id = str(uuid.uuid4())
    now = _now()
    event_metadata = {"source": source}
    if metadata:
        event_metadata.update(metadata)

    async with aiosqlite.connect(_resolve_db_path(db_path)) as db:
        await ensure_incident_schema(db)
        await db.execute(
            """
            INSERT INTO incidents
                (id, session_id, status, problem_statement, created_at, updated_at, metadata)
            VALUES (?, ?, 'open', ?, ?, ?, ?)
            """,
            (
                incident_id,
                session_id,
                problem_statement[:1000] or "(empty request)",
                now,
                now,
                _json_dumps(event_metadata),
            ),
        )
        await db.execute(
            """
            INSERT INTO incident_events
                (id, incident_id, session_id, timestamp, event_type, phase, title, detail, evidence, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                incident_id,
                session_id,
                now,
                "start",
                "problem_statement",
                "Problem statement captured",
                problem_statement[:4000],
                _json_dumps(
                    {
                        "claim": "事件问题描述来自用户输入",
                        "evidence_type": "user input",
                        "source": source,
                        "observed": problem_statement[:500],
                        "confidence": "high",
                        "execution_state": "executed",
                    }
                ),
                _json_dumps(event_metadata),
            ),
        )
        await db.commit()
    return incident_id


async def record_incident_event(
    *,
    incident_id: str,
    session_id: str,
    phase: str,
    event_type: str,
    title: str,
    detail: str = "",
    evidence: dict | None = None,
    metadata: dict | None = None,
    timestamp: str | None = None,
    db_path: str | None = None,
) -> str:
    """Persist a single incident timeline event."""
    event_id = str(uuid.uuid4())
    event_time = timestamp or _now()
    async with aiosqlite.connect(_resolve_db_path(db_path)) as db:
        await ensure_incident_schema(db)
        await db.execute(
            """
            INSERT INTO incident_events
                (id, incident_id, session_id, timestamp, event_type, phase, title, detail, evidence, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                incident_id,
                session_id,
                event_time,
                event_type or "event",
                phase or "unknown",
                title[:300] or "Incident event",
                detail[:4000] if detail else "",
                _json_dumps(evidence) if evidence else None,
                _json_dumps(metadata) if metadata else None,
            ),
        )
        await db.execute(
            "UPDATE incidents SET updated_at = ? WHERE id = ?",
            (event_time, incident_id),
        )
        await db.commit()
    return event_id


async def record_incident_from_message(
    *,
    incident_id: str,
    session_id: str,
    message: dict,
    db_path: str | None = None,
) -> str | None:
    """Convert a live server payload into an incident event when applicable."""
    msg_type = message.get("type")
    timestamp = message.get("timestamp") or _now()

    if msg_type == "trace":
        phase = str(message.get("phase") or "unknown")
        event_type = str(message.get("event_type") or "event")
        detail = str(message.get("content") or "")
        title = _first_line(detail) or f"{phase} {event_type}"
        metadata = _message_metadata(message)
        return await record_incident_event(
            incident_id=incident_id,
            session_id=session_id,
            phase=phase,
            event_type=event_type,
            title=title,
            detail=detail,
            evidence=_extract_evidence(message),
            metadata=metadata,
            timestamp=timestamp,
            db_path=db_path,
        )

    if msg_type == "approval_request":
        command = str(message.get("command") or "")
        detail_parts = [
            str(message.get("description") or ""),
            str(message.get("impact") or ""),
        ]
        detail = "\n".join(part for part in detail_parts if part)
        return await record_incident_event(
            incident_id=incident_id,
            session_id=session_id,
            phase="approval_request",
            event_type="pending",
            title=f"Approval requested: {command}"[:300],
            detail=detail or command,
            evidence={
                "claim": "Write or destructive operation is waiting for user approval",
                "evidence_type": "user input",
                "source": "approval_manager",
                "observed": command,
                "confidence": "high",
                "execution_state": "skipped",
            },
            metadata=_message_metadata(message),
            timestamp=timestamp,
            db_path=db_path,
        )

    return None


async def finalize_incident(
    *,
    incident_id: str,
    final_summary: str,
    status: str | None = None,
    followups: list[str] | None = None,
    db_path: str | None = None,
) -> dict:
    """Mark an incident complete and return its compact summary counts."""
    resolved_status = status if status in INCIDENT_STATUSES else None
    if resolved_status is None:
        resolved_status = await _infer_status(incident_id, db_path=db_path)

    now = _now()
    async with aiosqlite.connect(_resolve_db_path(db_path)) as db:
        await ensure_incident_schema(db)
        await db.execute(
            """
            UPDATE incidents
            SET status = ?, updated_at = ?, final_summary = ?, followups = ?
            WHERE id = ?
            """,
            (
                resolved_status,
                now,
                final_summary[:4000],
                _json_dumps(followups or []),
                incident_id,
            ),
        )
        await db.commit()
    return await get_incident_summary(incident_id, db_path=db_path)


async def append_incident_reference(
    response: str,
    incident_id: str,
    *,
    db_path: str | None = None,
) -> str:
    """Return the assistant response without appending internal incident metadata.

    Incident records are still persisted and available through the incident APIs,
    but normal chat replies should not expose trace ids, event counts, or debug
    endpoints to operators by default.
    """
    return response


async def get_incidents(
    *,
    session_id: str | None = None,
    limit: int = 50,
    db_path: str | None = None,
) -> list[dict]:
    """List incidents, optionally filtered by session."""
    query = (
        "SELECT * FROM incidents WHERE session_id = ? ORDER BY updated_at DESC LIMIT ?"
        if session_id
        else "SELECT * FROM incidents ORDER BY updated_at DESC LIMIT ?"
    )
    params: tuple[Any, ...] = (session_id, limit) if session_id else (limit,)
    async with aiosqlite.connect(_resolve_db_path(db_path)) as db:
        await ensure_incident_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
    return [_incident_from_row(row) for row in rows]


async def get_incident(incident_id: str, *, db_path: str | None = None) -> dict | None:
    """Return one incident by id."""
    async with aiosqlite.connect(_resolve_db_path(db_path)) as db:
        await ensure_incident_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        row = await cursor.fetchone()
    return _incident_from_row(row) if row else None


async def get_incident_events(
    incident_id: str,
    *,
    limit: int = 500,
    db_path: str | None = None,
) -> list[dict]:
    """Return ordered timeline events for one incident."""
    async with aiosqlite.connect(_resolve_db_path(db_path)) as db:
        await ensure_incident_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM incident_events
            WHERE incident_id = ?
            ORDER BY timestamp ASC, id ASC
            LIMIT ?
            """,
            (incident_id, limit),
        )
        rows = await cursor.fetchall()
    return [_event_from_row(row) for row in rows]


async def get_incident_summary(
    incident_id: str,
    *,
    db_path: str | None = None,
) -> dict | None:
    """Return compact counts used in assistant responses and reports."""
    incident = await get_incident(incident_id, db_path=db_path)
    if not incident:
        return None

    async with aiosqlite.connect(_resolve_db_path(db_path)) as db:
        await ensure_incident_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                COUNT(*) AS event_count,
                SUM(CASE WHEN phase = 'execution' AND event_type IN ('success', 'failure') THEN 1 ELSE 0 END) AS tool_result_count,
                SUM(CASE WHEN event_type IN ('failure', 'blocked') THEN 1 ELSE 0 END) AS failure_count
            FROM incident_events
            WHERE incident_id = ?
            """,
            (incident_id,),
        )
        row = await cursor.fetchone()

    incident.update(
        {
            "event_count": int(row["event_count"] or 0),
            "tool_result_count": int(row["tool_result_count"] or 0),
            "failure_count": int(row["failure_count"] or 0),
        }
    )
    return incident


async def get_recent_incident_stats(
    *,
    since: str,
    limit: int = 10,
    db_path: str | None = None,
) -> dict:
    """Return incident counts and recent rows for OpsReport."""
    async with aiosqlite.connect(_resolve_db_path(db_path)) as db:
        await ensure_incident_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT status, COUNT(*) AS cnt
            FROM incidents
            WHERE created_at >= ?
            GROUP BY status
            """,
            (since,),
        )
        counts = {row["status"]: row["cnt"] for row in await cursor.fetchall()}
        cursor = await db.execute(
            """
            SELECT id, session_id, status, problem_statement, created_at, updated_at
            FROM incidents
            WHERE created_at >= ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (since, limit),
        )
        recent = [dict(row) for row in await cursor.fetchall()]

    return {
        "total": sum(counts.values()),
        "by_status": counts,
        "recent": recent,
    }


async def get_recent_multimodal_evidence(
    *,
    since: str,
    limit: int = 20,
    db_path: str | None = None,
) -> dict:
    """Return multimodal incident evidence plus real tool verification events."""
    async with aiosqlite.connect(_resolve_db_path(db_path)) as db:
        await ensure_incident_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT *
            FROM incident_events
            WHERE timestamp >= ?
              AND phase IN ('image_recognition', 'voice_recognition', 'multimodal_recognition')
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (since, limit),
        )
        multimodal_rows = await cursor.fetchall()
        incident_ids = [row["incident_id"] for row in multimodal_rows]

        verification_by_incident: dict[str, list[dict]] = {}
        if incident_ids:
            placeholders = ",".join("?" for _ in incident_ids)
            cursor = await db.execute(
                f"""
                SELECT *
                FROM incident_events
                WHERE incident_id IN ({placeholders})
                  AND phase IN ('execution', 'verification')
                ORDER BY timestamp ASC, id ASC
                """,
                tuple(incident_ids),
            )
            for row in await cursor.fetchall():
                event = _event_from_row(row)
                evidence = event.get("evidence") if isinstance(event.get("evidence"), dict) else {}
                if evidence.get("execution_state") not in {"executed", "failed"}:
                    continue
                verification_by_incident.setdefault(row["incident_id"], []).append({
                    "timestamp": row["timestamp"],
                    "phase": row["phase"],
                    "event_type": row["event_type"],
                    "title": row["title"],
                    "detail": row["detail"],
                    "source": evidence.get("source"),
                    "execution_state": evidence.get("execution_state"),
                    "observed": evidence.get("observed"),
                })

    items = []
    image_count = 0
    audio_count = 0
    for row in multimodal_rows:
        event = _event_from_row(row)
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        evidence = event.get("evidence") if isinstance(event.get("evidence"), dict) else {}
        multimodal = metadata.get("multimodal") if isinstance(metadata.get("multimodal"), dict) else {}
        input_type = multimodal.get("input_type") or multimodal.get("type") or (
            "image" if row["phase"] == "image_recognition" else "audio" if row["phase"] == "voice_recognition" else "unknown"
        )
        if input_type == "image":
            image_count += 1
        elif input_type == "audio":
            audio_count += 1
        items.append({
            "incident_id": row["incident_id"],
            "session_id": row["session_id"],
            "timestamp": row["timestamp"],
            "input_type": input_type,
            "title": row["title"],
            "summary": multimodal.get("summary") or multimodal.get("normalized_transcript") or row["detail"],
            "recognized_text": multimodal.get("normalized_transcript") or multimodal.get("extracted_text") or multimodal.get("raw_transcript") or "",
            "entities": multimodal.get("entities") or {},
            "diagnosis_hints": multimodal.get("diagnosis_hints") or [],
            "confidence": multimodal.get("confidence") or evidence.get("confidence"),
            "source": evidence.get("source") or multimodal.get("provider"),
            "verification": verification_by_incident.get(row["incident_id"], [])[:5],
        })

    return {
        "total": len(items),
        "images": image_count,
        "audio": audio_count,
        "items": items,
    }


async def _infer_status(incident_id: str, *, db_path: str | None = None) -> str:
    async with aiosqlite.connect(_resolve_db_path(db_path)) as db:
        await ensure_incident_schema(db)
        cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM incident_events
            WHERE incident_id = ?
              AND event_type = 'blocked'
              AND phase IN ('safety_check', 'response', 'error')
            """,
            (incident_id,),
        )
        blocked_count = (await cursor.fetchone())[0]
        if blocked_count:
            return "failed"

        cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM incident_events
            WHERE incident_id = ?
              AND event_type = 'success'
              AND phase = 'response'
            """,
            (incident_id,),
        )
        response_success_count = (await cursor.fetchone())[0]
        if response_success_count:
            return "resolved"

        cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM incident_events
            WHERE incident_id = ?
              AND event_type = 'failure'
              AND phase IN ('safety_check', 'response', 'error')
            """,
            (incident_id,),
        )
        fatal_failure_count = (await cursor.fetchone())[0]
    return "failed" if fatal_failure_count else "resolved"


def _extract_evidence(message: dict) -> dict | None:
    metadata = message.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("evidence"), dict):
        return metadata["evidence"]
    evidence = {key: message[key] for key in _EVIDENCE_KEYS if key in message}
    return evidence or None


def _message_metadata(message: dict) -> dict:
    metadata = dict(message.get("metadata") or {}) if isinstance(message.get("metadata"), dict) else {}
    for key, value in message.items():
        if key not in _EVIDENCE_KEYS and key not in {"content", "metadata"}:
            metadata.setdefault(key, value)
    return metadata


def _incident_from_row(row: aiosqlite.Row) -> dict:
    item = dict(row)
    item["followups"] = _json_loads(item.get("followups"), [])
    item["metadata"] = _json_loads(item.get("metadata"), {})
    return item


def _event_from_row(row: aiosqlite.Row) -> dict:
    item = dict(row)
    item["evidence"] = _json_loads(item.get("evidence"), None)
    item["metadata"] = _json_loads(item.get("metadata"), {})
    return item


def _resolve_db_path(db_path: str | None) -> str:
    return db_path or get_knowledge_db_path()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _now() -> str:
    return datetime.now().isoformat()


def _first_line(value: str, max_chars: int = 140) -> str:
    line = " ".join((value or "").strip().splitlines()[:1])
    return line[:max_chars]
