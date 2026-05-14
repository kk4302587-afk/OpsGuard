"""Cron/scheduled task management MCP tools.

Tools for viewing, creating, and managing cron jobs.
"""

import subprocess

from app.mcp_tools.process_tools import ToolResult


def list_cron_jobs(user: str = "") -> ToolResult:
    """List cron jobs for a user (or current user if empty).

    Args:
        user: Username (empty for current user)
    """
    try:
        cmd = ["crontab", "-l"]
        if user:
            cmd.extend(["-u", user])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

        if result.returncode != 0:
            if "no crontab" in result.stderr.lower():
                return ToolResult(success=True, data={"jobs": [], "message": "无定时任务"})
            return ToolResult(success=False, data="", error=result.stderr)

        lines = result.stdout.strip().split("\n")
        jobs = []
        for line in lines:
            if line.strip() and not line.startswith("#"):
                jobs.append(line)

        return ToolResult(success=True, data={"jobs": jobs, "count": len(jobs), "raw": result.stdout.strip()})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def list_system_timers() -> ToolResult:
    """List all systemd timers (modern alternative to cron)."""
    try:
        cmd = ["systemctl", "list-timers", "--all", "--no-pager"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def add_cron_job(schedule: str, command: str, user: str = "") -> ToolResult:
    """Add a new cron job. REQUIRES APPROVAL.

    Args:
        schedule: Cron schedule expression (e.g., "0 2 * * *" for daily at 2am)
        command: Command to execute
        user: Username (empty for current user)
    """
    # Safety: validate schedule format
    parts = schedule.split()
    if len(parts) != 5:
        return ToolResult(success=False, data="", error=f"无效的 cron 表达式: {schedule} (需要5个字段)")

    try:
        # Get existing crontab
        get_cmd = ["crontab", "-l"]
        if user:
            get_cmd.extend(["-u", user])

        existing = subprocess.run(get_cmd, capture_output=True, text=True, timeout=5)
        current_crontab = existing.stdout if existing.returncode == 0 else ""

        # Append new job
        new_line = f"{schedule} {command}"
        new_crontab = current_crontab.rstrip() + "\n" + new_line + "\n"

        # Write back
        set_cmd = ["crontab", "-"]
        if user:
            set_cmd = ["sudo", "crontab", "-u", user, "-"]

        proc = subprocess.run(set_cmd, input=new_crontab, capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            return ToolResult(success=False, data="", error=proc.stderr)

        return ToolResult(success=True, data=f"已添加定时任务: {new_line}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def remove_cron_job(pattern: str, user: str = "") -> ToolResult:
    """Remove cron jobs matching a pattern. REQUIRES APPROVAL.

    Args:
        pattern: Text pattern to match (jobs containing this text will be removed)
        user: Username (empty for current user)
    """
    try:
        get_cmd = ["crontab", "-l"]
        if user:
            get_cmd.extend(["-u", user])

        existing = subprocess.run(get_cmd, capture_output=True, text=True, timeout=5)
        if existing.returncode != 0:
            return ToolResult(success=False, data="", error="无法读取 crontab")

        lines = existing.stdout.split("\n")
        removed = []
        kept = []
        for line in lines:
            if pattern in line and line.strip() and not line.startswith("#"):
                removed.append(line)
            else:
                kept.append(line)

        if not removed:
            return ToolResult(success=True, data=f"未找到匹配 '{pattern}' 的定时任务")

        # Write back
        new_crontab = "\n".join(kept) + "\n"
        set_cmd = ["crontab", "-"]
        if user:
            set_cmd = ["sudo", "crontab", "-u", user, "-"]

        proc = subprocess.run(set_cmd, input=new_crontab, capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            return ToolResult(success=False, data="", error=proc.stderr)

        return ToolResult(success=True, data={"removed": removed, "count": len(removed)})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
