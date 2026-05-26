"""Priority FIFO session queue with backpressure for concurrency control (FR65)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from heapq import heappop, heappush
from typing import Any


class QueuePriority(StrEnum):
    """Session request priority levels."""

    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


_PRIORITY_ORDER: dict[QueuePriority, int] = {
    QueuePriority.HIGH: 0,
    QueuePriority.NORMAL: 1,
    QueuePriority.LOW: 2,
}


@dataclass(order=True)
class _QueueEntry:
    """Internal heap entry — ordered by priority then creation time."""

    priority_order: int
    created_at: str
    request: Any = field(compare=False)  # SessionRequest, no-op in comparison


@dataclass
class SessionRequest:
    """A queued session request with priority and timeout."""

    session_id: str
    priority: QueuePriority = QueuePriority.NORMAL
    created_at: str = ""
    timeout: float = 60.0


class SessionQueue:
    """Priority FIFO session queue with backpressure (FR65).

    Same-priority requests are dequeued in FIFO order.
    When queue depth exceeds max_depth, enqueue returns False (backpressure).
    """

    def __init__(self, max_depth: int = 10) -> None:
        self._max_depth = max_depth
        self._heap: list[_QueueEntry] = []
        self._pending: dict[str, SessionRequest] = {}
        self._paused = False

    @property
    def max_depth(self) -> int:
        return self._max_depth

    @property
    def queue_depth(self) -> int:
        """Number of active (non-cancelled) pending requests."""
        return len(self._pending)

    @property
    def is_full(self) -> bool:
        """Queue depth has reached max_depth — backpressure active."""
        return self.queue_depth >= self._max_depth

    @property
    def is_paused(self) -> bool:
        """Whether scheduling new queued requests is currently paused."""
        return self._paused

    def pause(self) -> None:
        """Pause dequeue scheduling without dropping queued requests."""
        self._paused = True

    def resume(self) -> None:
        """Resume dequeue scheduling."""
        self._paused = False

    def enqueue(self, request: SessionRequest) -> bool:
        """Add a request to the queue.

        Returns True on success, False if queue is full (backpressure).
        """
        if self.is_full:
            return False

        if not request.created_at:
            request.created_at = datetime.now(timezone.utc).isoformat()

        prio = _PRIORITY_ORDER.get(request.priority, 1)
        entry = _QueueEntry(
            priority_order=prio,
            created_at=request.created_at,
            request=request,
        )
        heappush(self._heap, entry)
        self._pending[request.session_id] = request
        return True

    def dequeue(self) -> SessionRequest | None:
        """Remove and return the highest-priority longest-waiting request.

        Returns None if the queue is empty.
        """
        if self._paused:
            return None

        while self._heap:
            entry = heappop(self._heap)
            req: SessionRequest = entry.request
            if req.session_id in self._pending:
                del self._pending[req.session_id]
                return req
            # Entry was cancelled — skip
        return None

    def cancel(self, session_id: str) -> bool:
        """Cancel a queued request by session_id.

        Returns True if found and cancelled, False otherwise.
        The entry remains in the heap but is skipped on dequeue.
        """
        if session_id in self._pending:
            del self._pending[session_id]
            return True
        return False

    def peek(self) -> SessionRequest | None:
        """Return the next request without removing it, or None if empty."""
        while self._heap:
            entry = self._heap[0]
            req: SessionRequest = entry.request
            if req.session_id in self._pending:
                return req
            # Stale entry at top — pop and continue
            heappop(self._heap)
        return None

    def clear(self) -> None:
        """Remove all pending requests."""
        self._heap.clear()
        self._pending.clear()
