# Keep Requests Running Across Navigation

## Problem

When a user sends an OpsGuard request and then switches to another conversation or page, the submitted operation is terminated. The UI behavior suggests the request is tied to the currently mounted chat view or active WebSocket connection instead of continuing as a server-side operation for that session.

## Requirements

- Switching away from a conversation must not cancel the already submitted Agent or Runbook operation for that session.
- A WebSocket disconnect should remove only the live client connection, not the session runtime state or active task.
- Streaming updates should be best-effort: if the client is disconnected, the operation should continue, persist audit trace and assistant responses, and resume normal updates when the session reconnects.
- Reconnecting to a session with an active operation should tell the client that work is still running.
- Pending approvals should not be rejected merely because the client disconnected; reconnecting to the same session should resend pending approval prompts.
- Regression coverage should prove that disconnect cleanup does not cancel active tasks or pending approvals.

## Notes

- This is a real-execution correctness bug. The fix must avoid fake completion status and must not silently swallow backend operation failures.
