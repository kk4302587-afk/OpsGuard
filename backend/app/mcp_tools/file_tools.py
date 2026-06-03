"""File management MCP tools.

Tools for creating, editing, and managing files safely.
All write operations respect protected paths and require approval.
"""

import os
import shutil
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from app.mcp_tools.process_tools import ToolResult
from app.config import settings


def _check_protected(filepath: str) -> str | None:
    """Check if a path is protected. Returns error message or None."""
    for protected in settings.execution.protected_paths:
        if filepath.startswith(protected):
            return f"路径受保护，禁止修改: {protected}"
    return None


def _path_type(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def list_directory(path: str, show_hidden: bool = False, limit: int = 100) -> ToolResult:
    """List directory entries. READ-ONLY.

    Args:
        path: Directory path
        show_hidden: Include dotfiles if True
        limit: Maximum entries to return
    """
    try:
        target = Path(path)
        if not target.exists():
            return ToolResult(success=False, data="", error=f"目录不存在: {path}")
        if not target.is_dir():
            return ToolResult(success=False, data="", error=f"不是目录: {path}")

        safe_limit = max(1, min(int(limit or 100), 500))
        entries = []
        for entry in sorted(target.iterdir(), key=lambda item: item.name.lower()):
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                stat = entry.lstat()
                entries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "type": _path_type(entry),
                    "size": stat.st_size,
                    "mode": oct(stat.st_mode & 0o777),
                })
            except OSError as e:
                entries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "type": "unknown",
                    "error": str(e),
                })
            if len(entries) >= safe_limit:
                break

        return ToolResult(
            success=True,
            data={
                "path": str(target),
                "count": len(entries),
                "limit": safe_limit,
                "truncated": len(entries) >= safe_limit,
                "entries": entries,
            },
        )
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def read_file(filepath: str, max_bytes: int = 65536) -> ToolResult:
    """Read a regular text file with a byte limit. READ-ONLY.

    Args:
        filepath: File path to read
        max_bytes: Maximum bytes to read, capped at 1 MiB
    """
    try:
        path = Path(filepath)
        if not path.exists():
            return ToolResult(success=False, data="", error=f"文件不存在: {filepath}")
        if path.is_dir():
            return ToolResult(success=False, data="", error=f"目标是目录，请使用 list_directory: {filepath}")
        if not path.is_file():
            return ToolResult(success=False, data="", error=f"不是普通文件: {filepath}")

        safe_max = max(1, min(int(max_bytes or 65536), 1024 * 1024))
        size = path.stat().st_size
        with open(path, "rb") as f:
            raw = f.read(safe_max)
        content = raw.decode("utf-8", errors="replace")
        return ToolResult(
            success=True,
            data={
                "path": str(path),
                "size": size,
                "max_bytes": safe_max,
                "truncated": size > safe_max,
                "content": content,
            },
        )
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def read_document(filepath: str, max_bytes: int = 65536) -> ToolResult:
    """Read a text document for verbatim display. READ-ONLY.

    This is separate from read_file so the Agent can distinguish diagnostic
    reads from user requests such as "output this document" or "cat this file".

    Args:
        filepath: Document path to read
        max_bytes: Maximum bytes to read, capped at 1 MiB
    """
    result = read_file(filepath=filepath, max_bytes=max_bytes)
    if not result.success or not isinstance(result.data, dict):
        return result

    data = dict(result.data)
    data["render_mode"] = "verbatim"
    data["content_type"] = "text/plain"
    return ToolResult(success=True, data=data, error=result.error)


def find_files(path: str, pattern: str, file_type: str = "any", limit: int = 100) -> ToolResult:
    """Find files or directories by name pattern. READ-ONLY.

    Args:
        path: Root directory to search
        pattern: Shell-style name pattern, for example "*.log" or "nginx*"
        file_type: any, file, or directory
        limit: Maximum matches to return
    """
    if file_type not in {"any", "file", "directory"}:
        return ToolResult(success=False, data="", error="file_type 必须是 any、file 或 directory")

    try:
        root = Path(path)
        if not root.exists():
            return ToolResult(success=False, data="", error=f"路径不存在: {path}")
        if not root.is_dir():
            return ToolResult(success=False, data="", error=f"不是目录: {path}")

        safe_limit = max(1, min(int(limit or 100), 500))
        matches = []
        for current_root, dirnames, filenames in os.walk(root):
            names = []
            if file_type in {"any", "directory"}:
                names.extend((name, "directory") for name in dirnames)
            if file_type in {"any", "file"}:
                names.extend((name, "file") for name in filenames)

            for name, kind in names:
                if fnmatch(name, pattern):
                    matched = Path(current_root) / name
                    matches.append({
                        "path": str(matched),
                        "name": name,
                        "type": kind,
                    })
                    if len(matches) >= safe_limit:
                        return ToolResult(
                            success=True,
                            data={
                                "path": str(root),
                                "pattern": pattern,
                                "file_type": file_type,
                                "count": len(matches),
                                "limit": safe_limit,
                                "truncated": True,
                                "matches": matches,
                            },
                        )

        return ToolResult(
            success=True,
            data={
                "path": str(root),
                "pattern": pattern,
                "file_type": file_type,
                "count": len(matches),
                "limit": safe_limit,
                "truncated": False,
                "matches": matches,
            },
        )
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


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


def create_directory(dirpath: str, parents: bool = True, exist_ok: bool = False, mode: str = "755") -> ToolResult:
    """Create a directory. REQUIRES APPROVAL.

    Args:
        dirpath: Target directory path
        parents: Create missing parent directories if True
        exist_ok: Treat existing directory as success if True
        mode: Permission mode for newly created directories
    """
    error = _check_protected(dirpath)
    if error:
        return ToolResult(success=False, data="", error=error)

    if mode in ("777", "666"):
        return ToolResult(success=False, data="", error=f"权限 {mode} 过于宽松，存在安全风险")

    try:
        path = Path(dirpath)
        if path.exists() and not path.is_dir():
            return ToolResult(success=False, data="", error=f"目标已存在但不是目录: {dirpath}")
        if path.exists() and not exist_ok:
            return ToolResult(success=False, data="", error=f"目录已存在，如需视为成功请设置 exist_ok=true: {dirpath}")

        path.mkdir(mode=int(mode, 8), parents=parents, exist_ok=exist_ok)
        return ToolResult(success=True, data=f"已创建目录: {dirpath}")
    except FileExistsError:
        return ToolResult(success=False, data="", error=f"目录已存在，如需视为成功请设置 exist_ok=true: {dirpath}")
    except FileNotFoundError:
        return ToolResult(success=False, data="", error=f"父目录不存在: {Path(dirpath).parent}")
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
    error = _check_protected(destination)
    if error:
        return ToolResult(success=False, data="", error=error)

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
