"""Small MCP stdio client used by the OpsGuard Agent tool executor.

The client implements only the protocol surface OpsGuard currently needs:
initialize, tools/list, and tools/call. It is deliberately isolated behind the
tool executor so the existing local tool path remains available as a fallback.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.config import settings
from app.mcp_tools.process_tools import ToolResult

MCP_PROTOCOL_VERSION = "2025-06-18"


@dataclass
class MCPToolCallError(Exception):
    """Raised when the MCP server returns a JSON-RPC error or bad response."""

    message: str

    def __str__(self) -> str:
        return self.message


class MCPStdioClient:
    """One-shot stdio MCP client.

    A short-lived subprocess is slower than a pooled session, but it is a safe
    first integration step: no shared process lifecycle, no cross-request state,
    and no change to existing Agent concurrency semantics.
    """

    def __init__(
        self,
        command: str | None = None,
        args: list[str] | None = None,
        timeout: float | None = None,
    ) -> None:
        self.command = command or settings.mcp.command or sys.executable
        self.args = args if args is not None else list(settings.mcp.args)
        self.timeout = timeout if timeout is not None else settings.mcp.timeout
        self._next_id = 1

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP tools exposed by the configured server."""
        async with _MCPProcess(self.command, self.args, self.timeout) as proc:
            await proc.initialize()
            result = await proc.request("tools/list", {})
        tools = result.get("tools", [])
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Call an MCP tool and adapt the result to the local ToolResult shape."""
        async with _MCPProcess(self.command, self.args, self.timeout) as proc:
            await proc.initialize()
            result = await proc.request("tools/call", {"name": name, "arguments": arguments})
        return _tool_result_from_mcp(result)


class _MCPProcess:
    """Async context manager for one MCP stdio subprocess."""

    def __init__(self, command: str, args: list[str], timeout: float) -> None:
        self.command = command
        self.args = args
        self.timeout = timeout
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def __aenter__(self) -> "_MCPProcess":
        self.process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if not self.process:
            return
        if self.process.stdin:
            self.process.stdin.close()
            try:
                await self.process.stdin.wait_closed()
            except Exception:
                pass
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

        if self.process.stderr:
            try:
                stderr = await asyncio.wait_for(self.process.stderr.read(), timeout=0.2)
            except asyncio.TimeoutError:
                stderr = b""
            if stderr:
                logger.debug(f"MCP server stderr: {stderr.decode(errors='replace')[:1000]}")

    async def initialize(self) -> None:
        await self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "opsguard-agent",
                    "version": settings.app.version,
                },
            },
        )
        await self.notify("notifications/initialized", {})

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        response = await self._round_trip(message)
        if "error" in response:
            error = response["error"]
            if isinstance(error, dict):
                raise MCPToolCallError(str(error.get("message") or error))
            raise MCPToolCallError(str(error))
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        })

    async def _round_trip(self, message: dict[str, Any]) -> dict[str, Any]:
        await self._write(message)
        if not self.process or not self.process.stdout:
            raise MCPToolCallError("MCP process stdout is unavailable")
        try:
            raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise MCPToolCallError(f"MCP request timed out: {message.get('method')}") from exc
        if not raw:
            raise MCPToolCallError("MCP server closed stdout before responding")
        try:
            response = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise MCPToolCallError(f"Invalid MCP JSON response: {raw!r}") from exc
        if not isinstance(response, dict):
            raise MCPToolCallError("Invalid MCP response object")
        return response

    async def _write(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise MCPToolCallError("MCP process stdin is unavailable")
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        self.process.stdin.write(payload)
        await self.process.stdin.drain()


def _tool_result_from_mcp(result: dict[str, Any]) -> ToolResult:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return ToolResult(
            success=bool(structured.get("success", not result.get("isError", False))),
            data=structured.get("data", structured),
            error=structured.get("error"),
        )

    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            text = first.get("text")
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    return ToolResult(
                        success=bool(parsed.get("success", not result.get("isError", False))),
                        data=parsed.get("data", parsed),
                        error=parsed.get("error"),
                    )
                return ToolResult(success=not bool(result.get("isError", False)), data=text, error=None)

    return ToolResult(success=not bool(result.get("isError", False)), data=result, error=None)
