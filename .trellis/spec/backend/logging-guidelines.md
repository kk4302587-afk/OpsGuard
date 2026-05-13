# Logging Guidelines

> How logging is done in OpsGuard.

---

## Library

We use **loguru** (`from loguru import logger`). No stdlib logging.

---

## Log Levels

| Level | When to use |
|-------|-------------|
| `logger.debug()` | Detailed diagnostic info (tool args, LLM responses) |
| `logger.info()` | Normal operations (startup, tool loaded, DB init) |
| `logger.warning()` | Recoverable issues (classifier degraded, pattern invalid) |
| `logger.error()` | Failures that affect functionality (DB write failed, LLM call failed) |

---

## What to Log

- Service startup/shutdown
- Safety guardrail blocks (with matched pattern)
- Tool execution results (success/failure)
- LLM call failures and fallback attempts
- WebSocket connect/disconnect
- Knowledge save operations

---

## What NOT to Log

- API keys or tokens
- Full user messages (use truncation: `message[:100]`)
- Full LLM responses (use `[:200]` preview)
- File contents read by tools
- Passwords or credentials found in configs

---

## Format

Loguru auto-formats with timestamp, level, module, and message. No custom format needed.
```
2026-05-12 20:15:00.123 | INFO | app.safety.rule_engine:_load_patterns:47 - Rule engine loaded
```
