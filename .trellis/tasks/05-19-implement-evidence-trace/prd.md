# Implement Evidence-Based Reasoning Trace

## Background

OpsGuard already emits reasoning trace events, tool calls, and runbook progress,
but the UI still relies too much on natural-language status text. The first P0
roadmap item is to make trace events evidence-aware so operators can distinguish
real execution, inference, skipped work, and failures.

## Scope

Implement the MVP for the "Evidence-Based Reasoning Trace" section of
`docs/ops-innovation-roadmap-prd.md`.

## Functional Requirements

- Extend trace event payloads with optional structured evidence fields:
  - `claim`
  - `evidence_type`
  - `source`
  - `observed`
  - `confidence`
  - `execution_state`
  - `failure_reason`
  - `next_check`
- Backend agent and runbook trace events should populate evidence where the
  event is based on a tool result, knowledge result, user input, or inference.
- Failed tool calls must emit `execution_state: failed` and must not be rendered
  as successful evidence.
- Inferred or planning-only claims must be explicitly marked as inferred or
  skipped instead of looking executed.
- Frontend TracePanel should render evidence blocks under each step when present.
- Raw tool output remains available but secondary/collapsed where the existing
  UI supports detail display.

## Non-Goals

- Do not build a new page.
- Do not change the approval policy.
- Do not implement Runbook health/versioning in this task.
- Do not implement rollback preview APIs in this task.

## Acceptance Criteria

- A write/service operation trace can show which tool ran, what it observed, and
  how verification confirmed or failed the result.
- A read-only diagnosis can show logs/status/config/knowledge evidence before
  the final answer.
- A failed tool call cannot appear as successful evidence in trace data or UI.
- Existing trace payload consumers remain backward-compatible.
- Backend tests cover success, failure, inference/skipped, and runbook evidence
  events.
- Frontend build passes.
