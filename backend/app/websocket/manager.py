"""WebSocket connection manager."""

from fastapi import WebSocket
from loguru import logger


class ConnectionManager:
    """Manages active WebSocket connections by session ID."""

    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self._connections[session_id] = websocket
        logger.info(f"Session {session_id} connected. Active: {len(self._connections)}")

    def disconnect(self, session_id: str):
        """Remove a disconnected session."""
        self._connections.pop(session_id, None)
        logger.info(f"Session {session_id} removed. Active: {len(self._connections)}")

    def get(self, session_id: str) -> WebSocket | None:
        """Get WebSocket connection by session ID."""
        return self._connections.get(session_id)

    async def send_to_session(self, session_id: str, data: dict):
        """Send a message to a specific session."""
        ws = self._connections.get(session_id)
        if ws:
            await ws.send_json(data)

    @property
    def active_count(self) -> int:
        return len(self._connections)
