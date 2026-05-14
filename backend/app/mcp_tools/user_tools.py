"""User and permission management MCP tools.

Tools for managing system users, groups, and access control.
"""

import subprocess

from app.mcp_tools.process_tools import ToolResult


def list_users() -> ToolResult:
    """List all system users with their details."""
    try:
        cmd = ["cat", "/etc/passwd"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

        users = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split(":")
            if len(parts) >= 7:
                uid = int(parts[2])
                # Only show human users (UID >= 1000) and root
                if uid >= 1000 or uid == 0:
                    users.append({
                        "username": parts[0],
                        "uid": uid,
                        "gid": int(parts[3]),
                        "home": parts[5],
                        "shell": parts[6],
                    })

        return ToolResult(success=True, data={"users": users, "count": len(users)})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def list_groups() -> ToolResult:
    """List all system groups."""
    try:
        cmd = ["cat", "/etc/group"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

        groups = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split(":")
            if len(parts) >= 4:
                gid = int(parts[2])
                members = parts[3].split(",") if parts[3] else []
                if gid >= 1000 or members:
                    groups.append({
                        "name": parts[0],
                        "gid": gid,
                        "members": members,
                    })

        return ToolResult(success=True, data={"groups": groups, "count": len(groups)})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_user_info(username: str) -> ToolResult:
    """Get detailed info about a specific user.

    Args:
        username: Username to look up
    """
    try:
        cmd = ["id", username]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=f"用户不存在: {username}")

        # Get last login
        last_cmd = ["lastlog", "-u", username]
        last_result = subprocess.run(last_cmd, capture_output=True, text=True, timeout=5)

        return ToolResult(success=True, data={
            "id_info": result.stdout.strip(),
            "last_login": last_result.stdout.strip() if last_result.returncode == 0 else "未知",
        })
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def create_user(username: str, home_dir: str = "", shell: str = "/bin/bash") -> ToolResult:
    """Create a new system user. REQUIRES APPROVAL.

    Args:
        username: New username
        home_dir: Home directory (auto-created if empty)
        shell: Login shell
    """
    try:
        cmd = ["sudo", "useradd", "-m", "-s", shell]
        if home_dir:
            cmd.extend(["-d", home_dir])
        cmd.append(username)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)
        return ToolResult(success=True, data=f"用户已创建: {username}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def delete_user(username: str, remove_home: bool = False) -> ToolResult:
    """Delete a system user. REQUIRES APPROVAL.

    Args:
        username: Username to delete
        remove_home: Whether to remove the user's home directory
    """
    # Safety: never delete root or system users
    if username in ("root", "opsguard", "nobody"):
        return ToolResult(success=False, data="", error=f"禁止删除系统用户: {username}")

    try:
        cmd = ["sudo", "userdel"]
        if remove_home:
            cmd.append("-r")
        cmd.append(username)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)
        return ToolResult(success=True, data=f"用户已删除: {username}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def lock_user(username: str) -> ToolResult:
    """Lock a user account (disable login). REQUIRES APPROVAL.

    Args:
        username: Username to lock
    """
    try:
        cmd = ["sudo", "usermod", "-L", username]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)
        return ToolResult(success=True, data=f"用户已锁定: {username}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def unlock_user(username: str) -> ToolResult:
    """Unlock a user account. REQUIRES APPROVAL.

    Args:
        username: Username to unlock
    """
    try:
        cmd = ["sudo", "usermod", "-U", username]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)
        return ToolResult(success=True, data=f"用户已解锁: {username}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
