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

    def disconnect(self, session_id: str, websocket: WebSocket | None = None):
        """Remove a disconnected session connection.

        When a stale socket disconnects after a reconnect, do not remove the
        newer connection for the same session.
        """
        current = self._connections.get(session_id)
        if websocket is not None and current is not websocket:
            return
        self._connections.pop(session_id, None)
        logger.info(f"Session {session_id} removed. Active: {len(self._connections)}")

    def get(self, session_id: str) -> WebSocket | None:
        """Get WebSocket connection by session ID."""
        return self._connections.get(session_id)

    async def send_to_session(self, session_id: str, data: dict) -> bool:
        """Best-effort send to the current connection for a session."""
        ws = self._connections.get(session_id)
        if not ws:
            return False
        try:
            await ws.send_json(data)
            return True
        except Exception as e:
            logger.warning(f"Failed to send to session {session_id}: {e}")
            self.disconnect(session_id, ws)
            return False

    @property
    def active_count(self) -> int:
        return len(self._connections)
