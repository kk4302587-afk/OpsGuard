"""Knowledge base API endpoints."""

import aiosqlite
from fastapi import APIRouter

from app.database import get_knowledge_db_path

router = APIRouter()


@router.get("/")
async def list_knowledge():
    """List all knowledge entries."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM knowledge_entries ORDER BY success_count DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
        entries = [dict(row) for row in rows]
    return {"entries": entries}


@router.get("/search")
async def search_knowledge(q: str):
    """Search knowledge base by problem description."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, problem_signature, diagnosis_path, solution, tools_used, success_count "
            "FROM knowledge_entries WHERE problem_signature LIKE ? ORDER BY success_count DESC LIMIT 10",
            (f"%{q}%",),
        )
        rows = await cursor.fetchall()
        entries = [dict(row) for row in rows]
    return {"entries": entries}
