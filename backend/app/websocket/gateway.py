"""WebSocket gateway for real-time Agent communication."""

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from app.websocket.manager import ConnectionManager

router = APIRouter()
manager = ConnectionManager()


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for Agent interaction.

    Message protocol:
    - Client → Server: { "type": "message", "content": "..." }
    - Client → Server: { "type": "approve", "request_id": "...", "approved": true/false }
    - Server → Client: { "type": "thinking", "content": "..." }
    - Server → Client: { "type": "tool_call", "tool": "...", "args": {...} }
    - Server → Client: { "type": "approval_request", "request_id": "...", "command": "...", "risk_level": "..." }
    - Server → Client: { "type": "response", "content": "..." }
    - Server → Client: { "type": "error", "content": "..." }
    - Server → Client: { "type": "trace", "phase": "...", "data": {...} }
    """
    await manager.connect(websocket, session_id)

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            msg_type = message.get("type")

            if msg_type == "message":
                # User sent a new message
                await handle_user_message(websocket, session_id, message)

            elif msg_type == "approve":
                # User responded to an approval request
                await handle_approval(websocket, session_id, message)

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})

            else:
                await websocket.send_json({
                    "type": "error",
                    "content": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        manager.disconnect(session_id)
        # Cancel any pending approvals for this session
        from app.websocket.approval import approval_manager
        approval_manager.cancel_all(session_id)
        logger.info(f"Session {session_id} disconnected")
    except Exception as e:
        logger.error(f"WebSocket error in session {session_id}: {e}")
        from app.websocket.approval import approval_manager
        approval_manager.cancel_all(session_id)
        manager.disconnect(session_id)


async def handle_user_message(websocket: WebSocket, session_id: str, message: dict):
    """Process a user message through the Agent pipeline."""
    content = message.get("content", "").strip()
    if not content:
        return

    # Save user message to database
    import aiosqlite
    from app.database import get_knowledge_db_path
    user_msg_id = str(uuid.uuid4())
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute(
            "INSERT OR REPLACE INTO messages (id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_msg_id, session_id, "user", content, datetime.now().isoformat()),
        )
        # Update session title from first message
        cursor = await db.execute("SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'", (session_id,))
        count = (await cursor.fetchone())[0]
        if count <= 1:
            title = content[:30] + ("..." if len(content) > 30 else "")
            await db.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?", (title, datetime.now().isoformat(), session_id))
        await db.commit()

    # Send acknowledgment
    await websocket.send_json({
        "type": "thinking",
        "content": "正在分析您的请求...",
    })

    # Define callback to send messages to client
    async def send_to_client(data: dict):
        data.setdefault("timestamp", datetime.now().isoformat())
        await websocket.send_json(data)

    try:
        # Run the Agent pipeline
        from app.agent.graph import run_agent

        conversation_history = []
        # Load conversation history from database
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
            conversation_history=conversation_history[:-1],  # Exclude the message we just added
            send_to_client=send_to_client,
        )

        # Save assistant response to database
        assistant_msg_id = str(uuid.uuid4())
        async with aiosqlite.connect(get_knowledge_db_path()) as db:
            await db.execute(
                "INSERT OR REPLACE INTO messages (id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                (assistant_msg_id, session_id, "assistant", response, datetime.now().isoformat()),
            )
            await db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (datetime.now().isoformat(), session_id))
            await db.commit()

        # Send final response
        await websocket.send_json({
            "type": "response",
            "content": response,
            "message_id": assistant_msg_id,
            "timestamp": datetime.now().isoformat(),
        })

    except Exception as e:
        logger.error(f"Agent error in session {session_id}: {e}")
        await websocket.send_json({
            "type": "error",
            "content": f"Agent 执行出错: {str(e)}",
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
