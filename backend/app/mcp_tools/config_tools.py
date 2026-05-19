"""Configuration file inspection MCP tools.

Atomic tools for reading and comparing system configuration files.
"""

import subprocess
import hashlib
from pathlib import Path

from app.mcp_tools.process_tools import ToolResult, command_error


def read_config_file(filepath: str) -> ToolResult:
    """Read a configuration file's content.

    Args:
        filepath: Path to the config file
    """
    # Safety: only allow reading config files, not arbitrary files
    allowed_prefixes = ["/etc/", "/var/lib/opsguard/", "/tmp/"]
    if not any(filepath.startswith(prefix) for prefix in allowed_prefixes):
        return ToolResult(
            success=False,
            data="",
            error=f"Access denied: can only read from {allowed_prefixes}",
        )

    try:
        path = Path(filepath)
        if not path.exists():
            return ToolResult(success=False, data="", error=f"File not found: {filepath}")

        # Safety: don't read binary or very large files
        size = path.stat().st_size
        if size > 1024 * 1024:  # 1MB limit
            return ToolResult(success=False, data="", error=f"File too large: {size} bytes")

        content = path.read_text(encoding="utf-8", errors="replace")
        return ToolResult(
            success=True,
            data={"content": content, "size": size, "path": filepath},
        )
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def check_config_syntax(filepath: str) -> ToolResult:
    """Check syntax validity of common config file types.

    Args:
        filepath: Path to the config file
    """
    try:
        if filepath.endswith((".conf", ".cfg")) and "nginx" in filepath:
            cmd = ["nginx", "-t", "-c", filepath]
        elif filepath.endswith(".service"):
            cmd = ["systemd-analyze", "verify", filepath]
        elif filepath.endswith((".yaml", ".yml")):
            cmd = ["python3", "-c", f"import yaml; yaml.safe_load(open('{filepath}'))"]
        elif filepath.endswith(".json"):
            cmd = ["python3", "-c", f"import json; json.load(open('{filepath}'))"]
        else:
            return ToolResult(
                success=True,
                data={
                    "checked": False,
                    "valid": None,
                    "message": "No syntax checker available for this file type",
                },
            )

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        if result.returncode == 0:
            return ToolResult(success=True, data={"checked": True, "valid": True, "message": "Syntax OK"})
        return ToolResult(
            success=True,
            data={
                "checked": True,
                "valid": False,
                "errors": result.stderr.strip() or result.stdout.strip(),
                "exit_code": result.returncode,
            },
        )
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def get_config_hash(filepath: str) -> ToolResult:
    """Get SHA256 hash of a config file (for drift detection).

    Args:
        filepath: Path to the config file
    """
    try:
        path = Path(filepath)
        if not path.exists():
            return ToolResult(success=False, data="", error=f"File not found: {filepath}")

        content = path.read_bytes()
        file_hash = hashlib.sha256(content).hexdigest()

        return ToolResult(
            success=True,
            data={"path": filepath, "sha256": file_hash, "size": len(content)},
        )
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def diff_config(filepath: str, baseline_path: str) -> ToolResult:
    """Compare a config file against a baseline version.

    Args:
        filepath: Current config file path
        baseline_path: Baseline/expected config file path
    """
    try:
        cmd = ["diff", "--unified=3", baseline_path, filepath]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        if result.returncode == 0:
            return ToolResult(success=True, data={"drift": False, "message": "Files are identical"})
        elif result.returncode == 1:
            return ToolResult(
                success=True,
                data={"drift": True, "diff": result.stdout.strip()},
            )
        else:
            return ToolResult(success=False, data="", error=result.stderr)
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))


def list_config_files(directory: str = "/etc") -> ToolResult:
    """List configuration files in a directory.

    Args:
        directory: Directory to scan (default: /etc)
    """
    allowed_dirs = ["/etc", "/var/lib/opsguard"]
    if not any(directory.startswith(d) for d in allowed_dirs):
        return ToolResult(success=False, data="", error="Access denied")

    try:
        cmd = ["find", directory, "-maxdepth", "2", "-name", "*.conf", "-o", "-name", "*.cfg", "-o", "-name", "*.yaml", "-o", "-name", "*.yml"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode not in (0, 1):
            return ToolResult(success=False, data="", error=command_error(result))

        files = [f for f in result.stdout.strip().split("\n") if f]
        return ToolResult(success=True, data={"files": files, "count": len(files)})
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
