# Implement Change-Aware RCA

## Goal

Add a Linux-first, read-only "recent changes" signal to OpsGuard RCA so the
Agent can surface configuration, package, service, cron, firewall, and OpsGuard
backup changes that may explain a symptom. The output must be evidence-backed
and must not imply a root cause unless the data supports it.

## Requirements

- Add a read-only MCP tool for recent change collection.
- Collect recent changes from best-effort local sources:
  - service unit state and recent service-related journal entries,
  - common config file mtimes and hashes,
  - package manager history for apt, yum, dnf, or rpm where available,
  - cron/systemd timer metadata,
  - firewall command history where available,
  - OpsGuard backup/rollback manifest entries.
- The tool must return structured data with:
  - `window_hours`,
  - `changes`,
  - `source_status`,
  - `summary`.
- Each change should include:
  - `source`,
  - `change_type`,
  - `target`,
  - `timestamp`,
  - `detail`,
  - `confidence`.
- Register the tool as `RiskLevel.READ` under category `recent_changes`.
- Agent should automatically run the recent changes check after knowledge
  retrieval for normal substantive requests.
- Recent change results must be added to the prompt context and streamed as a
  trace event with structured evidence.
- Search failures or unavailable sources must be visible in `source_status`;
  they must not be reported as "no changes".
- Incident timeline should automatically capture the trace event through the
  existing trace-to-incident pipeline.

## Acceptance Criteria

- A normal Agent request emits a `recent_changes` trace event.
- If changes are found, the trace and LLM context include the observed changes.
- If no changes are found, the trace says the check executed and found none.
- If a source cannot be inspected, the response preserves source status instead
  of turning it into a fake no-change result.
- The recent changes tool is read-only and does not require approval.
- Regression tests cover tool output, Agent integration, and failure semantics.
- Existing truthfulness, runbook, rollback, knowledge, incident, and frontend
  build checks continue passing.

## Definition of Done

- Tests added/updated.
- Backend compile/import checks pass.
- Frontend build passes if touched.
- Backend spec updated with the recent-change evidence contract.
- Task changes committed without including unrelated dirty files.

## Technical Approach

Implement a new `backend/app/mcp_tools/recent_changes.py` module with
side-effect-free collectors. Use short subprocess timeouts and treat missing
commands/files as source-level unavailable statuses. Register
`get_recent_changes` in `tools_registry`.

Integrate the Agent with a new `recent_changes_node` between
`knowledge_retrieval` and `reasoning`. The node runs the registered read-only
tool directly, emits an evidence-aware trace event, and injects a compact
context block into LLM messages. This keeps the LLM aware of recent changes
without requiring it to choose the tool first.

## Decision (ADR-lite)

**Context**: Change-aware RCA can be implemented either as an optional tool the
LLM may call, or as an automatic read-only signal before reasoning.

**Decision**: Use an automatic read-only pre-reasoning check for this MVP.

**Consequences**: Operators get consistent recent-change visibility on every
substantive Agent request. The check must remain bounded and best-effort to
avoid slowing normal chat. Future tasks can add more targeted collectors or make
the check conditional on request classification.

## Out of Scope

- No write/destructive remediation.
- No package manager simulation or rollback.
- No broad filesystem crawling.
- No Kubernetes, Ansible, cloud audit, or external deployment integrations.
- No new frontend page.
- No automatic root-cause assertion from a recent change alone.

## Technical Notes

- Existing trace evidence helpers live in `backend/app/agent/trace_evidence.py`.
- Agent graph nodes live in `backend/app/agent/graph.py`.
- Tool registration lives in `backend/app/agent/tools_registry.py`.
- Incident timeline already records trace events from Agent/Runbook payloads.
- OpsGuard backup manifest can be inspected through
  `backend/app/mcp_tools/backup.py`.
