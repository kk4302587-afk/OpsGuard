"""System overview MCP tools.

Composite tools that aggregate multiple data sources for quick context.
These are convenience tools - the Agent can always use atomic tools instead.
"""

import subprocess
import platform
from app.mcp_tools.process_tools import ToolResult, command_error


def system_overview() -> ToolResult:
    """Get a comprehensive system overview in one call.

    Returns CPU, memory, disk, load, uptime, and kernel info.
    This is a composite tool for efficiency - reduces Agent round-trips.
    """
    try:
        data = {}

        # Uptime and load
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.read().split()[0])
            days = int(uptime_seconds // 86400)
            hours = int((uptime_seconds % 86400) // 3600)
            data["uptime"] = f"{days}d {hours}h"

        with open("/proc/loadavg", "r") as f:
            parts = f.read().split()
            data["load_avg"] = {"1min": parts[0], "5min": parts[1], "15min": parts[2]}

        # Memory
        cmd = ["free", "-h"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        data["memory"] = result.stdout.strip()

        # Disk
        cmd = ["df", "-h", "/"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        data["disk_root"] = result.stdout.strip()

        # CPU info
        cmd = ["nproc"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        data["cpu_cores"] = result.stdout.strip()

        # Kernel
        data["kernel"] = platform.release()
        data["hostname"] = platform.node()
        data["arch"] = platform.machine()

        return ToolResult(success=True, data=data)
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def health_check() -> ToolResult:
    """Quick health check - identifies obvious issues.

    Checks: disk > 90%, memory > 90%, zombie processes, failed services.
    Composite tool for the health report feature.
    """
    issues = []

    try:
        # Check disk usage
        cmd = ["df", "--output=pcent,target", "-x", "tmpfs", "-x", "devtmpfs"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.strip().split()
            if parts and int(parts[0].rstrip("%")) > 90:
                issues.append({"type": "disk", "severity": "high", "detail": f"{parts[1]} is {parts[0]} full"})

        # Check memory
        cmd = ["free", "--bytes"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        lines = result.stdout.strip().split("\n")
        if len(lines) > 1:
            mem_parts = lines[1].split()
            if len(mem_parts) >= 3:
                total = int(mem_parts[1])
                used = int(mem_parts[2])
                if total > 0 and (used / total) > 0.9:
                    issues.append({"type": "memory", "severity": "high", "detail": f"Memory usage: {used/total:.0%}"})

        # Check for zombie processes
        cmd = ["ps", "aux"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        zombies = [l for l in result.stdout.split("\n") if " Z " in l or "defunct" in l]
        if zombies:
            issues.append({"type": "zombie", "severity": "medium", "detail": f"{len(zombies)} zombie processes"})

        # Check failed services
        cmd = ["systemctl", "--failed", "--no-pager", "--plain"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        if "0 loaded" not in result.stdout:
            failed_lines = [l for l in result.stdout.split("\n") if ".service" in l]
            if failed_lines:
                issues.append({"type": "service", "severity": "high", "detail": f"{len(failed_lines)} failed services"})

        status = "healthy" if not issues else ("warning" if all(i["severity"] == "medium" for i in issues) else "critical")

        return ToolResult(
            success=True,
            data={"status": status, "issues": issues, "issue_count": len(issues)},
        )
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_crontab_list(user: str | None = None) -> ToolResult:
    """List crontab entries.

    Args:
        user: Specific user's crontab (None for current user)
    """
    try:
        cmd = ["crontab", "-l"]
        if user:
            cmd.extend(["-u", user])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

        if result.returncode != 0:
            if "no crontab" in result.stderr.lower():
                return ToolResult(success=True, data={"entries": [], "message": "No crontab entries"})
            return ToolResult(success=False, data="", error=result.stderr)

        entries = [l for l in result.stdout.strip().split("\n") if l and not l.startswith("#")]
        return ToolResult(success=True, data={"entries": entries, "count": len(entries)})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_user_sessions() -> ToolResult:
    """Get currently logged-in user sessions."""
    try:
        cmd = ["who"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
