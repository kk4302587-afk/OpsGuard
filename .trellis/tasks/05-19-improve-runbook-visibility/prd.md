# Improve Runbook Execution Visibility

## Problem

Runbook replay currently streams low-level tool calls such as
`find_large_files({"path": "/tmp", ...})` and ends with a short "all steps
successful" message. Users cannot easily tell what the Runbook planned to do,
which steps were read-only vs write/destructive, what each step actually
checked or changed, and what the final outcome means.

## Requirements

- Before executing, stream a human-readable Runbook plan that lists every step,
  its purpose, target, and risk level.
- During execution, trace entries should lead with user-facing step labels and
  keep raw tool call details as secondary text.
- Step success traces should include a short result summary derived from real
  tool output, not just "执行成功".
- The final assistant reply should be an execution report with step outcomes,
  read/write/destructive counts, failure reason if any, and a clear statement
  about whether system changes occurred.
- Keep existing safety behavior: re-run guardrails, approvals for every write or
  destructive replay step, verification/diff for write steps.
- Add regression coverage for the streamed plan and final report formatting.
