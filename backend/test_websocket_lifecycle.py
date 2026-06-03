"""Regression checks for WebSocket disconnect vs operation lifecycle.

These tests do not start the FastAPI server. They exercise the gateway helpers
that previously cancelled active operations when the user switched sessions.
"""

import asyncio
import os
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.websocket import gateway
from app.websocket.approval import approval_manager


class FakeWebSocket:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(data)


async def _sleep_forever() -> None:
    await asyncio.Event().wait()


def test_disconnect_does_not_cancel_active_task_or_approval() -> None:
    async def scenario() -> None:
        session_id = "disconnect-keeps-work"
        ws = FakeWebSocket()
        task = asyncio.create_task(_sleep_forever())
        future = asyncio.get_running_loop().create_future()

        try:
            gateway.manager._connections[session_id] = ws
            gateway._set_active_task(session_id, task)
            approval_manager.register_pending(
                "approval-1",
                session_id,
                "restart_service",
                {"service": "nginx"},
                "write",
                "Restart service",
                future,
            )

            gateway._disconnect_client(session_id, ws)

            assert gateway.manager.get(session_id) is None
            assert not task.done()
            assert not task.cancelled()
            assert not future.done()
            assert approval_manager.get_pending(session_id)
        finally:
            task.cancel()
            approval_manager.cancel_all(session_id)
            gateway._session_state.pop(session_id, None)
            gateway.manager.disconnect(session_id)
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_stale_disconnect_does_not_remove_new_connection() -> None:
    session_id = "stale-disconnect"
    old_ws = FakeWebSocket()
    new_ws = FakeWebSocket()

    try:
        gateway.manager._connections[session_id] = new_ws
        gateway.manager.disconnect(session_id, old_ws)
        assert gateway.manager.get(session_id) is new_ws
    finally:
        gateway.manager.disconnect(session_id)


def test_send_to_disconnected_session_is_best_effort() -> None:
    async def scenario() -> None:
        sender = gateway._make_sender("missing-session")
        await sender({"type": "trace", "content": "still running"})

        session_id = "failed-send"
        gateway.manager._connections[session_id] = FakeWebSocket(fail=True)
        sent = await gateway._send_to_session(session_id, {"type": "trace"})
        assert sent is False
        assert gateway.manager.get(session_id) is None

    asyncio.run(scenario())


def test_reconnect_snapshot_replays_running_state_and_approval() -> None:
    async def scenario() -> None:
        session_id = "snapshot"
        ws = FakeWebSocket()
        task = asyncio.create_task(_sleep_forever())
        future = asyncio.get_running_loop().create_future()

        try:
            state = gateway._get_state(session_id)
            gateway._set_active_task(session_id, task)
            approval_manager.register_pending(
                "approval-2",
                session_id,
                "start_service",
                {"service": "nginx"},
                "write",
                "Start service",
                future,
                preview={"status": "partial", "preview_type": "impact_only", "target": "service"},
            )

            await gateway._send_runtime_snapshot(ws, session_id, state)

            assert any(msg["type"] == "thinking" for msg in ws.sent)
            approvals = [msg for msg in ws.sent if msg["type"] == "approval_request"]
            assert approvals
            assert approvals[0]["request_id"] == "approval-2"
            assert approvals[0]["preview"]["target"] == "service"
        finally:
            task.cancel()
            approval_manager.cancel_all(session_id)
            gateway._session_state.pop(session_id, None)
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def main() -> None:
    test_disconnect_does_not_cancel_active_task_or_approval()
    test_stale_disconnect_does_not_remove_new_connection()
    test_send_to_disconnected_session_is_best_effort()
    test_reconnect_snapshot_replays_running_state_and_approval()
    print("websocket lifecycle regression OK")


if __name__ == "__main__":
    main()
