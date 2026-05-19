# Implement Incident Timeline Object

## Background

OpsGuard currently persists chat messages and audit trace events, but it does
not have a first-class incident object. A troubleshooting session should produce
structured operational memory: problem statement, checks, hypotheses, approvals,
changes, evidence, verification, final status, and follow-up items.

This task implements the P1 "Incident Timeline / Incident Object" MVP from
`docs/ops-innovation-roadmap-prd.md`.

## Scope

- Create incident records for substantive operations requests.
- Record incident timeline events alongside agent/runbook execution.
- Expose incident data through API endpoints.
- Add a compact incident summary to final assistant responses.
- Keep the implementation compatible with existing sessions, messages, audit
  logs, trace events, knowledge saving, and OpsReport.

## Functional Requirements

- Add tables:
  - `incidents`
  - `incident_events`
- Incident fields:
  - `id`
  - `session_id`
  - `status`: `open`, `resolved`, `failed`
  - `problem_statement`
  - `created_at`
  - `updated_at`
  - `final_summary`
  - `followups`
- Incident event fields:
  - `id`
  - `incident_id`
  - `session_id`
  - `timestamp`
  - `event_type`
  - `phase`
  - `title`
  - `detail`
  - `evidence`
  - `metadata`
- Create an incident when an Agent or Runbook task starts for a substantive user
  request or direct runbook execution.
- Record timeline events for:
  - problem statement,
  - knowledge retrieval,
  - planning,
  - tool call start,
  - tool result success/failure,
  - approval request/response,
  - backup/rollback points,
  - verification,
  - final response.
- Final assistant response should include a compact incident summary reference
  with incident id, status, checks/changes count, and where to view the timeline.
- Add API endpoints:
  - `GET /api/incidents?session_id=...`
  - `GET /api/incidents/{incident_id}`
  - `GET /api/incidents/{incident_id}/events`
- OpsReport can include incident counts and recent incidents.

## Non-Goals

- Do not build a new full incident-management page.
- Do not implement automated postmortem drafts in this task.
- Do not replace existing audit logs; incident timeline should reuse and
  complement them.
- Do not auto-run write actions from incident logic.

## Acceptance Criteria

- A normal Agent request creates one incident linked to the session.
- Trace/tool events create structured timeline events with evidence when
  available.
- Runbook execution creates/updates an incident and records step outcomes.
- Final assistant message includes a concise incident summary block.
- API returns incident detail and ordered events.
- Existing tests continue passing.
- New regression tests cover incident creation, event recording, finalization,
  API serialization, and runbook path integration.
