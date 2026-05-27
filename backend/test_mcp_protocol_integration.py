"""Regression checks for OpsGuard's MCP protocol bridge."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent.tool_executor import execute_tool, get_tools_for_llm
from app.agent.tools_registry import tools_registry
from app.config import settings


def _send(proc: subprocess.Popen, message: dict) -> dict:
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    raw = proc.stdout.readline()
    assert raw, "MCP server did not respond"
    return json.loads(raw)


def test_mcp_stdio_server_lists_and_calls_tools() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_server"],
        cwd=Path(__file__).parent,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        init = _send(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        })
        assert init["result"]["capabilities"]["tools"]["listChanged"] is False

        tools = _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in tools["result"]["tools"]}
        assert "list_directory" in names
        assert "read_file" in names

        called = _send(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_directory", "arguments": {"path": "."}},
        })
        result = called["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["success"] is True
        assert "entries" in result["structuredContent"]["data"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_tool_executor_defaults_to_local_path() -> None:
    previous = settings.mcp.enabled
    settings.mcp.enabled = False
    try:
        tool_def = tools_registry.get_tool("list_directory")
        result = asyncio.run(execute_tool("list_directory", {"path": "."}, tool_def))
        assert result.success is True
        assert "entries" in result.data
    finally:
        settings.mcp.enabled = previous


def test_llm_tool_schema_defaults_to_local_registry() -> None:
    previous = settings.mcp.enabled
    settings.mcp.enabled = False
    try:
        tools = asyncio.run(get_tools_for_llm())
        names = {tool["name"] for tool in tools}
        assert "list_directory" in names
        assert "read_file" in names
    finally:
        settings.mcp.enabled = previous
