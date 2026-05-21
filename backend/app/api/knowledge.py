"""Knowledge base API endpoints."""

import aiosqlite
from fastapi import APIRouter, HTTPException

from app.database import get_knowledge_db_path
from app.knowledge.store import KnowledgeSearchError, ensure_knowledge_schema, knowledge_store

router = APIRouter()


@router.get("/")
async def list_knowledge():
    """List all knowledge entries."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_knowledge_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM knowledge_entries ORDER BY success_count DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
        entries = [_serialize_row(row) for row in rows]
    return {"entries": entries}


@router.get("/search")
async def search_knowledge(q: str):
    """Search knowledge base by problem description."""
    try:
        entries = await knowledge_store.search(q, limit=10)
    except KnowledgeSearchError as e:
        raise HTTPException(status_code=500, detail=f"Knowledge search failed: {e}") from e
    return {"entries": entries}


def _serialize_row(row: aiosqlite.Row) -> dict:
    """Serialize a knowledge row, parsing structured JSON fields."""
    import json

    item = dict(row)
    for field in (
        "symptoms",
        "evidence",
        "successful_actions",
        "failed_attempts",
        "applicability_conditions",
        "non_applicability_conditions",
        "source_modalities",
        "multimodal_evidence",
    ):
        if item.get(field):
            try:
                item[field] = json.loads(item[field])
            except Exception:
                item[field] = []
        else:
            item[field] = []
    if item.get("tools_used"):
        try:
            item["tools_used"] = json.loads(item["tools_used"])
        except Exception:
            item["tools_used"] = []
    else:
        item["tools_used"] = []
    item["safe_to_reuse"] = bool(item.get("validation_method") and item.get("applicability_conditions") and not item.get("non_applicability_conditions"))
    return item
