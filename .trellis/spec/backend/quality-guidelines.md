# Quality Guidelines

> Code quality standards for OpsGuard backend.

---

## Required Patterns

- Type hints on all function signatures
- Docstrings on all public functions and classes
- `async def` for all I/O operations (DB, network, subprocess)
- `subprocess.run(..., timeout=N)` — always set timeout
- Check `CompletedProcess.returncode` before returning `ToolResult(success=True)` for subprocess-backed tools. Handle documented non-zero data cases explicitly.
- Agent/runbook trace success must follow the tool's real `ToolResult.success`
  value. A tool function returning normally with `success=False` is still an
  execution failure, not a successful trace event.
- Runbook replay traces must include a user-facing execution plan, per-step
  purpose/risk/result summaries, and keep raw tool calls as secondary technical
  detail. Do not make users infer intent from tool names alone.
- `ToolResult` return type for all MCP tools
- Risk level annotation for all registered tools

---

## Forbidden Patterns

| Pattern | Why | Alternative |
|---------|-----|-------------|
| `shell=True` in subprocess | Command injection risk | Use `args` list |
| Bare `except:` | Hides bugs | `except Exception as e:` + log |
| Global mutable state | Thread safety | Use function-scoped or class instances |
| `os.system()` | No output capture, injection risk | `subprocess.run()` |
| Hardcoded paths | Not portable | Use `config.yaml` or `Path` |
| `print()` for logging | No levels, no format | `logger.info()` |

---

## Security Requirements

- All file paths must be validated against allowed prefixes
- All subprocess commands must have timeout
- No user input directly interpolated into shell commands
- API keys loaded from config, never hardcoded
- Protected paths list checked before any write operation
- Before/after change diffs must use a live pre-execution snapshot captured immediately before the write tool runs. Never hardcode "Before" values from assumptions.
- Write-completion hallucination guards must consider the user's current write intent, not only completion-looking words in the final response. Read-only analysis may legitimately describe system state with phrases such as "已启动".
- Knowledge retrieval must distinguish a real no-match result from search
  failure. Do not catch database/search exceptions and report them as "no
  related history".
- Firewall writes must verify reload/runtime state before returning success.
- Inferred topology relationships must be marked with `inferred: true`; observed runtime relationships should use `inferred: false`.

---

## Testing

- Manual testing via API calls and browser
- `python -c "from app.main import app"` — smoke test for import chain
- `python backend/test_write_guard.py` — focused regression for write-completion guard false positives
- `python backend/test_fake_success_outputs.py` — focused regression for fake success / inferred-output handling
- `python backend/test_knowledge_retrieval.py` — focused regression for DB-backed knowledge retrieval semantics
- `python backend/test_agent_trace_truthfulness.py` — focused regression for Agent trace success/failure truthfulness
- `python backend/test_runbook_visibility.py` — focused regression for Runbook replay plan/result readability
- Security rules tested via `test_rules.py` pattern (create, run, delete)
- TypeScript `npx tsc --noEmit` for frontend
