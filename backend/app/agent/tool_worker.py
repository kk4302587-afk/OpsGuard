"""Subprocess worker for isolated OpsGuard tool execution.

The worker is intentionally small: it accepts one JSON request on stdin,
executes a registered tool, and writes one JSON response on stdout. Approval,
policy checks, rollback preparation, and audit logging stay in the parent
process.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any

from app.agent.tools_registry import tools_registry


def _serialize_result(result: Any) -> dict[str, Any]:
    if hasattr(result, "__dict__"):
        data = dict(result.__dict__)
    elif isinstance(result, dict):
        data = dict(result)
    else:
        data = {"success": True, "data": result, "error": None}

    return {
        "success": bool(data.get("success", True)),
        "data": data.get("data", data),
        "error": data.get("error"),
    }


def _error(message: str) -> dict[str, Any]:
    return {"success": False, "data": "", "error": message}


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name") or "")
    tool_args = payload.get("tool_args") or {}
    if not tool_name:
        return _error("tool_name is required")
    if not isinstance(tool_args, dict):
        return _error("tool_args must be an object")

    tool_def = tools_registry.get_tool(tool_name)
    if not tool_def:
        return _error(f"Unknown tool: {tool_name}")

    try:
        return _serialize_result(tool_def.function(**tool_args))
    except Exception as exc:
        return _error(str(exc))


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            response = _error("worker payload must be an object")
        else:
            # Tool internals may log to stdout through third-party libraries.
            # Keep stdout reserved for the response envelope.
            with contextlib.redirect_stdout(sys.stderr):
                response = handle(payload)
    except Exception as exc:
        response = _error(str(exc))

    sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
