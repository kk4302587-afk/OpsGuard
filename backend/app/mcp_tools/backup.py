"""Backup and rollback utilities for write operations.

Before any destructive/write operation, automatically creates a backup.
Supports rollback to the previous state if something goes wrong.
"""

import os
import shutil
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

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

    def _append_record(self, record: dict) -> dict:
        self._manifest.append(record)
        self._save_manifest()
        return record

    def _new_id(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    def find_backup(self, backup_id: str) -> dict | None:
        """Find a backup record by exact or punctuation-insensitive id."""
        normalized = _normalize_backup_id(backup_id)
        for record in self._manifest:
            record_id = str(record.get("id") or "")
            if record_id == backup_id or _normalize_backup_id(record_id) == normalized:
                return record
        return None

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

        timestamp = self._new_id()
        backup_name = f"{source.name}.{timestamp}.bak"
        backup_path = self._backup_dir / backup_name

        try:
            shutil.copy2(str(source), str(backup_path))

            record = {
                "id": timestamp,
                "original_path": str(source.absolute()),
                "backup_path": str(backup_path.absolute()),
                "operation": operation,
                "rollback_type": "restore_file",
                "timestamp": datetime.now().isoformat(),
                "size": source.stat().st_size,
                "restored": False,
            }

            logger.info(f"Backup created: {filepath} -> {backup_path}")
            return self._append_record(record)

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

        timestamp = self._new_id()
        backup_name = f"{source.name}.{timestamp}.bak"
        backup_path = self._backup_dir / backup_name

        try:
            shutil.copytree(str(source), str(backup_path))

            record = {
                "id": timestamp,
                "original_path": str(source.absolute()),
                "backup_path": str(backup_path.absolute()),
                "operation": operation,
                "rollback_type": "restore_directory",
                "timestamp": datetime.now().isoformat(),
                "is_directory": True,
                "restored": False,
            }

            logger.info(f"Directory backup created: {dirpath} -> {backup_path}")
            return self._append_record(record)

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
        record = self.find_backup(backup_id)
        if not record:
            logger.error(f"Backup record not found: {backup_id}")
            return False

        if record.get("restored"):
            logger.warning(f"Backup already restored: {backup_id}")
            return False

        rollback_type = record.get("rollback_type") or ("restore_directory" if record.get("is_directory") else "restore_file")
        backup_path = Path(record.get("backup_path") or "")
        original_path = Path(record["original_path"])

        if rollback_type in {"restore_file", "restore_directory"} and not backup_path.exists():
            logger.error(f"Backup file missing: {backup_path}")
            return False

        try:
            if rollback_type == "delete_created_path":
                paths = [Path(item) for item in record.get("created_paths", []) if item]
                if not paths:
                    paths = [original_path]
                for path in paths:
                    if path.is_dir():
                        shutil.rmtree(str(path))
                    elif path.exists() or path.is_symlink():
                        path.unlink()
            elif rollback_type == "move_path":
                source_path = Path(record.get("source_path") or (record.get("metadata") or {}).get("source_path") or "")
                destination_path = Path(record.get("destination_path") or (record.get("metadata") or {}).get("destination_path") or "")
                if source_path.exists():
                    if destination_path.exists():
                        raise OSError(f"Rollback destination already exists: {destination_path}")
                    shutil.move(str(source_path), str(destination_path))
            elif rollback_type == "restore_permissions":
                os.chmod(original_path, int(str(record["mode"]), 8))
            elif rollback_type == "restore_owner":
                subprocess.run(
                    ["sudo", "chown", str(record["owner"]), str(original_path)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                )
            elif rollback_type == "restore_service_state":
                service = str(record["service"])
                previous = str(record.get("previous_state") or "")
                action = "start" if previous == "active" else "stop"
                subprocess.run(
                    ["sudo", "systemctl", action, service],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                )
            elif rollback_type == "restore_firewall_rule":
                _restore_firewall_rule(record)
            elif rollback_type == "restore_crontab":
                _restore_crontab(record)
            elif record.get("is_directory"):
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

        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return False

    def create_inverse_record(
        self,
        *,
        rollback_type: str,
        operation: str,
        original_path: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        """Create a rollback record that does not need a copied backup file."""
        timestamp = self._new_id()
        record = {
            "id": timestamp,
            "rollback_type": rollback_type,
            "operation": operation,
            "original_path": str(Path(original_path).absolute()) if original_path else "",
            "backup_path": "",
            "timestamp": datetime.now().isoformat(),
            "restored": False,
        }
        record.update(metadata or {})
        return self._append_record(record)

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
        record = backup_manager.find_backup(backup_id)
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


def _normalize_backup_id(backup_id: str) -> str:
    return "".join(char for char in str(backup_id or "") if char.isalnum())


def _restore_crontab(record: dict[str, Any]) -> None:
    user = str(record.get("user") or "")
    had_crontab = bool(record.get("had_crontab"))
    previous = str(record.get("previous_crontab") or "")
    if had_crontab:
        cmd = ["crontab", "-"]
        if user:
            cmd = ["sudo", "crontab", "-u", user, "-"]
        subprocess.run(cmd, input=previous, capture_output=True, text=True, timeout=10, check=True)
        return

    cmd = ["crontab", "-r"]
    if user:
        cmd = ["sudo", "crontab", "-u", user, "-r"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0 and "no crontab" not in (result.stderr or "").lower():
        raise RuntimeError(result.stderr or result.stdout or "failed to remove crontab")


def _restore_firewall_rule(record: dict[str, Any]) -> None:
    backend = str(record.get("firewall_backend") or "iptables")
    port = int(record.get("port"))
    protocol = str(record.get("protocol") or "tcp")
    before_allowed = bool(record.get("before_allowed"))
    before_blocked = bool(record.get("before_blocked"))

    if backend == "firewall-cmd":
        if before_allowed:
            _run_checked(["sudo", "firewall-cmd", "--permanent", f"--add-port={port}/{protocol}"])
        else:
            _run_checked(["sudo", "firewall-cmd", "--permanent", f"--remove-port={port}/{protocol}"], allow_failure=True)
        _run_checked(["sudo", "firewall-cmd", "--reload"])
        return

    if backend == "ufw":
        if before_allowed:
            _run_checked(["sudo", "ufw", "allow", f"{port}/{protocol}"])
        else:
            _run_checked(["sudo", "ufw", "delete", "allow", f"{port}/{protocol}"], allow_failure=True)
        if before_blocked:
            _run_checked(["sudo", "ufw", "deny", f"{port}/{protocol}"])
        else:
            _run_checked(["sudo", "ufw", "delete", "deny", f"{port}/{protocol}"], allow_failure=True)
        return

    _restore_iptables_rule(port, protocol, "ACCEPT", before_allowed)
    _restore_iptables_rule(port, protocol, "DROP", before_blocked)


def _restore_iptables_rule(port: int, protocol: str, target: str, should_exist: bool) -> None:
    check = ["sudo", "iptables", "-C", "INPUT", "-p", protocol, "--dport", str(port), "-j", target]
    present = subprocess.run(check, capture_output=True, text=True, timeout=10).returncode == 0
    if should_exist and not present:
        _run_checked(["sudo", "iptables", "-A", "INPUT", "-p", protocol, "--dport", str(port), "-j", target])
    elif not should_exist and present:
        _run_checked(["sudo", "iptables", "-D", "INPUT", "-p", protocol, "--dport", str(port), "-j", target])


def _run_checked(command: list[str], *, allow_failure: bool = False) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode != 0 and not allow_failure:
        raise RuntimeError(result.stderr or result.stdout or f"command failed: {' '.join(command)}")
