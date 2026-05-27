"""Service and systemd management MCP tools.

Atomic tools for inspecting and managing system services.
"""

import subprocess
from app.mcp_tools.process_tools import ToolResult, command_error


_READ_ONLY_SERVICE_ALIASES = {
    "ssh": ["sshd"],
}


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
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_service_status(service: str) -> ToolResult:
    """Get detailed status of a specific service.

    Args:
        service: Service name (e.g., "nginx", "sshd")
    """
    service_candidates = [service]
    service_candidates.extend(_READ_ONLY_SERVICE_ALIASES.get(service.replace(".service", ""), []))

    try:
        last_result = None
        for candidate in service_candidates:
            cmd = ["systemctl", "status", candidate, "--no-pager"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            last_result = result
            if result.returncode != 0 and "could not be found" in (result.stdout + result.stderr).lower():
                continue
            if candidate != service:
                data = f"[service alias: {service} -> {candidate}]\n{result.stdout.strip()}"
            else:
                data = result.stdout.strip()
            return ToolResult(success=True, data=data)
        result = last_result
        if result.returncode != 0 and "could not be found" in (result.stdout + result.stderr).lower():
            return ToolResult(success=False, data="", error=command_error(result))
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_failed_services() -> ToolResult:
    """Get all failed services."""
    try:
        cmd = ["systemctl", "--failed", "--no-pager"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
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


def start_service(service: str) -> ToolResult:
    """Start a service. REQUIRES APPROVAL.

    Args:
        service: Service name to start
    """
    try:
        cmd = ["sudo", "systemctl", "start", service]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)

        return ToolResult(success=True, data=f"Service {service} started successfully")
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
    service_candidates = [service]
    service_candidates.extend(_READ_ONLY_SERVICE_ALIASES.get(service.replace(".service", ""), []))

    try:
        last_result = None
        for candidate in service_candidates:
            cmd = ["journalctl", "-u", candidate, "--no-pager", "-n", str(lines)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            last_result = result
            if result.returncode != 0 and "could not be found" in (result.stdout + result.stderr).lower():
                continue
            if result.returncode != 0:
                return ToolResult(success=False, data="", error=command_error(result))
            data = result.stdout.strip()
            if candidate != service:
                data = f"[service alias: {service} -> {candidate}]\n{data}"
            return ToolResult(success=True, data=data)
        result = last_result
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
