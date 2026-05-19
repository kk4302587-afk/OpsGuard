# Implement Incident Memory Knowledge

## Goal

Upgrade OpsGuard knowledge entries from a loose `problem/diagnosis/solution`
record into structured incident memory that can explain why a historical case
matched, what evidence supported it, and when it is safe to reuse.

## Requirements

- Extend `knowledge_entries` with backward-compatible structured fields:
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
- Add a schema helper/migration path that safely `ALTER TABLE ADD COLUMN` for
  existing databases.
- Keep old rows readable and searchable.
- Update `save_resolution` to accept optional structured incident-memory data.
- Update LLM extraction prompt to request structured incident-memory JSON.
- Save structured memory only when real tool calls existed; do not store
  inferred-only chat as executed evidence.
- Search should score against structured fields as well as legacy fields.
- Search results should include:
  - `match_score`
  - `match_reason`
  - structured memory fields
  - `safe_to_reuse`: true only when validation/applicability data exists and
    there are no non-applicability warnings that block reuse.
- Agent knowledge retrieval trace and prompt context should show root cause,
  evidence, validation, applicability, non-applicability, and reuse safety.
- Knowledge API list/search should return structured fields as parsed JSON
  where possible.

## Acceptance Criteria

- Legacy `knowledge_entries` schemas migrate without dropping old data.
- Saving a resolution with structured fields persists and updates them.
- Searching a repeated nginx-style problem returns structured root cause,
  evidence, validation method, match reason, and safe reuse flag.
- A search backend failure still emits a failure trace, not "no history".
- Agent prompt context warns that historical write actions must not be reused
  without fresh checks and approval.
- Existing knowledge retrieval, incident, trace truthfulness, runbook, rollback,
  and frontend build checks continue passing.

## Definition of Done

- Focused regression tests for migration, structured save/search, legacy
  compatibility, and Agent knowledge trace formatting.
- Backend compile/import checks pass.
- Frontend build passes if touched.
- Backend spec updated with the incident-memory knowledge contract.
- Task changes committed without unrelated dirty files.

## Technical Approach

Add `ensure_knowledge_schema(db)` and serialization helpers in
`app.knowledge.store`. Store structured list/dict fields as JSON text and parse
them at API/search boundaries. Keep scoring simple and deterministic by adding
structured fields into the existing candidate text and generating a compact
`match_reason` from shared terms plus structured field hits.

Update `_extract_resolution_summary` to accept both legacy three-field JSON and
the richer schema, so old test stubs and future model variants remain
compatible. Knowledge retrieval formatting should be compact enough for the LLM
context and TracePanel, and must say historical write actions require fresh
execution and approval.

## Decision (ADR-lite)

**Context**: Incident memory could either be a new table linked to incidents or
a backward-compatible extension of `knowledge_entries`.

**Decision**: Extend `knowledge_entries` for the MVP.

**Consequences**: Retrieval and existing API behavior stay simple. Future work
can split high-volume evidence into separate tables after the schema proves its
value.

## Out of Scope

- No vector database or embedding search in this task.
- No new knowledge management page.
- No automatic execution of historical write actions.
- No migration tool beyond safe `ALTER TABLE ADD COLUMN`.
- No postmortem draft generation.

## Technical Notes

- Knowledge store lives in `backend/app/knowledge/store.py`.
- Agent retrieval/save flow lives in `backend/app/agent/graph.py`.
- Knowledge API lives in `backend/app/api/knowledge.py`.
- Incident records are already persisted by `backend/app/incidents/store.py`.
