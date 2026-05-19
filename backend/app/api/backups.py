"""Backup and rollback API endpoints."""

from fastapi import APIRouter, Query

from app.mcp_tools.backup import backup_manager

router = APIRouter()


@router.get("/")
async def list_backups(filepath: str | None = Query(default=None), limit: int = Query(default=20, ge=1, le=200)):
    """List backup records from the backup manifest."""
    records = backup_manager.get_backups(filepath=filepath, limit=limit)
    return {"backups": records, "count": len(records)}


@router.post("/{backup_id}/rollback")
async def rollback_backup(backup_id: str):
    """Restore a backup record by id."""
    record = next((r for r in backup_manager.get_backups(limit=1000) if r.get("id") == backup_id), None)
    if not record:
        return {"success": False, "error": "Backup not found"}
    ok = backup_manager.rollback(backup_id)
    return {
        "success": ok,
        "backup_id": backup_id,
        "restored_path": record.get("original_path") if ok else None,
        "error": None if ok else "Rollback failed or backup was already restored",
    }
