"""Tests for core/orchestrator.py — TestOrchestrator lifecycle."""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.common.errors import ErrorCategory, STS2Error
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.action_model import TestResult
from sts2_autotest.core.orchestrator import SessionSummary, TestOrchestrator
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

    def test_run_failed_none_skips(self, orchestrator: TestOrchestrator) -> None:
        orchestrator._last_results = [
            TestResult("TC-001", "pass"),
            TestResult("TC-002", "pass"),
        ]
        summary = _run(orchestrator.run_failed())
        assert summary.total == 0


class TestResultClassification:
    """AC#3: pass/fail/crash/skip classification."""

    def test_pass_classification(self, orchestrator: TestOrchestrator) -> None:
        result = _run(orchestrator.execute_case("TC-001"))
        assert result.status == "pass"

    def test_crash_stops_subsequent_cases(
        self, mock_adapter: Any
    ) -> None:
        """AC#6: crash marks subsequent cases as skip."""
        mock = _make_mock_adapter()
        mock.act.side_effect = STS2Error(
            category=ErrorCategory.CRASH_ERROR,
            message="Game crashed",
        )
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
        orch = TestOrchestrator(adapter=mock)
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
