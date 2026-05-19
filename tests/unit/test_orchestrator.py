"""Tests for core/orchestrator.py — TestOrchestrator lifecycle and recovery."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.common.errors import ErrorCategory, STS2Error
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.action_model import TestResult
from sts2_autotest.core.orchestrator import SessionSummary, TestOrchestrator
from sts2_autotest.core.recovery import (
    DefaultRecoveryStrategy,
    FailureRecord,
    RecoveryAction,
    StubRecoveryStrategy,
)
from sts2_autotest.core.state_engine import StateEngine


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_mock_adapter(
    healthy: bool = True,
    screen: GameScreen = GameScreen.MAP,
    post_action_screen: GameScreen = GameScreen.COMBAT,
    actions: list[str] | None = None,
    act_result: ActionResult | None = None,
) -> Any:
    """Create a mock adapter satisfying GameAdapterProtocol.

    get_state alternates: returns `screen` before action, then
    `post_action_screen` after action — enabling valid transitions.
    """
    import itertools

    states = itertools.cycle([
        GameState(screen=screen),
        GameState(screen=post_action_screen),
    ])
    mock = MagicMock(spec=GameAdapterProtocol)
    mock.health_check.return_value = HealthStatus(healthy=healthy)
    mock.get_state.side_effect = lambda: next(states)
    mock.get_available_actions.return_value = (
        actions if actions is not None else ["probe", "play_card", "end_turn"]
    )
    mock.act.return_value = act_result or ActionResult(status="success", state_changed=True)
    mock.wait_until_actionable.return_value = True
    mock.capture_bug_snapshot.return_value = {}
    return mock


@pytest.fixture
def mock_adapter() -> Any:
    return _make_mock_adapter()


@pytest.fixture
def orchestrator(mock_adapter: Any) -> TestOrchestrator:
    return TestOrchestrator(adapter=mock_adapter)


class TestSessionLifecycle:
    """AC#1: Session startup and teardown."""

    def test_start_session_succeeds(self, orchestrator: TestOrchestrator) -> None:
        result = _run(orchestrator.start_session())
        assert result is True
        assert orchestrator._session_active is True

    def test_start_session_fails_unhealthy_adapter(
        self, orchestrator: TestOrchestrator, mock_adapter: Any
    ) -> None:
        mock_adapter.health_check.return_value = HealthStatus(healthy=False)
        result = _run(orchestrator.start_session())
        assert result is False

    def test_start_session_fails_unknown_state(
        self, orchestrator: TestOrchestrator, mock_adapter: Any
    ) -> None:
        mock_adapter.get_state.side_effect = lambda: GameState(screen=GameScreen.UNKNOWN)
        result = _run(orchestrator.start_session())
        assert result is False

    def test_stop_session(self, orchestrator: TestOrchestrator) -> None:
        _run(orchestrator.start_session())
        _run(orchestrator.stop_session())
        assert orchestrator._session_active is False


class TestExecutionModes:
    """AC#2: run_all, run_cases, run_failed."""

    def test_run_all_executes_each_case(self, orchestrator: TestOrchestrator) -> None:
        summary = _run(orchestrator.run_all(["TC-001", "TC-002", "TC-003"]))
        assert summary.total == 3
        assert summary.passed == 3

    def test_run_cases(self, orchestrator: TestOrchestrator) -> None:
        summary = _run(orchestrator.run_cases(["TC-001"]))
        assert summary.total == 1
        assert summary.passed == 1

    def test_run_failed_re_runs_failures(self, orchestrator: TestOrchestrator) -> None:
        orchestrator._last_results = [
            TestResult("TC-001", "pass"),
            TestResult("TC-002", "fail", "error"),
            TestResult("TC-003", "pass"),
        ]
        summary = _run(orchestrator.run_failed())
        assert summary.total == 1

    def test_run_failed_excludes_deterministic_fail(
        self, orchestrator: TestOrchestrator,
    ) -> None:
        """deterministic_fail cases should not be re-run."""
        orchestrator._last_results = [
            TestResult("TC-001", "pass"),
            TestResult("TC-002", "fail", "recoverable"),
            TestResult("TC-003", "deterministic_fail", "env issue",
                       crash_signature="OSError:none"),
        ]
        summary = _run(orchestrator.run_failed())
        assert summary.total == 1

    def test_run_failed_none_skips(self, orchestrator: TestOrchestrator) -> None:
        orchestrator._last_results = [
            TestResult("TC-001", "pass"),
            TestResult("TC-002", "pass"),
        ]
        summary = _run(orchestrator.run_failed())
        assert summary.total == 0


class TestResultClassification:
    """AC#3: pass/fail/crash/skip/deterministic_fail classification."""

    def test_pass_classification(self, orchestrator: TestOrchestrator) -> None:
        result = _run(orchestrator.execute_case("TC-001"))
        assert result.status == "pass"

    def test_crash_stops_subsequent_cases(
        self, mock_adapter: Any
    ) -> None:
        """AC#6: unknown exception (non-STS2Error) marks subsequent cases as skip.

        CRASH_ERROR STS2Error now routes through progressive recovery;
        only unknown/unexpected exceptions (caught by the bare except Exception
        in execute_case) immediately terminate the session.
        """
        mock = _make_mock_adapter()
        mock.act.side_effect = RuntimeError("Unexpected game crash")
        orch = TestOrchestrator(adapter=mock)
        summary = _run(orch.run_all(["TC-001", "TC-002", "TC-003"]))
        assert summary.results[0].status == "crash"
        assert summary.results[1].status == "skip"
        assert summary.results[2].status == "skip"

    def test_adapter_error_classified_as_fail(
        self, mock_adapter: Any
    ) -> None:
        mock = _make_mock_adapter()
        mock.act.side_effect = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="Connection timeout",
        )
        orch = TestOrchestrator(
            adapter=mock,
            recovery=DefaultRecoveryStrategy(),
        )
        result = _run(orch.execute_case("TC-001"))
        assert result.status == "fail"


class TestStateFirstExecution:
    """AC#4: read → action → re-read pattern."""

    def test_reads_state_before_action(
        self, orchestrator: TestOrchestrator, mock_adapter: Any
    ) -> None:
        _run(orchestrator.execute_case("TC-001"))
        assert mock_adapter.get_state.call_count >= 2

    def test_state_updated_after_action(
        self, orchestrator: TestOrchestrator, mock_adapter: Any
    ) -> None:
        import itertools
        mock_adapter.get_state.side_effect = itertools.cycle([
            GameState(screen=GameScreen.MAP),
            GameState(screen=GameScreen.COMBAT),
        ])
        result = _run(orchestrator.execute_case("TC-001"))
        assert result.status == "pass"


class TestCrashHandling:
    """AC#6: crash detection and response."""

    def test_handle_crash_sets_crashed_flag(
        self, orchestrator: TestOrchestrator
    ) -> None:
        orchestrator._handle_crash("TC-001", Exception("BOOM"))
        assert orchestrator._crashed is True

    def test_unknown_exception_triggers_crash(
        self, mock_adapter: Any
    ) -> None:
        mock = _make_mock_adapter()
        mock.act.side_effect = RuntimeError("Unexpected failure")
        orch = TestOrchestrator(adapter=mock)
        result = _run(orch.execute_case("TC-001"))
        assert result.status == "crash"

    def test_crash_goes_through_recovery_not_immediate_terminate(self) -> None:
        """CRASH_ERROR should reach recovery.decide(), not immediately crash.

        Beta: crash errors flow through progressive recovery
        (GAME_RESTART -> FULL_RESTART -> TERMINATE) instead of
        immediately terminating the session.
        """
        from unittest.mock import AsyncMock, MagicMock

        mock_adapter = MagicMock(spec=GameAdapterProtocol)
        mock_adapter.get_available_actions = AsyncMock(return_value=["probe"])
        mock_adapter.act = AsyncMock(return_value=ActionResult("success", True))
        mock_adapter.health_check = AsyncMock(return_value=HealthStatus(True))
        mock_adapter.get_state = AsyncMock(
            return_value=GameState(screen=GameScreen.COMBAT),
        )
        mock_adapter.wait_until_actionable = AsyncMock(return_value=True)
        mock_adapter.cleanup = AsyncMock()

        strategy = DefaultRecoveryStrategy()
        orch = TestOrchestrator(
            adapter=mock_adapter,
            recovery=strategy,
        )

        crash = STS2Error(
            category=ErrorCategory.CRASH_ERROR,
            message="game crashed",
        )
        result = _run(orch._handle_failure("TC-001", crash))

        # First crash -> GAME_RESTART -> execute returns (False, None)
        # since no steam_controller. Falls back to RECREATE which also
        # fails since no factory. Fall through to consecutive check -> "fail"
        assert result.status != "crash"  # Not immediate crash
        assert isinstance(orch.recovery, DefaultRecoveryStrategy)


class TestSessionSummary:
    """SessionSummary aggregate statistics."""

    def test_counts_by_status(self) -> None:
        results = [
            TestResult("1", "pass"),
            TestResult("2", "pass"),
            TestResult("3", "fail"),
            TestResult("4", "crash"),
            TestResult("5", "skip"),
        ]
        summary = SessionSummary(session_id="s1", results=results)
        assert summary.total == 5
        assert summary.passed == 2
        assert summary.failed == 1
        assert summary.crashed == 1
        assert summary.skipped == 1

    def test_deterministic_fails_counted(self) -> None:
        results = [
            TestResult("1", "pass"),
            TestResult("2", "deterministic_fail", crash_signature="TimeoutError:1"),
            TestResult("3", "deterministic_fail", crash_signature="TimeoutError:1"),
        ]
        summary = SessionSummary(session_id="s1", results=results)
        assert summary.deterministic_fails == 2
        assert summary.total == 3


class TestHandleFailure:
    """_handle_failure() centralized recovery path."""

    def test_p0_exception_sets_crashed(self) -> None:
        mock = _make_mock_adapter()
        evidence = MagicMock()
        evidence.on_crash = MagicMock()
        evidence.on_case_end = MagicMock()
        orch = TestOrchestrator(adapter=mock, evidence=evidence)
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="File not found",
            detail={"sub_type": "version_mismatch"},
        )
        result = _run(orch._handle_failure("TC-001", exc))
        assert result.status == "crash"
        assert orch._crashed is True

    def test_non_p0_records_failure_history(self) -> None:
        mock = _make_mock_adapter()
        evidence = MagicMock()
        evidence.on_case_end = MagicMock()
        orch = TestOrchestrator(adapter=mock, evidence=evidence)
        exc = STS2Error(
            category=ErrorCategory.TIMEOUT_ERROR,
            message="cmd timed out",
        )
        _run(orch._handle_failure("TC-001", exc))
        assert len(orch._failure_history) == 1
        assert orch._failure_history[0].error_type == "timeout_error"

    def test_consecutive_failures_triggers_deterministic_fail(self) -> None:
        mock = _make_mock_adapter()
        evidence = MagicMock()
        evidence.on_case_end = MagicMock()
        orch = TestOrchestrator(
            adapter=mock,
            evidence=evidence,
            max_consecutive_failures=3,
        )
        # Pre-fill history with 2 consecutive timeout errors
        orch._failure_history = [
            FailureRecord(error_type="timeout_error", message="a", timestamp="t1"),
            FailureRecord(error_type="timeout_error", message="b", timestamp="t2"),
        ]
        exc = STS2Error(
            category=ErrorCategory.TIMEOUT_ERROR,
            message="third timeout",
        )
        result = _run(orch._handle_failure("TC-001", exc))
        assert result.status == "deterministic_fail"
        assert result.crash_signature is not None

    def test_crash_error_routes_through_recovery(self) -> None:
        """CRASH_ERROR now flows through recovery, not immediate crash."""
        mock = _make_mock_adapter()
        evidence = MagicMock()
        evidence.on_crash = MagicMock()
        evidence.on_case_end = MagicMock()
        orch = TestOrchestrator(adapter=mock, evidence=evidence)
        exc = STS2Error(
            category=ErrorCategory.CRASH_ERROR,
            message="Game process died",
        )
        result = _run(orch._handle_failure("TC-001", exc))
        # CRASH_ERROR now routes through recovery instead of immediate crash.
        # Without steam_controller/factory, recovery fails and returns "fail".
        assert result.status == "fail"
        # _crashed is NOT set because recovery was attempted (not P0)
        assert orch._crashed is False

    def test_p0_not_downgraded_to_deterministic_fail(self) -> None:
        """P0 version_mismatch always crashes, even with consecutive history."""
        mock = _make_mock_adapter()
        evidence = MagicMock()
        evidence.on_crash = MagicMock()
        evidence.on_case_end = MagicMock()
        orch = TestOrchestrator(
            adapter=mock,
            evidence=evidence,
            max_consecutive_failures=3,
        )
        # Pre-fill history with 2 consecutive version_mismatch failures
        orch._failure_history = [
            FailureRecord(error_type="adapter_error", message="v1", timestamp="t1",
                          exit_code=1),
            FailureRecord(error_type="adapter_error", message="v2", timestamp="t2",
                          exit_code=1),
        ]
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="Version mismatch",
            detail={"sub_type": "version_mismatch", "exit_code": 1},
        )
        result = _run(orch._handle_failure("TC-001", exc))
        # P0 must crash, never become deterministic_fail
        assert result.status == "crash"
        assert orch._crashed is True

    def test_p0_file_not_found_not_downgraded(self) -> None:
        """P0 FileNotFoundError always crashes regardless of history."""
        mock = _make_mock_adapter()
        evidence = MagicMock()
        evidence.on_crash = MagicMock()
        evidence.on_case_end = MagicMock()
        orch = TestOrchestrator(
            adapter=mock,
            evidence=evidence,
            max_consecutive_failures=2,
        )
        # Pre-fill with one previous adapter error
        orch._failure_history = [
            FailureRecord(error_type="adapter_error", message="prev", timestamp="t1"),
        ]
        # FileNotFoundError is not STS2Error, so wrap it — it goes through
        # the except Exception path in execute_case, not _handle_failure.
        # Test via direct _handle_failure with a P0-mimicking STS2Error:
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="cli not found",
            detail={"sub_type": "version_mismatch"},
        )
        result = _run(orch._handle_failure("TC-001", exc))
        assert result.status == "crash"

    def test_recreate_switches_adapter(self) -> None:
        """RECREATE recovery replaces the orchestrator's adapter with the new one."""
        mock = _make_mock_adapter()
        new_mock = _make_mock_adapter(screen=GameScreen.MAIN_MENU)

        def factory():
            return new_mock

        recovery = DefaultRecoveryStrategy(adapter_factory=factory)
        evidence = MagicMock()
        evidence.on_case_end = MagicMock()
        orch = TestOrchestrator(
            adapter=mock,
            recovery=recovery,
            evidence=evidence,
            max_consecutive_failures=3,
        )
        # Pre-fill with 2 records → decide() sees consecutive=2
        # which triggers RECREATE (≥ max_consecutive-1 = 2)
        orch._failure_history = [
            FailureRecord(error_type="adapter_error", message="a", timestamp="t1"),
            FailureRecord(error_type="adapter_error", message="b", timestamp="t2"),
        ]
        exc = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="Connection lost",
        )
        result = _run(orch._handle_failure("TC-001", exc))
        # After RECREATE, the orchestrator should use the new adapter
        assert orch.adapter is new_mock
        # State should be reset to MAIN_MENU
        assert orch._current_screen == GameScreen.MAIN_MENU

    def test_recovery_resets_state_to_main_menu(self) -> None:
        """Successful FAST_PATH recovery resets state through CRASHED → MAIN_MENU."""
        mock = _make_mock_adapter()
        evidence = MagicMock()
        evidence.on_case_end = MagicMock()
        orch = TestOrchestrator(
            adapter=mock,
            evidence=evidence,
            max_consecutive_failures=5,
            game_startup_timeout=10.0,
        )
        orch._current_screen = GameScreen.COMBAT
        exc = STS2Error(
            category=ErrorCategory.TIMEOUT_ERROR,
            message="cmd timed out",
        )
        result = _run(orch._handle_failure("TC-001", exc))
        # Recovery succeeded → state should be MAIN_MENU
        assert orch._current_screen == GameScreen.MAIN_MENU
        # Single failure, well under threshold → "fail"
        assert result.status == "fail"


class TestDefaultConstruction:
    """Default TestOrchestrator uses proper recovery strategy."""

    def test_default_recovery_is_default_strategy(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)
        assert isinstance(orch.recovery, DefaultRecoveryStrategy)

    def test_default_recovery_not_stub(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)
        assert not isinstance(orch.recovery, StubRecoveryStrategy)

    def test_explicit_stub_still_works(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock, recovery=StubRecoveryStrategy())
        assert isinstance(orch.recovery, StubRecoveryStrategy)


class TestConsecutiveSameType:
    """_consecutive_same_type() counter."""

    def test_empty_history(self, orchestrator: TestOrchestrator) -> None:
        assert orchestrator._consecutive_same_type("any") == 0

    def test_consecutive_count(self, orchestrator: TestOrchestrator) -> None:
        orchestrator._failure_history = [
            FailureRecord(error_type="a", message="x", timestamp="t1"),
            FailureRecord(error_type="a", message="x", timestamp="t2"),
            FailureRecord(error_type="a", message="x", timestamp="t3"),
        ]
        assert orchestrator._consecutive_same_type("a") == 3

    def test_mixed_history(self, orchestrator: TestOrchestrator) -> None:
        orchestrator._failure_history = [
            FailureRecord(error_type="a", message="x", timestamp="t1"),
            FailureRecord(error_type="b", message="x", timestamp="t2"),
            FailureRecord(error_type="a", message="x", timestamp="t3"),
        ]
        assert orchestrator._consecutive_same_type("a") == 1


# ── state validation (Story 4.4, AC2) ───────────────────────


class TestStateValidation:
    """AC2: GameState semantic validation in orchestrator flow."""

    def test_non_strict_validation_returns_cached_state(
        self, mock_adapter: Any
    ) -> None:
        """With strict_validation=False, invalid state returns last valid cache."""
        orch = TestOrchestrator(adapter=mock_adapter, strict_validation=False)

        # First call is valid
        valid_state = GameState(screen=GameScreen.MAP, floor=3)
        orch._last_valid_state = valid_state

        # Second call returns invalid state — should fall back to cache
        mock_adapter.get_state.side_effect = lambda: GameState(
            screen=GameScreen.COMBAT,
        )

        state = _run(orch._get_state_validated())
        assert state == valid_state
        assert orch._last_valid_state == valid_state

    def test_strict_validation_raises_on_invalid(
        self, mock_adapter: Any
    ) -> None:
        """With strict_validation=True, invalid state raises STS2Error."""
        orch = TestOrchestrator(adapter=mock_adapter, strict_validation=True)

        mock_adapter.get_state.side_effect = lambda: GameState(
            screen=GameScreen.COMBAT,
        )

        with pytest.raises(STS2Error, match="Game state validation failed"):
            _run(orch._get_state_validated())

    def test_valid_state_updates_cache(
        self, orchestrator: TestOrchestrator, mock_adapter: Any
    ) -> None:
        """Valid state updates _last_valid_state cache."""
        state = GameState(screen=GameScreen.MAP, floor=5, gold=200)
        mock_adapter.get_state.side_effect = lambda: state

        result = _run(orchestrator._get_state_validated())
        assert result.screen == GameScreen.MAP
        assert orchestrator._last_valid_state is not None
        assert orchestrator._last_valid_state.screen == GameScreen.MAP

    def test_no_cache_returns_invalid_state(
        self, mock_adapter: Any
    ) -> None:
        """Without cached state, returns invalid state when non-strict."""
        orch = TestOrchestrator(adapter=mock_adapter, strict_validation=False)

        mock_adapter.get_state.side_effect = lambda: GameState(
            screen=GameScreen.COMBAT,
        )

        # No last_valid_state — returns the invalid state
        state = _run(orch._get_state_validated())
        assert state.screen == GameScreen.COMBAT

    def test_strict_validation_flag_init(self) -> None:
        """strict_validation flag is passed through constructor."""
        mock = _make_mock_adapter()

        strict = TestOrchestrator(adapter=mock, strict_validation=True)
        assert strict._strict_validation is True

        non_strict = TestOrchestrator(adapter=mock, strict_validation=False)
        assert non_strict._strict_validation is False


# ── adapter degradation (Story 4.4, AC4) ────────────────────


class TestAdapterDegradation:
    """AC4: adapter degradation detection and fallback."""

    def test_adapter_result_success_resets_counter(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)

        _run(orch._record_adapter_result(True))
        assert orch._primary_adapter_failures == 0
        assert orch._adapter_degraded is False

    def test_adapter_result_failure_increments(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)

        _run(orch._record_adapter_result(False))
        assert orch._primary_adapter_failures == 1

    def test_degradation_after_two_failures(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)

        _run(orch._record_adapter_result(False))
        assert orch._adapter_degraded is False

        _run(orch._record_adapter_result(False))
        assert orch._adapter_degraded is True
        assert orch._primary_adapter_failures == 2


# ── lock integration (Story 4.6, FR65) ──────────────────────


class TestLockIntegration:
    """FR65: process-level mutex lock in orchestrator lifecycle."""

    def test_start_session_acquires_lock(self, tmp_path: Path) -> None:
        """start_session with lock_path acquires the lock."""
        mock = _make_mock_adapter()
        lock_path = tmp_path / "session.lock"
        orch = TestOrchestrator(adapter=mock, lock_path=str(lock_path))

        with patch("sts2_autotest.core.lock_manager.portalocker.lock"):
            result = _run(orch.start_session())

        assert result is True
        assert orch._lock_manager is not None
        assert orch._lock_manager._lock_file is not None

    def test_start_session_fails_when_locked(self, tmp_path: Path) -> None:
        """start_session returns False when lock is held by another process."""
        mock = _make_mock_adapter()
        lock_path = tmp_path / "session.lock"
        orch = TestOrchestrator(adapter=mock, lock_path=str(lock_path))

        # Lock acquisition fails
        with patch(
            "sts2_autotest.core.lock_manager.portalocker.lock",
            side_effect=Exception("locked"),
        ):
            result = _run(orch.start_session())

        assert result is False

    def test_stop_session_releases_lock(self, tmp_path: Path) -> None:
        """stop_session releases the acquired lock."""
        mock = _make_mock_adapter()
        lock_path = tmp_path / "session.lock"
        orch = TestOrchestrator(adapter=mock, lock_path=str(lock_path))

        with patch("sts2_autotest.core.lock_manager.portalocker.lock"), \
             patch("sts2_autotest.core.lock_manager.portalocker.unlock"):
            _run(orch.start_session())
            assert orch._lock_manager._lock_file is not None

            _run(orch.stop_session())

        assert orch._lock_manager._lock_file is None
        assert not lock_path.exists()

    def test_release_lock_if_held(self, tmp_path: Path) -> None:
        """_release_lock_if_held cleans up when start_session fails mid-way."""
        mock = _make_mock_adapter()
        mock.health_check.return_value = HealthStatus(healthy=False)
        lock_path = tmp_path / "session.lock"
        orch = TestOrchestrator(adapter=mock, lock_path=str(lock_path))

        # Lock acquired but health check fails → lock should be released
        with patch("sts2_autotest.core.lock_manager.portalocker.lock"), \
             patch("sts2_autotest.core.lock_manager.portalocker.unlock"):
            result = _run(orch.start_session())

        assert result is False
        assert orch._lock_manager._lock_file is None

    def test_no_lock_path_skips_check(self) -> None:
        """Without lock_path, start_session does not check locks."""
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)  # No lock_path
        result = _run(orch.start_session())
        assert result is True
