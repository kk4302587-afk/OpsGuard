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
- Evidence-aware trace events must keep execution truth explicit. When a trace
  claim is based on tool output, knowledge retrieval, verification, or an
  inference, include structured evidence fields and set `execution_state` to
  `executed`, `failed`, `skipped`, or `inferred` truthfully.
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

## Scenario: Evidence-Aware Trace Events

### 1. Scope / Trigger
- Trigger: any change to Agent, Runbook, audit trace API, or TracePanel payloads.
- Goal: users must know whether a trace claim came from real execution,
  knowledge retrieval, user approval, skipped work, or inference.

### 2. Signatures
- Backend helper: `trace_event(phase, event_type, content, evidence=None, metadata=None) -> dict`
- Evidence fields:
  - `claim: str`
  - `evidence_type: command | log | config | metric | topology | knowledge | user input`
  - `source: str`
  - `observed: str`
  - `confidence: low | medium | high`
  - `execution_state: executed | inferred | skipped | failed`
  - `failure_reason?: str`
  - `next_check?: str`

### 3. Contracts
- Live WebSocket trace events expose evidence fields at the top level.
- Persisted audit metadata should mirror evidence under `metadata.evidence`
  when the trace event is stored.
- `/api/sessions/{session_id}/trace` must preserve backward compatibility:
  old rows without evidence still return `timestamp`, `phase`, `event_type`,
  `content`, and optional `metadata`.

### 4. Validation & Error Matrix
- `ToolResult.success is True` -> `execution_state: executed`.
- `ToolResult.success is False` -> `event_type: failure`,
  `execution_state: failed`, and `failure_reason` from the tool error.
- Approval pending/rejected before execution -> `execution_state: skipped`.
- LLM-only planning/final response -> `execution_state: inferred`.
- Knowledge search exception -> `event_type: failure`, not "no history".

### 5. Good/Base/Bad Cases
- Good: `execution` failure for `restart_service` includes `source:
  restart_service`, observed stderr/error, and `execution_state: failed`.
- Base: read-only knowledge search with zero hits includes `execution_state:
  executed` and observed "no matching entries".
- Bad: rendering "执行成功" or green evidence when the tool returned
  `success=False`.

### 6. Tests Required
- Agent trace truthfulness test must assert failure evidence fields, not only
  human text.
- Runbook replay tests must assert successful execution events include
  `execution_state: executed` and real tool source.
- Frontend type-check/build must pass after adding new trace fields.

### 7. Wrong vs Correct

#### Wrong
```python
await send_to_client({"type": "trace", "phase": "execution", "event_type": "success", "content": "执行成功"})
```

#### Correct
```python
await send_to_client(trace_event(
    phase="execution",
    event_type="failure",
    content=f"执行失败: {tool_name} - {failure_message}",
    evidence=tool_result_evidence(
        tool_name=tool_name,
        tool_args=tool_args,
        tool_def=tool_def,
        result=result,
    ),
))
```

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
