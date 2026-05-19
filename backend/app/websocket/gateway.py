"""WebSocket gateway for real-time Agent communication.

Message protocol:
  Client → Server:
    {type: "message", content}                          regular Agent turn
    {type: "approve", request_id, approved}             response to approval modal
    {type: "runbook_decision", decision, original_message?}
                                                        "execute" or "dismiss" the
                                                        last suggested runbook
    {type: "run_runbook", runbook_id}                   direct execute from Runbook UI
    {type: "ping"}                                      heartbeat

  Server → Client:
    {type: "thinking", content}
    {type: "tool_call", ...}
    {type: "approval_request", request_id, command, risk_level, description, impact}
    {type: "runbook_suggestion", runbook_id, name, description, step_count,
       match_ratio, original_message}                   match-and-suggest before
                                                        running Agent
    {type: "response", content, message_id}
    {type: "trace", phase, event_type, content}
    {type: "error", content}
    {type: "pong"}
"""

import asyncio
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from app.websocket.manager import ConnectionManager

router = APIRouter()
manager = ConnectionManager()

# Per-session runtime state. One entry per submitted session operation.
# Holds the currently running background task (Agent or Runbook replay) and
# the runbook suggestion the user has not yet decided on.
_session_state: dict[str, dict] = {}


def _get_state(session_id: str) -> dict:
    """Return the session's mutable state dict, creating it if absent."""
    st = _session_state.get(session_id)
    if st is None:
        st = {"active_task": None, "pending_suggestion": None}
        _session_state[session_id] = st
    return st


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for Agent + Runbook interaction. See module docstring."""
    await manager.connect(websocket, session_id)
    state = _get_state(session_id)
    await _send_runtime_snapshot(websocket, session_id, state)

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get("type")

            if msg_type == "message":
                # Reject if another long-running task is in flight
                active = state.get("active_task")
                if active and not active.done():
                    await websocket.send_json({"type": "error", "content": "上一个请求还在处理中，请稍候"})
                    continue
                _set_active_task(session_id, asyncio.create_task(
                    handle_user_message(session_id, message)
                ))

            elif msg_type == "approve":
                # Approval responses must NOT be blocked by the active task — they
                # are what unblock it. Handle inline.
                await handle_approval(websocket, session_id, message)

            elif msg_type == "runbook_decision":
                # User's choice on a previously suggested Runbook.
                active = state.get("active_task")
                if active and not active.done():
                    await websocket.send_json({"type": "error", "content": "上一个请求还在处理中，请稍候"})
                    continue
                _set_active_task(session_id, asyncio.create_task(
                    handle_runbook_decision(session_id, message)
                ))

            elif msg_type == "run_runbook":
                # Direct invocation from the Runbook page (no suggestion involved).
                active = state.get("active_task")
                if active and not active.done():
                    await websocket.send_json({"type": "error", "content": "上一个请求还在处理中，请稍候"})
                    continue
                _set_active_task(session_id, asyncio.create_task(
                    handle_run_runbook(session_id, message)
                ))

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})

            else:
                await websocket.send_json({
                    "type": "error",
                    "content": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        _disconnect_client(session_id, websocket)
        logger.info(f"Session {session_id} disconnected")
    except Exception as e:
        logger.error(f"WebSocket error in session {session_id}: {e}")
        _disconnect_client(session_id, websocket)


def _disconnect_client(session_id: str, websocket: WebSocket) -> None:
    """Drop only the client connection; submitted operations keep running."""
    manager.disconnect(session_id, websocket)


def _set_active_task(session_id: str, task: asyncio.Task) -> None:
    """Track a submitted operation independently from the client socket."""
    state = _get_state(session_id)
    state["active_task"] = task

    def _clear_finished(done_task: asyncio.Task) -> None:
        current_state = _session_state.get(session_id)
        if current_state and current_state.get("active_task") is done_task:
            current_state["active_task"] = None
            if (
                manager.get(session_id) is None
                and not current_state.get("pending_suggestion")
            ):
                _session_state.pop(session_id, None)
        if done_task.cancelled():
            logger.warning(f"Session task cancelled: {session_id}")
            return
        error = done_task.exception()
        if error:
            logger.error(f"Session task failed: {session_id}: {error}")

    task.add_done_callback(_clear_finished)


async def _send_runtime_snapshot(websocket: WebSocket, session_id: str, state: dict) -> None:
    """Replay live runtime prompts that are not persisted as messages."""
    active = state.get("active_task")
    if active and not active.done():
        await websocket.send_json({
            "type": "thinking",
            "content": "请求仍在后台处理中...",
            "timestamp": datetime.now().isoformat(),
        })

    suggestion = state.get("pending_suggestion")
    if suggestion:
        await websocket.send_json({
            "type": "runbook_suggestion",
            "runbook_id": suggestion["runbook_id"],
            "name": suggestion["name"],
            "description": suggestion.get("description") or "",
            "step_count": suggestion.get("step_count", 0),
            "match_ratio": suggestion.get("match_ratio", 0.0),
            "original_message": suggestion.get("original_message") or "",
            "timestamp": datetime.now().isoformat(),
        })

    from app.websocket.approval import approval_manager
    for request in approval_manager.get_pending(session_id):
        await websocket.send_json({
            "type": "approval_request",
            "request_id": request.request_id,
            "command": f"{request.tool_name}({json.dumps(request.tool_args, ensure_ascii=False)})",
            "risk_level": request.risk_level,
            "description": request.description,
            "timestamp": datetime.now().isoformat(),
        })


async def handle_user_message(session_id: str, message: dict):
    """Process a user message.

    Step 1: persist the user message.
    Step 2: fuzzy-match against existing runbooks. If a high-confidence match
            is found, push a ``runbook_suggestion`` and PAUSE — the Agent will
            be started only after the user decides (or dismisses) via
            ``runbook_decision``.
    Step 3: otherwise, run the regular Agent pipeline.
    """
    content = message.get("content", "").strip()
    if not content:
        return

    await _persist_user_message(session_id, content)

    # === Step 2: Runbook fuzzy match (C side) ===
    try:
        from app.agent.runbook_matcher import find_matching_runbook
        match = await find_matching_runbook(content)
    except Exception as e:
        logger.warning(f"Runbook match failed (non-fatal): {e}")
        match = None

    if match:
        # Cache the suggestion so the upcoming runbook_decision can find it.
        state = _get_state(session_id)
        state["pending_suggestion"] = {
            "runbook_id": match["id"],
            "name": match["name"],
            "description": match.get("description") or "",
            "step_count": match.get("step_count", 0),
            "match_ratio": match.get("match_ratio", 0.0),
            "original_message": content,
        }
        await _send_to_session(session_id, {
            "type": "runbook_suggestion",
            "runbook_id": match["id"],
            "name": match["name"],
            "description": match.get("description") or "",
            "step_count": match.get("step_count", 0),
            "match_ratio": match.get("match_ratio", 0.0),
            "original_message": content,
            "timestamp": datetime.now().isoformat(),
        })
        logger.info(
            f"Runbook suggestion for session {session_id}: "
            f"{match['name']!r} ratio={match['match_ratio']}"
        )
        return  # Stop here — wait for runbook_decision.

    # === Step 3: No match → regular Agent pipeline ===
    await _run_agent_for_message(session_id, content)


async def handle_runbook_decision(session_id: str, message: dict):
    """Handle the user's accept/dismiss of a previously suggested runbook."""
    decision = message.get("decision")  # "execute" | "dismiss"
    state = _get_state(session_id)
    suggestion = state.pop("pending_suggestion", None)

    if not suggestion:
        await _send_to_session(session_id, {
            "type": "error",
            "content": "没有待决策的 Runbook 建议",
        })
        return

    if decision == "execute":
        await _replay_runbook(
            session_id,
            runbook_id=suggestion["runbook_id"],
            origin="suggestion",
        )
    else:
        # Dismiss → fall back to the regular Agent flow with the original message.
        original = suggestion.get("original_message") or message.get("original_message") or ""
        if not original:
            await _send_to_session(session_id, {
                "type": "error",
                "content": "Runbook 建议已忽略，但找不到原始问题",
            })
            return
        await _run_agent_for_message(session_id, original)


async def handle_run_runbook(session_id: str, message: dict):
    """Handle a direct ``run_runbook`` request (from the Runbook UI)."""
    runbook_id = message.get("runbook_id")
    if not runbook_id:
        await _send_to_session(session_id, {"type": "error", "content": "缺少 runbook_id"})
        return
    await _replay_runbook(
        session_id,
        runbook_id=runbook_id,
        origin="direct",
    )


# === Helpers shared by the message / decision / direct paths ===


async def _persist_user_message(session_id: str, content: str) -> str:
    """Store a user message and bump the session title if it's the first one."""
    import aiosqlite
    from app.database import get_knowledge_db_path
    user_msg_id = str(uuid.uuid4())
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute(
            "INSERT OR REPLACE INTO messages (id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_msg_id, session_id, "user", content, datetime.now().isoformat()),
        )
        cursor = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'",
            (session_id,),
        )
        count = (await cursor.fetchone())[0]
        if count <= 1:
            title = content[:30] + ("..." if len(content) > 30 else "")
            await db.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, datetime.now().isoformat(), session_id),
            )
        await db.commit()
    return user_msg_id


async def _send_to_session(session_id: str, data: dict) -> bool:
    data.setdefault("timestamp", datetime.now().isoformat())
    return await manager.send_to_session(session_id, data)


def _make_sender(session_id: str):
    """Build a send_to_client callback that auto-stamps timestamps."""
    async def send_to_client(data: dict):
        await _send_to_session(session_id, data)
    return send_to_client


async def _save_assistant_response(session_id: str, response: str) -> str:
    """Persist an assistant message and return its id."""
    import aiosqlite
    from app.database import get_knowledge_db_path
    assistant_msg_id = str(uuid.uuid4())
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute(
            "INSERT OR REPLACE INTO messages (id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (assistant_msg_id, session_id, "assistant", response, datetime.now().isoformat()),
        )
        await db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), session_id),
        )
        await db.commit()
    return assistant_msg_id


async def _run_agent_for_message(session_id: str, content: str) -> None:
    """Run the full Agent pipeline on a user message and stream results."""
    import aiosqlite
    from app.database import get_knowledge_db_path

    await _send_to_session(session_id, {"type": "thinking", "content": "正在分析您的请求..."})
    send_to_client = _make_sender(session_id)

    try:
        from app.agent.graph import run_agent

        conversation_history: list[dict] = []
        async with aiosqlite.connect(get_knowledge_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
            for row in rows:
                conversation_history.append({"role": row["role"], "content": row["content"]})

        response = await run_agent(
            session_id=session_id,
            user_message=content,
            conversation_history=conversation_history[:-1],  # exclude the just-saved msg
            send_to_client=send_to_client,
        )

        assistant_msg_id = await _save_assistant_response(session_id, response)
        await _send_to_session(session_id, {
            "type": "response",
            "content": response,
            "message_id": assistant_msg_id,
            "timestamp": datetime.now().isoformat(),
        })

    except Exception as e:
        logger.error(f"Agent error in session {session_id}: {e}")
        await _send_to_session(session_id, {
            "type": "error",
            "content": f"Agent 执行出错: {e}",
            "timestamp": datetime.now().isoformat(),
        })


async def _replay_runbook(
    session_id: str,
    *,
    runbook_id: str,
    origin: str,
) -> None:
    """Run a Runbook (B side) end-to-end and stream progress.

    ``origin`` is just for logging ("suggestion" vs "direct").
    """
    await _send_to_session(session_id, {"type": "thinking", "content": "正在准备执行 Runbook..."})
    send_to_client = _make_sender(session_id)

    try:
        from app.agent.runbook_executor import execute_runbook
        response = await execute_runbook(
            session_id=session_id,
            runbook_id=runbook_id,
            send_to_client=send_to_client,
        )

        assistant_msg_id = await _save_assistant_response(session_id, response)
        await _send_to_session(session_id, {
            "type": "response",
            "content": response,
            "message_id": assistant_msg_id,
            "timestamp": datetime.now().isoformat(),
        })
        logger.info(f"Runbook {runbook_id} replay finished ({origin})")

    except Exception as e:
        logger.error(f"Runbook replay failed in session {session_id}: {e}")
        await _send_to_session(session_id, {
            "type": "error",
            "content": f"Runbook 执行出错: {e}",
            "timestamp": datetime.now().isoformat(),
        })


async def handle_approval(websocket: WebSocket, session_id: str, message: dict):
    """Handle user's approval/rejection of a high-risk operation."""
    from app.websocket.approval import approval_manager

    request_id = message.get("request_id")
    approved = message.get("approved", False)

    if not request_id:
        await websocket.send_json({"type": "error", "content": "缺少 request_id"})
        return

    resolved = approval_manager.resolve_approval(request_id, approved)

    if not resolved:
        await websocket.send_json({
            "type": "error",
            "content": f"审批请求 {request_id} 未找到或已过期",
        })
        return

    logger.info(f"Approval response for {request_id}: {'approved' if approved else 'rejected'}")

    await websocket.send_json({
        "type": "trace",
        "phase": "approval_response",
        "event_type": "success" if approved else "failure",
        "content": f"操作已{'批准' if approved else '拒绝'}",
        "timestamp": datetime.now().isoformat(),
    })
