"""Tool execution adapter for local and MCP-backed tool calls."""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import sys
from typing import Any

from loguru import logger

from app.agent.mcp_client import MCPStdioClient
from app.agent.tools_registry import RiskLevel, ToolDefinition, tools_registry
from app.config import settings
from app.mcp_tools.process_tools import ToolResult


async def get_tools_for_llm() -> list[dict[str, Any]]:
    """Return tool schemas for LLM tool calling.

    With MCP enabled this discovers tools via MCP `tools/list`; otherwise it
    uses the in-process registry exactly as before.
    """
    if not settings.mcp.enabled:
        return tools_registry.get_all_tools_for_llm()

    try:
        mcp_tools = await MCPStdioClient().list_tools()
        return [
            _mcp_tool_for_llm(tool)
            for tool in mcp_tools
            if tools_registry.get_tool(str(tool.get("name", "")))
        ]
    except Exception as exc:
        if not settings.mcp.fallback_to_local:
            raise
        logger.warning(f"MCP tools/list failed, falling back to local registry: {exc}")
        return tools_registry.get_all_tools_for_llm()


async def execute_tool(tool_name: str, tool_args: dict[str, Any], tool_def: ToolDefinition | None = None) -> Any:
    """Execute a tool using the configured transport.

    Local direct calls remain the default. When `settings.mcp.enabled` is true,
    execution goes through the MCP stdio client and optionally falls back to the
    local function path if the MCP call fails.
    """
    tool_def = tool_def or tools_registry.get_tool(tool_name)
    if not tool_def:
        raise ValueError(f"Unknown tool: {tool_name}")

    if _should_use_worker(tool_def):
        return await _execute_tool_in_worker(tool_name, tool_args, tool_def)

    if not settings.mcp.enabled:
        return tool_def.function(**tool_args)

    try:
        return await MCPStdioClient().call_tool(tool_name, tool_args)
    except Exception as exc:
        if not settings.mcp.fallback_to_local:
            raise
        logger.warning(f"MCP tool call failed, falling back to local execution: {tool_name}: {exc}")
        return tool_def.function(**tool_args)


def _should_use_worker(tool_def: ToolDefinition) -> bool:
    if not settings.execution.sandbox_enabled:
        return False
    if settings.execution.sandbox_for_read_tools:
        return True
    return tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE)


async def _execute_tool_in_worker(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_def: ToolDefinition,
) -> ToolResult:
    command = _worker_command()
    cwd = settings.execution.worker_cwd or str(_backend_root())
    env = dict(os.environ)
    env["PYTHONPATH"] = _pythonpath_with_backend(env.get("PYTHONPATH", ""))

    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )

    payload = json.dumps(
        {
            "tool_name": tool_name,
            "tool_args": tool_args,
            "risk_level": tool_def.risk_level.value,
        },
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(payload),
            timeout=max(1, int(settings.execution.timeout or 30) + 5),
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return ToolResult(success=False, data="", error=f"Sandbox worker timed out: {tool_name}")

    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        return ToolResult(
            success=False,
            data="",
            error=stderr_text or f"Sandbox worker exited with code {proc.returncode}",
        )

    try:
        data = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        return ToolResult(
            success=False,
            data="",
            error=f"Invalid sandbox worker response: {exc}; stderr={stderr_text[:500]}",
        )

    if not isinstance(data, dict):
        return ToolResult(success=False, data="", error="Invalid sandbox worker response object")
    return ToolResult(
        success=bool(data.get("success", False)),
        data=data.get("data", ""),
        error=data.get("error"),
    )


def _worker_command() -> list[str]:
    python = settings.execution.worker_python or sys.executable
    command = [python, "-m", "app.agent.tool_worker"]
    run_as_user = str(settings.execution.run_as_user or "").strip()
    if not run_as_user or run_as_user == _current_username():
        return command

    try:
        pwd.getpwnam(run_as_user)
    except KeyError:
        if settings.execution.require_run_as_user:
            raise RuntimeError(f"Configured run_as_user does not exist: {run_as_user}")
        logger.warning(f"Configured run_as_user does not exist, running worker as current user: {run_as_user}")
        return command

    return ["sudo", "-n", "-u", run_as_user, "--", *command]


def _backend_root() -> str:
    return str(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


def _pythonpath_with_backend(existing: str) -> str:
    root = _backend_root()
    if not existing:
        return root
    parts = existing.split(os.pathsep)
    return existing if root in parts else os.pathsep.join([root, existing])


def _current_username() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        return ""


def _mcp_tool_for_llm(tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("name", ""))
    local_tool = tools_registry.get_tool(name)
    return {
        "name": name,
        "description": str(tool.get("description") or getattr(local_tool, "description", "")),
        "parameters": tool.get("inputSchema")
        or getattr(local_tool, "parameters", None)
        or {"type": "object", "properties": {}},
    }
