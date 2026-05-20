"""File management MCP tools.

Tools for creating, editing, and managing files safely.
All write operations respect protected paths and require approval.
"""

import os
import shutil
import subprocess
from pathlib import Path

from app.mcp_tools.process_tools import ToolResult
from app.config import settings


def _check_protected(filepath: str) -> str | None:
    """Check if a path is protected. Returns error message or None."""
    for protected in settings.execution.protected_paths:
        if filepath.startswith(protected):
            return f"路径受保护，禁止修改: {protected}"
    return None


def write_file(filepath: str, content: str, append: bool = False) -> ToolResult:
    """Write or append content to a file. REQUIRES APPROVAL.

    Args:
        filepath: Target file path
        content: Content to write
        append: If True, append to file; if False, overwrite
    """
    error = _check_protected(filepath)
    if error:
        return ToolResult(success=False, data="", error=error)

    try:
        mode = "a" if append else "w"
        with open(filepath, mode, encoding="utf-8") as f:
            f.write(content)
        return ToolResult(success=True, data=f"已{'追加' if append else '写入'}: {filepath} ({len(content)} bytes)")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def create_file(filepath: str, content: str = "", overwrite: bool = False) -> ToolResult:
    """Create a new file. REQUIRES APPROVAL.

    Args:
        filepath: Target file path
        content: Initial file content
        overwrite: If True, overwrite an existing file; defaults to safe create-only
    """
    error = _check_protected(filepath)
    if error:
        return ToolResult(success=False, data="", error=error)

    try:
        path = Path(filepath)
        parent = path.parent
        if parent and not parent.exists():
            return ToolResult(success=False, data="", error=f"父目录不存在: {parent}")
        if path.exists() and path.is_dir():
            return ToolResult(success=False, data="", error=f"目标是目录，不能创建为文件: {filepath}")
        if path.exists() and not overwrite:
            return ToolResult(success=False, data="", error=f"文件已存在，如需覆盖请设置 overwrite=true: {filepath}")

        mode = "w" if overwrite else "x"
        with open(path, mode, encoding="utf-8") as f:
            f.write(content)
        action = "已覆盖创建" if overwrite else "已创建"
        return ToolResult(success=True, data=f"{action}: {filepath} ({len(content)} bytes)")
    except FileExistsError:
        return ToolResult(success=False, data="", error=f"文件已存在，如需覆盖请设置 overwrite=true: {filepath}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def delete_file(filepath: str) -> ToolResult:
    """Delete a file. REQUIRES APPROVAL.

    Args:
        filepath: File path to delete
    """
    error = _check_protected(filepath)
    if error:
        return ToolResult(success=False, data="", error=error)

    try:
        path = Path(filepath)
        if not path.exists():
            return ToolResult(success=False, data="", error=f"文件不存在: {filepath}")
        if path.is_dir():
            return ToolResult(success=False, data="", error="不能删除目录，请使用 delete_directory")

        size = path.stat().st_size
        path.unlink()
        return ToolResult(success=True, data=f"已删除: {filepath} ({size} bytes)")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def delete_directory(dirpath: str, force: bool = False) -> ToolResult:
    """Delete a directory. REQUIRES APPROVAL. Use force=True for non-empty dirs.

    Args:
        dirpath: Directory path to delete
        force: If True, delete even if non-empty (recursive)
    """
    error = _check_protected(dirpath)
    if error:
        return ToolResult(success=False, data="", error=error)

    # Extra safety: never delete root-level directories
    if dirpath.count("/") <= 1:
        return ToolResult(success=False, data="", error=f"禁止删除顶级目录: {dirpath}")

    try:
        path = Path(dirpath)
        if not path.exists():
            return ToolResult(success=False, data="", error=f"目录不存在: {dirpath}")
        if not path.is_dir():
            return ToolResult(success=False, data="", error="不是目录")

        if force:
            shutil.rmtree(dirpath)
        else:
            path.rmdir()  # Only works if empty
        return ToolResult(success=True, data=f"已删除目录: {dirpath}")
    except OSError as e:
        if "not empty" in str(e).lower() or "Directory not empty" in str(e):
            return ToolResult(success=False, data="", error=f"目录非空，使用 force=True 强制删除")
        return ToolResult(success=False, data="", error=str(e))


def move_file(source: str, destination: str) -> ToolResult:
    """Move or rename a file/directory. REQUIRES APPROVAL.

    Args:
        source: Source path
        destination: Destination path
    """
    error = _check_protected(source) or _check_protected(destination)
    if error:
        return ToolResult(success=False, data="", error=error)

    try:
        shutil.move(source, destination)
        return ToolResult(success=True, data=f"已移动: {source} → {destination}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def copy_file(source: str, destination: str) -> ToolResult:
    """Copy a file. REQUIRES APPROVAL.

    Args:
        source: Source file path
        destination: Destination path
    """
    try:
        if Path(source).is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        return ToolResult(success=True, data=f"已复制: {source} → {destination}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def change_permissions(filepath: str, mode: str) -> ToolResult:
    """Change file permissions. REQUIRES APPROVAL.

    Args:
        filepath: Target file path
        mode: Permission mode (e.g., "644", "755")
    """
    error = _check_protected(filepath)
    if error:
        return ToolResult(success=False, data="", error=error)

    # Safety: block overly permissive modes
    if mode in ("777", "666"):
        return ToolResult(success=False, data="", error=f"权限 {mode} 过于宽松，存在安全风险")

    try:
        os.chmod(filepath, int(mode, 8))
        return ToolResult(success=True, data=f"已修改权限: {filepath} → {mode}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def change_owner(filepath: str, owner: str) -> ToolResult:
    """Change file ownership. REQUIRES APPROVAL.

    Args:
        filepath: Target file path
        owner: New owner in format "user:group" or "user"
    """
    error = _check_protected(filepath)
    if error:
        return ToolResult(success=False, data="", error=error)

    try:
        cmd = ["sudo", "chown", owner, filepath]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)
        return ToolResult(success=True, data=f"已修改所有者: {filepath} → {owner}")
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
