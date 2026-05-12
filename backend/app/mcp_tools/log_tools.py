"""Log inspection MCP tools.

Atomic tools for system and service log analysis.
"""

import subprocess
from app.mcp_tools.process_tools import ToolResult


def get_journal_logs(
    unit: str | None = None,
    since: str = "1h ago",
    priority: str | None = None,
    lines: int = 50,
) -> ToolResult:
    """Get systemd journal logs with filters.

    Args:
        unit: Service unit name (e.g., "nginx", "sshd")
        since: Time range (e.g., "1h ago", "today", "2024-01-01")
        priority: Minimum priority (emerg, alert, crit, err, warning, notice, info, debug)
        lines: Maximum number of lines to return
    """
    lines = min(lines, 500)  # Safety limit

    try:
        cmd = ["journalctl", "--no-pager", "-n", str(lines), "--since", since]

        if unit:
            cmd.extend(["-u", unit])
        if priority:
            cmd.extend(["-p", priority])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_recent_errors(lines: int = 30) -> ToolResult:
    """Get recent error-level and above log entries.

    Args:
        lines: Maximum number of lines
    """
    lines = min(lines, 200)
    try:
        cmd = ["journalctl", "--no-pager", "-p", "err", "-n", str(lines), "--since", "24h ago"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def tail_log_file(filepath: str, lines: int = 50) -> ToolResult:
    """Read the last N lines of a log file.

    Args:
        filepath: Path to the log file
        lines: Number of lines to read from the end
    """
    # Safety: only allow reading from known log directories
    allowed_prefixes = ["/var/log/", "/tmp/", "/var/lib/opsguard/"]
    if not any(filepath.startswith(prefix) for prefix in allowed_prefixes):
        return ToolResult(
            success=False,
            data="",
            error=f"Access denied: can only read logs from {allowed_prefixes}",
        )

    lines = min(lines, 500)
    try:
        cmd = ["tail", "-n", str(lines), filepath]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)

        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def search_logs(pattern: str, filepath: str | None = None, lines: int = 30) -> ToolResult:
    """Search for a pattern in logs.

    Args:
        pattern: Regex pattern to search for
        filepath: Specific log file (if None, searches journal)
        lines: Maximum matching lines to return
    """
    lines = min(lines, 200)

    try:
        if filepath:
            # Safety check
            allowed_prefixes = ["/var/log/", "/tmp/"]
            if not any(filepath.startswith(prefix) for prefix in allowed_prefixes):
                return ToolResult(success=False, data="", error="Access denied")

            cmd = ["grep", "-n", "-i", pattern, filepath]
        else:
            cmd = ["journalctl", "--no-pager", "-g", pattern, "-n", str(lines), "--since", "24h ago"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

        output_lines = result.stdout.strip().split("\n")[:lines]
        return ToolResult(
            success=True,
            data={"matches": output_lines, "count": len(output_lines)},
        )
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_boot_logs() -> ToolResult:
    """Get logs from the current boot."""
    try:
        cmd = ["journalctl", "--no-pager", "-b", "0", "-p", "warning", "-n", "100"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
