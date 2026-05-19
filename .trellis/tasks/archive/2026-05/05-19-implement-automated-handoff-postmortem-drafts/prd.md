# Implement Automated Handoff and Postmortem Drafts

## Background

OpsGuard now persists evidence-aware incident timelines and can annotate topology
with RCA evidence. The next roadmap item is to help operators produce handoff
notes and postmortem drafts from those incident records without rewriting the
same facts manually after an incident.

## Goals

- Generate a short operational handoff note for one incident.
- Generate a detailed postmortem draft for one incident.
- Include action items and Runbook improvement suggestions.
- Preserve truthfulness: confirmed facts must be separated from inferred
  hypotheses/placeholders.
- Provide a simple API and frontend entry point using the existing OpsReport
  surface.

## Non-Goals

- Do not invent root cause, business impact, or remediation success when the
  incident timeline does not contain evidence.
- Do not call the LLM for this MVP.
- Do not create a new report authoring product surface.
- Do not export PDF/DOCX in this task; Markdown output is enough.

## Functional Requirements

1. Add backend endpoint(s) to generate incident reports:
   - `GET /api/incidents/{incident_id}/handoff`
   - `GET /api/incidents/{incident_id}/postmortem`
2. Report generation reads from existing incident records and incident events.
3. Handoff note includes:
   - incident id/status,
   - problem statement,
   - current state,
   - key confirmed facts,
   - failures/risks,
   - next suggested checks.
4. Postmortem draft includes:
   - summary,
   - impact placeholder,
   - timeline,
   - confirmed facts,
   - inferred hypotheses,
   - cause/mitigation/verification sections,
   - action items,
   - Runbook improvement suggestions.
5. Confirmed facts may only come from event evidence with `execution_state` of
   `executed` or `failed`.
6. Inferred hypotheses must be explicitly labeled and may come from
   `execution_state: inferred` evidence or unresolved/failure patterns.
7. The existing OpsReport page should expose a lightweight way to open/copy
   incident handoff and postmortem Markdown for recent incidents.

## Acceptance Criteria

- A resolved incident produces Markdown with timeline, confirmed facts,
  mitigation, verification, and follow-ups.
- A failed/unresolved incident produces Markdown that clearly states unresolved
  status and next checks.
- Reports do not claim root cause or business impact when source evidence is
  missing; they use placeholders instead.
- Existing incident, topology, report, and frontend build checks pass.

