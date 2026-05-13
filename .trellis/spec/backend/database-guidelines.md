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

---

## Common Mistakes

- Forgetting `await db.commit()` after INSERT/UPDATE
- Using `db.row_factory` without `aiosqlite.Row` (returns tuples by default)
- Not handling the case where `fetchone()` returns `None`
- Reusing `aiosqlite.Connection` objects across requests (causes "threads can only be started once" error)
- Forgetting to create tables with `IF NOT EXISTS` (breaks on restart)
- Not using parameterized queries (SQL injection risk)
