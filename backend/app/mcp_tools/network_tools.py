"""Network inspection MCP tools.

Atomic tools for network status and connection analysis.
"""

import subprocess
from app.mcp_tools.process_tools import ToolResult


def get_listening_ports() -> ToolResult:
    """Get all listening ports and their associated processes."""
    try:
        cmd = ["ss", "-tlnp"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_connections(state: str = "established") -> ToolResult:
    """Get network connections filtered by state.

    Args:
        state: Connection state filter (established, time-wait, close-wait, etc.)
    """
    try:
        cmd = ["ss", "-tnp", "state", state]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_connection_count() -> ToolResult:
    """Get connection count grouped by state."""
    try:
        cmd = ["ss", "-s"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def check_port(port: int) -> ToolResult:
    """Check what process is using a specific port.

    Args:
        port: Port number to check
    """
    try:
        cmd = ["ss", "-tlnp", f"sport = :{port}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def ping_host(host: str, count: int = 4) -> ToolResult:
    """Ping a host to check connectivity.

    Args:
        host: Hostname or IP to ping
        count: Number of ping packets
    """
    # Safety: limit count to prevent abuse
    count = min(count, 10)
    try:
        cmd = ["ping", "-c", str(count), "-W", "3", host]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=count * 4 + 5)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
