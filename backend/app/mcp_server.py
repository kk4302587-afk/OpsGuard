"""Minimal MCP stdio server for OpsGuard tools.

This module exposes the existing in-process OpsGuard tool registry through the
MCP JSON-RPC tool surface. It intentionally keeps approval, risk checks, and
agent policy outside the server; those remain enforced by the Agent before a
tool call is sent here.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from app.agent.tools_registry import tools_registry
from app.config import settings

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-06-18"


def _json_default(value: Any) -> Any:
    """Serialize dataclasses and enums without leaking Python reprs."""
    if hasattr(value, "__dict__"):
        return value.__dict__
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _tool_to_mcp(tool_def) -> dict[str, Any]:
    """Convert an internal ToolDefinition to an MCP Tool object."""
    return {
        "name": tool_def.name,
        "title": tool_def.display_name or tool_def.name,
        "description": tool_def.description,
        "inputSchema": tool_def.parameters or {"type": "object", "properties": {}},
        "_meta": {
            "category": tool_def.category,
            "risk_level": tool_def.risk_level.value,
            "display_name": tool_def.display_name or tool_def.name,
            "supports_preview": tool_def.supports_preview,
            "preview_strategy": tool_def.preview_strategy,
            "supports_rollback": tool_def.supports_rollback,
            "rollback_strategy": tool_def.rollback_strategy,
        },
    }


def _result_to_structured(result: Any) -> dict[str, Any]:
    """Normalize a local tool return value for MCP structuredContent."""
    if hasattr(result, "__dict__"):
        return dict(result.__dict__)
    if isinstance(result, dict):
        return result
    return {"success": True, "data": result, "error": None}


def _content_text(structured: dict[str, Any]) -> str:
    """Return compact textual content alongside structuredContent."""
    return json.dumps(structured, ensure_ascii=False, default=_json_default)


def _handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
    client_version = params.get("protocolVersion") if isinstance(params, dict) else None
    return {
        "protocolVersion": client_version or MCP_PROTOCOL_VERSION,
        "capabilities": {
            "tools": {
                "listChanged": False,
            },
        },
        "serverInfo": {
            "name": "opsguard-mcp-server",
            "version": settings.app.version,
        },
    }


def _handle_tools_list() -> dict[str, Any]:
    tools = [
        _tool_to_mcp(tool_def)
        for _, tool_def in sorted(tools_registry._tools.items(), key=lambda item: item[0])
    ]
    return {"tools": tools}


def _handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise ValueError("tools/call params must be an object")

    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str) or not name:
        raise ValueError("tools/call params.name is required")
    if not isinstance(arguments, dict):
        raise ValueError("tools/call params.arguments must be an object")

    tool_def = tools_registry.get_tool(name)
    if not tool_def:
        raise LookupError(f"Unknown tool: {name}")

    result = tool_def.function(**arguments)
    structured = _result_to_structured(result)
    is_error = not bool(structured.get("success", True))
    return {
        "content": [
            {
                "type": "text",
                "text": _content_text(structured),
            }
        ],
        "structuredContent": structured,
        "isError": is_error,
    }


def _success_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if request_id is None:
        # Notifications do not receive JSON-RPC responses. The initialized
        # notification is accepted implicitly.
        return None

    try:
        if method == "initialize":
            return _success_response(request_id, _handle_initialize(params))
        if method == "tools/list":
            return _success_response(request_id, _handle_tools_list())
        if method == "tools/call":
            return _success_response(request_id, _handle_tools_call(params))
        if method == "ping":
            return _success_response(request_id, {})
        return _error_response(request_id, -32601, f"Method not found: {method}")
    except LookupError as exc:
        return _error_response(request_id, -32602, str(exc))
    except ValueError as exc:
        return _error_response(request_id, -32602, str(exc))
    except Exception as exc:
        return _error_response(
            request_id,
            -32603,
            str(exc),
            {"traceback": traceback.format_exc()},
        )


def serve_stdio() -> None:
    """Serve newline-delimited JSON-RPC messages on stdin/stdout."""
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            response = _error_response(None, -32700, f"Parse error: {exc}")
        else:
            if not isinstance(message, dict):
                response = _error_response(None, -32600, "Invalid request")
            else:
                response = _handle_request(message)

        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False, default=_json_default) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    serve_stdio()
