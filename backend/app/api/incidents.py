"""Incident timeline API endpoints."""

from fastapi import APIRouter, HTTPException, Query

from app.incidents.store import get_incident, get_incident_events, get_incidents

router = APIRouter()


@router.get("/")
async def list_incidents(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List incidents, optionally scoped to a chat session."""
    incidents = await get_incidents(session_id=session_id, limit=limit)
    return {"incidents": incidents}


@router.get("/{incident_id}")
async def get_incident_detail(incident_id: str):
    """Return one incident by id."""
    incident = await get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"incident": incident}


@router.get("/{incident_id}/events")
async def get_incident_timeline(
    incident_id: str,
    limit: int = Query(default=500, ge=1, le=1000),
):
    """Return ordered timeline events for one incident."""
    incident = await get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    events = await get_incident_events(incident_id, limit=limit)
    return {"incident": incident, "events": events}
