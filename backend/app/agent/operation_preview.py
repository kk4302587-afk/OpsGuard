"""Approval preview and dry-run artifacts for mutating operations.

Preview generation must be side-effect free. It may read target state and run
tool-native simulation commands, but it must not create backups or mutate the
host before the user approves the real operation.
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.mcp_tools.backup import backup_manager


MAX_TEXT_BYTES = 64 * 1024
MAX_DIFF_LINES = 240
MAX_OUTPUT_CHARS = 6000


def build_operation_preview(tool_name: str, tool_args: dict[str, Any], tool_def: Any | None = None) -> dict[str, Any]:
    """Return a structured preview artifact for an approval request."""
    try:
        if tool_name == "write_file":
            return _preview_write_file(tool_args)
        if tool_name == "create_file":
            return _preview_create_file(tool_args)
        if tool_name == "delete_file":
            return _preview_delete_file(tool_args)
        if tool_name == "rollback_backup":
            return _preview_rollback_backup(tool_args)
        if tool_name in {"install_package", "remove_package"}:
            return _preview_package_operation(tool_name, tool_args)
        if tool_name in {"add_cron_job", "remove_cron_job"}:
            return _preview_cron_operation(tool_name, tool_args)
        if tool_name in {"allow_port", "block_port"}:
            return _preview_firewall_operation(tool_name, tool_args)

        return _base_preview(
            status="unavailable",
            preview_type="impact_only",
            target=_target_for_tool(tool_name, tool_args, tool_def),
            limitations=[f"No concrete preview generator is available for {tool_name}."],
        )
    except Exception as exc:
        return _base_preview(
            status="unavailable",
            preview_type="impact_only",
            target=_target_for_tool(tool_name, tool_args, tool_def),
            limitations=[f"Preview generation failed: {exc}"],
        )


def _preview_write_file(tool_args: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(tool_args.get("filepath") or ""))
    content = str(tool_args.get("content") or "")
    append = bool(tool_args.get("append", False))
    before = _read_text_preview(path)
    proposed = before["text"] + content if append and before["exists"] and before["text"] is not None else content
    preview = _base_preview(
        status="available" if before["text"] is not None or not before["exists"] else "partial",
        preview_type="diff",
        target="file",
        before_summary=_file_summary(path, before),
        after_summary=_content_summary(proposed, prefix="计划文件"),
        diff=_unified_diff(
            before["text"] or "",
            proposed,
            fromfile=str(path) + " 当前",
            tofile=str(path) + " 计划",
        ),
        metadata={
            "path": str(path),
            "append": append,
            "operation": "追加" if append else "覆盖写入",
            "added_content": content if append else "",
            "proposed_content": "" if append else content,
            "content_bytes": len(content.encode("utf-8")),
            "current_bytes": _path_size(path),
            "planned_bytes": len(proposed.encode("utf-8")),
            "current_lines": _count_lines(before["text"] or ""),
            "planned_lines": _count_lines(proposed),
        },
    )
    if before["reason"]:
        preview["limitations"].append(before["reason"])
    if before["truncated"]:
        preview["warnings"].append("Current file preview was truncated before diff generation.")
    return preview


def _preview_create_file(tool_args: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(tool_args.get("filepath") or ""))
    content = str(tool_args.get("content") or "")
    overwrite = bool(tool_args.get("overwrite", False))
    before = _read_text_preview(path)
    status = "available"
    limitations: list[str] = []
    diff = ""
    before_summary = _file_summary(path, before)
    if path.exists() and path.is_dir():
        status = "unavailable"
        limitations.append("Target exists and is a directory.")
    elif path.exists() and not overwrite:
        status = "partial"
        limitations.append("Target exists and overwrite=false; execution would fail unless overwrite is enabled.")
    elif path.exists() and before["text"] is not None:
        diff = _unified_diff(before["text"], content, fromfile=str(path) + " (current)", tofile=str(path) + " (proposed)")
    elif before["reason"]:
        limitations.append(before["reason"])

    return _base_preview(
        status=status,
        preview_type="diff" if diff else "before_after",
        target="file",
        before_summary=before_summary,
        after_summary=_content_summary(content, prefix="计划文件"),
        diff=diff,
        limitations=limitations,
        metadata={
            "path": str(path),
            "exists": path.exists(),
            "overwrite": overwrite,
            "parent_exists": path.parent.exists(),
            "operation": "覆盖创建" if overwrite and path.exists() else "创建文件",
            "proposed_content": content,
            "content_bytes": len(content.encode("utf-8")),
        },
    )


def _preview_delete_file(tool_args: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(tool_args.get("filepath") or ""))
    exists = path.exists()
    metadata = _path_metadata(path)
    can_backup = exists and path.is_file() and not path.is_symlink()
    status = "available" if exists and path.is_file() else "partial"
    limitations = []
    if not exists:
        limitations.append("Target file does not exist; execution would fail.")
    elif path.is_dir():
        limitations.append("Target is a directory; delete_file will refuse it.")
    elif path.is_symlink():
        limitations.append("Target is a symlink; backup safety is limited.")
    return _base_preview(
        status=status,
        preview_type="before_after",
        target="file",
        before_summary=_metadata_summary(metadata),
        after_summary="Target file will be removed if approved.",
        warnings=["A rollback backup can be created before deletion."] if can_backup else [],
        limitations=limitations,
        metadata={
            "path": str(path),
            "exists": exists,
            "backup_capable": can_backup,
            "metadata": metadata,
        },
    )


def _preview_rollback_backup(tool_args: dict[str, Any]) -> dict[str, Any]:
    backup_id = str(tool_args.get("backup_id") or "")
    record = backup_manager.find_backup(backup_id)
    if not record:
        return _base_preview(
            status="unavailable",
            preview_type="restore_preview",
            target="backup",
            limitations=[f"Backup record not found: {backup_id}"],
            metadata={"backup_id": backup_id},
        )

    backup_path = Path(str(record.get("backup_path") or ""))
    original_path = Path(str(record.get("original_path") or ""))
    current_metadata = _path_metadata(original_path)
    backup_metadata = _path_metadata(backup_path)
    limitations = []
    diff = ""
    if not backup_path.exists():
        limitations.append("Backup artifact is missing; rollback would fail.")
    if record.get("restored"):
        limitations.append("Backup has already been restored once.")
    if record.get("is_directory"):
        limitations.append("Directory rollback diff is not available; preview shows metadata only.")
    elif backup_path.exists():
        current = _read_text_preview(original_path)
        backup = _read_text_preview(backup_path)
        if current["text"] is not None and backup["text"] is not None:
            diff = _unified_diff(
                current["text"],
                backup["text"],
                fromfile=str(original_path) + " 当前",
                tofile=str(original_path) + " 回滚后",
            )
            if current["truncated"] or backup["truncated"]:
                limitations.append("Rollback content diff was generated from truncated file previews.")
        else:
            if current["reason"]:
                limitations.append(f"Current target diff unavailable: {current['reason']}")
            if backup["reason"]:
                limitations.append(f"Backup content diff unavailable: {backup['reason']}")
    return _base_preview(
        status="available" if backup_path.exists() and not record.get("restored") else "partial",
        preview_type="restore_preview",
        target="backup",
        before_summary=f"Current target: {_metadata_summary(current_metadata)}",
        after_summary=f"Restore backup {backup_id} to {record.get('original_path')}. Existing target will be overwritten.",
        diff=diff,
        warnings=["Rollback is itself a destructive recovery action and still requires approval."],
        limitations=limitations,
        metadata={
            "backup_id": backup_id,
            "operation": "恢复备份",
            "path": str(original_path),
            "backup_path": str(backup_path),
            "current_bytes": current_metadata.get("size") if current_metadata.get("exists") else None,
            "planned_bytes": backup_metadata.get("size") if backup_metadata.get("exists") else None,
            "content_bytes": backup_metadata.get("size") if backup_metadata.get("exists") else None,
            "current_exists": original_path.exists(),
            "backup_exists": backup_path.exists(),
            "backup_record": record,
            "backup_metadata": backup_metadata,
            "current_target_metadata": current_metadata,
            "will_overwrite": original_path.exists(),
        },
    )


def _preview_package_operation(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
    package = str(tool_args.get("name") or "")
    command = _package_simulation_command(tool_name, package)
    if not command:
        return _base_preview(
            status="unavailable",
            preview_type="command_dry_run",
            target="package",
            limitations=["No supported package manager simulation command was found."],
            metadata={"package": package},
        )

    result = _run_bounded(command, timeout=45)
    return _base_preview(
        status="available" if result["returncode"] in (0, 100) else "partial",
        preview_type="command_dry_run",
        target="package",
        before_summary=f"Package operation simulation for {package}.",
        after_summary="No host package state was changed by this simulation.",
        warnings=[] if result["returncode"] in (0, 100) else ["Package simulation returned a non-zero status."],
        limitations=[],
        metadata={
            "command": command,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "returncode": result["returncode"],
        },
    )


def _preview_cron_operation(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
    user = str(tool_args.get("user") or "")
    existing = _read_crontab(user)
    if existing["returncode"] != 0 and "no crontab" not in existing["stderr"].lower():
        return _base_preview(
            status="unavailable",
            preview_type="command_dry_run",
            target="cron",
            limitations=[existing["stderr"] or "Unable to read current crontab."],
            metadata={"user": user},
        )
    current = existing["stdout"] if existing["returncode"] == 0 else ""
    if tool_name == "add_cron_job":
        schedule = str(tool_args.get("schedule") or "")
        command = str(tool_args.get("command") or "")
        new_line = f"{schedule} {command}".strip()
        proposed = current.rstrip() + ("\n" if current.strip() else "") + new_line + "\n"
    else:
        pattern = str(tool_args.get("pattern") or "")
        proposed = "\n".join(line for line in current.splitlines() if not (pattern in line and line.strip() and not line.startswith("#")))
        proposed = proposed + ("\n" if proposed else "")

    return _base_preview(
        status="available",
        preview_type="diff",
        target="cron",
        before_summary=f"Current crontab lines: {len([line for line in current.splitlines() if line.strip()])}.",
        after_summary=f"Planned crontab lines: {len([line for line in proposed.splitlines() if line.strip()])}.",
        diff=_unified_diff(current, proposed, fromfile="crontab (current)", tofile="crontab (planned)"),
        warnings=["Cron preview is a planned mutation only; crontab was not changed."],
        metadata={"user": user, "native_dry_run": True},
    )


def _preview_firewall_operation(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
    port = tool_args.get("port")
    protocol = str(tool_args.get("protocol") or "tcp")
    action = "allow" if tool_name == "allow_port" else "block"
    planned = _planned_firewall_commands(action, port, protocol)
    return _base_preview(
        status="partial",
        preview_type="command_dry_run",
        target="firewall",
        before_summary="Firewall rule preview generated without applying changes.",
        after_summary=f"Planned {action} rule for {port}/{protocol}.",
        warnings=["Firewall tools do not expose a reliable universal dry-run mode; commands are planned only."],
        metadata={"planned_commands": planned, "port": port, "protocol": protocol},
    )


def _package_simulation_command(tool_name: str, package: str) -> list[str] | None:
    if not package:
        return None
    if shutil.which("apt-get"):
        action = "install" if tool_name == "install_package" else "remove"
        return ["apt-get", "-s", action, package]
    if shutil.which("dnf"):
        action = "install" if tool_name == "install_package" else "remove"
        return ["dnf", action, "--assumeno", package]
    if shutil.which("yum"):
        action = "install" if tool_name == "install_package" else "remove"
        return ["yum", action, "--assumeno", package]
    return None


def _planned_firewall_commands(action: str, port: Any, protocol: str) -> list[str]:
    port_proto = f"{port}/{protocol}"
    if shutil.which("firewall-cmd"):
        op = "--add-port" if action == "allow" else "--remove-port"
        return [f"firewall-cmd --permanent {op}={port_proto}", "firewall-cmd --reload"]
    if shutil.which("ufw"):
        return [f"ufw {action} {port_proto}"]
    chain_action = "-A" if action == "allow" else "-D"
    target = "ACCEPT" if action == "allow" else "DROP"
    return [f"iptables {chain_action} INPUT -p {protocol} --dport {port} -j {target}"]


def _read_crontab(user: str) -> dict[str, Any]:
    cmd = ["crontab", "-l"]
    if user:
        cmd.extend(["-u", user])
    return _run_bounded(cmd, timeout=8)


def _run_bounded(command: list[str], *, timeout: int) -> dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return {
            "returncode": result.returncode,
            "stdout": _truncate_output(result.stdout),
            "stderr": _truncate_output(result.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": _truncate_output(exc.stdout or ""),
            "stderr": f"Timed out after {timeout}s",
        }
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


def _read_text_preview(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "text": "", "truncated": False, "reason": ""}
    if path.is_dir():
        return {"exists": True, "text": None, "truncated": False, "reason": "Target is a directory."}
    if not path.is_file():
        return {"exists": True, "text": None, "truncated": False, "reason": "Target is not a regular file."}
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            raw = handle.read(MAX_TEXT_BYTES)
        if b"\x00" in raw:
            return {"exists": True, "text": None, "truncated": size > MAX_TEXT_BYTES, "reason": "Binary file preview is unsupported."}
        return {
            "exists": True,
            "text": raw.decode("utf-8", errors="replace"),
            "truncated": size > MAX_TEXT_BYTES,
            "reason": "",
        }
    except Exception as exc:
        return {"exists": True, "text": None, "truncated": False, "reason": f"Unable to read target: {exc}"}


def _path_metadata(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    try:
        if not path.exists() and not path.is_symlink():
            return metadata
        stat = path.lstat()
        metadata.update({
            "type": _path_type(path),
            "size": stat.st_size,
            "mode": oct(stat.st_mode & 0o777),
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "is_symlink": path.is_symlink(),
        })
        if path.is_symlink():
            metadata["link_target"] = os.readlink(path)
    except Exception as exc:
        metadata["error"] = str(exc)
    return metadata


def _path_type(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def _file_summary(path: Path, read_state: dict[str, Any]) -> str:
    if not read_state["exists"]:
        return f"{path} 不存在"
    metadata = _path_metadata(path)
    return _metadata_summary(metadata)


def _metadata_summary(metadata: dict[str, Any]) -> str:
    if not metadata.get("exists"):
        return f"{metadata.get('path')} 不存在"
    parts = [
        str(metadata.get("path")),
        f"type={metadata.get('type', 'unknown')}",
        f"size={metadata.get('size', '-')}",
        f"mode={metadata.get('mode', '-')}",
    ]
    if metadata.get("mtime"):
        parts.append(f"mtime={metadata['mtime']}")
    if metadata.get("error"):
        parts.append(f"error={metadata['error']}")
    return "; ".join(parts)


def _content_summary(content: str, *, prefix: str) -> str:
    encoded = content.encode("utf-8")
    lines = _count_lines(content)
    return f"{prefix}: {len(encoded)} bytes, {lines} lines"


def _count_lines(content: str) -> int:
    return content.count("\n") + (1 if content else 0)


def _path_size(path: Path) -> int | None:
    try:
        return path.stat().st_size if path.exists() and path.is_file() else None
    except Exception:
        return None


def _unified_diff(before: str, after: str, *, fromfile: str, tofile: str) -> str:
    diff_lines = list(difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=fromfile,
        tofile=tofile,
        lineterm="",
    ))
    truncated = len(diff_lines) > MAX_DIFF_LINES
    if truncated:
        diff_lines = diff_lines[:MAX_DIFF_LINES] + ["... diff truncated ..."]
    return "\n".join(diff_lines)


def _base_preview(
    *,
    status: str,
    preview_type: str,
    target: str,
    before_summary: str = "",
    after_summary: str = "",
    diff: str = "",
    warnings: list[str] | None = None,
    limitations: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "preview_type": preview_type,
        "target": target,
        "before_summary": before_summary,
        "after_summary": after_summary,
        "diff": diff,
        "warnings": warnings or [],
        "limitations": limitations or [],
        "metadata": _redact_metadata(metadata or {}),
    }


def _target_for_tool(tool_name: str, tool_args: dict[str, Any], tool_def: Any | None) -> str:
    category = str(getattr(tool_def, "category", "") or "")
    if category:
        return category
    if tool_args.get("backup_id"):
        return "backup"
    if any(tool_args.get(key) for key in ("filepath", "dirpath", "path")):
        return "file"
    return tool_name


def _truncate_output(value: Any) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    text = _redact_text(text)
    return text[:MAX_OUTPUT_CHARS] + ("... output truncated ..." if len(text) > MAX_OUTPUT_CHARS else "")


def _redact_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if any(secret in str(key).lower() for secret in ("token", "secret", "password", "api_key")):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_metadata(item)
        return redacted
    if isinstance(value, list):
        return [_redact_metadata(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(text: str) -> str:
    import re

    text = re.sub(r"ghp_[A-Za-z0-9_]+", "[REDACTED_GITHUB_TOKEN]", text)
    text = re.sub(r"github_pat_[A-Za-z0-9_]+", "[REDACTED_GITHUB_TOKEN]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[REDACTED_API_KEY]", text)
    return text
