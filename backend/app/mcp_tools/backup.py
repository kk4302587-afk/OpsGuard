"""Backup and rollback utilities for write operations.

Before any destructive/write operation, automatically creates a backup.
Supports rollback to the previous state if something goes wrong.
"""

import os
import shutil
import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.config import settings
from app.mcp_tools.process_tools import ToolResult


class BackupManager:
    """Manages file backups before write operations."""

    def __init__(self):
        self._backup_dir = Path(settings.execution.backup_dir)
        # Fallback to local directory if configured path doesn't exist
        if not self._backup_dir.parent.exists():
            self._backup_dir = Path("./data/backups")
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._backup_dir / "manifest.json"
        self._manifest: list[dict] = self._load_manifest()

    def _load_manifest(self) -> list[dict]:
        """Load the backup manifest."""
        if self._manifest_path.exists():
            try:
                with open(self._manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_manifest(self):
        """Save the backup manifest."""
        try:
            with open(self._manifest_path, "w", encoding="utf-8") as f:
                json.dump(self._manifest, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"Failed to save backup manifest: {e}")

    def backup_file(self, filepath: str, operation: str = "unknown") -> dict | None:
        """Create a backup of a file before modifying it.

        Args:
            filepath: Path to the file to backup
            operation: Description of the operation (for audit)

        Returns:
            Backup record dict, or None if backup failed
        """
        source = Path(filepath)
        if not source.exists():
            logger.debug(f"No backup needed: {filepath} does not exist (new file)")
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"{source.name}.{timestamp}.bak"
        backup_path = self._backup_dir / backup_name

        try:
            shutil.copy2(str(source), str(backup_path))

            record = {
                "id": timestamp,
                "original_path": str(source.absolute()),
                "backup_path": str(backup_path.absolute()),
                "operation": operation,
                "timestamp": datetime.now().isoformat(),
                "size": source.stat().st_size,
                "restored": False,
            }

            self._manifest.append(record)
            self._save_manifest()

            logger.info(f"Backup created: {filepath} -> {backup_path}")
            return record

        except (IOError, OSError) as e:
            logger.error(f"Backup failed for {filepath}: {e}")
            return None

    def backup_directory(self, dirpath: str, operation: str = "unknown") -> dict | None:
        """Create a backup of an entire directory.

        Args:
            dirpath: Path to the directory to backup
            operation: Description of the operation

        Returns:
            Backup record dict, or None if backup failed
        """
        source = Path(dirpath)
        if not source.exists() or not source.is_dir():
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"{source.name}.{timestamp}.bak"
        backup_path = self._backup_dir / backup_name

        try:
            shutil.copytree(str(source), str(backup_path))

            record = {
                "id": timestamp,
                "original_path": str(source.absolute()),
                "backup_path": str(backup_path.absolute()),
                "operation": operation,
                "timestamp": datetime.now().isoformat(),
                "is_directory": True,
                "restored": False,
            }

            self._manifest.append(record)
            self._save_manifest()

            logger.info(f"Directory backup created: {dirpath} -> {backup_path}")
            return record

        except (IOError, OSError) as e:
            logger.error(f"Directory backup failed for {dirpath}: {e}")
            return None

    def rollback(self, backup_id: str) -> bool:
        """Rollback a file to its backed-up state.

        Args:
            backup_id: The backup record ID to restore

        Returns:
            True if rollback succeeded
        """
        record = next((r for r in self._manifest if r["id"] == backup_id), None)
        if not record:
            logger.error(f"Backup record not found: {backup_id}")
            return False

        if record.get("restored"):
            logger.warning(f"Backup already restored: {backup_id}")
            return False

        backup_path = Path(record["backup_path"])
        original_path = Path(record["original_path"])

        if not backup_path.exists():
            logger.error(f"Backup file missing: {backup_path}")
            return False

        try:
            if record.get("is_directory"):
                if original_path.exists():
                    shutil.rmtree(str(original_path))
                shutil.copytree(str(backup_path), str(original_path))
            else:
                shutil.copy2(str(backup_path), str(original_path))

            record["restored"] = True
            record["restored_at"] = datetime.now().isoformat()
            self._save_manifest()

            logger.info(f"Rollback successful: {original_path}")
            return True

        except (IOError, OSError) as e:
            logger.error(f"Rollback failed: {e}")
            return False

    def get_backups(self, filepath: str = None, limit: int = 20) -> list[dict]:
        """Get backup history, optionally filtered by file path.

        Args:
            filepath: Filter by original file path (optional)
            limit: Maximum records to return
        """
        records = self._manifest
        if filepath:
            records = [r for r in records if r["original_path"] == str(Path(filepath).absolute())]
        return sorted(records, key=lambda r: r["timestamp"], reverse=True)[:limit]

    def cleanup_old_backups(self, max_age_days: int = 30):
        """Remove backups older than max_age_days."""
        cutoff = datetime.now().timestamp() - (max_age_days * 86400)
        to_remove = []

        for record in self._manifest:
            try:
                record_time = datetime.fromisoformat(record["timestamp"]).timestamp()
                if record_time < cutoff:
                    backup_path = Path(record["backup_path"])
                    if backup_path.exists():
                        if backup_path.is_dir():
                            shutil.rmtree(str(backup_path))
                        else:
                            backup_path.unlink()
                    to_remove.append(record)
            except (ValueError, OSError):
                continue

        for record in to_remove:
            self._manifest.remove(record)

        if to_remove:
            self._save_manifest()
            logger.info(f"Cleaned up {len(to_remove)} old backups")


# Global backup manager instance
backup_manager = BackupManager()


def list_backups(filepath: str = "", limit: int = 20) -> ToolResult:
    """List real backup records from the backup manifest."""
    try:
        records = backup_manager.get_backups(filepath=filepath or None, limit=limit)
        return ToolResult(success=True, data={"backups": records, "count": len(records)})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def rollback_backup(backup_id: str) -> ToolResult:
    """Restore a backup by id."""
    try:
        record = next((r for r in backup_manager.get_backups(limit=1000) if r.get("id") == backup_id), None)
        if not record:
            return ToolResult(success=False, data="", error=f"Backup not found: {backup_id}")
        ok = backup_manager.rollback(backup_id)
        if not ok:
            return ToolResult(success=False, data="", error=f"Rollback failed or already restored: {backup_id}")
        return ToolResult(
            success=True,
            data={
                "backup_id": backup_id,
                "restored_path": record.get("original_path"),
                "strategy": "backup",
            },
        )
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
