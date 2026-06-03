"""Knowledge base API endpoints."""

import aiosqlite
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import get_knowledge_db_path
from app.knowledge.store import KnowledgeSearchError, ensure_knowledge_schema, knowledge_store

router = APIRouter()


class KnowledgeReviewUpdate(BaseModel):
    review_status: str = Field(pattern="^(draft|reviewed|deprecated)$")
    owner: str | None = None


@router.get("/")
async def list_knowledge(include_deprecated: bool = False):
    """List all knowledge entries."""
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_knowledge_schema(db)
        db.row_factory = aiosqlite.Row
        where = "" if include_deprecated else "WHERE COALESCE(review_status, 'draft') != 'deprecated' AND deprecated_at IS NULL"
        cursor = await db.execute(
            f"SELECT * FROM knowledge_entries {where} ORDER BY success_count DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
        entries = [_serialize_row(row) for row in rows]
    return {"entries": entries}


@router.get("/search")
async def search_knowledge(
    q: str,
    service: str | None = None,
    host: str | None = None,
    path: str | None = None,
    port: str | None = None,
    incident_type: str | None = None,
    source_modality: str | None = None,
    confidence: list[str] | None = Query(default=None),
    min_success_count: int | None = Query(default=None, ge=0),
    max_age_days: int | None = Query(default=None, ge=1),
    review_status: list[str] | None = Query(default=None),
    include_deprecated: bool = False,
    limit: int = Query(default=10, ge=1, le=50),
):
    """Search knowledge base by problem description."""
    confidence = _coerce_query_list(confidence)
    review_status = _coerce_query_list(review_status)
    min_success_count = _coerce_query_int(min_success_count)
    max_age_days = _coerce_query_int(max_age_days)
    filters = {
        "service": service,
        "host": host,
        "path": path,
        "port": port,
        "incident_type": incident_type,
        "source_modality": source_modality,
        "confidence": confidence,
        "min_success_count": min_success_count,
        "max_age_days": max_age_days,
        "review_status": review_status,
        "include_deprecated": include_deprecated or None,
    }
    filters = {key: value for key, value in filters.items() if value not in (None, "", [])}
    try:
        entries = await knowledge_store.search(q, limit=limit, filters=filters or None)
    except KnowledgeSearchError as e:
        raise HTTPException(status_code=500, detail=f"Knowledge search failed: {e}") from e
    return {"entries": entries, "filters": filters}


def _coerce_query_list(value) -> list[str] | None:
    if value in (None, "", []):
        return None
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if isinstance(value, tuple):
        return [str(item) for item in value if item not in (None, "")]
    # Direct unit-test calls can see FastAPI's Query object default instead of
    # the request-parsed value. Treat non-value sentinels as absent filters.
    if value.__class__.__module__.startswith("fastapi"):
        return None
    return [str(value)]


def _coerce_query_int(value) -> int | None:
    if value in (None, ""):
        return None
    if value.__class__.__module__.startswith("fastapi"):
        return None
    return int(value)


@router.patch("/{entry_id}/review")
async def update_knowledge_review(entry_id: int, payload: KnowledgeReviewUpdate):
    """Update human review status for one knowledge entry."""
    try:
        entry = await knowledge_store.set_review_status(
            entry_id,
            review_status=payload.review_status,
            owner=payload.owner,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"entry": entry}


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
        "entities",
        "evidence_refs",
        "tool_call_ids",
        "trace_event_ids",
        "evidence_summaries",
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
    item["has_write_action"] = bool(item.get("has_write_action"))
    item["write_approved"] = bool(item.get("write_approved"))
    item["structured_final_valid"] = bool(item.get("structured_final_valid")) if item.get("structured_final_valid") is not None else None
    if not item.get("validation_status"):
        item["validation_status"] = "validated" if item.get("validation_method") else "missing"
    if not item.get("confidence"):
        item["confidence"] = "low" if item["validation_status"] == "missing" else "medium"
    if not item.get("review_status"):
        item["review_status"] = "draft"
    if not item.get("staleness_status"):
        item["staleness_status"] = "unknown"
    return item
