"""Tests for core/recovery.py — StubRecoveryStrategy."""

from sts2_autotest.core.recovery import FailureRecord, StubRecoveryStrategy


class TestStubRecoveryStrategy:
    """MVP always returns TERMINATE."""

    def test_always_returns_terminate(self) -> None:
        strategy = StubRecoveryStrategy()
        result = strategy.decide(Exception("test"), [])
        assert result == "TERMINATE"

    def test_with_failure_history(self) -> None:
        strategy = StubRecoveryStrategy()
        history = [
            FailureRecord(
                error_type="adapter", message="timeout", timestamp="2026-01-01T00:00:00Z"
            ),
            FailureRecord(
                error_type="adapter", message="timeout", timestamp="2026-01-01T00:01:00Z"
            ),
        ]
        result = strategy.decide(Exception("third failure"), history)
        assert result == "TERMINATE"
