# Broaden Write Guard Regression Coverage

## Problem

The write-completion hallucination guard was fixed for one observed read-only nginx config case, but the underlying risk is broader: any read-only or diagnostic response may describe system state with completion-like phrases such as "已启动", "已停止", or "已配置".

## Goals

- Validate the guard against multiple read-only operation categories, not just nginx config reads.
- Preserve guard behavior for real write-intent requests that claim completion without a write tool.
- Add a repeatable regression script that can run without starting the FastAPI server or modifying the host.

## Acceptance Criteria

- Read-only scenarios for config, status, logs, disk/process diagnosis, report/knowledge queries do not trigger the guard.
- Write-intent scenarios for start/restart/stop/delete/modify/install operations do trigger if the response claims completion and no write tool ran.
- Ambiguous state inspection phrases like "查看 nginx 启动状态" remain read-only.
- Backend compile/import and the regression script pass.
