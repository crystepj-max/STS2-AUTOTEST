"""Tests for core/session_queue.py — priority FIFO queue (Story 4.6, FR65)."""

from __future__ import annotations

from sts2_autotest.core.session_queue import (
    QueuePriority,
    SessionQueue,
    SessionRequest,
)


class TestQueuePriority:
    def test_enum_values(self) -> None:
        assert QueuePriority.HIGH.value == "HIGH"
        assert QueuePriority.NORMAL.value == "NORMAL"
        assert QueuePriority.LOW.value == "LOW"

    def test_enum_str(self) -> None:
        assert str(QueuePriority.HIGH) == "HIGH"


class TestSessionRequest:
    def test_defaults(self) -> None:
        req = SessionRequest(session_id="sess-1")
        assert req.session_id == "sess-1"
        assert req.priority == QueuePriority.NORMAL
        assert req.timeout == 60.0

    def test_custom_priority(self) -> None:
        req = SessionRequest(session_id="sess-1", priority=QueuePriority.HIGH)
        assert req.priority == QueuePriority.HIGH


class TestSessionQueue:
    def test_new_queue_empty(self) -> None:
        q = SessionQueue()
        assert q.queue_depth == 0
        assert q.dequeue() is None
        assert q.peek() is None

    def test_enqueue_dequeue_fifo(self) -> None:
        """Same-priority requests are dequeued FIFO."""
        q = SessionQueue()
        r1 = SessionRequest(session_id="s1", priority=QueuePriority.NORMAL)
        r2 = SessionRequest(session_id="s2", priority=QueuePriority.NORMAL)
        assert q.enqueue(r1) is True
        assert q.enqueue(r2) is True

        assert q.dequeue() is r1
        assert q.dequeue() is r2

    def test_priority_order(self) -> None:
        """HIGH priority dequeued before NORMAL before LOW."""
        q = SessionQueue()
        r_low = SessionRequest(session_id="low", priority=QueuePriority.LOW)
        r_high = SessionRequest(session_id="high", priority=QueuePriority.HIGH)
        r_normal = SessionRequest(session_id="normal", priority=QueuePriority.NORMAL)

        # Enqueue in reverse priority order
        q.enqueue(r_low)
        q.enqueue(r_high)
        q.enqueue(r_normal)

        # Dequeue should respect priority
        assert q.dequeue() is r_high
        assert q.dequeue() is r_normal
        assert q.dequeue() is r_low

    def test_fifo_within_same_priority(self) -> None:
        """Multiple HIGH items: FIFO within the same priority level."""
        q = SessionQueue()
        items = [SessionRequest(session_id=f"s{i}", priority=QueuePriority.HIGH) for i in range(5)]
        for it in items:
            q.enqueue(it)

        for i in range(5):
            assert q.dequeue() is items[i]

    def test_max_depth_backpressure(self) -> None:
        """When queue is full, enqueue returns False."""
        q = SessionQueue(max_depth=2)
        r1 = SessionRequest(session_id="s1")
        r2 = SessionRequest(session_id="s2")
        r3 = SessionRequest(session_id="s3")

        assert q.enqueue(r1) is True
        assert q.enqueue(r2) is True
        assert q.enqueue(r3) is False  # Backpressure

    def test_dequeue_after_max_depth(self) -> None:
        """After dequeue, new requests can be enqueued."""
        q = SessionQueue(max_depth=2)
        q.enqueue(SessionRequest(session_id="s1"))
        q.enqueue(SessionRequest(session_id="s2"))
        assert q.enqueue(SessionRequest(session_id="s3")) is False

        q.dequeue()
        assert q.enqueue(SessionRequest(session_id="s3")) is True

    def test_cancel_removes_request(self) -> None:
        """Cancelled request is not returned by dequeue."""
        q = SessionQueue()
        r1 = SessionRequest(session_id="s1")
        r2 = SessionRequest(session_id="s2")
        q.enqueue(r1)
        q.enqueue(r2)

        assert q.cancel("s1") is True
        assert q.dequeue() is r2
        assert q.dequeue() is None

    def test_cancel_nonexistent(self) -> None:
        """Cancelling a non-existent session returns False."""
        q = SessionQueue()
        assert q.cancel("nonexistent") is False

    def test_peek_does_not_remove(self) -> None:
        """peek returns the next item without removing it."""
        q = SessionQueue()
        r1 = SessionRequest(session_id="s1")
        q.enqueue(r1)

        peeked = q.peek()
        assert peeked is r1
        # Still in queue
        assert q.dequeue() is r1

    def test_peek_empty(self) -> None:
        q = SessionQueue()
        assert q.peek() is None

    def test_clear_empties_queue(self) -> None:
        q = SessionQueue()
        q.enqueue(SessionRequest(session_id="s1"))
        q.enqueue(SessionRequest(session_id="s2"))
        q.clear()
        assert q.queue_depth == 0
        assert q.dequeue() is None

    def test_is_full_property(self) -> None:
        q = SessionQueue(max_depth=1)
        assert q.is_full is False
        q.enqueue(SessionRequest(session_id="s1"))
        assert q.is_full is True

    def test_default_max_depth(self) -> None:
        q = SessionQueue()
        assert q.max_depth == 10

    def test_cancel_frees_capacity(self) -> None:
        """Regression: cancelled item frees capacity immediately for backpressure."""
        q = SessionQueue(max_depth=1)
        q.enqueue(SessionRequest(session_id="a"))
        assert q.is_full is True

        q.cancel("a")
        assert q.queue_depth == 0
        assert q.is_full is False
        # A new request should be accepted
        assert q.enqueue(SessionRequest(session_id="b")) is True
