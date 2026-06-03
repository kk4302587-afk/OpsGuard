"""Runbook schema, health, and validation helpers."""

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from app.agent.tools_registry import RiskLevel, tools_registry


RUNBOOK_COLUMNS: dict[str, str] = {
    "version": "INTEGER DEFAULT 1",
    "success_count": "INTEGER DEFAULT 0",
    "failure_count": "INTEGER DEFAULT 0",
    "last_success": "TEXT",
    "last_failure": "TEXT",
    "last_failure_reason": "TEXT",
    "staleness_status": "TEXT DEFAULT 'fresh'",
    "updated_from_session_id": "TEXT",
    "variables": "TEXT",
    "preconditions": "TEXT",
    "applicability_conditions": "TEXT",
    "non_applicability_conditions": "TEXT",
    "postconditions": "TEXT",
    "failure_branches": "TEXT",
    "rollback_steps": "TEXT",
    "owner": "TEXT",
    "review_status": "TEXT DEFAULT 'draft'",
    "ttl_days": "INTEGER DEFAULT 90",
    "last_validated_at": "TEXT",
    "source_incident_id": "TEXT",
}


async def ensure_runbook_schema(db: aiosqlite.Connection) -> None:
    """Create or upgrade the runbooks table in-place."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS runbooks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            trigger_pattern TEXT,
            steps TEXT NOT NULL,
            run_count INTEGER DEFAULT 0,
            last_run TEXT,
            created_at TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            last_success TEXT,
            last_failure TEXT,
            last_failure_reason TEXT,
            staleness_status TEXT DEFAULT 'fresh',
            updated_from_session_id TEXT,
            variables TEXT,
            preconditions TEXT,
            applicability_conditions TEXT,
            non_applicability_conditions TEXT,
            postconditions TEXT,
            failure_branches TEXT,
            rollback_steps TEXT,
            owner TEXT,
            review_status TEXT DEFAULT 'draft',
            ttl_days INTEGER DEFAULT 90,
            last_validated_at TEXT,
            source_incident_id TEXT
        )
    """)
    cursor = await db.execute("PRAGMA table_info(runbooks)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column, ddl in RUNBOOK_COLUMNS.items():
        if column not in existing:
            await db.execute(f"ALTER TABLE runbooks ADD COLUMN {column} {ddl}")
    await db.commit()


async def save_or_update_runbook(
    db: aiosqlite.Connection,
    *,
    name: str,
    description: str,
    trigger_pattern: str,
    steps: list[dict],
    session_id: str | None = None,
    variables: list[dict] | None = None,
    preconditions: list[dict] | None = None,
    applicability_conditions: list[dict] | None = None,
    non_applicability_conditions: list[dict] | None = None,
    postconditions: list[dict] | None = None,
    failure_branches: list[dict] | None = None,
    rollback_steps: list[dict] | None = None,
    owner: str | None = None,
    review_status: str | None = None,
    ttl_days: int | None = None,
    source_incident_id: str | None = None,
) -> tuple[str, bool]:
    """Insert or refresh a Runbook by name and return ``(id, updated)``."""
    await ensure_runbook_schema(db)
    steps_json = json.dumps(steps, ensure_ascii=False)
    optional_json = {
        "variables": json.dumps(variables or [], ensure_ascii=False),
        "preconditions": json.dumps(preconditions or [], ensure_ascii=False),
        "applicability_conditions": json.dumps(applicability_conditions or [], ensure_ascii=False),
        "non_applicability_conditions": json.dumps(non_applicability_conditions or [], ensure_ascii=False),
        "postconditions": json.dumps(postconditions or [], ensure_ascii=False),
        "failure_branches": json.dumps(failure_branches or [], ensure_ascii=False),
        "rollback_steps": json.dumps(rollback_steps or [], ensure_ascii=False),
    }
    now_iso = datetime.now().isoformat()

    cursor = await db.execute("SELECT id FROM runbooks WHERE name = ? LIMIT 1", (name,))
    existing = await cursor.fetchone()
    if existing:
        runbook_id = existing[0]
        await db.execute(
            """
            UPDATE runbooks
            SET steps = ?,
                last_run = ?,
                trigger_pattern = ?,
                description = ?,
                version = COALESCE(version, 1) + 1,
                updated_from_session_id = ?,
                variables = ?,
                preconditions = ?,
                applicability_conditions = ?,
                non_applicability_conditions = ?,
                postconditions = ?,
                failure_branches = ?,
                rollback_steps = ?,
                owner = COALESCE(?, owner),
                review_status = COALESCE(?, review_status),
                ttl_days = COALESCE(?, ttl_days),
                source_incident_id = COALESCE(?, source_incident_id)
            WHERE id = ?
            """,
            (
                steps_json, now_iso, trigger_pattern, description, session_id,
                optional_json["variables"], optional_json["preconditions"],
                optional_json["applicability_conditions"], optional_json["non_applicability_conditions"],
                optional_json["postconditions"], optional_json["failure_branches"],
                optional_json["rollback_steps"], owner, review_status, ttl_days,
                source_incident_id, runbook_id,
            ),
        )
        await refresh_runbook_staleness(db, runbook_id)
        await db.commit()
        return runbook_id, True

    runbook_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO runbooks (
            id, name, description, trigger_pattern, steps, created_at,
            version, updated_from_session_id, variables, preconditions,
            applicability_conditions, non_applicability_conditions, postconditions,
            failure_branches, rollback_steps, owner, review_status, ttl_days,
            source_incident_id
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            runbook_id, name, description, trigger_pattern, steps_json, now_iso,
            session_id, optional_json["variables"], optional_json["preconditions"],
            optional_json["applicability_conditions"], optional_json["non_applicability_conditions"],
            optional_json["postconditions"], optional_json["failure_branches"],
            optional_json["rollback_steps"], owner, review_status or "draft",
            ttl_days or 90, source_incident_id,
        ),
    )
    await refresh_runbook_staleness(db, runbook_id)
    await db.commit()
    return runbook_id, False


async def record_runbook_result(
    db: aiosqlite.Connection,
    *,
    runbook_id: str,
    succeeded: bool,
    failure_reason: str | None = None,
) -> None:
    """Update run counters and health metadata after one replay attempt."""
    await ensure_runbook_schema(db)
    now_iso = datetime.now().isoformat()
    if succeeded:
        await db.execute(
            """
            UPDATE runbooks
            SET run_count = COALESCE(run_count, 0) + 1,
                success_count = COALESCE(success_count, 0) + 1,
                last_run = ?,
                last_success = ?,
                staleness_status = 'fresh'
            WHERE id = ?
            """,
            (now_iso, now_iso, runbook_id),
        )
    else:
        await db.execute(
            """
            UPDATE runbooks
            SET run_count = COALESCE(run_count, 0) + 1,
                failure_count = COALESCE(failure_count, 0) + 1,
                last_run = ?,
                last_failure = ?,
                last_failure_reason = ?
            WHERE id = ?
            """,
            (now_iso, now_iso, failure_reason or "Runbook did not complete", runbook_id),
        )
    await refresh_runbook_staleness(db, runbook_id)
    await db.commit()


async def refresh_runbook_staleness(db: aiosqlite.Connection, runbook_id: str) -> str:
    """Recompute and persist staleness for a single Runbook."""
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM runbooks WHERE id = ?", (runbook_id,))
    row = await cursor.fetchone()
    if not row:
        return "stale"
    status = compute_staleness(dict(row))
    await db.execute(
        "UPDATE runbooks SET staleness_status = ? WHERE id = ?",
        (status, runbook_id),
    )
    return status


def serialize_runbook(row: aiosqlite.Row | dict) -> dict:
    """Convert a runbook DB row into the API/frontend contract."""
    data = dict(row)
    steps = _load_steps(data.get("steps"))
    status = compute_staleness({**data, "steps": steps})
    success_count = int(data.get("success_count") or 0)
    failure_count = int(data.get("failure_count") or 0)
    attempts = success_count + failure_count
    success_rate = round(success_count / attempts, 3) if attempts else None
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "description": data.get("description"),
        "trigger_pattern": data.get("trigger_pattern"),
        "steps": steps,
        "step_count": len(steps),
        "run_count": int(data.get("run_count") or 0),
        "last_run": data.get("last_run"),
        "created_at": data.get("created_at"),
        "version": int(data.get("version") or 1),
        "success_count": success_count,
        "failure_count": failure_count,
        "last_success": data.get("last_success"),
        "last_failure": data.get("last_failure"),
        "last_failure_reason": data.get("last_failure_reason"),
        "staleness_status": status,
        "updated_from_session_id": data.get("updated_from_session_id"),
        "success_rate": success_rate,
        "variables": _json_load(data.get("variables"), []),
        "preconditions": _json_load(data.get("preconditions"), []),
        "applicability_conditions": _json_load(data.get("applicability_conditions"), []),
        "non_applicability_conditions": _json_load(data.get("non_applicability_conditions"), []),
        "postconditions": _json_load(data.get("postconditions"), []),
        "failure_branches": _json_load(data.get("failure_branches"), []),
        "rollback_steps": _json_load(data.get("rollback_steps"), []),
        "owner": data.get("owner"),
        "review_status": data.get("review_status") or "draft",
        "ttl_days": int(data.get("ttl_days") or 90),
        "last_validated_at": data.get("last_validated_at"),
        "source_incident_id": data.get("source_incident_id"),
    }


def compute_staleness(row: dict) -> str:
    """Return fresh/warning/stale for a runbook row."""
    steps = _load_steps(row.get("steps"))
    if any(not tools_registry.get_tool(step.get("tool_name") or "") for step in steps):
        return "stale"

    failure_count = int(row.get("failure_count") or 0)
    if failure_count >= 3:
        return "stale"
    if row.get("last_failure"):
        return "warning"

    last_success = row.get("last_success")
    if not last_success:
        return "warning" if int(row.get("run_count") or 0) else "fresh"

    age_days = _age_days(last_success)
    if age_days is None:
        return "warning"
    if age_days >= 90:
        return "stale"
    if age_days >= 30:
        return "warning"
    return "fresh"


async def validate_runbook(runbook_id: str) -> dict:
    """Read-only validation for a Runbook without executing its steps."""
    from app.database import get_knowledge_db_path

    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_runbook_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM runbooks WHERE id = ?", (runbook_id,))
        row = await cursor.fetchone()

    if not row:
        return {"status": "invalid", "issues": [{"level": "error", "message": "Runbook not found"}]}

    runbook = serialize_runbook(row)
    issues: list[dict[str, str]] = []
    for idx, step in enumerate(runbook["steps"], start=1):
        issues.extend(_validate_step(idx, step))

    status = "valid"
    if any(issue["level"] == "error" for issue in issues):
        status = "invalid"
    elif issues or runbook["staleness_status"] != "fresh":
        status = "warning"

    return {
        "runbook_id": runbook_id,
        "status": status,
        "staleness_status": runbook["staleness_status"],
        "issues": issues,
        "checked_at": datetime.now().isoformat(),
    }


def _validate_step(index: int, step: dict) -> list[dict[str, str]]:
    """Validate one step using read-only checks."""
    tool_name = step.get("tool_name") or ""
    tool_args = step.get("tool_args") or {}
    if not isinstance(tool_args, dict):
        tool_args = {}

    tool_def = tools_registry.get_tool(tool_name)
    if not tool_def:
        return [{
            "level": "error",
            "step": str(index),
            "message": f"Step {index}: tool {tool_name or '(empty)'} is not registered",
        }]

    issues: list[dict[str, str]] = []
    target_issue = _validate_target(index, tool_name, tool_args, tool_def.risk_level)
    if target_issue:
        issues.append(target_issue)
    return issues


def _validate_target(index: int, tool_name: str, tool_args: dict, risk_level: RiskLevel) -> dict[str, str] | None:
    """Check common targets without executing the Runbook step."""
    path_key = next((key for key in ("filepath", "dirpath", "path", "source") if tool_args.get(key)), None)
    if path_key:
        target = Path(str(tool_args[path_key]))
        if tool_name == "write_file" and not target.exists():
            parent = target.parent
            if not parent.exists():
                return {"level": "error", "step": str(index), "message": f"Step {index}: parent path missing: {parent}"}
            return None
        if not target.exists():
            level = "error" if risk_level in (RiskLevel.READ, RiskLevel.DESTRUCTIVE) else "warning"
            return {"level": level, "step": str(index), "message": f"Step {index}: target path missing: {target}"}

    service = tool_args.get("service")
    if service:
        available = _service_exists(str(service))
        if available is False:
            return {"level": "warning", "step": str(index), "message": f"Step {index}: service may not exist: {service}"}
    return None


def _service_exists(service: str) -> bool | None:
    """Best-effort read-only systemd service existence check."""
    try:
        result = subprocess.run(
            ["systemctl", "status", service, "--no-pager"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode == 4:
        return False
    return True


def _load_steps(value: Any) -> list[dict]:
    """Safely parse stored runbook steps."""
    if isinstance(value, list):
        return [step for step in value if isinstance(step, dict)]
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except Exception:
        return []
    return [step for step in loaded if isinstance(step, dict)] if isinstance(loaded, list) else []


def _json_load(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _age_days(iso_value: str) -> int | None:
    """Return age in days for an ISO timestamp."""
    try:
        value = datetime.fromisoformat(str(iso_value))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max((now - value.astimezone(timezone.utc)).days, 0)
    except Exception:
        return None
