"""Rollback point preparation for mutating tool calls."""

from __future__ import annotations

import os
import pwd
import grp
import subprocess
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from app.mcp_tools.backup import backup_manager


def prepare_rollback_point(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    operation_prefix: str = "",
) -> dict | None:
    """Create a concrete rollback record before a mutating tool executes.

    Returns a manifest record that can later be restored via rollback_backup.
    The function is best-effort: unsupported or already-unsafe states return
    None rather than claiming rollback capability.
    """
    operation = f"{operation_prefix}:{tool_name}" if operation_prefix else tool_name
    try:
        if tool_name == "write_file":
            path = _path_arg(tool_args, "filepath")
            if not path:
                return None
            if path.exists() and path.is_file():
                return backup_manager.backup_file(str(path), operation=operation)
            if path.exists():
                return None
            if path.parent.exists():
                return backup_manager.create_inverse_record(
                    rollback_type="delete_created_path",
                    operation=operation,
                    original_path=str(path),
                    metadata={"path_type": "file", "reason": "write created a file that did not exist before execution"},
                )
            return None

        if tool_name == "create_file":
            path = _path_arg(tool_args, "filepath")
            if not path:
                return None
            if path.exists():
                return backup_manager.backup_file(str(path), operation=operation)
            return backup_manager.create_inverse_record(
                rollback_type="delete_created_path",
                operation=operation,
                original_path=str(path),
                metadata={"path_type": "file", "reason": "created file did not exist before execution"},
            )

        if tool_name == "create_directory":
            path = _path_arg(tool_args, "dirpath")
            if not path or path.exists():
                return None
            created_paths = _missing_directory_chain(path, bool(tool_args.get("parents", True)))
            return backup_manager.create_inverse_record(
                rollback_type="delete_created_path",
                operation=operation,
                original_path=str(path),
                metadata={
                    "path_type": "directory",
                    "created_paths": [str(item) for item in created_paths],
                    "reason": "created directory did not exist before execution",
                },
            )

        if tool_name == "delete_file":
            return _backup_existing_file(tool_args.get("filepath"), operation)

        if tool_name == "delete_directory":
            path = _path_arg(tool_args, "dirpath")
            if path and path.exists() and path.is_dir():
                return backup_manager.backup_directory(str(path), operation=operation)
            return None

        if tool_name == "move_file":
            source = _path_arg(tool_args, "source")
            destination = _path_arg(tool_args, "destination")
            if not source or not destination or not source.exists() or destination.exists():
                return None
            return backup_manager.create_inverse_record(
                rollback_type="move_path",
                operation=operation,
                original_path=str(source),
                metadata={
                    "source_path": str(destination.absolute()),
                    "destination_path": str(source.absolute()),
                    "reason": "move can be reverted if destination remains available",
                },
            )

        if tool_name == "copy_file":
            destination = _path_arg(tool_args, "destination")
            if not destination or destination.exists():
                return None
            return backup_manager.create_inverse_record(
                rollback_type="delete_created_path",
                operation=operation,
                original_path=str(destination),
                metadata={"reason": "copy destination did not exist before execution"},
            )

        if tool_name == "change_permissions":
            path = _path_arg(tool_args, "filepath")
            if not path or not path.exists():
                return None
            mode = oct(path.stat().st_mode & 0o777)[2:]
            return backup_manager.create_inverse_record(
                rollback_type="restore_permissions",
                operation=operation,
                original_path=str(path),
                metadata={"mode": mode},
            )

        if tool_name == "change_owner":
            path = _path_arg(tool_args, "filepath")
            if not path or not path.exists():
                return None
            stat = path.stat()
            owner = _owner_text(stat.st_uid, stat.st_gid)
            return backup_manager.create_inverse_record(
                rollback_type="restore_owner",
                operation=operation,
                original_path=str(path),
                metadata={"owner": owner},
            )

        if tool_name in {"restart_service", "start_service", "stop_service"}:
            service = str(tool_args.get("service") or "")
            if not service:
                return None
            previous_state = _service_state(service)
            return backup_manager.create_inverse_record(
                rollback_type="restore_service_state",
                operation=operation,
                original_path=service,
                metadata={"service": service, "previous_state": previous_state},
            )

        if tool_name in {"allow_port", "block_port"}:
            snapshot = _firewall_snapshot(tool_name, tool_args)
            if not snapshot:
                return None
            return backup_manager.create_inverse_record(
                rollback_type="restore_firewall_rule",
                operation=operation,
                original_path=f"{snapshot['port']}/{snapshot['protocol']}",
                metadata=snapshot,
            )

        if tool_name in {"add_cron_job", "remove_cron_job"}:
            snapshot = _crontab_snapshot(tool_args)
            if not snapshot:
                return None
            return backup_manager.create_inverse_record(
                rollback_type="restore_crontab",
                operation=operation,
                original_path=snapshot.get("user") or "current-user crontab",
                metadata=snapshot,
            )

    except Exception as exc:
        logger.warning(f"Rollback point preparation failed for {tool_name}: {exc}")
        return None
    return None


def effective_rollback_capability(tool_name: str, tool_args: dict[str, Any], tool_def: Any) -> tuple[bool, str]:
    """Return rollback capability that can be truthfully claimed before approval."""
    if not tool_def or not getattr(tool_def, "supports_rollback", False):
        return False, "none"

    if tool_name == "rollback_backup":
        return False, "manual"

    if tool_name in {"restart_service", "start_service", "stop_service"}:
        return (bool(tool_args.get("service")), "service_state")

    if tool_name in {"allow_port", "block_port"}:
        return (_can_snapshot_firewall(tool_args), "snapshot_restore") if _can_snapshot_firewall(tool_args) else (False, "none")

    if tool_name in {"add_cron_job", "remove_cron_job"}:
        return (_can_snapshot_crontab(tool_args), "snapshot_restore") if _can_snapshot_crontab(tool_args) else (False, "none")

    if tool_name in {"create_file", "write_file"}:
        path = _path_arg(tool_args, "filepath")
        if not path:
            return False, "none"
        if path.exists() and path.is_file():
            if tool_name == "create_file" and not tool_args.get("overwrite"):
                return False, "none"
            return True, "backup"
        if path.exists():
            return False, "none"
        return (path.parent.exists(), "inverse_action") if path.parent.exists() else (False, "none")

    if tool_name == "create_directory":
        path = _path_arg(tool_args, "dirpath")
        if not path or path.exists():
            return False, "none"
        parent_ok = bool(tool_args.get("parents", True)) or path.parent.exists()
        return (parent_ok, "inverse_action") if parent_ok else (False, "none")

    if tool_name == "delete_file":
        path = _path_arg(tool_args, "filepath")
        return (True, "backup") if path and path.exists() and path.is_file() else (False, "none")

    if tool_name == "delete_directory":
        path = _path_arg(tool_args, "dirpath")
        return (True, "backup") if path and path.exists() and path.is_dir() else (False, "none")

    if tool_name == "move_file":
        source = _path_arg(tool_args, "source")
        destination = _path_arg(tool_args, "destination")
        if source and destination and source.exists() and not destination.exists():
            return True, "inverse_action"
        return False, "none"

    if tool_name == "copy_file":
        source = _path_arg(tool_args, "source")
        destination = _path_arg(tool_args, "destination")
        if source and destination and source.exists() and not destination.exists():
            return True, "inverse_action"
        return False, "none"

    if tool_name in {"change_permissions", "change_owner"}:
        path = _path_arg(tool_args, "filepath")
        return (True, "inverse_action") if path and path.exists() else (False, "none")

    return False, "none"


def rollback_summary(record: dict | None) -> str:
    if not record:
        return "未创建可执行回滚点"
    labels = {
        "restore_file": "恢复文件备份",
        "restore_directory": "恢复目录备份",
        "delete_created_path": "删除本次新建目标",
        "move_path": "反向移动路径",
        "restore_permissions": "恢复原权限",
        "restore_owner": "恢复原属主",
        "restore_service_state": "恢复原服务状态",
        "restore_firewall_rule": "恢复防火墙规则状态",
        "restore_crontab": "恢复定时任务快照",
    }
    kind = labels.get(str(record.get("rollback_type") or ""), str(record.get("rollback_type") or "备份回滚"))
    return f"{kind}，回滚 ID：{record.get('id')}"


def _backup_existing_file(value: Any, operation: str) -> dict | None:
    path = Path(str(value or ""))
    if path.exists() and path.is_file():
        return backup_manager.backup_file(str(path), operation=operation)
    return None


def _path_arg(tool_args: dict[str, Any], key: str) -> Path | None:
    value = tool_args.get(key)
    if not isinstance(value, str) or not value:
        return None
    return Path(value)


def _missing_directory_chain(path: Path, parents: bool) -> list[Path]:
    if not parents:
        return [path]
    missing: list[Path] = []
    current = path
    while current and not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    return missing


def _owner_text(uid: int, gid: int) -> str:
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = str(uid)
    try:
        group = grp.getgrgid(gid).gr_name
    except KeyError:
        group = str(gid)
    return f"{user}:{group}"


def _service_state(service: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (result.stdout.strip() or result.stderr.strip() or "unknown").strip()
    except Exception as exc:
        return f"unknown({exc})"


def _can_snapshot_crontab(tool_args: dict[str, Any]) -> bool:
    if not shutil.which("crontab"):
        return False
    if not tool_args.get("schedule") and not tool_args.get("pattern"):
        return False
    return True


def _crontab_snapshot(tool_args: dict[str, Any]) -> dict[str, Any] | None:
    if not _can_snapshot_crontab(tool_args):
        return None
    user = str(tool_args.get("user") or "")
    cmd = ["crontab", "-l"]
    if user:
        cmd.extend(["-u", user])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    had_crontab = result.returncode == 0
    no_crontab = "no crontab" in (result.stderr or "").lower()
    if result.returncode != 0 and not no_crontab:
        return None
    return {
        "user": user,
        "had_crontab": had_crontab,
        "previous_crontab": result.stdout if had_crontab else "",
        "schedule": str(tool_args.get("schedule") or ""),
        "command": str(tool_args.get("command") or ""),
        "pattern": str(tool_args.get("pattern") or ""),
    }


def _can_snapshot_firewall(tool_args: dict[str, Any]) -> bool:
    port = tool_args.get("port")
    protocol = str(tool_args.get("protocol") or "tcp")
    if not isinstance(port, int) or port < 1 or port > 65535:
        return False
    if protocol not in {"tcp", "udp"}:
        return False
    return bool(shutil.which("firewall-cmd") or shutil.which("ufw") or shutil.which("iptables"))


def _firewall_snapshot(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any] | None:
    if not _can_snapshot_firewall(tool_args):
        return None
    port = int(tool_args.get("port"))
    protocol = str(tool_args.get("protocol") or "tcp")
    backend = _detect_firewall_backend()
    allowed = _firewall_rule_present(backend, port, protocol, "allow")
    blocked = _firewall_rule_present(backend, port, protocol, "block")
    return {
        "firewall_backend": backend,
        "port": port,
        "protocol": protocol,
        "tool_name": tool_name,
        "before_allowed": allowed,
        "before_blocked": blocked,
    }


def _detect_firewall_backend() -> str:
    if shutil.which("firewall-cmd"):
        return "firewall-cmd"
    if shutil.which("ufw"):
        return "ufw"
    return "iptables"


def _firewall_rule_present(backend: str, port: int, protocol: str, rule: str) -> bool:
    try:
        if backend == "firewall-cmd":
            if rule != "allow":
                return False
            result = subprocess.run(
                ["sudo", "firewall-cmd", "--list-ports"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            return result.returncode == 0 and f"{port}/{protocol}" in result.stdout.split()
        if backend == "ufw":
            result = subprocess.run(["sudo", "ufw", "status"], capture_output=True, text=True, timeout=8)
            if result.returncode != 0:
                return False
            needle = str(port)
            marker = "ALLOW" if rule == "allow" else "DENY"
            return any(needle in line and protocol in line and marker in line.upper() for line in result.stdout.splitlines())

        target = "ACCEPT" if rule == "allow" else "DROP"
        result = subprocess.run(
            ["sudo", "iptables", "-C", "INPUT", "-p", protocol, "--dport", str(port), "-j", target],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return result.returncode == 0
    except Exception:
        return False
