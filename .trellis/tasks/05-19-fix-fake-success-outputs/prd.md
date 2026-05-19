# Fix Fake Success Outputs

## Problem

Several OpsGuard paths can report success even when the underlying command failed, or present heuristic data as if it were verified fact. This can mislead users during operations.

## Goals

- Check `subprocess.run` return codes in MCP tools before returning `success=True`.
- Ensure firewall permanent-rule changes verify reload and effective runtime state.
- Make config syntax results explicit (`valid: true/false`, `checked: true/false`) instead of treating syntax errors as generic success.
- Make Runbook replay reuse the same post-action verification and before/after diff logic as normal Agent execution.
- Mark topology inferred relationships explicitly.

## Acceptance Criteria

- MCP read tools return `ToolResult(success=False)` when the subprocess exits non-zero, except for commands where non-zero is documented as meaningful data and is handled explicitly.
- `allow_port` / `block_port` fail if reload fails and verify whether the runtime firewall output reflects the change when possible.
- `check_config_syntax` returns structured validity information.
- Runbook replay emits verification and change-diff trace events for write/destructive steps.
- Topology graph relationship edges include an `inferred` boolean.
- Backend compile/import smoke checks pass.
