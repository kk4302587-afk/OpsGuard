"""Service and systemd management MCP tools.

Atomic tools for inspecting and managing system services.
"""

import subprocess
from app.mcp_tools.process_tools import ToolResult


def list_services(state: str | None = None) -> ToolResult:
    """List systemd services, optionally filtered by state.

    Args:
        state: Filter by state (running, failed, inactive, etc.)
    """
    try:
        cmd = ["systemctl", "list-units", "--type=service", "--no-pager"]
        if state:
            cmd.extend([f"--state={state}"])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_service_status(service: str) -> ToolResult:
    """Get detailed status of a specific service.

    Args:
        service: Service name (e.g., "nginx", "sshd")
    """
    try:
        cmd = ["systemctl", "status", service, "--no-pager"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_failed_services() -> ToolResult:
    """Get all failed services."""
    try:
        cmd = ["systemctl", "--failed", "--no-pager"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def restart_service(service: str) -> ToolResult:
    """Restart a service. REQUIRES APPROVAL.

    Args:
        service: Service name to restart
    """
    try:
        cmd = ["sudo", "systemctl", "restart", service]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)

        return ToolResult(success=True, data=f"Service {service} restarted successfully")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def stop_service(service: str) -> ToolResult:
    """Stop a service. REQUIRES APPROVAL.

    Args:
        service: Service name to stop
    """
    try:
        cmd = ["sudo", "systemctl", "stop", service]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)

        return ToolResult(success=True, data=f"Service {service} stopped successfully")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_service_logs(service: str, lines: int = 50) -> ToolResult:
    """Get recent logs for a specific service.

    Args:
        service: Service name
        lines: Number of log lines
    """
    lines = min(lines, 200)
    try:
        cmd = ["journalctl", "-u", service, "--no-pager", "-n", str(lines)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
