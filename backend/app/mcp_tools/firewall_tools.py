"""Firewall management MCP tools.

Tools for inspecting and managing firewall rules.
Supports firewalld (Kylin/RHEL) and ufw (Ubuntu/Debian).
"""

import subprocess

from app.mcp_tools.process_tools import ToolResult


def _detect_firewall() -> str:
    """Detect which firewall is active."""
    for fw in ["firewall-cmd", "ufw"]:
        try:
            result = subprocess.run(["which", fw], capture_output=True, timeout=5)
            if result.returncode == 0:
                return fw
        except Exception:
            continue
    return "iptables"


def get_firewall_status() -> ToolResult:
    """Get current firewall status and active rules."""
    fw = _detect_firewall()
    try:
        if fw == "firewall-cmd":
            cmd = ["sudo", "firewall-cmd", "--state"]
            state = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            rules_cmd = ["sudo", "firewall-cmd", "--list-all"]
            rules = subprocess.run(rules_cmd, capture_output=True, text=True, timeout=10)
            return ToolResult(success=True, data={
                "firewall": "firewalld",
                "state": state.stdout.strip(),
                "rules": rules.stdout.strip(),
            })
        elif fw == "ufw":
            cmd = ["sudo", "ufw", "status", "verbose"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return ToolResult(success=True, data={
                "firewall": "ufw",
                "status": result.stdout.strip(),
            })
        else:
            cmd = ["sudo", "iptables", "-L", "-n", "--line-numbers"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return ToolResult(success=True, data={
                "firewall": "iptables",
                "rules": result.stdout.strip(),
            })
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def list_open_ports() -> ToolResult:
    """List all ports allowed through the firewall."""
    fw = _detect_firewall()
    try:
        if fw == "firewall-cmd":
            cmd = ["sudo", "firewall-cmd", "--list-ports"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            services_cmd = ["sudo", "firewall-cmd", "--list-services"]
            services = subprocess.run(services_cmd, capture_output=True, text=True, timeout=10)
            return ToolResult(success=True, data={
                "ports": result.stdout.strip(),
                "services": services.stdout.strip(),
            })
        elif fw == "ufw":
            cmd = ["sudo", "ufw", "status"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return ToolResult(success=True, data=result.stdout.strip())
        else:
            cmd = ["sudo", "iptables", "-L", "INPUT", "-n"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def allow_port(port: int, protocol: str = "tcp") -> ToolResult:
    """Open a port in the firewall. REQUIRES APPROVAL.

    Args:
        port: Port number to open
        protocol: Protocol (tcp or udp)
    """
    fw = _detect_firewall()
    try:
        if fw == "firewall-cmd":
            cmd = ["sudo", "firewall-cmd", "--permanent", f"--add-port={port}/{protocol}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                subprocess.run(["sudo", "firewall-cmd", "--reload"], capture_output=True, timeout=10)
                return ToolResult(success=True, data=f"已开放端口: {port}/{protocol}")
        elif fw == "ufw":
            cmd = ["sudo", "ufw", "allow", f"{port}/{protocol}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return ToolResult(success=True, data=f"已开放端口: {port}/{protocol}")
        else:
            cmd = ["sudo", "iptables", "-A", "INPUT", "-p", protocol, "--dport", str(port), "-j", "ACCEPT"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return ToolResult(success=True, data=f"已开放端口: {port}/{protocol}")

        return ToolResult(success=False, data="", error=result.stderr if result else "未知错误")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def block_port(port: int, protocol: str = "tcp") -> ToolResult:
    """Block a port in the firewall. REQUIRES APPROVAL.

    Args:
        port: Port number to block
        protocol: Protocol (tcp or udp)
    """
    fw = _detect_firewall()
    try:
        if fw == "firewall-cmd":
            cmd = ["sudo", "firewall-cmd", "--permanent", f"--remove-port={port}/{protocol}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                subprocess.run(["sudo", "firewall-cmd", "--reload"], capture_output=True, timeout=10)
                return ToolResult(success=True, data=f"已关闭端口: {port}/{protocol}")
        elif fw == "ufw":
            cmd = ["sudo", "ufw", "deny", f"{port}/{protocol}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return ToolResult(success=True, data=f"已关闭端口: {port}/{protocol}")
        else:
            cmd = ["sudo", "iptables", "-A", "INPUT", "-p", protocol, "--dport", str(port), "-j", "DROP"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return ToolResult(success=True, data=f"已关闭端口: {port}/{protocol}")

        return ToolResult(success=False, data="", error=result.stderr if result else "未知错误")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
