"""Read-only recent change collectors for RCA.

The collectors are deliberately best-effort. Missing commands, missing log
files, or permission issues are returned as source statuses rather than being
reported as "no changes".
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.mcp_tools.backup import backup_manager
from app.mcp_tools.process_tools import ToolResult, command_error


_CONFIG_PATHS = [
    "/etc/nginx/nginx.conf",
    "/etc/apache2/apache2.conf",
    "/etc/httpd/conf/httpd.conf",
    "/etc/ssh/sshd_config",
    "/etc/fstab",
    "/etc/hosts",
    "/etc/resolv.conf",
    "/etc/crontab",
]

_CONFIG_DIRS = [
    "/etc/nginx/conf.d",
    "/etc/nginx/sites-enabled",
    "/etc/systemd/system",
    "/etc/firewalld/zones",
    "/etc/ufw",
    "/etc/cron.d",
    "/etc/cron.daily",
    "/etc/cron.hourly",
    "/etc/cron.weekly",
    "/var/spool/cron",
    "/var/spool/cron/crontabs",
]

_PACKAGE_HISTORY_FILES = [
    "/var/log/apt/history.log",
    "/var/log/dpkg.log",
    "/var/log/yum.log",
    "/var/log/dnf.log",
]

_FIREWALL_FILES = [
    "/var/log/ufw.log",
    "/var/log/firewalld",
    "/etc/ufw/user.rules",
    "/etc/ufw/user6.rules",
]


def get_recent_changes(window_hours: int = 24, limit: int = 30) -> ToolResult:
    """Collect recent local system changes for root-cause analysis.

    Args:
        window_hours: Lookback window in hours.
        limit: Maximum change records to return.
    """
    try:
        safe_window = max(1, min(int(window_hours or 24), 168))
        safe_limit = max(1, min(int(limit or 30), 100))
        since = datetime.now() - timedelta(hours=safe_window)

        changes: list[dict[str, Any]] = []
        source_status: dict[str, dict[str, Any]] = {}

        _collect_service_changes(changes, source_status, since, safe_limit)
        _collect_config_changes(changes, source_status, since)
        _collect_package_changes(changes, source_status, since, safe_limit)
        _collect_cron_changes(changes, source_status, since)
        _collect_firewall_changes(changes, source_status, since, safe_limit)
        _collect_backup_changes(changes, source_status, since, safe_limit)

        changes = sorted(
            changes,
            key=lambda item: item.get("timestamp") or "",
            reverse=True,
        )[:safe_limit]

        return ToolResult(
            success=True,
            data={
                "window_hours": safe_window,
                "changes": changes,
                "source_status": source_status,
                "summary": _summarize_changes(changes, source_status, safe_window),
            },
        )
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def _collect_service_changes(
    changes: list[dict[str, Any]],
    source_status: dict[str, dict[str, Any]],
    since: datetime,
    limit: int,
) -> None:
    source = "systemd_journal"
    if not shutil.which("journalctl"):
        source_status[source] = {"status": "unavailable", "reason": "journalctl not found"}
        return

    result = _run_command(
        [
            "journalctl",
            "--since",
            since.isoformat(timespec="seconds"),
            "--no-pager",
            "-n",
            str(max(limit, 20)),
        ],
        timeout=12,
    )
    if result.returncode != 0:
        source_status[source] = {"status": "failed", "reason": command_error(result)}
        return

    patterns = re.compile(r"\b(started|stopped|restarted|reloaded|failed|reload|restart)\b", re.IGNORECASE)
    found = 0
    for line in result.stdout.splitlines():
        if not patterns.search(line):
            continue
        changes.append(
            _change(
                source=source,
                change_type="service_event",
                target=_extract_systemd_target(line),
                timestamp=_journal_timestamp(line),
                detail=line[-500:],
                confidence="medium",
            )
        )
        found += 1
    source_status[source] = {"status": "ok", "observed": found}


def _collect_config_changes(
    changes: list[dict[str, Any]],
    source_status: dict[str, dict[str, Any]],
    since: datetime,
) -> None:
    source = "config_mtime"
    checked = 0
    found = 0

    for filepath in _CONFIG_PATHS:
        path = Path(filepath)
        checked += 1
        if _add_path_mtime_change(changes, source, path, since, "config_file_modified"):
            found += 1

    for dirpath in _CONFIG_DIRS:
        root = Path(dirpath)
        if not root.exists():
            continue
        try:
            for path in _iter_limited_files(root, max_files=40):
                checked += 1
                if _add_path_mtime_change(changes, source, path, since, "config_file_modified"):
                    found += 1
        except OSError as e:
            source_status.setdefault(source, {"status": "partial", "errors": []})
            source_status[source].setdefault("errors", []).append(f"{dirpath}: {e}")

    status = source_status.get(source, {"status": "ok"})
    status.update({"checked": checked, "observed": found})
    source_status[source] = status


def _collect_package_changes(
    changes: list[dict[str, Any]],
    source_status: dict[str, dict[str, Any]],
    since: datetime,
    limit: int,
) -> None:
    source = "package_history"
    found = 0
    available = 0
    package_pattern = re.compile(r"\b(install|installed|upgrade|upgraded|remove|removed|erase|erased)\b", re.IGNORECASE)

    for filepath in _PACKAGE_HISTORY_FILES:
        path = Path(filepath)
        if not path.exists():
            continue
        available += 1
        try:
            for line in _tail_lines(path, max_lines=300):
                if not package_pattern.search(line):
                    continue
                timestamp = _parse_log_timestamp(line) or _path_timestamp(path)
                if timestamp and _timestamp_before(timestamp, since):
                    continue
                changes.append(
                    _change(
                        source=source,
                        change_type="package_event",
                        target=path.name,
                        timestamp=timestamp or _path_timestamp(path),
                        detail=line[-500:],
                        confidence="medium",
                    )
                )
                found += 1
                if found >= limit:
                    break
        except OSError as e:
            source_status.setdefault(source, {"status": "partial", "errors": []})
            source_status[source].setdefault("errors", []).append(f"{filepath}: {e}")

    if available == 0:
        source_status[source] = {"status": "unavailable", "reason": "no supported package history logs found"}
    else:
        status = source_status.get(source, {"status": "ok"})
        status.update({"observed": found, "files": available})
        source_status[source] = status


def _collect_cron_changes(
    changes: list[dict[str, Any]],
    source_status: dict[str, dict[str, Any]],
    since: datetime,
) -> None:
    source = "cron_mtime"
    found = 0
    checked = 0
    for path_text in ["/etc/crontab", "/etc/cron.d", "/var/spool/cron", "/var/spool/cron/crontabs"]:
        path = Path(path_text)
        if not path.exists():
            continue
        try:
            paths = [path] if path.is_file() else list(_iter_limited_files(path, max_files=50))
            for item in paths:
                checked += 1
                if _add_path_mtime_change(changes, source, item, since, "cron_modified"):
                    found += 1
        except OSError as e:
            source_status.setdefault(source, {"status": "partial", "errors": []})
            source_status[source].setdefault("errors", []).append(f"{path_text}: {e}")

    source_status[source] = {**source_status.get(source, {"status": "ok"}), "checked": checked, "observed": found}


def _collect_firewall_changes(
    changes: list[dict[str, Any]],
    source_status: dict[str, dict[str, Any]],
    since: datetime,
    limit: int,
) -> None:
    source = "firewall_history"
    found = 0
    available = 0

    for filepath in _FIREWALL_FILES:
        path = Path(filepath)
        if not path.exists():
            continue
        available += 1
        if path.is_file():
            if _add_path_mtime_change(changes, source, path, since, "firewall_file_modified"):
                found += 1
            try:
                for line in _tail_lines(path, max_lines=200):
                    if "ALLOW" not in line.upper() and "BLOCK" not in line.upper() and "DENY" not in line.upper():
                        continue
                    timestamp = _parse_log_timestamp(line) or _path_timestamp(path)
                    if timestamp and _timestamp_before(timestamp, since):
                        continue
                    changes.append(
                        _change(
                            source=source,
                            change_type="firewall_event",
                            target=path.name,
                            timestamp=timestamp or _path_timestamp(path),
                            detail=line[-500:],
                            confidence="medium",
                        )
                    )
                    found += 1
                    if found >= limit:
                        break
            except OSError as e:
                source_status.setdefault(source, {"status": "partial", "errors": []})
                source_status[source].setdefault("errors", []).append(f"{filepath}: {e}")

    if available == 0:
        source_status[source] = {"status": "unavailable", "reason": "no supported firewall files found"}
    else:
        source_status[source] = {**source_status.get(source, {"status": "ok"}), "observed": found, "files": available}


def _collect_backup_changes(
    changes: list[dict[str, Any]],
    source_status: dict[str, dict[str, Any]],
    since: datetime,
    limit: int,
) -> None:
    source = "opsguard_backups"
    try:
        records = backup_manager.get_backups(limit=limit)
    except Exception as e:
        source_status[source] = {"status": "failed", "reason": str(e)}
        return

    found = 0
    for record in records:
        timestamp = record.get("timestamp")
        if timestamp and _timestamp_before(timestamp, since):
            continue
        changes.append(
            _change(
                source=source,
                change_type="opsguard_backup",
                target=record.get("original_path") or "unknown",
                timestamp=timestamp,
                detail={
                    "backup_id": record.get("id"),
                    "operation": record.get("operation"),
                    "restored": record.get("restored", False),
                },
                confidence="high",
            )
        )
        found += 1
    source_status[source] = {"status": "ok", "observed": found}


def _add_path_mtime_change(
    changes: list[dict[str, Any]],
    source: str,
    path: Path,
    since: datetime,
    change_type: str,
) -> bool:
    try:
        if not path.exists() or not path.is_file():
            return False
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime)
        if mtime < since:
            return False
        changes.append(
            _change(
                source=source,
                change_type=change_type,
                target=str(path),
                timestamp=mtime.isoformat(),
                detail={"size": stat.st_size, "sha256": _safe_sha256(path)},
                confidence="high",
            )
        )
        return True
    except OSError:
        return False


def _run_command(args: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    """Run a read-only command with captured output."""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _iter_limited_files(root: Path, *, max_files: int) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file():
            files.append(path)
            if len(files) >= max_files:
                break
    return files


def _tail_lines(path: Path, *, max_lines: int) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.splitlines()[-max_lines:]


def _safe_sha256(path: Path) -> str | None:
    try:
        if path.stat().st_size > 5 * 1024 * 1024:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _change(
    *,
    source: str,
    change_type: str,
    target: str,
    timestamp: str | None,
    detail: Any,
    confidence: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "change_type": change_type,
        "target": target,
        "timestamp": timestamp,
        "detail": detail,
        "confidence": confidence,
    }


def _summarize_changes(changes: list[dict[str, Any]], source_status: dict[str, dict[str, Any]], window_hours: int) -> str:
    unavailable = [
        name for name, status in source_status.items()
        if status.get("status") in {"unavailable", "failed", "partial"}
    ]
    if changes:
        by_type: dict[str, int] = {}
        for change in changes:
            change_type = str(change.get("change_type") or "unknown")
            by_type[change_type] = by_type.get(change_type, 0) + 1
        parts = ", ".join(f"{name}: {count}" for name, count in sorted(by_type.items()))
        suffix = f"; limited sources: {', '.join(unavailable)}" if unavailable else ""
        return f"Found {len(changes)} recent changes in the last {window_hours}h ({parts}){suffix}"
    suffix = f"; unavailable/partial sources: {', '.join(unavailable)}" if unavailable else ""
    return f"No recent changes found in inspected sources for the last {window_hours}h{suffix}"


def _extract_systemd_target(line: str) -> str:
    match = re.search(r"([\w@.+-]+\.service)", line)
    return match.group(1) if match else "systemd"


def _journal_timestamp(line: str) -> str | None:
    parts = line.split()
    if len(parts) < 3:
        return None
    candidate = " ".join(parts[:3])
    try:
        parsed = datetime.strptime(f"{datetime.now().year} {candidate}", "%Y %b %d %H:%M:%S")
        return parsed.isoformat()
    except ValueError:
        return None


def _parse_log_timestamp(line: str) -> str | None:
    for pattern in (
        r"(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})",
        r"(?P<ts>\d{4}-\d{2}-\d{2})",
    ):
        match = re.search(pattern, line)
        if not match:
            continue
        value = match.group("ts").replace(" ", "T")
        try:
            return datetime.fromisoformat(value).isoformat()
        except ValueError:
            continue
    return None


def _path_timestamp(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return None


def _timestamp_before(timestamp: str, since: datetime) -> bool:
    try:
        normalized = timestamp.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed < since
    except ValueError:
        return False


def compact_recent_changes(data: dict[str, Any], *, max_changes: int = 8) -> str:
    """Return a compact prompt/trace representation of recent changes."""
    changes = data.get("changes") or []
    if not isinstance(changes, list):
        changes = []
    lines = [str(data.get("summary") or "Recent change check completed")]
    for change in changes[:max_changes]:
        lines.append(
            "- "
            f"{change.get('timestamp') or 'unknown time'} "
            f"[{change.get('source')}/{change.get('change_type')}] "
            f"{change.get('target')}: {json.dumps(change.get('detail'), ensure_ascii=False, default=str)[:220]}"
        )
    source_status = data.get("source_status")
    if source_status:
        lines.append(f"source_status: {json.dumps(source_status, ensure_ascii=False, default=str)[:500]}")
    return "\n".join(lines)
