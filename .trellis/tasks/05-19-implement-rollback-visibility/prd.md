# Implement Rollback Visibility and Manual Restore

## Background

OpsGuard already creates backups for some write/destructive operations, but the
capability is mostly internal. Users cannot see rollback coverage before
approval, list rollback points after execution, or trigger a controlled restore.

This task implements the first P0 slice of "Pre-Execution Preview and Rollback
Control" from `docs/ops-innovation-roadmap-prd.md`.

## Scope

- Add rollback/preview capability metadata for registered tools.
- Surface impact and rollback coverage in approval requests.
- Emit rollback point trace evidence after backed-up file/directory changes.
- Add backup listing and approved rollback endpoints/tools.
- Treat rollback execution as write/destructive.

## Functional Requirements

- Tool definitions include:
  - `supports_preview`
  - `preview_strategy`
  - `supports_rollback`
  - `rollback_strategy`
- Approval requests for write/destructive actions include:
  - target resource,
  - expected impact,
  - rollback option,
  - rollback confidence,
  - verification plan,
  - explicit wording when no reliable rollback exists.
- After a successful write/destructive action with a backup, trace shows:
  - rollback id,
  - target,
  - strategy,
  - created_at,
  - restore availability.
- Add APIs:
  - `GET /api/backups`
  - `GET /api/backups?filepath=...`
  - `POST /api/backups/{id}/rollback`
- Register MCP tools:
  - `list_backups`
  - `rollback_backup`
- `rollback_backup` must be write/destructive and approval-gated.

## Non-Goals

- Do not implement generic host sandboxing.
- Do not claim reliable rollback for service/process/package/firewall actions
  unless an actual inverse/backup exists.
- Do not implement full Runbook rollback plans yet.

## Acceptance Criteria

- Restarting nginx says service may be unavailable briefly and does not claim
  full rollback.
- File write/delete approval shows backup-based rollback coverage.
- After modifying a backed-up file, trace exposes a rollback id.
- Backup list API returns real backup records from the backup manager.
- Rollback API restores a backup only through an explicit request.
- `rollback_backup` is registered as a destructive/write tool and requires
  approval through the existing Agent flow.
- Regression tests cover capability metadata, approval impact text, backup list,
  rollback restore, and rollback tool risk classification.
