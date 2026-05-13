"""Runbook API - automated operation playbooks.

Runbooks are replayable sequences of tool calls that were previously
executed successfully. Users can save a diagnosis flow as a runbook
and replay it later (still subject to approval for write operations).
"""

import json
import uuid
from datetime import datetime

import aiosqlite
from fastapi import APIRouter
from pydantic import BaseModel

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
        # Ensure table exists
        await db.execute("""
            CREATE TABLE IF NOT EXISTS runbooks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                trigger_pattern TEXT,
                steps TEXT NOT NULL,
                run_count INTEGER DEFAULT 0,
                last_run TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()

        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, name, description, trigger_pattern, steps, run_count, last_run, created_at "
            "FROM runbooks ORDER BY run_count DESC"
        )
        rows = await cursor.fetchall()
        runbooks = []
        for row in rows:
            runbooks.append({
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "trigger_pattern": row["trigger_pattern"],
                "steps": json.loads(row["steps"]),
                "step_count": len(json.loads(row["steps"])),
                "run_count": row["run_count"],
                "last_run": row["last_run"],
                "created_at": row["created_at"],
            })
    return {"runbooks": runbooks}


@router.post("/")
async def create_runbook(request: CreateRunbookRequest):
    """Create a new runbook from a sequence of tool calls."""
    runbook_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS runbooks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                trigger_pattern TEXT,
                steps TEXT NOT NULL,
                run_count INTEGER DEFAULT 0,
                last_run TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute(
            "INSERT INTO runbooks (id, name, description, trigger_pattern, steps, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (runbook_id, request.name, request.description, request.trigger_pattern,
             json.dumps([s.model_dump() for s in request.steps], ensure_ascii=False), now),
        )
        await db.commit()

    return {"id": runbook_id, "name": request.name, "created_at": now}


@router.get("/{runbook_id}")
async def get_runbook(runbook_id: str):
    """Get a specific runbook with full step details."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM runbooks WHERE id = ?", (runbook_id,))
        row = await cursor.fetchone()
        if not row:
            return {"error": "Runbook not found"}
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "trigger_pattern": row["trigger_pattern"],
            "steps": json.loads(row["steps"]),
            "run_count": row["run_count"],
            "last_run": row["last_run"],
            "created_at": row["created_at"],
        }


@router.delete("/{runbook_id}")
async def delete_runbook(runbook_id: str):
    """Delete a runbook."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute("DELETE FROM runbooks WHERE id = ?", (runbook_id,))
        await db.commit()
    return {"status": "deleted"}


@router.post("/{runbook_id}/run")
async def record_run(runbook_id: str):
    """Record that a runbook was executed (increment counter)."""
    now = datetime.now().isoformat()
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute(
            "UPDATE runbooks SET run_count = run_count + 1, last_run = ? WHERE id = ?",
            (now, runbook_id),
        )
        await db.commit()
    return {"status": "recorded"}
