"""Tool execution adapter for local and MCP-backed tool calls."""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.agent.mcp_client import MCPStdioClient
from app.agent.tools_registry import ToolDefinition, tools_registry
from app.config import settings


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

    if not settings.mcp.enabled:
        return tool_def.function(**tool_args)

    try:
        return await MCPStdioClient().call_tool(tool_name, tool_args)
    except Exception as exc:
        if not settings.mcp.fallback_to_local:
            raise
        logger.warning(f"MCP tool call failed, falling back to local execution: {tool_name}: {exc}")
        return tool_def.function(**tool_args)


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
