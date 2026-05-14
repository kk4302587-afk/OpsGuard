"""Package management MCP tools.

Tools for installing, removing, and querying system packages.
Supports apt (Debian/Ubuntu/Kylin) and yum/dnf (RHEL/CentOS).
"""

import subprocess
import platform

from app.mcp_tools.process_tools import ToolResult


def _detect_package_manager() -> str:
    """Detect the system's package manager."""
    for pm in ["apt", "dnf", "yum", "pacman"]:
        try:
            result = subprocess.run(["which", pm], capture_output=True, timeout=5)
            if result.returncode == 0:
                return pm
        except Exception:
            continue
    return "unknown"


def list_installed_packages(filter_name: str = "") -> ToolResult:
    """List installed packages, optionally filtered by name.

    Args:
        filter_name: Filter packages containing this string
    """
    pm = _detect_package_manager()
    try:
        if pm == "apt":
            cmd = ["dpkg", "-l"]
        elif pm in ("dnf", "yum"):
            cmd = ["rpm", "-qa"]
        elif pm == "pacman":
            cmd = ["pacman", "-Q"]
        else:
            return ToolResult(success=False, data="", error="未检测到支持的包管理器")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        lines = result.stdout.strip().split("\n")

        if filter_name:
            lines = [l for l in lines if filter_name.lower() in l.lower()]

        return ToolResult(success=True, data={"packages": lines[:50], "total": len(lines), "package_manager": pm})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def search_package(name: str) -> ToolResult:
    """Search for available packages by name.

    Args:
        name: Package name to search
    """
    pm = _detect_package_manager()
    try:
        if pm == "apt":
            cmd = ["apt-cache", "search", name]
        elif pm == "dnf":
            cmd = ["dnf", "search", name]
        elif pm == "yum":
            cmd = ["yum", "search", name]
        else:
            return ToolResult(success=False, data="", error="未检测到支持的包管理器")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        lines = result.stdout.strip().split("\n")[:20]
        return ToolResult(success=True, data={"results": lines, "count": len(lines)})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def install_package(name: str) -> ToolResult:
    """Install a package. REQUIRES APPROVAL.

    Args:
        name: Package name to install
    """
    pm = _detect_package_manager()
    try:
        if pm == "apt":
            cmd = ["sudo", "apt-get", "install", "-y", name]
        elif pm == "dnf":
            cmd = ["sudo", "dnf", "install", "-y", name]
        elif pm == "yum":
            cmd = ["sudo", "yum", "install", "-y", name]
        else:
            return ToolResult(success=False, data="", error="未检测到支持的包管理器")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr[:500])
        return ToolResult(success=True, data=f"已安装: {name}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def remove_package(name: str) -> ToolResult:
    """Remove a package. REQUIRES APPROVAL.

    Args:
        name: Package name to remove
    """
    pm = _detect_package_manager()
    try:
        if pm == "apt":
            cmd = ["sudo", "apt-get", "remove", "-y", name]
        elif pm == "dnf":
            cmd = ["sudo", "dnf", "remove", "-y", name]
        elif pm == "yum":
            cmd = ["sudo", "yum", "remove", "-y", name]
        else:
            return ToolResult(success=False, data="", error="未检测到支持的包管理器")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr[:500])
        return ToolResult(success=True, data=f"已卸载: {name}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def check_package_updates() -> ToolResult:
    """Check for available package updates."""
    pm = _detect_package_manager()
    try:
        if pm == "apt":
            subprocess.run(["sudo", "apt-get", "update", "-qq"], capture_output=True, timeout=60)
            cmd = ["apt", "list", "--upgradable"]
        elif pm == "dnf":
            cmd = ["dnf", "check-update", "--quiet"]
        elif pm == "yum":
            cmd = ["yum", "check-update", "--quiet"]
        else:
            return ToolResult(success=False, data="", error="未检测到支持的包管理器")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
        return ToolResult(success=True, data={"updates_available": lines[:30], "count": len(lines)})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
