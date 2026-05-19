# Implement Alert Webhook Auto-Triage

## Background

OpsGuard already has evidence-aware traces, incident timelines, change-aware RCA,
and structured incident memory. The next roadmap item is to let external alert
systems trigger an initial read-only investigation before an operator opens the
console.

## Goals

- Add a generic alert webhook endpoint for Alertmanager/Grafana-style payloads.
- Normalize incoming alerts into a stable internal alert object.
- Match alerts to one of two built-in triage templates:
  - service down / service unhealthy
  - high disk usage
- Create a chat session and incident for each accepted alert.
- Run only read-only checks during webhook auto-triage.
- Persist an auto-triage report as an assistant message.
- Persist trace/timeline evidence showing what checks actually ran or failed.

## Non-Goals

- Do not execute write, destructive, or approval-gated tools from webhook flow.
- Do not build a full alert management UI in this task.
- Do not add authentication/secrets for inbound webhooks yet.
- Do not implement sandbox execution or automatic remediation here.

## Functional Requirements

1. `POST /api/alerts/webhook` accepts generic JSON payloads.
2. The endpoint supports common Alertmanager/Grafana shapes:
   - top-level `alerts` list with `labels`, `annotations`, `status`.
   - single-alert payloads with top-level `labels` and `annotations`.
   - simple custom payloads with fields such as `alertname`, `service`,
     `instance`, `severity`, `mountpoint`, and `description`.
3. For each accepted alert, OpsGuard creates:
   - a session with a user-facing alert title.
   - an incident linked to the session.
   - an initial user message containing the normalized alert summary.
4. The webhook runs deterministic read-only triage:
   - Service-down alerts check service status, recent logs, listening ports, and
     recent changes when enough labels are present.
   - High-disk alerts check disk usage and recent changes.
5. Every triage step records a trace event with truthful execution state:
   `executed`, `failed`, or `skipped`.
6. Trace events are also copied into the incident timeline using the existing
   incident store.
7. The assistant message must clearly state:
   - alert summary,
   - matched template,
   - checks executed,
   - skipped checks and why,
   - failures and next suggested checks.
8. The endpoint returns session id, incident id, matched template, check
   summaries, and report text.

## Acceptance Criteria

- A service-down alert creates a session, incident, trace events, and assistant
  report with service status, logs, ports, and recent changes represented as
  executed/failed/skipped evidence.
- A high-disk alert creates a session, incident, trace events, and assistant
  report with disk usage and recent changes represented truthfully.
- Webhook auto-triage never invokes write/destructive tools.
- Source/tool failures are explicit in returned check summaries and trace
  evidence, never converted into fake success.
- Existing agent, runbook, knowledge, incident, and frontend build tests keep
  passing.

