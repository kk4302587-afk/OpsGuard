# Fix False Write Completion Guard

## Problem

When the user asks for a read-only action such as viewing nginx config, the Agent may respond with analysis text like "当前已成功启动". The hallucination guard matches "已成功启动" as if the model claimed to complete a write operation, even though the phrase describes observed system state and the user did not request a write.

## Goals

- Keep the guard for genuine write requests where the model claims completion without invoking a write tool.
- Do not trigger the guard for read-only user requests and read-only tool turns.
- Make the trace message appear only for likely write-intent turns.

## Acceptance Criteria

- Read-only prompt "查看一下 nginx 配置文件" with response text containing "当前已成功启动" does not trigger `_claims_write_completion` guard path.
- Write prompt "帮我重启 nginx" with response text "已重启 nginx" still triggers if no write tool ran.
- Backend compile/import smoke checks pass.
