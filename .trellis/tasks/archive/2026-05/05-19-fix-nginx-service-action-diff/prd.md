# Fix Nginx Service Action Diff

## Problem

When nginx is stopped and the user asks OpsGuard to start it, the Agent proposes `restart_service({"service":"nginx"})` instead of a start operation. After approval, the verification trace shows a before/after diff that claims nginx was running before the restart, even though the user had stopped it.

## Goals

- Ensure service start requests can execute a true start operation rather than being forced through restart.
- Ensure service before/after change diff reflects real system state captured before and after execution.
- Avoid hardcoded or fake "Before" values in service verification output.

## Acceptance Criteria

- `tools_registry` exposes a service start tool with WRITE risk.
- Service action impact and verification handle `start_service` consistently with restart/stop.
- Change diff captures service state before executing write tools and compares it to live state after execution.
- Backend import smoke test passes.
