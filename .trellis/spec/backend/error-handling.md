# Error Handling

> How errors are handled in OpsGuard.

---

## Error Types

| Type | Usage |
|------|-------|
| `ToolResult(success=False, error=...)` | MCP tool execution failures |
| `SafetyCheckResult(is_safe=False)` | Security guardrail blocks |
| `WebSocket JSON error message` | Client-facing errors |
| Python exceptions | Internal errors (logged, not exposed) |

---

## Error Handling Patterns

### MCP Tools
Tools never raise exceptions. They return `ToolResult(success=False, error="message")`:
```python
def some_tool(arg: str) -> ToolResult:
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        if result.returncode != 0:
            return ToolResult(success=False, data="", error=result.stderr)
        return ToolResult(success=True, data=result.stdout)
    except Exception as e:
        return ToolResult(success=False, data="", error=str(e))
```

### Agent Graph
Errors in the Agent pipeline are caught and sent to the client:
```python
try:
    response = await run_agent(...)
except Exception as e:
    await websocket.send_json({"type": "error", "content": str(e)})
```

### API Endpoints
Use FastAPI's built-in exception handling. Don't catch generic exceptions in endpoints.

---

## Forbidden Patterns

- Never expose stack traces to the client
- Never swallow exceptions silently (always log)
- Never use bare `except:` without logging
- Never return 500 with internal details in production
