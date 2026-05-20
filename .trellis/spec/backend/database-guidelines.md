# Database Guidelines

> Database patterns and conventions in OpsGuard.

---

## Library

**aiosqlite** — async SQLite wrapper. No ORM.

---

## Connection Pattern

Always use `async with` for connections (auto-closes):
```python
async with aiosqlite.connect(get_knowledge_db_path()) as db:
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT ...", (param,))
    rows = await cursor.fetchall()
```

Never reuse connection objects across requests.

---

## Databases

| Database | Path | Purpose |
|----------|------|---------|
| knowledge.db | `./data/knowledge.db` | Sessions, messages, knowledge entries |
| audit.db | `./data/audit.db` | Audit trail logs |

---

## Naming Conventions

- Tables: `snake_case` plural (`knowledge_entries`, `audit_logs`, `sessions`)
- Columns: `snake_case` (`session_id`, `created_at`)
- Indexes: `idx_{table}_{column}` (`idx_audit_session`)

---

## Schema Changes

No migration tool. Schema is defined in `database.py:init_db()` using `CREATE TABLE IF NOT EXISTS`. To add columns, use `ALTER TABLE ... ADD COLUMN` with a default value.

## Scenario: Replaying Session Trace After Reconnect

### 1. Scope / Trigger
- Trigger: `/api/sessions/{session_id}/trace` is a cross-layer recovery API for UI reconnects.
- Applies when trace evidence may exist in both `audit.db.audit_logs` and `knowledge.db.incident_events`.

### 2. Signatures
- `audit_logs(session_id, timestamp, phase, event_type, content, metadata)`
- `incident_events(session_id, timestamp, phase, event_type, title, detail, evidence, metadata)`
- API response: `{ "trace": TraceEvent[] }`, where each event has `timestamp`, `phase`, `event_type`, `content`, `metadata`, and optional evidence fields.

### 3. Contracts
- `incident_events` are derived from actual trace payloads emitted by Agent/Runbook execution and should be included when replaying a session.
- `audit_logs` are legacy/coarse checkpoints and should still be included for compatibility.
- `problem_statement` incident phase maps to `input_received` for frontend trace display.
- Evidence fields from `incident_events.evidence` or `metadata.evidence` must be lifted to top-level response fields.

### 4. Validation & Error Matrix
- Empty session -> return `{"trace": []}`.
- Duplicate exact `(phase, event_type, content)` rows -> return only one.
- Invalid JSON metadata/evidence -> ignore the malformed value and keep the trace row.
- Mixed audit and incident rows -> sort chronologically by `timestamp`.

### 5. Good/Base/Bad Cases
- Good: user disconnects while Agent runs; `/trace` recovers incident timeline events missed by WebSocket.
- Base: old session has only `audit_logs`; `/trace` still returns audit rows.
- Bad: reading only `audit_logs`, because many live trace payloads are only complete in incident timeline storage.

### 6. Tests Required
- `python backend/test_session_trace_replay.py` must assert incident evidence is returned through the session trace API.
- Existing incident timeline tests must continue to pass.

### 7. Wrong vs Correct
#### Wrong
```python
SELECT timestamp, phase, event_type, content, metadata
FROM audit_logs
WHERE session_id = ?
```

#### Correct
```python
trace = await _load_incident_trace(session_id)
trace.extend(await _load_audit_trace(session_id))
return {"trace": _dedupe_and_sort_trace(trace)}
```

---

## Common Mistakes

- Forgetting `await db.commit()` after INSERT/UPDATE
- Using `db.row_factory` without `aiosqlite.Row` (returns tuples by default)
- Not handling the case where `fetchone()` returns `None`
- Reusing `aiosqlite.Connection` objects across requests (causes "threads can only be started once" error)
- Forgetting to create tables with `IF NOT EXISTS` (breaks on restart)
- Not using parameterized queries (SQL injection risk)
