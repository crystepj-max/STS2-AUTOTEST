"""Tests for core/recovery.py — RecoveryAction, DefaultRecoveryStrategy, crash_signature, is_p0_exception."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sts2_autotest.adapters.base import HealthStatus
from sts2_autotest.common.errors import ErrorCategory, STS2Error
from sts2_autotest.core.recovery import (
    DefaultRecoveryStrategy,
    FailureRecord,
    RecoveryAction,
    StubRecoveryStrategy,
    crash_signature,
    is_p0_exception,
)


# ── RecoveryAction enum ────────────────────────────────────


class TestRecoveryAction:
    def test_enum_values(self) -> None:
        assert RecoveryAction.FAST_PATH == "FAST_PATH"
        assert RecoveryAction.RECREATE == "RECREATE"
        assert RecoveryAction.TERMINATE == "TERMINATE"

    def test_is_str(self) -> None:
        assert isinstance(RecoveryAction.FAST_PATH, str)


# ── FailureRecord ──────────────────────────────────────────


class TestFailureRecord:
    def test_fields(self) -> None:
        r = FailureRecord(
            error_type="timeout_error",
            message="cmd timed out",
            timestamp="2026-01-01T00:00:00Z",
            exit_code=1,
        )
        assert r.error_type == "timeout_error"
        assert r.exit_code == 1

    def test_exit_code_defaults_none(self) -> None:
        r = FailureRecord(
            error_type="adapter_error", message="x", timestamp="t",
        )
        assert r.exit_code is None

    def test_frozen(self) -> None:
        r = FailureRecord(
            error_type="a", message="b", timestamp="c",
        )
        with pytest.raises(AttributeError):
            r.error_type = "changed"  # type: ignore[misc]


# ── crash_signature ────────────────────────────────────────


class TestCrashSignature:
    def test_same_type_same_code(self) -> None:
        assert crash_signature(TimeoutError("a"), 1) == crash_signature(TimeoutError("b"), 1)

    def test_different_type_different_sig(self) -> None:
        assert crash_signature(TimeoutError("a"), 1) != crash_signature(OSError("a"), 1)

    def test_different_code_different_sig(self) -> None:
        assert crash_signature(TimeoutError(), 1) != crash_signature(TimeoutError(), 2)

    def test_none_exit_code(self) -> None:
        sig = crash_signature(RuntimeError())
        assert sig.endswith(":none")

    def test_deterministic(self) -> None:
        sig1 = crash_signature(ValueError("x"), 42)
        sig2 = crash_signature(ValueError("y"), 42)
        assert sig1 == sig2


# ── is_p0_exception ───────────────────────────────────────


class TestIsP0Exception:
    def test_file_not_found_is_p0(self) -> None:
        assert is_p0_exception(FileNotFoundError("cli not found")) is True

    def test_oserror_is_p0(self) -> None:
        assert is_p0_exception(OSError("cannot start")) is True

    def test_version_mismatch_is_p0(self) -> None:
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="Version mismatch",
            detail={"sub_type": "version_mismatch"},
        )
        assert is_p0_exception(exc) is True

    def test_timeout_is_not_p0(self) -> None:
        exc = STS2Error(
            category=ErrorCategory.TIMEOUT_ERROR,
            message="cmd timed out",
        )
        assert is_p0_exception(exc) is False

    def test_adapter_error_without_sub_type_is_not_p0(self) -> None:
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="Connection lost",
        )
        assert is_p0_exception(exc) is False

    def test_game_error_is_not_p0(self) -> None:
        exc = STS2Error(
            category=ErrorCategory.GAME_ERROR,
            message="Invalid state",
        )
        assert is_p0_exception(exc) is False

    def test_crash_error_is_not_p0(self) -> None:
        exc = STS2Error(
            category=ErrorCategory.CRASH_ERROR,
            message="Game process died",
        )
        assert is_p0_exception(exc) is False

    def test_runtime_error_is_not_p0(self) -> None:
        assert is_p0_exception(RuntimeError("unexpected")) is False


# ── StubRecoveryStrategy ───────────────────────────────────


class TestStubRecoveryStrategy:
    def test_always_returns_terminate(self) -> None:
        strategy = StubRecoveryStrategy()
        result = strategy.decide(Exception("test"), [])
        assert result.action == RecoveryAction.TERMINATE

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
        assert result.action == RecoveryAction.TERMINATE

    def test_execute_returns_false_none(self) -> None:
        strategy = StubRecoveryStrategy()
        adapter = MagicMock()
        ok, new_adapter = asyncio.run(strategy.execute(RecoveryAction.TERMINATE, adapter))
        assert ok is False
        assert new_adapter is None


# ── DefaultRecoveryStrategy.decide() ───────────────────────


class TestDecide:
    @pytest.fixture
    def strategy(self) -> DefaultRecoveryStrategy:
        return DefaultRecoveryStrategy()

    def test_p0_file_not_found_terminates(self, strategy: DefaultRecoveryStrategy) -> None:
        result = strategy.decide(FileNotFoundError("cli not found"), [])
        assert result.action == RecoveryAction.TERMINATE
        assert result.is_p0 is True

    def test_p0_oserror_terminates(self, strategy: DefaultRecoveryStrategy) -> None:
        result = strategy.decide(OSError("cannot start"), [])
        assert result.action == RecoveryAction.TERMINATE
        assert result.is_p0 is True

    def test_version_mismatch_terminates(self, strategy: DefaultRecoveryStrategy) -> None:
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="Version mismatch",
            detail={"sub_type": "version_mismatch"},
        )
        result = strategy.decide(exc, [])
        assert result.action == RecoveryAction.TERMINATE
        assert result.is_p0 is True

    def test_timeout_fast_path(self, strategy: DefaultRecoveryStrategy) -> None:
        exc = STS2Error(
            category=ErrorCategory.TIMEOUT_ERROR,
            message="cmd timed out",
        )
        result = strategy.decide(exc, [])
        assert result.action == RecoveryAction.FAST_PATH

    def test_adapter_error_fast_path(self, strategy: DefaultRecoveryStrategy) -> None:
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="Connection lost",
        )
        result = strategy.decide(exc, [])
        assert result.action == RecoveryAction.FAST_PATH

    def test_game_error_fast_path(self, strategy: DefaultRecoveryStrategy) -> None:
        exc = STS2Error(
            category=ErrorCategory.GAME_ERROR,
            message="Invalid state",
        )
        result = strategy.decide(exc, [])
        assert result.action == RecoveryAction.FAST_PATH

    def test_crash_error_initially_returns_game_restart(
        self, strategy: DefaultRecoveryStrategy,
    ) -> None:
        """First crash returns GAME_RESTART (progressive recovery, not FAST_PATH)."""
        exc = STS2Error(
            category=ErrorCategory.CRASH_ERROR,
            message="Game crashed",
        )
        result = strategy.decide(exc, [])
        assert result.action == RecoveryAction.GAME_RESTART

    def test_consecutive_threshold_triggers_recreate(
        self, strategy: DefaultRecoveryStrategy,
    ) -> None:
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="Connection lost",
        )
        history = [
            FailureRecord(
                error_type="adapter_error", message="x", timestamp="t1",
            ),
            FailureRecord(
                error_type="adapter_error", message="x", timestamp="t2",
            ),
        ]
        result = strategy.decide(exc, history, max_consecutive=3)
        assert result.action == RecoveryAction.RECREATE

    def test_consecutive_threshold_triggers_terminate(
        self, strategy: DefaultRecoveryStrategy,
    ) -> None:
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="Connection lost",
        )
        history = [
            FailureRecord(
                error_type="adapter_error", message="x", timestamp="t1",
            ),
            FailureRecord(
                error_type="adapter_error", message="x", timestamp="t2",
            ),
            FailureRecord(
                error_type="adapter_error", message="x", timestamp="t3",
            ),
        ]
        result = strategy.decide(exc, history, max_consecutive=3)
        assert result.action == RecoveryAction.TERMINATE
        assert result.is_p0 is False

    def test_mixed_errors_reset_counter(self, strategy: DefaultRecoveryStrategy) -> None:
        history = [
            FailureRecord(error_type="adapter_error", message="a", timestamp="t1"),
            FailureRecord(error_type="timeout_error", message="b", timestamp="t2"),
            FailureRecord(error_type="adapter_error", message="c", timestamp="t3"),
        ]
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR, message="d",
        )
        result = strategy.decide(exc, history, max_consecutive=3)
        assert result.action == RecoveryAction.FAST_PATH

    def test_custom_max_consecutive(self, strategy: DefaultRecoveryStrategy) -> None:
        history = [
            FailureRecord(error_type="adapter_error", message="a", timestamp="t1"),
        ]
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR, message="b",
        )
        result = strategy.decide(exc, history, max_consecutive=2)
        assert result.action == RecoveryAction.RECREATE


# ── DefaultRecoveryStrategy.decide() — crash levels ─────────


class TestDecideCrashLevels:
    """Progressive crash recovery: GAME_RESTART → FULL_RESTART → TERMINATE."""

    def make_history(self, error_type: str, count: int) -> list[FailureRecord]:
        return [
            FailureRecord(error_type=error_type, message="test", timestamp="now")
            for _ in range(count)
        ]

    def test_first_crash_returns_game_restart(self) -> None:
        strategy = DefaultRecoveryStrategy()
        error = STS2Error(category=ErrorCategory.CRASH_ERROR, message="game crashed")
        decision = strategy.decide(error, [])
        assert decision.action == RecoveryAction.GAME_RESTART
        assert decision.is_p0 is False

    def test_second_consecutive_crash_returns_full_restart(self) -> None:
        strategy = DefaultRecoveryStrategy()
        error = STS2Error(category=ErrorCategory.CRASH_ERROR, message="crashed again")
        history = self.make_history("crash_error", 1)
        decision = strategy.decide(error, history)
        assert decision.action == RecoveryAction.FULL_RESTART

    def test_third_consecutive_crash_returns_terminate(self) -> None:
        strategy = DefaultRecoveryStrategy()
        error = STS2Error(category=ErrorCategory.CRASH_ERROR, message="crashed x3")
        history = self.make_history("crash_error", 2)
        decision = strategy.decide(error, history)
        assert decision.action == RecoveryAction.TERMINATE

    def test_non_crash_error_not_affected(self) -> None:
        """Adapter errors should still get standard recovery, not crash levels."""
        strategy = DefaultRecoveryStrategy()
        error = STS2Error(category=ErrorCategory.ADAPTER_ERROR, message="adapter failed")
        decision = strategy.decide(error, [])
        assert decision.action == RecoveryAction.FAST_PATH

    def test_crash_after_other_errors_resets_to_game_restart(self) -> None:
        """Crash after non-crash errors should start from GAME_RESTART, not escalate."""
        strategy = DefaultRecoveryStrategy()
        error = STS2Error(category=ErrorCategory.CRASH_ERROR, message="crashed")
        # Previous errors were adapter errors, not crashes
        history = [
            FailureRecord(error_type="adapter_error", message="prev", timestamp="now"),
        ]
        decision = strategy.decide(error, history)
        # Different error_type — consecutive count for 'crash_error' is 0 → GAME_RESTART
        assert decision.action == RecoveryAction.GAME_RESTART


# ── DefaultRecoveryStrategy._consecutive_count ──────────────


class TestConsecutiveCount:
    def test_empty_history(self) -> None:
        strategy = DefaultRecoveryStrategy()
        assert strategy._consecutive_count([], "any") == 0

    def test_all_same(self) -> None:
        strategy = DefaultRecoveryStrategy()
        history = [
            FailureRecord(error_type="a", message="x", timestamp="t1"),
            FailureRecord(error_type="a", message="x", timestamp="t2"),
            FailureRecord(error_type="a", message="x", timestamp="t3"),
        ]
        assert strategy._consecutive_count(history, "a") == 3

    def test_mixed_at_end(self) -> None:
        strategy = DefaultRecoveryStrategy()
        history = [
            FailureRecord(error_type="a", message="x", timestamp="t1"),
            FailureRecord(error_type="b", message="x", timestamp="t2"),
            FailureRecord(error_type="a", message="x", timestamp="t3"),
        ]
        assert strategy._consecutive_count(history, "a") == 1

    def test_different_type_at_end(self) -> None:
        strategy = DefaultRecoveryStrategy()
        history = [
            FailureRecord(error_type="a", message="x", timestamp="t1"),
            FailureRecord(error_type="b", message="x", timestamp="t2"),
        ]
        assert strategy._consecutive_count(history, "a") == 0


# ── DefaultRecoveryStrategy.execute() ──────────────────────


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_mock_adapter_factory(mock_adapter: Any):
    """Create a callable factory that returns a pre-built mock adapter."""
    def factory():
        return mock_adapter
    return factory


class TestExecute:
    @pytest.fixture
    def mock_adapter(self) -> Any:
        adapter = AsyncMock()
        adapter.health_check.return_value = HealthStatus(healthy=True)
        adapter.cleanup.return_value = None
        return adapter

    def test_fast_path_healthy_succeeds(self, mock_adapter: Any) -> None:
        strategy = DefaultRecoveryStrategy()
        ok, new_adapter = _run(strategy.execute(RecoveryAction.FAST_PATH, mock_adapter))
        assert ok is True
        assert new_adapter is None
        mock_adapter.health_check.assert_awaited_once()

    def test_fast_path_unhealthy_fails(self, mock_adapter: Any) -> None:
        mock_adapter.health_check.return_value = HealthStatus(
            healthy=False, message="dead",
        )
        strategy = DefaultRecoveryStrategy()
        ok, new_adapter = _run(strategy.execute(RecoveryAction.FAST_PATH, mock_adapter))
        assert ok is False
        assert new_adapter is None

    def test_fast_path_exception_fails(self, mock_adapter: Any) -> None:
        mock_adapter.health_check.side_effect = RuntimeError("boom")
        strategy = DefaultRecoveryStrategy()
        ok, new_adapter = _run(strategy.execute(RecoveryAction.FAST_PATH, mock_adapter))
        assert ok is False
        assert new_adapter is None

    def test_terminate_returns_false_none(self, mock_adapter: Any) -> None:
        strategy = DefaultRecoveryStrategy()
        ok, new_adapter = _run(strategy.execute(RecoveryAction.TERMINATE, mock_adapter))
        assert ok is False
        assert new_adapter is None

    def test_recreate_no_factory_fails(self, mock_adapter: Any) -> None:
        strategy = DefaultRecoveryStrategy()
        ok, new_adapter = _run(strategy.execute(RecoveryAction.RECREATE, mock_adapter))
        assert ok is False
        assert new_adapter is None

    def test_recreate_with_factory_succeeds(self, mock_adapter: Any) -> None:
        new_mock = AsyncMock()
        new_mock.health_check.return_value = HealthStatus(healthy=True)
        new_mock.cleanup.return_value = None
        factory = _make_mock_adapter_factory(new_mock)

        strategy = DefaultRecoveryStrategy(adapter_factory=factory)
        ok, returned_adapter = _run(strategy.execute(RecoveryAction.RECREATE, mock_adapter))
        assert ok is True
        assert returned_adapter is new_mock
        mock_adapter.cleanup.assert_awaited_once()
        new_mock.health_check.assert_awaited_once()

    def test_recreate_cleanup_failure_still_succeeds(self, mock_adapter: Any) -> None:
        mock_adapter.cleanup.side_effect = RuntimeError("cleanup failed")
        new_mock = AsyncMock()
        new_mock.health_check.return_value = HealthStatus(healthy=True)
        factory = _make_mock_adapter_factory(new_mock)

        strategy = DefaultRecoveryStrategy(adapter_factory=factory)
        ok, returned_adapter = _run(strategy.execute(RecoveryAction.RECREATE, mock_adapter))
        assert ok is True
        assert returned_adapter is new_mock

    def test_recreate_factory_exception_fails(self, mock_adapter: Any) -> None:
        def bad_factory():
            raise RuntimeError("cannot create adapter")
        strategy = DefaultRecoveryStrategy(adapter_factory=bad_factory)
        ok, new_adapter = _run(strategy.execute(RecoveryAction.RECREATE, mock_adapter))
        assert ok is False
        assert new_adapter is None

    def test_recreate_new_adapter_unhealthy_fails(self, mock_adapter: Any) -> None:
        new_mock = AsyncMock()
        new_mock.health_check.return_value = HealthStatus(healthy=False, message="sick")
        factory = _make_mock_adapter_factory(new_mock)

        strategy = DefaultRecoveryStrategy(adapter_factory=factory)
        ok, new_adapter = _run(strategy.execute(RecoveryAction.RECREATE, mock_adapter))
        assert ok is False
        assert new_adapter is None

    def test_recreate_new_adapter_health_check_exception_fails(self, mock_adapter: Any) -> None:
        new_mock = AsyncMock()
        new_mock.health_check.side_effect = RuntimeError("health check boom")
        factory = _make_mock_adapter_factory(new_mock)

        strategy = DefaultRecoveryStrategy(adapter_factory=factory)
        ok, new_adapter = _run(strategy.execute(RecoveryAction.RECREATE, mock_adapter))
        assert ok is False
        assert new_adapter is None


# ── DefaultRecoveryStrategy.execute() — GAME_RESTART ─────────


class TestExecuteGameRestart:
    """GAME_RESTART calls steam_controller methods."""

    @staticmethod
    def _make_healthy_adapter() -> Any:
        adapter = AsyncMock()
        adapter.health_check.return_value = HealthStatus(healthy=True)
        adapter.cleanup.return_value = None
        return adapter

    def test_game_restart_with_steam_controller(self) -> None:
        mock_steam = MagicMock()
        mock_steam.restart_game = MagicMock(return_value=12345)
        new_adapter_mock = self._make_healthy_adapter()
        mock_factory = MagicMock(return_value=new_adapter_mock)
        strategy = DefaultRecoveryStrategy(adapter_factory=mock_factory, steam_controller=mock_steam)
        old_adapter = self._make_healthy_adapter()

        success, result_adapter = _run(strategy.execute(RecoveryAction.GAME_RESTART, old_adapter))

        assert success is True
        assert result_adapter is new_adapter_mock
        mock_steam.restart_game.assert_called_once()

    def test_game_restart_no_steam_falls_back(self) -> None:
        """Without steam_controller, GAME_RESTART falls back to RECREATE."""
        new_adapter_mock = self._make_healthy_adapter()
        mock_factory = MagicMock(return_value=new_adapter_mock)
        strategy = DefaultRecoveryStrategy(adapter_factory=mock_factory)
        old_adapter = self._make_healthy_adapter()

        success, result_adapter = _run(strategy.execute(RecoveryAction.GAME_RESTART, old_adapter))

        # Falls back to RECREATE — should still succeed
        assert success is True
        assert result_adapter is new_adapter_mock


# ── DefaultRecoveryStrategy.execute() — FULL_RESTART ─────────


class TestExecuteFullRestart:
    """FULL_RESTART stops game+Steam, then starts them again."""

    @staticmethod
    def _make_healthy_adapter() -> Any:
        adapter = AsyncMock()
        adapter.health_check.return_value = HealthStatus(healthy=True)
        adapter.cleanup.return_value = None
        return adapter

    def test_full_restart_with_steam_controller(self) -> None:
        mock_steam = MagicMock()
        mock_steam.stop_game = MagicMock()
        mock_steam.stop_steam = MagicMock()
        mock_steam.start_steam = MagicMock(return_value=9999)
        mock_steam.start_game = MagicMock(return_value=12345)
        new_adapter_mock = self._make_healthy_adapter()
        mock_factory = MagicMock(return_value=new_adapter_mock)
        strategy = DefaultRecoveryStrategy(adapter_factory=mock_factory, steam_controller=mock_steam)
        old_adapter = self._make_healthy_adapter()

        success, result_adapter = _run(strategy.execute(RecoveryAction.FULL_RESTART, old_adapter))

        assert success is True
        assert result_adapter is new_adapter_mock
        mock_steam.stop_game.assert_called_once()
        mock_steam.stop_steam.assert_called_once()
        mock_steam.start_steam.assert_called_once()
        mock_steam.start_game.assert_called_once()

    def test_full_restart_failure_returns_false(self) -> None:
        mock_steam = MagicMock()
        mock_steam.stop_game = MagicMock(side_effect=RuntimeError("stop failed"))
        new_adapter_mock = self._make_healthy_adapter()
        mock_factory = MagicMock(return_value=new_adapter_mock)
        strategy = DefaultRecoveryStrategy(adapter_factory=mock_factory, steam_controller=mock_steam)
        old_adapter = self._make_healthy_adapter()

        success, result_adapter = _run(strategy.execute(RecoveryAction.FULL_RESTART, old_adapter))

        assert success is False
        assert result_adapter is None

    def test_full_restart_no_steam_falls_back(self) -> None:
        """Without steam_controller, FULL_RESTART falls back to RECREATE."""
        new_adapter_mock = self._make_healthy_adapter()
        mock_factory = MagicMock(return_value=new_adapter_mock)
        strategy = DefaultRecoveryStrategy(adapter_factory=mock_factory)
        old_adapter = self._make_healthy_adapter()

        success, result_adapter = _run(strategy.execute(RecoveryAction.FULL_RESTART, old_adapter))

        # Falls back to RECREATE — should still succeed
        assert success is True
        assert result_adapter is new_adapter_mock
