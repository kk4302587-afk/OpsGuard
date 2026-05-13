"""Approval mechanism for high-risk operations.

When the Agent wants to execute a WRITE/DESTRUCTIVE tool,
it creates a pending approval request and waits for the user
to approve or reject via WebSocket.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from loguru import logger


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


@dataclass
class ApprovalRequest:
    """A pending approval request."""
    request_id: str
    session_id: str
    tool_name: str
    tool_args: dict
    risk_level: str
    description: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: ApprovalStatus = ApprovalStatus.PENDING
    future: asyncio.Future | None = field(default=None, repr=False)


class ApprovalManager:
    """Manages pending approval requests per session.

    Flow:
    1. Agent calls `request_approval()` → creates a Future and sends request to client
    2. Client responds via WebSocket with approve/reject
    3. `resolve_approval()` is called → sets the Future result
    4. Agent's `await` on the Future unblocks and continues
    """

    def __init__(self):
        self._pending: dict[str, ApprovalRequest] = {}  # request_id → ApprovalRequest

    def register_pending(
        self,
        request_id: str,
        session_id: str,
        tool_name: str,
        tool_args: dict,
        risk_level: str,
        description: str,
        future: asyncio.Future,
    ):
        """Register a pending approval request with its Future.

        Must be called BEFORE sending the approval_request to the client,
        so that if the client responds immediately, the Future is already registered.
        """
        request = ApprovalRequest(
            request_id=request_id,
            session_id=session_id,
            tool_name=tool_name,
            tool_args=tool_args,
            risk_level=risk_level,
            description=description,
            future=future,
        )
        self._pending[request_id] = request
        logger.info(f"Approval registered: {request_id} ({tool_name})")

    def remove_pending(self, request_id: str):
        """Remove a pending request (after resolution or timeout)."""
        self._pending.pop(request_id, None)

    async def request_approval(
        self,
        request_id: str,
        session_id: str,
        tool_name: str,
        tool_args: dict,
        risk_level: str,
        description: str,
        timeout: float = 300.0,  # 5 minutes default timeout
    ) -> bool:
        """Create an approval request and wait for user response.

        Args:
            request_id: Unique ID for this request
            session_id: Session this belongs to
            tool_name: Name of the tool requesting approval
            tool_args: Arguments to the tool
            risk_level: Risk level (write/destructive)
            description: Human-readable description of the operation
            timeout: How long to wait for approval (seconds)

        Returns:
            True if approved, False if rejected or timed out
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        request = ApprovalRequest(
            request_id=request_id,
            session_id=session_id,
            tool_name=tool_name,
            tool_args=tool_args,
            risk_level=risk_level,
            description=description,
            future=future,
        )

        self._pending[request_id] = request
        logger.info(f"Approval requested: {request_id} ({tool_name})")

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"Approval timeout: {request_id}")
            request.status = ApprovalStatus.TIMEOUT
            return False
        finally:
            self._pending.pop(request_id, None)

    def resolve_approval(self, request_id: str, approved: bool) -> bool:
        """Resolve a pending approval request.

        Called when the user responds via WebSocket.

        Args:
            request_id: The request to resolve
            approved: Whether the user approved

        Returns:
            True if the request was found and resolved, False if not found
        """
        request = self._pending.get(request_id)
        if not request or not request.future:
            logger.warning(f"Approval resolve failed: {request_id} not found")
            return False

        if request.future.done():
            logger.warning(f"Approval already resolved: {request_id}")
            return False

        request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        request.future.set_result(approved)
        logger.info(f"Approval resolved: {request_id} → {'approved' if approved else 'rejected'}")
        return True

    def get_pending(self, session_id: str) -> list[ApprovalRequest]:
        """Get all pending approvals for a session."""
        return [r for r in self._pending.values() if r.session_id == session_id and r.status == ApprovalStatus.PENDING]

    def cancel_all(self, session_id: str):
        """Cancel all pending approvals for a session (e.g., on disconnect)."""
        to_cancel = [r for r in self._pending.values() if r.session_id == session_id]
        for request in to_cancel:
            if request.future and not request.future.done():
                request.future.set_result(False)
            request.status = ApprovalStatus.REJECTED
            self._pending.pop(request.request_id, None)
        if to_cancel:
            logger.info(f"Cancelled {len(to_cancel)} pending approvals for session {session_id}")


# Global approval manager instance
approval_manager = ApprovalManager()
