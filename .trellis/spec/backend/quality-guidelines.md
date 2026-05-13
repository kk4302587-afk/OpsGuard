# Quality Guidelines

> Code quality standards for OpsGuard backend.

---

## Required Patterns

- Type hints on all function signatures
- Docstrings on all public functions and classes
- `async def` for all I/O operations (DB, network, subprocess)
- `subprocess.run(..., timeout=N)` — always set timeout
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

---

## Testing

- Manual testing via API calls and browser
- `python -c "from app.main import app"` — smoke test for import chain
- Security rules tested via `test_rules.py` pattern (create, run, delete)
- TypeScript `npx tsc --noEmit` for frontend
