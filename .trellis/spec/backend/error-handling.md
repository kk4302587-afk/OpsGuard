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

### Approval Flow
If approval times out or is rejected, the tool call is skipped (not retried). The Agent receives a "REJECTED" tool result and adapts its plan.

---

## Lessons Learned

### Race Condition in Async Approval
**Problem**: If the approval Future is registered AFTER sending the request to the client, a fast client response can arrive before the Future exists.
**Solution**: Always `register_pending()` BEFORE `send_to_client(approval_request)`.

### aiosqlite Connection Reuse
**Problem**: `aiosqlite.Connection` objects cannot be reused across requests (thread start error).
**Solution**: Always use `async with aiosqlite.connect(path) as db:` per operation. Never store connections globally.

---

## Forbidden Patterns

- Never expose stack traces to the client
- Never swallow exceptions silently (always log)
- Never use bare `except:` without logging
- Never return 500 with internal details in production
