"""Runbook API - automated operation playbooks.

Runbooks are replayable sequences of tool calls that were previously
executed successfully. Users can save a diagnosis flow as a runbook
and replay it later (still subject to approval for write operations).
"""

from datetime import datetime

import aiosqlite
from fastapi import APIRouter
from pydantic import BaseModel

from app.agent.runbook_governance import (
    ensure_runbook_schema,
    record_runbook_result,
    save_or_update_runbook,
    serialize_runbook,
    validate_runbook,
)
from app.database import get_knowledge_db_path

router = APIRouter()


class RunbookStep(BaseModel):
    """A single step in a runbook."""
    tool_name: str
    tool_args: dict
    description: str
    risk_level: str


class CreateRunbookRequest(BaseModel):
    """Request to create a new runbook."""
    name: str
    description: str
    trigger_pattern: str  # What problem pattern triggers this runbook
    steps: list[RunbookStep]


@router.get("/")
async def list_runbooks():
    """List all saved runbooks."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_runbook_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM runbooks ORDER BY run_count DESC, last_run DESC"
        )
        rows = await cursor.fetchall()
        runbooks = [serialize_runbook(row) for row in rows]
    return {"runbooks": runbooks}


@router.post("/")
async def create_runbook(request: CreateRunbookRequest):
    """Create a new runbook from a sequence of tool calls."""
    now = datetime.now().isoformat()

    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        runbook_id, updated = await save_or_update_runbook(
            db,
            name=request.name,
            description=request.description,
            trigger_pattern=request.trigger_pattern,
            steps=[s.model_dump() for s in request.steps],
        )

    return {"id": runbook_id, "name": request.name, "created_at": now, "updated": updated}


@router.get("/{runbook_id}")
async def get_runbook(runbook_id: str):
    """Get a specific runbook with full step details."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_runbook_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM runbooks WHERE id = ?", (runbook_id,))
        row = await cursor.fetchone()
        if not row:
            return {"error": "Runbook not found"}
        return serialize_runbook(row)


@router.delete("/{runbook_id}")
async def delete_runbook(runbook_id: str):
    """Delete a runbook."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_runbook_schema(db)
        await db.execute("DELETE FROM runbooks WHERE id = ?", (runbook_id,))
        await db.commit()
    return {"status": "deleted"}


@router.post("/{runbook_id}/validate")
async def validate_runbook_endpoint(runbook_id: str):
    """Validate a runbook using read-only checks."""
    return await validate_runbook(runbook_id)


@router.post("/{runbook_id}/run")
async def record_run(runbook_id: str):
    """Record that a runbook was executed (increment counter)."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await record_runbook_result(
            db,
            runbook_id=runbook_id,
            succeeded=True,
        )
    return {"status": "recorded"}
