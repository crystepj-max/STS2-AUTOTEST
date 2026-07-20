"""Tests for adapters/base.py — Protocol, ActionResult, HealthStatus."""

from typing import Any

import pytest

from sts2_autotest.adapters.base import (
    ActionResult,
    DebugVerification,
    GameAdapterProtocol,
    HealthStatus,
)


class TestActionResult:
    """ActionResult frozen dataclass tests."""

    def test_required_fields(self) -> None:
        r = ActionResult(status="success", state_changed=True)
        assert r.status == "success"
        assert r.state_changed is True
        assert r.detail is None

    def test_with_detail(self) -> None:
        r = ActionResult(status="failure", state_changed=False, detail="HP mismatch")
        assert r.status == "failure"
        assert r.detail == "HP mismatch"

    def test_frozen(self) -> None:
        r = ActionResult(status="success", state_changed=True)
        with pytest.raises(Exception):
            r.status = "failure"  # type: ignore[misc]

    def test_literal_status_values(self) -> None:
        for s in ("success", "failure", "timeout"):
            r = ActionResult(status=s, state_changed=True)  # type: ignore[arg-type]
            assert r.status == s


class TestHealthStatus:
    """HealthStatus frozen dataclass tests."""

    def test_healthy(self) -> None:
        h = HealthStatus(healthy=True)
        assert h.healthy is True
        assert h.message is None

    def test_unhealthy_with_message(self) -> None:
        h = HealthStatus(healthy=False, message="Connection refused")
        assert h.healthy is False
        assert h.message == "Connection refused"

    def test_frozen(self) -> None:
        h = HealthStatus(healthy=True)
        with pytest.raises(Exception):
            h.healthy = False  # type: ignore[misc]


class TestGameAdapterProtocol:
    """Protocol structural typing tests (FR25)."""

    def test_protocol_is_runtime_checkable(self) -> None:
        assert isinstance(GameAdapterProtocol, type)

    def test_mock_satisfies_protocol(self) -> None:
        """A class with all 8 async methods satisfies the Protocol."""

        class MockAdapter:
            async def health_check(self) -> HealthStatus:
                return HealthStatus(healthy=True)

            async def get_state(self) -> Any:
                return "state"

            async def get_available_actions(self) -> list[str]:
                return []

            async def act(self, action: str, args: dict[str, Any] | None = None) -> ActionResult:
                return ActionResult(status="success", state_changed=True)

            async def wait_until_actionable(self, timeout: float) -> bool:
                return True

            async def capture_bug_snapshot(self) -> dict[str, Any]:
                return {}

            async def cleanup(self) -> None:
                pass

            async def verify_debug_actions(self) -> DebugVerification:
                return DebugVerification(configured=False, verified=False)

        mock = MockAdapter()
        assert isinstance(mock, GameAdapterProtocol)

    def test_incomplete_class_fails_protocol(self) -> None:
        """A class missing methods should not pass Protocol check."""

        class IncompleteAdapter:
            async def health_check(self) -> HealthStatus:
                return HealthStatus(healthy=True)

        incomplete = IncompleteAdapter()
        assert not isinstance(incomplete, GameAdapterProtocol)
