"""Alert webhook API endpoints."""

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.alerts.triage import run_alert_auto_triage


router = APIRouter()


@router.post("/webhook")
async def receive_alert_webhook(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Receive an external alert and run read-only auto-triage."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object")
    result = await run_alert_auto_triage(payload)
    if result["count"] == 0:
        raise HTTPException(status_code=400, detail="Webhook payload did not contain any alert objects")
    return result

