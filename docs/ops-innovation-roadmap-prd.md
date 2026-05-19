# OpsGuard Innovation Roadmap PRD

## 1. Background

OpsGuard already has a strong base for AI-assisted operations:

- Chat-driven operations requests.
- Safety checks and approval gates.
- Real MCP tool execution.
- Reasoning trace panel.
- Knowledge saving and retrieval.
- Runbook generation, suggestion, and replay.
- Topology, health report, tools, and operations report pages.

Recent testing exposed a clear product direction: users do not only need an AI
that can run commands. They need an AI system they can trust under operational
pressure. Trust comes from visible evidence, explicit impact, repeatable
automation, and durable incident memory.

This PRD turns the product research discussion into an implementation roadmap.
The goal is to add useful innovations that solve real SRE/Ops pain points
without turning OpsGuard into a page-heavy dashboard.

## 2. Product Principles

1. **Truth before fluency**
   The UI must distinguish actual execution, inferred conclusions, failed
   checks, and no-op recommendations.

2. **Evidence before answers**
   Every diagnosis should show the evidence behind it: command output, logs,
   metric checks, topology facts, knowledge hits, and validation results.

3. **Human control for change**
   Write/destructive actions must remain reviewable, approvable, auditable, and
   reversible where possible.

4. **Automation should age visibly**
   Runbooks are operational assets. They need versions, success rates, freshness,
   failure reasons, and retirement signals.

5. **Incidents should become memory**
   Each troubleshooting session should produce reusable structured knowledge,
   not just chat history.

## 3. Target Users

- On-call SREs handling alerts and production incidents.
- Platform engineers maintaining Linux services, network rules, packages,
  users, logs, and system health.
- Small teams that need reliable Runbook automation but cannot maintain a large
  commercial AIOps stack.
- Operators who want AI assistance but need proof of what was checked or changed.

## 4. Pain Points

| Pain Point | Current Impact |
| --- | --- |
| AI answers are hard to trust | Users cannot tell whether a claim came from real evidence or generated text |
| Runbook replay is opaque | Users see tool calls but not operational intent, impact, or summarized results |
| Runbooks become stale | Saved automation may stop working as services, paths, or tools change |
| Repeated incidents lose context | Chat history is hard to convert into structured incident memory |
| Approvals lack impact context | Users approve technical tool names instead of business/ops impact |
| RCA is too linear | Logs, topology, recent changes, and historical incidents are not merged into one causal view |

## 5. Proposed Innovation Features

### 5.1 Evidence-Based Reasoning Trace

**Priority:** P0

Enhance the existing TracePanel so each reasoning event can expose structured
evidence instead of only short status text.

#### User Value

Operators can verify why OpsGuard reached a conclusion and whether the system
really executed a check or change.

#### Functional Requirements

- Add evidence fields to trace events:
  - `claim`: what the agent believes.
  - `evidence_type`: command, log, config, metric, topology, knowledge, user input.
  - `source`: tool name, file path, service, endpoint, or knowledge entry.
  - `observed`: concise observed output.
  - `confidence`: low, medium, high.
  - `execution_state`: executed, inferred, skipped, failed.
- Show evidence under each trace step in the existing right-side panel.
- Keep raw tool output available but collapsed.
- Mark inferred topology or RCA claims explicitly.
- For failures, show failure reason and next suggested check.

#### MVP Scope

- Backend: extend trace payloads emitted by Agent and Runbook executor.
- Frontend: render optional evidence blocks inside existing TracePanel.
- No new page required.

#### Acceptance Criteria

- A service restart request shows which tool ran, what it returned, and how
  verification confirmed the result.
- A read-only diagnosis shows logs/status/config evidence before the final answer.
- A failed tool call cannot appear as successful evidence.

---

### 5.2 Runbook Governance: Versioning, Health, and Aging

**Priority:** P0

Turn saved Runbooks from static tool sequences into governed operational assets.

#### User Value

Operators know whether a Runbook is reliable before replaying it.

#### Functional Requirements

- Add Runbook metadata:
  - `version`
  - `success_count`
  - `failure_count`
  - `last_success`
  - `last_failure`
  - `last_failure_reason`
  - `staleness_status`: fresh, warning, stale
  - `updated_from_session_id`
- Keep old versions when auto-updating a Runbook.
- Show success rate and last failure reason in Runbook list and suggestion card.
- Detect stale Runbooks using:
  - no successful run in N days,
  - repeated failures,
  - tool no longer exists,
  - target path/service no longer exists,
  - verification failures.
- Add a read-only "validate Runbook" action that checks tool existence and target
  availability without making changes.

#### MVP Scope

- Backend schema migration via `CREATE TABLE IF NOT EXISTS` and safe `ALTER TABLE`.
- Extend Runbook executor bookkeeping.
- Update RunbookPanel cards with health badges.

#### Acceptance Criteria

- Replaying a Runbook updates success/failure statistics.
- A failed Runbook records the exact failing step and reason.
- The UI warns before replaying a stale or recently failing Runbook.

---

### 5.3 Pre-Execution Preview and Rollback Control

**Priority:** P0

Before a write/destructive action or Runbook replay, show what will be touched,
what may change, whether the operation can be previewed, and what rollback or
verification exists. After execution, expose rollback points and rollback
coverage instead of leaving backup/restore as an internal implementation detail.

#### User Value

Approvals become meaningful. Users approve operational impact, not opaque tool
calls. If something goes wrong, operators can quickly see whether OpsGuard can
restore the previous state, perform an inverse action, or only provide manual
recovery guidance.

#### Current Foundation

OpsGuard already has partial building blocks:

- `BackupManager` can back up files/directories and restore by backup id.
- Agent and Runbook execution already attempt backups before some write actions.
- Write/destructive actions already require approval.
- Write actions already emit verification and before/after change diff events.

These capabilities are not yet productized. Users cannot reliably see rollback
coverage before approval, browse rollback points, or trigger rollback through a
controlled workflow.

#### Functional Requirements

- Add capability metadata to every registered tool:
  - `supports_preview`: whether a real dry-run/preview is available.
  - `supports_rollback`: whether OpsGuard can provide an automated rollback.
  - `rollback_strategy`: `backup`, `inverse_action`, `manual`, or `none`.
  - `preview_strategy`: `diff`, `check_mode`, `state_comparison`, `impact_only`,
    or `none`.
- Implement a preflight stage before approval:
  - Run real previews where supported.
  - Generate impact-only previews where true dry-run is impossible.
  - Explicitly label unsupported preview cases as "impact estimate only".
- For each proposed write/destructive step, show:
  - target resource,
  - expected change,
  - possible user-facing impact,
  - rollback option,
  - rollback confidence,
  - backup status or inverse action,
  - verification plan,
  - risk level.
- For Runbooks, show an aggregate impact summary:
  - read-only steps count,
  - write steps count,
  - destructive steps count,
  - services/files/ports/users affected.
  - rollback coverage by step.
- Approval modal should display the human-readable impact summary first and raw
  tool call second.
- If no reliable rollback exists, say so explicitly.
- After a successful write/destructive action, show a rollback point in the
  trace:
  - rollback id,
  - target,
  - strategy,
  - created_at,
  - restore command/API availability.
- Add rollback APIs and MCP tools:
  - `GET /api/backups`
  - `GET /api/backups?filepath=...`
  - `POST /api/backups/{id}/rollback`
  - `list_backups`
  - `rollback_backup`
- Treat `rollback_backup` as write/destructive and require approval.
- Runbooks should store `rollback_plan` metadata per write/destructive step
  where possible. On partial failure, OpsGuard should recommend which completed
  steps can be rolled back safely.

#### MVP Scope

- Reuse existing `assess_impact` path.
- Extend Runbook executor plan generation.
- Improve ApprovalModal rendering.
- Expose backup manifest through an authenticated API.
- Register rollback-related MCP tools with approval.
- Start with truthful rollback support for file/directory operations and
  impact-only previews for service/process operations.

#### Acceptance Criteria

- Restarting nginx says service may be briefly unavailable and status will be
  verified afterward. It must not claim full rollback unless an actual safe
  inverse plan exists.
- Deleting a file shows the path, backup status, and whether rollback is
  possible.
- A read-only Runbook clearly says it will not modify the system.
- After modifying a backed-up file, the trace shows the rollback id and the
  backup can be restored through a controlled rollback request.
- If a Runbook fails after step 3, the final report lists which completed steps
  have rollback coverage and which do not.

#### Phased Delivery

1. **Rollback visibility**
   Surface rollback capability, backup status, and rollback points in approval
   and trace events.

2. **Manual rollback workflow**
   Add backup listing and approved rollback APIs/tools.

3. **Preflight preview**
   Add tool-level preview strategies and show preview results before approval.

4. **Runbook rollback plans**
   Store per-step rollback metadata and recommend rollback on partial failure.

5. **Selective sandbox/dry-run adapters**
   Add tool-specific dry-run support only where the underlying system supports
   it reliably, such as config syntax checks, package manager simulation,
   Kubernetes dry-run/diff, or Ansible check/diff style integrations.

---

### 5.4 Incident Timeline / Incident Object

**Priority:** P1

Convert each meaningful troubleshooting thread into a structured incident object
that can power reports, handoffs, knowledge, and Runbooks.

#### User Value

Operators can hand off, review, and learn from incidents without manually
summarizing chat history.

#### Functional Requirements

- Create an incident record when a user starts a substantive operations request.
- Track:
  - problem statement,
  - timeline events,
  - hypotheses,
  - checks performed,
  - evidence,
  - approvals,
  - changes,
  - verification,
  - final status,
  - follow-up action items.
- Let the final assistant response reference the incident summary.
- Use incident records as the source for OpsReport and future knowledge entries.

#### MVP Scope

- Backend incident table.
- Emit incident timeline events alongside audit trace.
- Add a compact incident summary section inside the chat response or report page.

#### Acceptance Criteria

- After a troubleshooting session, OpsGuard can produce a concise handoff note.
- The incident record includes all write actions and verification results.
- Knowledge saving can reference incident evidence, not only LLM-extracted text.

---

### 5.5 Change-Aware RCA

**Priority:** P1

Automatically check recent system changes when diagnosing failures.

#### User Value

Many incidents are caused by recent changes. Surfacing them early reduces RCA
time.

#### Functional Requirements

- Collect recent changes from:
  - service status and restart timestamps,
  - config file mtimes/hashes,
  - package manager history,
  - crontab changes,
  - firewall rules,
  - available deployment/git metadata when configured.
- During RCA, add a "recent changes" evidence block.
- Highlight changes within a configurable time window before the symptom.

#### MVP Scope

- Linux-first recent change collectors.
- Add a `recent_changes` tool category.
- Show results in TracePanel and final diagnosis.

#### Acceptance Criteria

- If nginx fails after config modification, the RCA mentions the config mtime/hash
  as a candidate cause.
- If no recent change evidence exists, the trace says the check ran and found none.

---

### 5.6 Knowledge Base as Incident Memory

**Priority:** P1

Upgrade knowledge entries from `problem/solution` into structured incident
memory.

#### User Value

Similar incidents become actionable references instead of vague "historical
experience".

#### Functional Requirements

- Store:
  - symptoms,
  - root cause,
  - evidence,
  - successful actions,
  - failed attempts,
  - validation method,
  - applicability conditions,
  - non-applicability conditions.
- Retrieval should show:
  - similarity score,
  - why it matched,
  - what evidence overlapped,
  - whether the old solution is safe to reuse.

#### MVP Scope

- Extend `knowledge_entries` schema.
- Update save extractor prompt and retrieval formatting.
- Render richer knowledge hits in trace.

#### Acceptance Criteria

- A repeated nginx problem returns a historical entry with root cause, evidence,
  and validation steps.
- The agent does not directly reuse a historical write action without fresh tool
  execution and approval.

---

### 5.7 Alert Webhook Auto-Triage

**Priority:** P2

Allow external alert systems to trigger read-only triage automatically.

#### User Value

On-call users receive an initial investigation before opening the console.

#### Functional Requirements

- Add webhook endpoint for Alertmanager/Grafana-style payloads.
- Map alert labels to triage templates.
- Run read-only checks only by default.
- Create an incident timeline and chat session for the alert.
- Notify UI that an auto-triage report is ready.

#### MVP Scope

- Generic webhook endpoint.
- One or two built-in templates: service down, high disk usage.

#### Acceptance Criteria

- A service-down alert creates a session with service status, logs, ports, and
  recent changes checked.
- No write/destructive tool runs from webhook auto-triage.

---

### 5.8 Topology-Based RCA View

**Priority:** P2

Make the topology graph reflect diagnosis evidence, impact, and root-cause
candidates.

#### User Value

Operators can see blast radius and causal paths visually.

#### Functional Requirements

- Highlight:
  - affected node,
  - suspected root cause,
  - downstream impacted services,
  - observed vs inferred edges.
- Let trace events add dynamic topology annotations.
- Show evidence for each highlighted edge or node.

#### MVP Scope

- Extend current topology dynamic update payload.
- Add trace-to-topology annotations for service, port, config, and process checks.

#### Acceptance Criteria

- During nginx diagnosis, topology highlights service, config file, listening
  port, and related process.
- Inferred relationships remain visibly distinct from observed runtime facts.

---

### 5.9 Automated Handoff and Postmortem Drafts

**Priority:** P2

Generate operational handoff and postmortem drafts from incident records.

#### User Value

Operators spend less time writing reports after stressful incidents.

#### Functional Requirements

- Generate:
  - short handoff note,
  - detailed postmortem draft,
  - action items,
  - customer/business impact placeholder,
  - Runbook improvement suggestions.
- Pull from incident timeline, evidence, approvals, and final state.

#### MVP Scope

- Add report generation endpoint using existing OpsReport concepts.
- Render in report page or as downloadable markdown.

#### Acceptance Criteria

- A resolved incident produces a postmortem draft with timeline, cause,
  mitigation, verification, and follow-ups.
- The draft distinguishes confirmed facts from inferred hypotheses.

## 6. Recommended Implementation Order

1. **Evidence-Based Reasoning Trace**
   This strengthens user trust across every workflow and prevents fake-output
   regressions.

2. **Runbook Governance**
   Runbooks are already central to OpsGuard. Adding health, versions, and aging
   makes them safer to replay.

3. **Pre-Execution Preview and Rollback Control**
   This improves approvals, makes recovery explicit, and directly reduces
   production risk.

4. **Incident Timeline**
   This creates the structured substrate for knowledge, reports, handoffs, and
   RCA.

5. **Change-Aware RCA**
   This adds high-value root cause signal once incident/evidence structures exist.

6. **Knowledge as Incident Memory**
   This is more powerful after incident objects exist.

7. **Alert Webhook Auto-Triage**
   Useful after read-only triage and incident creation are reliable.

8. **Topology-Based RCA View**
   Best after evidence and incident annotations are structured.

9. **Postmortem Drafts**
   Best after incident timeline quality is high.

## 7. P0 MVP Definition

The first innovation milestone should ship:

- Evidence blocks in TracePanel.
- Runbook health fields and execution statistics.
- Runbook plan/impact preview before replay.
- Approval modal impact-first display with rollback coverage.
- Visible rollback points for backed-up file/directory changes.

### P0 Success Metrics

- Operators can answer "what did OpsGuard actually execute?" from the UI alone.
- Runbook replay shows whether it is fresh, stale, or recently failing.
- Every write approval includes target, expected impact, rollback coverage, and
  verification plan.
- Backed-up file/directory changes can be restored through an approved rollback
  workflow.
- No final answer claims a system change without evidence or successful tool
  execution.

## 8. Non-Goals

- Full autonomous remediation without approval.
- Generic system-level sandbox execution for all host operations. Sandbox/dry-run
  support is reserved for tool-specific integrations where results are truthful
  and representative.
- Replacing Prometheus/Grafana/Datadog-style monitoring systems.
- Building a separate complex incident management product before core evidence
  and Runbook governance are reliable.
- Adding decorative dashboards that do not reduce operational effort.

## 9. Key Risks

| Risk | Mitigation |
| --- | --- |
| False confidence from AI summaries | Require evidence references and execution states |
| Runbook false positives | Show match reason, similarity, health, and require approval |
| Schema drift without migrations | Use safe `ALTER TABLE` helpers and regression tests |
| Overloaded trace panel | Use progressive disclosure: summary first, raw detail collapsed |
| Excessive automation risk | Default to read-only auto-triage; keep writes approval-gated |
| False sense of rollback safety | Show rollback strategy and confidence per operation; never claim rollback when only manual recovery exists |
| Sandbox results differ from host reality | Keep general sandbox execution out of near-term scope; implement only tool-specific dry-run adapters |

## 10. Open Questions

- Should incident objects be created for every chat message or only when a tool
  call is involved?
- What default staleness threshold should Runbooks use: 30, 60, or 90 days?
- Should Runbook version rollback be exposed in the UI immediately or only via
  backend API first?
- Which operations should support automated rollback first: files only, firewall
  inverse actions, service state restoration, package operations, or user
  management?
- What language should approval UI use for operations that are reversible in
  theory but risky in practice?
- Which sandbox/dry-run adapters are worth implementing later: package manager
  simulation, Kubernetes dry-run, Ansible check mode, or containerized config
  validation?
- Which alert source should be supported first: Alertmanager, Grafana, or a
  generic webhook?
- Should topology RCA annotations be session-scoped only or persisted into
  incident records?
