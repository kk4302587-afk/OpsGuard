"""Process management MCP tools.

Atomic tools for process inspection and management.
"""

import subprocess
from dataclasses import dataclass


@dataclass
class ToolResult:
    """Standard result from any MCP tool."""
    success: bool
    data: dict | str | list
    error: str | None = None


def command_error(result: subprocess.CompletedProcess, fallback: str = "Command failed") -> str:
    """Format a subprocess failure without losing stderr/stdout context."""
    stderr = result.stderr.strip() if isinstance(result.stderr, str) else ""
    stdout = result.stdout.strip() if isinstance(result.stdout, str) else ""
    detail = stderr or stdout or fallback
    return f"{detail} (exit code {result.returncode})"


def list_processes(sort_by: str = "cpu", limit: int = 20) -> ToolResult:
    """List running processes sorted by resource usage.

    Args:
        sort_by: Sort criteria - "cpu", "memory", or "pid"
        limit: Maximum number of processes to return
    """
    try:
        cmd = ["ps", "aux", "--sort", f"-{sort_by}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)

        lines = result.stdout.strip().split("\n")
        header = lines[0]
        processes = lines[1 : limit + 1]

        return ToolResult(success=True, data={"header": header, "processes": processes})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def find_zombie_processes() -> ToolResult:
    """Find zombie (defunct) processes."""
    try:
        cmd = ["ps", "aux"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))

        zombies = [
            line for line in result.stdout.split("\n") if "Z" in line.split()[7:8] or "defunct" in line
        ]

        return ToolResult(success=True, data={"zombies": zombies, "count": len(zombies)})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_process_detail(pid: int) -> ToolResult:
    """Get detailed information about a specific process.

    Args:
        pid: Process ID to inspect
    """
    try:
        cmd = ["ps", "-p", str(pid), "-o", "pid,ppid,user,stat,pcpu,pmem,vsz,rss,tty,start,time,comm"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            return ToolResult(success=False, data="", error=f"Process {pid} not found")

        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def kill_process(pid: int, signal: int = 15) -> ToolResult:
    """Send a signal to a process. REQUIRES APPROVAL for signal 9.

    Args:
        pid: Process ID to signal
        signal: Signal number (15=TERM, 9=KILL)
    """
    # Safety: never kill PID 1 or kernel threads
    if pid <= 1:
        return ToolResult(success=False, data="", error="Cannot kill PID 0 or 1")

    try:
        cmd = ["kill", f"-{signal}", str(pid)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)

        return ToolResult(success=True, data=f"Signal {signal} sent to PID {pid}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
