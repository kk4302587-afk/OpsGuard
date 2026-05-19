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
- Runbook persistence, health, validation, and replay bookkeeping must go
  through `app.agent.runbook_governance`. Do not create or update the
  `runbooks` table inline from API, Agent, or executor code.
- Write/destructive approval payloads must state preview and rollback coverage
  truthfully. Only file/directory operations with a real backup may claim
  backup-based rollback; service/process/package/firewall operations must not
  claim reliable automated rollback unless implemented by a real inverse action.
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

## Scenario: Runbook Governance Metadata

### 1. Scope / Trigger
- Trigger: any change to Runbook creation, listing, matching, replay, validation,
  or database initialization.
- Goal: saved Runbooks must expose reliability and freshness before users replay
  them.

### 2. Signatures
- Schema helper: `ensure_runbook_schema(db: aiosqlite.Connection) -> None`
- Save/update: `save_or_update_runbook(db, name, description, trigger_pattern, steps, session_id=None) -> tuple[str, bool]`
- Replay bookkeeping: `record_runbook_result(db, runbook_id, succeeded, failure_reason=None) -> None`
- API validation: `POST /api/runbooks/{runbook_id}/validate`

### 3. Contracts
- Runbook API responses include:
  - `version`
  - `success_count`
  - `failure_count`
  - `last_success`
  - `last_failure`
  - `last_failure_reason`
  - `staleness_status`
  - `updated_from_session_id`
  - `success_rate`
- `staleness_status` is one of `fresh`, `warning`, or `stale`.
- Validation is read-only. It may check tool existence, path existence, parent
  directories, and service availability, but it must not execute write or
  destructive steps.

### 4. Validation & Error Matrix
- Missing registered tool -> validation `invalid`, staleness `stale`.
- Repeated failures >= 3 -> staleness `stale`.
- Any recent failure -> staleness at least `warning`.
- No success in 30 days -> `warning`.
- No success in 90 days -> `stale`.
- Successful full replay -> increment `success_count` and set `last_success`.
- Partial failure/rejection/exception -> increment `failure_count`, set
  `last_failure`, and record the exact failure reason.

### 5. Good/Base/Bad Cases
- Good: Runbook replay fails at step 3 and stores `last_failure_reason` with the
  failing step and tool error.
- Base: new never-run Runbook is `fresh` unless a tool is missing.
- Bad: API manually runs `CREATE TABLE runbooks (...)` without governance columns.

### 6. Tests Required
- Schema migration from legacy Runbook table.
- Save/update increments `version` while preserving execution statistics.
- Replay success/failure updates counters truthfully.
- Validation detects missing tools or missing targets without executing steps.
- Frontend build verifies new health fields are typed and rendered.

### 7. Wrong vs Correct

#### Wrong
```python
await db.execute("UPDATE runbooks SET run_count = run_count + 1 WHERE id = ?", (runbook_id,))
```

#### Correct
```python
await record_runbook_result(
    db,
    runbook_id=runbook_id,
    succeeded=failed_step is None,
    failure_reason=abort_reason,
)
```

## Scenario: Rollback Visibility and Manual Restore

### 1. Scope / Trigger
- Trigger: any change to write/destructive tools, approval payloads, backup
  manager, or rollback APIs/tools.
- Goal: users can see whether rollback is actually available before approval
  and can restore backed-up file/directory changes through a controlled path.

### 2. Signatures
- Tool metadata fields:
  - `supports_preview: bool`
  - `preview_strategy: str`
  - `supports_rollback: bool`
  - `rollback_strategy: str`
- APIs:
  - `GET /api/backups?filepath=&limit=`
  - `POST /api/backups/{backup_id}/rollback`
- MCP tools:
  - `list_backups(filepath="", limit=20) -> ToolResult`
  - `rollback_backup(backup_id: str) -> ToolResult`

### 3. Contracts
- `rollback_backup` is `RiskLevel.DESTRUCTIVE`; it must go through the same
  approval path as other destructive tools.
- Approval request payloads include `preview_strategy`, `supports_rollback`,
  and `rollback_strategy`.
- A successful backed-up write emits a trace rollback point with backup id,
  target, strategy, created time, and restore availability.
- Service/process operations may include impact-only previews, but must say no
  reliable automated rollback exists.

### 4. Validation & Error Matrix
- Backup id missing -> rollback returns `success=False`.
- Backup already restored -> rollback returns `success=False`.
- Backup file missing -> rollback returns `success=False`.
- File write/delete with successful backup -> rollback point trace is emitted.
- File write/delete without existing target -> no backup id is claimed.

### 5. Good/Base/Bad Cases
- Good: writing `/etc/nginx/nginx.conf` creates a backup and trace shows its
  rollback id.
- Base: restarting nginx says impact-only preview and no reliable rollback.
- Bad: claiming a service restart can be rolled back by backup when no inverse
  action was executed.

### 6. Tests Required
- Backup list and restore operate on a real temporary file.
- `rollback_backup` is registered as destructive.
- Impact text distinguishes file backup rollback from service no-rollback.

### 7. Wrong vs Correct

#### Wrong
```python
impact_lines.append("Rollback: available")
```

#### Correct
```python
if tool_def.supports_rollback:
    impact_lines.append(f"Rollback: {tool_def.rollback_strategy} strategy")
else:
    impact_lines.append("Rollback: no reliable automated rollback will be claimed")
```

## Scenario: Incident Timeline Truthfulness

### 1. Scope / Trigger
- Trigger: any change to Agent, Runbook replay, trace payloads, incident APIs,
  or OpsReport incident aggregation.
- Goal: incident timelines must be a persisted operational record derived from
  real trace/evidence payloads, not a parallel narrative invented after the
  fact.

### 2. Signatures
- Schema helper: `ensure_incident_schema(db: aiosqlite.Connection) -> None`
- Create: `create_incident(session_id, problem_statement, source, metadata=None) -> str`
- Record: `record_incident_from_message(incident_id, session_id, message) -> str | None`
- Finalize: `finalize_incident(incident_id, final_summary, status=None) -> dict`
- APIs:
  - `GET /api/incidents?session_id=...`
  - `GET /api/incidents/{incident_id}`
  - `GET /api/incidents/{incident_id}/events`

### 3. Contracts
- Agent and Runbook executions create at most one incident per submitted
  operation.
- Timeline events are recorded from live `trace` and `approval_request`
  payloads, preserving structured evidence when present.
- A timeline event may claim execution only when its evidence says
  `execution_state: executed` or `failed` from a real tool/result path.
- Approval pending/rejected entries use `execution_state: skipped`.
- Final assistant responses include a compact incident reference with the
  incident id, status, event count, tool result count, failure count, and API
  endpoint for the timeline.
- OpsReport may aggregate incident counts, but it must not synthesize missing
  incident events.

### 4. Validation & Error Matrix
- Tool result success trace -> incident event keeps `execution_state: executed`.
- Tool result failure trace -> incident event keeps `execution_state: failed`
  and failure details.
- Knowledge no-match -> incident event is a successful executed search with
  zero matches, not a failure.
- Knowledge/search exception -> failure event, not "no related history".
- Incident persistence failure should be logged and must not interrupt the real
  Agent/Runbook execution.

### 5. Good/Base/Bad Cases
- Good: Runbook step execution event is copied into `incident_events` with the
  same evidence source and observed result.
- Base: read-only diagnostic request creates an incident with planning,
  knowledge, and response events.
- Bad: creating a timeline item that says "service restarted" when no service
  tool returned a real success result.

### 6. Tests Required
- Store-level test for schema, event recording, evidence serialization, and
  finalization.
- Agent integration test proving a normal request creates one incident and
  appends an incident reference.
- Runbook integration test proving step traces become incident events.

## Scenario: Change-Aware RCA Evidence

### 1. Scope / Trigger
- Trigger: any change to recent-change collectors, Agent RCA pre-reasoning
  nodes, trace evidence payloads, or tools in category `recent_changes`.
- Goal: recent system changes should be surfaced as candidate RCA evidence
  without implying causality or hiding unavailable data sources.

### 2. Signatures
- MCP tool: `get_recent_changes(window_hours=24, limit=30) -> ToolResult`
- Agent graph node: `recent_changes_node(state: AgentState) -> dict`
- Context field: `recent_changes_hint: str`

### 3. Contracts
- `get_recent_changes` is read-only and registered as `RiskLevel.READ`.
- The tool returns structured `changes`, `source_status`, `summary`, and
  `window_hours`.
- Each change includes `source`, `change_type`, `target`, `timestamp`,
  `detail`, and `confidence`.
- Missing commands, missing files, permission issues, and command failures are
  represented in `source_status`; they must not be collapsed into "no changes".
- Agent automatically emits a `recent_changes` trace event before LLM
  reasoning and injects a compact context block.
- Recent changes are candidate evidence only. The LLM must not claim root cause
  from recency alone without corroborating checks.

### 4. Validation & Error Matrix
- Collector found changes -> trace `event_type: success`,
  `execution_state: executed`, source `get_recent_changes`.
- Collector found zero changes with all sources inspected -> success with an
  explicit zero-change summary.
- Collector source unavailable or failed -> success may still be returned, but
  `source_status` must show `unavailable`, `partial`, or `failed`.
- Tool-level exception -> `ToolResult(success=False)` or Agent trace failure;
  never report "no recent changes".

### 5. Good/Base/Bad Cases
- Good: `/etc/nginx/nginx.conf` mtime within the lookback window appears as a
  `config_file_modified` change with timestamp and hash.
- Base: package history logs do not exist; `source_status.package_history` says
  unavailable.
- Bad: journalctl fails and the Agent tells the user no service changes were
  found without mentioning the failed source.

### 6. Tests Required
- Tool test for a real temp config mtime.
- Tool test proving failed sources are preserved in `source_status`.
- Agent node test proving trace evidence and prompt context are produced.

## Scenario: Incident-Memory Knowledge Entries

### 1. Scope / Trigger
- Trigger: any change to knowledge schema, knowledge save/search, Agent
  knowledge extraction, or knowledge retrieval trace formatting.
- Goal: historical experience should be structured incident memory with
  evidence, applicability, and reuse safety, not vague free-form advice.

### 2. Signatures
- Schema helper: `ensure_knowledge_schema(db: aiosqlite.Connection) -> None`
- Save: `save_resolution(problem_signature, diagnosis_path, solution, tools_used, incident_memory=None)`
- Search: `knowledge_store.search(query, limit=5) -> list[dict]`
- Structured fields:
  - `symptoms`
  - `root_cause`
  - `evidence`
  - `successful_actions`
  - `failed_attempts`
  - `validation_method`
  - `applicability_conditions`
  - `non_applicability_conditions`
  - `source_incident_id`
  - `confidence`

### 3. Contracts
- Existing `knowledge_entries` tables must migrate with safe
  `ALTER TABLE ... ADD COLUMN`; legacy rows remain searchable.
- JSON/list fields are stored as JSON text and parsed at API/search boundaries.
- Search scoring includes both legacy text fields and structured incident-memory
  fields.
- Search results include `match_score`, `match_reason`, structured fields, and
  `safe_to_reuse`.
- `safe_to_reuse` requires validation and applicability data; historical write
  actions still require fresh checks and approval.
- Knowledge search failures must raise/emit failure and must not be reported as
  "no related history".

### 4. Validation & Error Matrix
- Legacy table -> migration adds structured columns without dropping rows.
- Structured save -> fields persist and update on repeated problem signatures.
- Legacy row search -> returns empty structured lists/defaults, not crashes.
- Search backend failure -> `KnowledgeSearchError`, trace failure.
- Historical write action -> prompt says it is reference only until fresh
  execution and approval.

### 5. Good/Base/Bad Cases
- Good: nginx 502 history returns root cause, evidence, validation method,
  applicability, and match reason.
- Base: old `problem/solution` row still matches and returns
  `safe_to_reuse: false`.
- Bad: Agent directly replays a historical `restart_service` action without
  fresh tool execution and approval.

### 6. Tests Required
- Legacy schema migration test.
- Structured save/search test.
- Legacy compatibility search test.
- Agent knowledge trace formatting test.

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
