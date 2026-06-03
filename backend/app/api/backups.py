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
    """Return rollback instructions.

    Rollback mutates files, so AI-SRE 7.6 requires it to go through the normal
    Agent approval/audit path via the destructive `rollback_backup` tool.
    """
    record = next((r for r in backup_manager.get_backups(limit=1000) if r.get("id") == backup_id), None)
    if not record:
        return {"success": False, "error": "Backup not found"}
    return {
        "success": False,
        "requires_approval": True,
        "backup_id": backup_id,
        "restored_path": None,
        "target_path": record.get("original_path"),
        "tool_name": "rollback_backup",
        "tool_args": {"backup_id": backup_id},
        "error": "Rollback requires Agent approval and audit. Execute rollback_backup through the chat approval flow.",
    }
