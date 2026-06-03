# OpsGuard AI SRE Optimization Roadmap PRD

## 1. Document Info

- Document status: Draft
- Created date: 2026-06-01
- Target version: OpsGuard next-stage roadmap
- Target users: SRE, platform engineers, on-call operators, operations team leads
- Product positioning: safe, evidence-grounded Linux/SRE Agent with governed automation
- This PRD focuses on: Runbook 2.0, incident memory 2.0, context management, observability integrations, safety, rollback, and evaluation

## 2. Background

OpsGuard currently provides a working AI-assisted operations loop:

- Chat-driven operations requests.
- Real MCP/Linux tool execution.
- Safety checks and write-operation approval.
- Reasoning trace and tool ledger.
- Partial file backup and rollback.
- Knowledge saving and retrieval.
- Runbook generation, suggestion, and replay.
- Multimodal input, health reports, topology view, and incident reports.

Recent product review exposed a key limitation: the current Runbook mechanism is
mostly deterministic replay of previously recorded tool calls. This is useful
for stable repeated workflows, but weak for real incidents where symptoms are
similar while root causes, targets, versions, paths, and service state differ.

Market research on similar AI SRE / AIOps products shows that mature systems do
not rely on chat or static replay alone. They combine:

- Current telemetry and system state.
- Past incident memory.
- Code/deployment changes.
- Service topology and ownership.
- Governed automation.
- Evidence-based reports and incident communication.
- Policy-controlled execution.

OpsGuard should evolve from "an Agent that can run tools" into "an evidence-led
AI SRE system that can safely investigate, decide when automation applies, and
learn from every incident."

## 3. Competitive Reference

### 3.1 incident.io AI SRE

Observed product direction:

- AI SRE investigates incidents with access to telemetry, source control,
  incident context, and team communication.
- Provides suggestions grounded in existing incident history and operational
  context.
- Supports MCP-style integration for external assistants and tools.

Implication for OpsGuard:

- We need stronger external context ingestion: incidents, chat, deployments,
  code changes, dashboards, and ownership.
- Historical memory must not be isolated from current telemetry.

### 3.2 Datadog Bits AI / Incident AI

Observed product direction:

- Incident channel summaries.
- Remediation suggestions.
- Impact, contributing factors, open questions, and follow-up generation.
- Investigation launched from monitoring and incident surfaces.

Implication for OpsGuard:

- OpsGuard should generate structured incident updates, not only final chat
  answers.
- Investigation output should include impact, suspected factors, verified facts,
  open questions, and follow-up actions.

### 3.3 Grafana Assistant Investigations

Observed product direction:

- Prompt-driven multi-agent investigation.
- Queries logs, metrics, traces, profiles, and SQL.
- Produces structured investigation reports and follow-up artifacts.

Implication for OpsGuard:

- We need observability connectors and a multi-source evidence model.
- Current Linux-only tool context should expand to metrics/logs/traces.

### 3.4 Dynatrace Davis AI

Observed product direction:

- Uses topology and dependency context to identify causation.
- RCA is not only text similarity or LLM reasoning; it is grounded in entity
  relationships and observed events.

Implication for OpsGuard:

- OpsGuard needs a first-class service/entity graph and causal scoring.
- Topology should influence retrieval, investigation planning, and final RCA.

### 3.5 PagerDuty Runbook Automation / Rundeck

Observed product direction:

- Runbooks are governed automation assets with RBAC, audit, secure access,
  environment targeting, and reusable job definitions.
- Automation can be triggered from incidents but remains controlled.

Implication for OpsGuard:

- Runbook replay must become conditional, parameterized, governed, and auditable.
- Execution policy and access control are product features, not implementation
  details.

### 3.6 Kubernetes-focused AI SRE tools

Representative products:

- K8sGPT.
- Robusta.
- Komodor.

Observed product direction:

- Domain analyzers inspect Kubernetes state, events, logs, deployments, and
  ownership.
- AI explains and prioritizes evidence rather than blindly executing generic
  shell actions.

Implication for OpsGuard:

- We should add domain analyzers for common targets such as Linux services,
  Nginx, systemd, disk pressure, ports, Kubernetes, and databases.

## 4. Current Gap Analysis

| Area | Current OpsGuard | Gap |
| --- | --- | --- |
| Runbook | Linear replay of saved tool calls | No preconditions, variables, branches, dynamic parameters, or applicability gate |
| Knowledge retrieval | SQLite fuzzy matching | No hybrid retrieval, embeddings, FTS, evidence re-ranking, environment similarity, or recency scoring |
| Context management | Session history + injected hints | No summary compaction, context budget, evidence index, or long-session memory strategy |
| Observability | Mostly local Linux tools | Missing Prometheus, Grafana, Loki, Elasticsearch, Kubernetes, cloud monitor, CI/CD, Git |
| RCA | LLM + tools + recent changes | Weak topology/causal model and limited event correlation |
| Safety | Approval, protected paths, partial rollback | No true sandbox, RBAC, policy engine, full rollback coverage, or environment scoping |
| Runbook governance | Version, success/failure, staleness foundation exists | No version history, owner, review workflow, TTL, promotion flow, or rollback plan per step |
| Evaluation | Unit/regression tests | No incident benchmark, MTTR measurement, hallucination score, unsafe-action score, or retrieval quality metric |

## 5. Product Goals

### 5.1 Primary Goals

1. Make Runbook replay condition-aware and safe enough for repeated operations.
2. Improve historical memory so it helps investigation without replacing fresh
   evidence.
3. Add external observability context so OpsGuard can handle real incidents.
4. Strengthen execution safety, rollback visibility, and policy control.
5. Build an evaluation framework for AI SRE behavior.

### 5.2 Non-Goals

- Do not remove human approval for write/destructive actions.
- Do not make historical knowledge authoritative for current state.
- Do not build a generic dashboard-heavy AIOps clone.
- Do not require Kubernetes or cloud dependencies for the local Linux MVP.

## 6. Product Principles

1. **Fresh evidence wins**
   Current state must come from this turn's tool results or telemetry queries.

2. **Historical memory is advisory**
   Knowledge entries can suggest likely checks, root causes, and validation
   methods, but cannot prove current facts.

3. **Automation must prove applicability**
   A Runbook must pass preflight checks before execution.

4. **Every change needs a recovery story**
   If rollback is impossible, the UI must say so before approval.

5. **Auditability over magic**
   Users must see what was checked, what was inferred, what was executed, and
   what remains unknown.

## 7. Proposed Roadmap

## 7.1 P0: Runbook 2.0 - Conditional, Parameterized, Evidence-Gated Automation

Implementation status as of 2026-06-01: **implemented for the P0 Runbook 2.0
MVP scope**.

Completed in the current iteration:

- Backward-compatible Runbook 2.0 schema fields were added.
- Basic variable discovery, deterministic extraction, and `{{variable}}`
  template rendering were added.
- Missing required variables now trigger a clarifying-question flow before a
  Runbook suggestion can execute.
- Runbook suggestion now runs preflight and returns `applicable`, `uncertain`,
  or `not_applicable`.
- Execution is blocked when preflight returns `not_applicable`.
- Arbitrary preconditions/applicability conditions now execute registered
  read-only tools and evaluate simple `expect` rules.
- Runbooks that may mutate services/config/files/packages/firewall/users/cron
  now run a recent-change preflight warning check.
- Basic step controls were added: `on_success`, `on_failure`, `max_retries`,
  and `continue_on_failure`.
- Named `failure_branches` can be used as fallback step sequences after failed
  steps; `fallback_agent` hands control back to the normal Agent flow.
- The suggestion card shows preflight status, extracted variables, summary,
  precondition health, last successful run, and rollback coverage.

Remaining beyond this MVP:

- Owner/review workflow, promotion flow, and TTL review enforcement are not
  complete.
- Rollback coverage is visible in the UI, but minimum coverage policy for
  `reviewed` Runbooks is still a future governance workflow.
- The branching model supports named failure branches as linear fallback
  sequences; a full visual branching editor is still future work.

### Problem

Current Runbooks replay fixed tool calls. They do not adapt to changed service
state, different paths, new ports, stale commands, or similar symptoms with
different causes.

### User Value

Operators can safely reuse automation while still verifying that the current
incident matches the historical scenario.

### Functional Requirements

#### 7.1.1 Runbook Schema Upgrade

Status: **implemented for storage/API compatibility; governance workflow pending**.

Add fields:

- `variables`: reusable parameters such as service name, path, port, package.
- `preconditions`: read-only checks that must pass before execution.
- `applicability_conditions`: conditions copied from incident memory or manually
  defined.
- `non_applicability_conditions`: conditions that block replay.
- `postconditions`: verification checks after execution.
- `failure_branches`: what to do if a step fails.
- `rollback_steps`: explicit recovery actions where possible.
- `owner`: person or team responsible.
- `review_status`: draft, reviewed, deprecated.
- `ttl_days`: maximum age before review is required.
- `last_validated_at`.
- `source_incident_id`.

#### 7.1.2 Parameter Extraction

Status: **implemented for MVP**. Common variables such as `service`, `path`,
`filepath`, `dirpath`, `port`, and package names are extracted deterministically.
Missing required variables now pause the suggestion and ask a clarifying
question before preflight is retried.

When a user request matches a Runbook, extract variables from the current user
message before suggesting execution.

Examples:

- "检查 nginx 启动失败" -> `service=nginx`
- "清理 /var/log 下的大文件" -> `path=/var/log`
- "开放 8443 端口" -> `port=8443`, `protocol=tcp`

If required variables cannot be extracted, ask a clarifying question instead of
executing.

#### 7.1.3 Preflight Gate

Status: **implemented for MVP**. Tool availability, common path/service target
checks, stale/warning status, read-only precondition execution, simple
applicability/non-applicability expectations, recent-change warning checks, and
applicability result are implemented.

Before Runbook execution:

- Run read-only preconditions.
- Check tool availability.
- Check target existence where relevant.
- Check service current state where relevant.
- Check recent changes if the Runbook modifies a service or config.
- Compute applicability result: `applicable`, `uncertain`, or `not_applicable`.

Rules:

- `not_applicable`: do not execute; explain why and offer normal Agent
  investigation.
- `uncertain`: require explicit user confirmation and show missing evidence.
- `applicable`: show impact summary and proceed to step-level approvals.

#### 7.1.4 Branching Execution

Status: **implemented for MVP**. Basic `on_success`, `on_failure`,
`max_retries`, and `continue_on_failure` controls are supported. Named
`failure_branches` are supported as fallback step sequences, and
`fallback_agent` hands control back to normal Agent investigation.

Support step controls:

- `on_success`: next step id.
- `on_failure`: abort, retry, alternative step, or fallback to Agent.
- `max_retries`.
- `requires_approval`.
- `continue_on_failure`.

#### 7.1.5 Runbook Suggestion UX

Status: **implemented for MVP**. Suggestion cards show match score, extracted
variables/preflight summary, success/failure count, staleness, preconditions
summary, rollback coverage, and last successful run.

Suggestion card should show:

- Match score.
- Extracted variables.
- Success rate.
- Last successful run.
- Last failure reason.
- Staleness status.
- Preconditions summary.
- Rollback coverage.
- "Execute Runbook", "Investigate with Agent", and "Dismiss" actions.

### Acceptance Criteria

- A stale Runbook cannot execute without warning.
- A Runbook with missing required variables asks a clarifying question.
- A Runbook whose target service does not exist is blocked before write steps.
- A Runbook can branch on a failed read-only check.
- Every write step still triggers the existing approval flow.

## 7.2 P0: Incident Memory 2.0 - Hybrid Retrieval and Evidence-Grounded Reuse

Implementation status as of 2026-06-02: **implemented for the MVP 2
Knowledge Retrieval 2.0 scope**.

Completed in the current iteration:

- SQLite FTS5 keyword index was added for `knowledge_entries`.
- Backward-compatible Incident Memory fields were added: entities,
  incident_type, source modality, source session id, source incident id,
  evidence refs, tool call ids, trace event ids, evidence summaries, write
  action flags, validation status, structured-final validation status,
  confidence, owner/review/TTL metadata, and staleness status.
- Hybrid retrieval now combines FTS5 keyword search, fuzzy text matching,
  structured semantic/entity matching, and an optional pluggable embedding
  reranker. The default embedding provider is disabled; a deterministic
  dependency-free `local_hash` provider is available for lightweight reranking
  and tests.
- Structured search filters were exposed through the API for service, host,
  path, port, incident type, source modality, confidence, age, success count,
  and result limit.
- Re-ranking now includes match score, recentness, environment similarity,
  validation completeness, success count, and evidence coverage.
- Saved knowledge now stores evidence references, tool call ids, trace event
  ids, final validation method, and whether write actions were approved.
- Prompt injection now labels retrieved memory as historical, includes match
  reason/confidence, non-applicability conditions, and recommended fresh checks,
  and explicitly forbids treating historical observations as current facts.
- Knowledge quality controls now skip saves when no current-turn tool ledger
  exists or when structured final-response validation fails. Entries without
  validation are marked low confidence.
- The Knowledge UI now shows confidence, validation status, review/staleness
  status, match scores, retrieval sources, score breakdown, evidence refs,
  recommended fresh checks, source ids, entities, and write-action metadata.
- Human review actions were added for Incident Memory entries. Operators can
  mark entries as reviewed or deprecated from the Knowledge UI. Deprecated
  entries are retained for audit but hidden from the default Knowledge list and
  excluded from default Agent retrieval.
- A retrieval evaluation script was added for precision@3, fresh-check
  coverage, and evidence-reference coverage.

Remaining beyond this MVP:

- External/cloud embedding providers and a real vector database are not yet
  required or integrated.
- Promotion flow and TTL enforcement are visible as metadata but not yet
  governed by a full approval workflow.

### Problem

Current knowledge retrieval uses fuzzy text scoring. It is useful but limited
for real incident similarity.

### User Value

OpsGuard can recall relevant previous incidents, explain why they match, and
suggest checks while clearly separating historical observations from current
facts.

### Functional Requirements

#### 7.2.1 Hybrid Search

Implement hybrid retrieval:

- SQLite FTS5 keyword index.
- Embedding vector search for semantic similarity.
- Structured filters: service, host, path, port, incident type, source modality,
  confidence, age, success count.
- Re-ranker based on:
  - match score,
  - recentness,
  - environment similarity,
  - validation completeness,
  - success count,
  - evidence coverage.

#### 7.2.2 Evidence References

Every saved knowledge entry should store:

- source session id.
- source incident id.
- tool call ids.
- trace event ids.
- evidence summaries.
- final validation method.
- whether any write action was approved and executed.

#### 7.2.3 Prompt Injection Contract

When injecting retrieved memory into LLM context:

- Label it as historical.
- Include match reason and confidence.
- Include "must re-check current state" guidance.
- Include non-applicability conditions.
- Include recommended fresh checks.

#### 7.2.4 Knowledge Quality Controls

Do not save knowledge if:

- No tool was executed.
- No validation method exists for a claimed resolution.
- The final response failed structured truthfulness validation.
- The incident only contains multimodal recognition without real tool evidence.

### Acceptance Criteria

- Search returns both keyword and semantic matches.
- Retrieved memory includes evidence references.
- LLM final answer cannot describe historical observations as current facts.
- A knowledge entry without validation is marked low confidence.

## 7.3 P0: Context Management 2.0

Implementation status as of 2026-06-02: **implemented for the P0 Context
Management 2.0 MVP scope**.

Completed in the current iteration:

- Added a dedicated Agent context manager that builds explicit context layers
  for current request, recent conversation, rolling session summary, historical
  memory, recent tool ledger, fresh evidence requirements, multimodal
  recognition, and current-turn recent-change evidence.
- Added persistent `session_context_summaries` storage so older conversation can
  be summarized instead of always injected verbatim.
- Agent prompt construction now keeps the most recent 8 user/assistant turns
  verbatim and summarizes older turns before LLM reasoning.
- Injected context is now source-labelled with `current_turn`,
  `previous_turn`, `historical_memory`, `inferred`, `user_claim`, and
  `multimodal_recognition` labels.
- Historical tool ledger injection is scoped to history-recall prompts such as
  "刚才执行了什么"; fresh/current-state prompts still go through the existing
  fresh evidence read-tool guard.
- Context management emits trace evidence so operators can see when budgeting,
  summary, and ledger injection were applied.
- Regression coverage was added for 30-turn bounded context, source labels,
  session-summary persistence, and prior tool-ledger recall.

Remaining beyond this MVP:

- Summary quality currently uses the configured LLM when available and falls
  back to deterministic compaction; there is no human-editable summary UI yet.
- Context budgets are character/count based rather than model-token accurate.
- Raw trace/log outputs remain accessible in trace/database, but there is no
  dedicated UI affordance yet for drilling from a context layer to raw storage.

### Problem

Current Agent context uses session history plus injected hints. Long sessions can
become noisy and may exceed model context limits.

### User Value

The Agent remains accurate in long investigations and does not confuse old
states with current facts.

### Functional Requirements

#### 7.3.1 Context Layers

Create explicit context layers:

- `current_user_request`
- `recent_conversation`: last N user/assistant turns.
- `session_summary`: rolling summary of older conversation.
- `current_turn_evidence`: this turn's tool results.
- `historical_memory`: retrieved knowledge entries.
- `recent_tool_ledger`: prior tool executions in this session.
- `fresh_evidence_requirements`.

#### 7.3.2 Context Budgeting

Add context budget rules:

- Keep last 6-10 turns verbatim.
- Summarize older turns.
- Never truncate current turn tool results before final response.
- Prefer evidence summaries over raw logs.
- Keep raw outputs accessible in trace/database, not always in prompt.

#### 7.3.3 State Labels

All injected context must label facts as:

- `current_turn`
- `previous_turn`
- `historical_memory`
- `inferred`
- `user_claim`
- `multimodal_recognition`

### Acceptance Criteria

- A 30-turn session still produces a bounded LLM prompt.
- The Agent can answer "刚才执行了什么" using prior tool ledger.
- The Agent must re-check when asked "现在状态如何".

## 7.4 P1: Observability and Change Integrations

Implementation status as of 2026-06-02: **implemented for the MVP 3 /
Phase 1 Prometheus-Loki-Alertmanager scope**.

Completed in the current iteration:

- Added read-only Prometheus instant and range query tools with compact metric
  evidence summaries.
- Added read-only Loki instant and range query tools with bounded log evidence
  summaries.
- Added observability configuration for Prometheus and Loki base URLs,
  timeout, default range window, and log result limits.
- Registered Prometheus/Loki tools in the Agent tool registry as read-only
  observability tools.
- Alertmanager webhook auto-triage now enriches incidents with alert labels,
  dashboard/runbook links, Prometheus query context, and Loki query context.
- Alert auto-triage can execute Prometheus/Loki evidence checks before local
  Linux checks when alert labels or annotations provide enough context.
- Trace evidence now distinguishes metric, log, alert, and dashboard-link
  context so operators can tell telemetry from local command evidence.
- Frontend trace labels were updated for Prometheus/Loki tools and alert /
  dashboard evidence types.
- Regression coverage was added for Prometheus/Loki query tools and
  Alertmanager webhook enrichment.

Remaining beyond this MVP:

- Elasticsearch/OpenSearch, Kubernetes, Git, and CI/CD integrations are still
  future Phase 2 work.
- PagerDuty/Opsgenie and chat-channel summaries are still future Phase 3 work.
- Grafana integration currently treats dashboard URLs as context links; it does
  not yet call Grafana APIs or snapshot panels.
- Deployment-event correlation is limited to existing local recent-change
  evidence plus alert timing; full CI/CD deployment correlation remains future
  work.

### Problem

Real incidents are not visible from local Linux commands alone.

### User Value

OpsGuard can investigate production incidents across metrics, logs, traces,
deployments, and code changes.

### Functional Requirements

Add connectors in phases:

#### Phase 1

- Prometheus metrics query.
- Grafana dashboard link/context.
- Loki log query.
- Alertmanager webhook.

#### Phase 2

- Elasticsearch/OpenSearch logs.
- Kubernetes resources, events, pods, deployments, ingress, nodes.
- Git recent commits and changed files.
- CI/CD deployment events.

#### Phase 3

- PagerDuty/Opsgenie incident import.
- Slack/Feishu/Enterprise WeChat incident channel summaries.
- Cloud provider monitor events.

### Acceptance Criteria

- Alert webhook can create an incident and launch Agent triage.
- Prometheus/Loki evidence appears in trace.
- Recent deployment changes can be correlated with a service incident.

## 7.5 P1: Topology and Causal RCA

Implementation status as of 2026-06-02: **implemented for the P1 Topology and
Causal RCA MVP scope**.

Completed in the current iteration:

- Session topology can now merge static system topology with diagnosis-time
  evidence from incident timelines and audit trace events.
- RCA annotations are derived only from executed or failed evidence, not from
  free-form LLM planning/inference text.
- Evidence can be mapped to service, process, port, config, log, and host
  entities, with inferred dependency and evidence-link edges.
- Topology extraction now recognizes service status, config checks, recent
  changes, port/process evidence, local/Loki logs, and Prometheus metric
  evidence.
- RCA candidates are scored using evidence role, entity type, failed checks,
  error/log signals, metric-down signals, recent-change evidence, dependency
  direction, and affected targets.
- The topology UI now supports system, latest-turn, and full-session views,
  highlights affected/suspected/impact/evidence nodes, shows evidence details,
  and lists ranked RCA candidates with confidence, reasons, evidence, and
  impact path.
- Regression coverage was added for incident-evidence annotation, latest-vs-
  session scope, log evidence nodes, inferred evidence edges, and upstream
  dependency root-cause ranking.

Remaining beyond this MVP:

- Kubernetes workload, ownership, deployment, and package topology are still
  future connector/dependency-model work.
- The final response is grounded by the structured evidence guard and separates
  observed facts from suggested/not-yet-executed checks, but a dedicated
  topology-aware RCA report template with hard-coded `confirmed`, `likely`, and
  `not yet checked` section titles remains a future presentation refinement.

### Problem

Current RCA is mostly linear. Similar symptoms can have different causes across
service dependencies.

### User Value

OpsGuard can prioritize likely root causes using service relationships and
recent changes.

### Functional Requirements

- Maintain entities: host, service, process, port, config, log source, package,
  Kubernetes workload.
- Maintain edges: depends_on, listens_on, writes_log_to, configured_by,
  deployed_by, owns.
- Attach evidence to entities and edges.
- Score likely causes using:
  - anomaly proximity,
  - dependency direction,
  - recent changes,
  - alert timing,
  - historical incident similarity.
- Show RCA candidates with evidence and confidence.

### Acceptance Criteria

- For an Nginx 502 incident, OpsGuard can distinguish Nginx config, upstream
  service, port conflict, and disk/log pressure as separate candidates.
- Final answer includes "confirmed", "likely", and "not yet checked" sections.

## 7.6 P1: Safety, Policy, and Rollback Expansion

Implementation status as of 2026-06-03: **MVP implemented; full 7.6 scope is
not complete yet**.

Scope status:

- **7.6 MVP - completed**: policy gate, approval metadata, rollback visibility,
  rollback approval enforcement, and regression/real-LLM smoke coverage.
- **7.6-A Real Preview - pending**: concrete preview/diff output for write and
  rollback operations before approval.
- **7.6-B Sandbox Execution - pending**: isolated dry-run/container execution
  for scripts or command-like operations.

Completed in the current iteration:

- Added a deterministic execution policy engine that evaluates write and
  destructive tool calls before approval.
- Policy checks now support deny/protect rules for tools, categories, paths,
  services, users, hosts, environments, and risk levels.
- Configurable policy controls were added for approved write paths, denied
  paths, protected services, maintenance windows, max blast radius,
  environment/host identity, and optional sudo allowlist enforcement.
- Agent write/destructive calls are blocked before approval when policy denies
  the requested target, including file operations outside configured approved
  paths.
- Runbook replay now uses the same policy gate before each write/destructive
  step, so saved automation cannot bypass current policy.
- Approval requests now include policy decision metadata, approval level,
  execution identity, max blast radius, preview strategy, rollback strategy, and
  rollback availability.
- The approval modal now shows policy status, approval level, matched rules,
  policy warnings/reasons, execution identity, sudo usage, preview mode, and
  rollback coverage.
- Rollback capability metadata was expanded beyond file backup rollback to
  service-state restore and snapshot-style restore guidance for firewall/cron
  operations.
- Direct backup rollback API execution is disabled; rollback now requires the
  destructive `rollback_backup` tool through the normal approval and audit flow.
- Regression coverage was added for policy path blocking, denied protected
  paths, execution identity metadata, truthful rollback visibility, and rollback
  API approval enforcement.
- Real LLM smoke coverage was extended through 7.6 and passed the policy-block
  case: a protected `/etc/passwd` delete request was routed to `delete_file`,
  blocked by `execution_policy` before approval, and reported as not executed.

Remaining beyond this MVP:

- Policy administration UI and role-based policy editing are not yet built.
- Real preview/diff output is not yet available in the approval modal; current
  preview support mostly remains impact-only unless a tool has a specific
  check/diff mode.
- Containerized script sandbox execution is not yet implemented.
- Firewall and cron snapshot restore are represented as rollback strategy
  metadata/guidance; full automatic snapshot capture/restore workflows remain a
  future hardening step.
- Package rollback remains manual guidance because safe automatic package
  downgrade/reinstall depends on package manager state and repository history.

### 7.6-A Real Preview / Diff Before Approval

Status: **pending**.

Goal: replace impact-only preview text with concrete, inspectable preview
artifacts for common write and rollback operations.

Functional requirements:

- Add a preview result model for approval requests:
  - `preview_type`: impact_only, diff, before_after, restore_preview,
    command_dry_run.
  - `target`: file, directory, service, firewall, cron, package, backup.
  - `before_summary`, `after_summary`, `diff`, `warnings`, and `limitations`.
- For file writes:
  - `write_file` append/overwrite preview should show current file summary,
    proposed content summary, and unified diff where feasible.
  - `create_file` should show whether the file exists, whether overwrite is
    requested, and proposed content summary.
  - `delete_file` should show file metadata and whether a backup point can be
    created before deletion.
- For `rollback_backup`:
  - Show selected backup metadata, target path, current target metadata, and
    whether restore will overwrite an existing file or directory.
  - Make clear that rollback itself is a destructive recovery action and still
    requires approval.
- Approval modal should render preview artifacts as structured sections, not
  only blue impact text.
- Trace/audit should persist preview metadata so reviewers can see what the
  user approved.

Acceptance criteria:

- A `write_file` append request shows a readable diff before approval.
- A `delete_file` request shows target file metadata and backup capability
  before approval.
- A `rollback_backup` request shows backup id, original path, current target
  state, and overwrite impact before approval.
- If a preview cannot be generated, the modal says exactly why and falls back to
  impact-only preview.

### 7.6-B Sandbox / Dry-Run Execution

Status: **pending**.

Goal: execute supported operations in an isolated or dry-run environment before
touching the real host.

Functional requirements:

- Add sandbox execution support for command/script-like tools where feasible:
  - containerized test execution for shell snippets or scripts.
  - bind-mounted read-only inputs where safe.
  - explicit denial of host-sensitive paths unless mapped into the sandbox.
- Add local dry-run adapters where tools support them:
  - package managers: simulate/install preview where supported.
  - config checks: syntax/check mode before write or reload.
  - firewall/cron: generate planned mutation without applying it.
- Approval modal should distinguish:
  - real sandbox result,
  - tool-native dry-run result,
  - static impact-only preview.
- Sandbox output must be bounded, redacted where needed, and persisted in trace.
- Sandbox failure must not imply the real operation was executed.

Acceptance criteria:

- A script-like operation can run in sandbox mode and show stdout/stderr without
  changing the host.
- A package operation can show a simulate/dry-run result or explicitly state
  that simulation is unsupported.
- A command that touches a protected host path is blocked or only sees a safe
  sandbox mapping.
- The final response separates sandbox observations from real host execution.

### Problem

Current safety relies on approval, protected paths, and partial backup rollback.
There is no true sandbox and rollback coverage is incomplete.

### User Value

Teams can trust OpsGuard in production-like environments.

### Functional Requirements

- Add policy engine:
  - allow/deny by tool, path, service, user, host, environment.
  - approval level by risk.
  - maintenance window constraints.
  - max blast radius.
- Add execution identity:
  - run as least-privilege service user.
  - per-tool sudo allowlist.
  - audit user and session.
- Add sandbox/dry-run modes:
  - local dry-run when tool supports it.
  - containerized test execution for scripts.
  - impact-only preview when no dry-run exists.
- Expand rollback:
  - config file diff patch rollback.
  - firewall rule snapshot and restore.
  - cron snapshot and restore.
  - package operation rollback guidance.
  - systemd service state restore where meaningful.

### Acceptance Criteria

- A policy can block deleting files outside approved paths. **Status:
  completed in 7.6 MVP.**
- Approval modal shows rollback coverage per write step. **Status: completed
  for rollback metadata/coverage in 7.6 MVP; concrete preview artifacts remain
  pending in 7.6-A.**
- Rollback action itself requires approval and is audited. **Status: completed
  in 7.6 MVP for `rollback_backup` via Agent approval flow.**
- Write operations show real preview/diff artifacts before approval. **Status:
  pending 7.6-A.**
- Supported command/script operations can run in sandbox or dry-run mode before
  host execution. **Status: pending 7.6-B.**

## 7.7 P2: AI SRE Evaluation Framework

### Problem

Without evaluation, improvements are hard to trust.

### User Value

Developers and evaluators can measure whether OpsGuard is becoming safer and
more useful.

### Functional Requirements

Create a benchmark suite with repeatable incidents:

- service down.
- port conflict.
- disk full.
- inode pressure.
- config syntax error.
- permission denied.
- certificate expiry.
- log explosion.
- zombie process.
- Kubernetes CrashLoopBackOff.
- deployment regression.

Metrics:

- RCA accuracy.
- required evidence coverage.
- unsafe action attempt rate.
- hallucinated execution rate.
- approval bypass rate.
- rollback availability.
- mean tool calls to diagnosis.
- mean time to useful answer.
- retrieval precision/recall.
- Runbook applicability accuracy.

### Acceptance Criteria

- CI can run a subset of deterministic evaluations.
- Manual test report includes objective scores.
- Regression tests cover at least 20 high-risk prompts.

## 8. MVP Scope Recommendation

### MVP 1: Runbook 2.0 Foundation

Deliver:

- Variables.
- Preconditions.
- Applicability gate.
- Staleness warning improvements.
- Suggestion card improvements.
- Read-only preflight checks.

Do not deliver yet:

- Full branching editor.
- Full RBAC.
- External observability connectors.

### MVP 2: Knowledge Retrieval 2.0

Deliver:

- SQLite FTS5.
- Evidence references.
- Retrieval labels.
- Recommended fresh checks.
- Recency and success weighting.

Do not deliver yet:

- Full embedding service if infrastructure is unavailable.
- Complex vector database.

### MVP 3: Prometheus/Loki Integration

Deliver:

- Prometheus query tool.
- Loki query tool.
- Alertmanager webhook enrichment.
- Trace evidence rendering for metric/log queries.

## 9. Data Model Drafts

### 9.1 Runbook

```json
{
  "id": "uuid",
  "name": "清理磁盘大文件",
  "description": "定位并清理指定路径下的大文件",
  "version": 3,
  "review_status": "reviewed",
  "owner": "platform",
  "trigger_pattern": "磁盘空间不足",
  "variables": [
    {
      "name": "path",
      "type": "path",
      "required": true,
      "default": "/var/log",
      "source": "user_message"
    }
  ],
  "preconditions": [
    {
      "id": "check_disk",
      "tool_name": "get_disk_usage",
      "tool_args": {"path": "${path}"},
      "expect": {"disk_percent_gt": 80}
    }
  ],
  "steps": [
    {
      "id": "find_large",
      "tool_name": "find_large_files",
      "tool_args": {"path": "${path}", "min_size": "500M", "limit": 20},
      "on_success": "ask_delete",
      "on_failure": "abort"
    }
  ],
  "postconditions": [
    {
      "tool_name": "get_disk_usage",
      "tool_args": {"path": "${path}"}
    }
  ],
  "rollback_steps": [],
  "success_count": 4,
  "failure_count": 1,
  "staleness_status": "fresh"
}
```

### 9.2 Incident Memory

```json
{
  "id": "uuid",
  "problem_signature": "nginx 502 upstream unavailable",
  "entities": {
    "services": ["nginx", "app-api"],
    "ports": [80, 8080],
    "paths": ["/etc/nginx/nginx.conf"]
  },
  "symptoms": ["HTTP 502", "upstream connection refused"],
  "root_cause": "app-api service inactive",
  "evidence_refs": [
    {"type": "tool_call", "call_id": "call_123", "summary": "app-api inactive"}
  ],
  "successful_actions": ["start_service app-api"],
  "validation_method": "curl health endpoint returned 200",
  "applicability_conditions": ["same service topology", "app-api inactive"],
  "non_applicability_conditions": ["nginx config syntax failure"],
  "confidence": "high",
  "created_at": "2026-06-01T00:00:00"
}
```

## 10. UX Requirements

### 10.1 Runbook Suggestion Card

Must show:

- Suggested Runbook name.
- Match reason.
- Match score.
- Variables extracted.
- Preconditions status.
- Success rate.
- Last failure.
- Staleness.
- Buttons:
  - Execute Runbook.
  - Investigate with Agent.
  - Dismiss.

### 10.2 Approval Modal

Must show:

- Human-readable impact.
- Current preflight evidence.
- Target resource.
- Risk level.
- Rollback strategy.
- Verification plan.
- Raw tool call collapsed by default.

### 10.3 Trace Panel

Must show:

- Context source labels.
- Historical vs current evidence.
- Runbook preflight results.
- Branch decisions.
- Tool results.
- Verification results.
- Rollback points.

## 11. Security Requirements

- Chat confirmation is not approval.
- Every write/destructive tool call requires system approval.
- Historical successful write actions cannot be replayed without fresh approval.
- Runbook applicability must be checked before write steps.
- Rollback operations are destructive and require approval.
- Protected paths remain enforced.
- Every executed step must be recorded in audit and incident timeline.

## 12. Metrics

Product metrics:

- Runbook suggestion acceptance rate.
- Runbook preflight block rate.
- Runbook success rate.
- Runbook stale rate.
- Knowledge retrieval hit rate.
- Knowledge reuse helpfulness rating.
- Mean time to first useful evidence.
- Mean time to diagnosis.

Safety metrics:

- Unsafe write attempt rate.
- Approval rejection rate.
- Approval timeout rate.
- Rollback point creation rate.
- Rollback success rate.
- Hallucinated execution claim rate.

Evaluation metrics:

- RCA accuracy.
- Retrieval precision at 3.
- Fresh evidence compliance.
- Tool-call success rate.
- Final-answer groundedness.

## 13. Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Runbook false positive | Wrong automation suggested | Conservative threshold, preflight, user confirmation |
| Stale historical memory | Misleading diagnosis | Recency scoring, labels, fresh checks |
| Context overload | LLM ignores important evidence | Context budgeting and evidence summaries |
| Unsafe write action | Production impact | Approval, policy engine, rollback coverage, protected paths |
| Poor external integration quality | Noisy RCA | Connector-specific validation and source labels |
| Evaluation blind spots | False confidence | Scenario benchmark and adversarial prompts |

## 14. Open Questions

1. Should Runbook applicability be decided by rules only, or rules plus LLM
   explanation?
2. Which observability integration should be first: Prometheus/Loki or
   Kubernetes?
3. Should embeddings use local models, cloud APIs, or pluggable providers?
4. What minimum rollback coverage is required before a write Runbook can be
   marked reviewed?
5. Do we need multi-host execution in the next version, or should we keep the
   MVP single-host?

## 15. Suggested Implementation Order

1. Add Runbook variables and precondition schema.
2. Add variable extraction and preflight execution.
3. Update Runbook suggestion UX with preflight and health metadata.
4. Add FTS5 index and evidence references for knowledge entries.
5. Add context budgeting and state labels.
6. Add Prometheus and Loki read-only tools.
7. Add policy engine skeleton.
8. Build incident evaluation scenarios.

## 16. Success Definition

OpsGuard reaches the next maturity level when:

- A Runbook is suggested only when it is likely applicable.
- The user can see why the Runbook applies before approving writes.
- Historical memory helps the Agent choose checks but never replaces current
  evidence.
- Final answers cite current tool evidence for current facts.
- At least one external observability source contributes evidence.
- A benchmark suite can measure RCA, retrieval, and safety regressions.
