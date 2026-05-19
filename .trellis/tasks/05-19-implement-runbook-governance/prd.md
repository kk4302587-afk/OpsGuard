# Implement Runbook Governance

## Background

OpsGuard can save and replay Runbooks, but saved Runbooks currently behave like
static tool sequences. Operators need to know whether a Runbook is reliable,
fresh, recently failing, or using missing tools before replaying it.

This task implements the P0 "Runbook Governance: Versioning, Health, and Aging"
MVP from `docs/ops-innovation-roadmap-prd.md`.

## Scope

- Add Runbook governance metadata to storage and API responses.
- Update Runbook replay bookkeeping with success/failure stats.
- Show health/freshness/failure information in existing Runbook UI.
- Add a read-only validation path for Runbooks.

## Functional Requirements

- Extend Runbook records with:
  - `version`
  - `success_count`
  - `failure_count`
  - `last_success`
  - `last_failure`
  - `last_failure_reason`
  - `staleness_status`: `fresh`, `warning`, `stale`
  - `updated_from_session_id`
- Use safe SQLite schema evolution with `CREATE TABLE IF NOT EXISTS` and
  idempotent `ALTER TABLE ... ADD COLUMN`.
- When auto-updating an existing Runbook, increment `version` and preserve
  execution statistics.
- Runbook replay must:
  - increment `success_count` only when every step succeeds,
  - increment `failure_count` on partial failure, rejection, missing tool, or
    execution exception,
  - set `last_success` / `last_failure`,
  - record the exact failing step and reason.
- Compute staleness using:
  - missing tool -> `stale`,
  - any recent failure -> at least `warning`,
  - repeated failures >= 3 -> `stale`,
  - no successful run in 30 days -> `warning`,
  - no successful run in 90 days -> `stale`.
- Add a read-only validate Runbook action that checks tool existence and common
  target availability without executing write/destructive steps.
- UI should show:
  - version,
  - success/failure counts,
  - success rate,
  - staleness badge,
  - last failure reason when present,
  - validate action/result.

## Non-Goals

- Do not implement rollback preview/control in this task.
- Do not add a new Runbook page.
- Do not implement full historical version browsing UI.
- Do not auto-run write/destructive validation checks.

## Acceptance Criteria

- Replaying a successful Runbook updates success statistics.
- A failed Runbook records failing step and reason.
- Existing saved Runbooks migrate safely without data loss.
- Runbook list/suggestion data includes health metadata.
- UI warns before replaying stale or recently failing Runbooks.
- Validate Runbook returns read-only validation status and does not execute
  write/destructive steps.
- Backend regression tests cover migration, success/failure bookkeeping,
  staleness calculation, and validation.
- Frontend build passes.
