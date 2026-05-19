"""Disk and filesystem MCP tools.

Atomic tools for disk space analysis and file management.
"""

import subprocess
from app.mcp_tools.process_tools import ToolResult, command_error


def get_disk_usage(path: str = "/") -> ToolResult:
    """Get disk usage for a filesystem path.

    Args:
        path: Filesystem path to check (default: root)
    """
    try:
        cmd = ["df", "-h", path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def find_large_files(path: str = "/", min_size: str = "100M", limit: int = 20) -> ToolResult:
    """Find large files under a given path.

    Args:
        path: Directory to search
        min_size: Minimum file size (e.g., "100M", "1G")
        limit: Maximum number of results
    """
    try:
        cmd = ["find", path, "-type", "f", "-size", f"+{min_size}", "-exec", "ls", "-lh", "{}", ";"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))

        files = result.stdout.strip().split("\n")[:limit]
        if files == [""]:
            files = []
        return ToolResult(success=True, data={"files": files, "count": len(files)})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_directory_size(path: str) -> ToolResult:
    """Get total size of a directory.

    Args:
        path: Directory path to measure
    """
    try:
        cmd = ["du", "-sh", path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_inode_usage() -> ToolResult:
    """Get inode usage for all filesystems."""
    try:
        cmd = ["df", "-i"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(result))
        return ToolResult(success=True, data=result.stdout.strip())
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def check_file_info(filepath: str) -> ToolResult:
    """Get detailed info about a file (type, permissions, owner, references).

    Args:
        filepath: Path to the file to inspect
    """
    try:
        # File stat
        cmd = ["stat", filepath]
        stat_result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if stat_result.returncode != 0:
            return ToolResult(success=False, data="", error=command_error(stat_result))

        # Check what processes have the file open
        lsof_cmd = ["lsof", filepath]
        lsof_result = subprocess.run(lsof_cmd, capture_output=True, text=True, timeout=5)

        return ToolResult(
            success=True,
            data={
                "stat": stat_result.stdout.strip(),
                "open_by": lsof_result.stdout.strip() if lsof_result.returncode == 0 else "No processes",
            },
        )
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
