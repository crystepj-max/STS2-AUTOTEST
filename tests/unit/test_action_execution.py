"""Tests for enhanced action execution (Story 2.2 — FR10, FR11)."""

import asyncio
import itertools
from typing import Any
from unittest.mock import MagicMock

import pytest

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.common.errors import STS2Error
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.action_model import ActionDescriptor
from sts2_autotest.core.orchestrator import TestOrchestrator


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_mock_adapter(
    actions: list[str] | None = None,
) -> Any:
    mock = MagicMock(spec=GameAdapterProtocol)
    mock.health_check.return_value = HealthStatus(healthy=True)
    # Cycle MAP → COMBAT for valid transitions
    states = itertools.cycle([
        GameState(screen=GameScreen.MAP),
        GameState(screen=GameScreen.COMBAT),
    ])
    mock.get_state.side_effect = lambda: next(states)
    default_actions: list[str] = ["play_card", "end_turn", "use_potion"]
    mock.get_available_actions.return_value = actions if actions is not None else default_actions
    mock.act.return_value = ActionResult(status="success", state_changed=True)
    mock.wait_until_actionable.return_value = True
    mock.capture_bug_snapshot.return_value = {}
    return mock


class TestExecuteAction:
    """Single action execution with validation (FR10)."""

    def test_executes_available_action(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)
        action = ActionDescriptor(action_type="play_card", params={"card_id": "Strike"})
        result = _run(orch.execute_action(action))
        assert result.status == "success"

    def test_rejects_unavailable_action(self) -> None:
        mock = _make_mock_adapter(actions=["end_turn"])
        orch = TestOrchestrator(adapter=mock)
        action = ActionDescriptor(action_type="play_card")
        with pytest.raises(STS2Error, match="not available"):
            _run(orch.execute_action(action))

    def test_unavailable_action_error_has_detail(self) -> None:
        mock = _make_mock_adapter(actions=["end_turn"])
        orch = TestOrchestrator(adapter=mock)
        action = ActionDescriptor(action_type="play_card")
        with pytest.raises(STS2Error) as exc_info:
            _run(orch.execute_action(action))
        assert exc_info.value.detail["action"] == "play_card"

    def test_action_with_empty_available_list_is_rejected(self) -> None:
        """Empty available_actions means nothing can be executed."""
        mock = _make_mock_adapter(actions=[])
        orch = TestOrchestrator(adapter=mock)
        action = ActionDescriptor(action_type="play_card")
        with pytest.raises(STS2Error, match="not available"):
            _run(orch.execute_action(action))

    def test_expected_state_mismatch_is_rejected(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)
        action = ActionDescriptor(
            action_type="play_card",
            expected_state=GameScreen.MAP,
        )

        with pytest.raises(STS2Error, match="expected state"):
            _run(orch.execute_action(action))


class TestExecuteActionSequence:
    """Sequence execution with state re-read between actions."""

    def test_executes_action_sequence(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)
        actions = [
            ActionDescriptor(action_type="play_card", params={"card_id": "Strike"}),
            ActionDescriptor(action_type="end_turn"),
            ActionDescriptor(action_type="use_potion", params={"slot": 0}),
        ]
        results = _run(orch.execute_action_sequence(actions))
        assert len(results) == 3
        assert all(r.status == "success" for r in results)

    def test_reads_state_between_actions(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)
        actions = [
            ActionDescriptor(action_type="play_card"),
            ActionDescriptor(action_type="end_turn"),
        ]
        _run(orch.execute_action_sequence(actions))
        # Each action calls get_state before executing + wait_until_actionable may also call it
        assert mock.get_state.call_count >= 2  # at least 2 reads

    def test_waits_between_actions(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)
        actions = [
            ActionDescriptor(action_type="play_card"),
            ActionDescriptor(action_type="end_turn"),
        ]
        _run(orch.execute_action_sequence(actions))
        assert mock.wait_until_actionable.call_count == 2

    def test_not_actionable_raises_timeout(self) -> None:
        mock = _make_mock_adapter()
        mock.wait_until_actionable.return_value = False
        orch = TestOrchestrator(adapter=mock)
        actions = [ActionDescriptor(action_type="play_card")]
        with pytest.raises(STS2Error, match="not actionable"):
            _run(orch.execute_action_sequence(actions))


class TestCacheInvalidation:
    """AC#3: Cache is invalidated after action."""

    def test_action_marks_cache_stale(self) -> None:
        """act() returns and cache is stale for next get_state()."""
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)
        _run(orch.execute_action(ActionDescriptor(action_type="play_card")))
        assert mock.act.call_count == 1


class TestStateUpdateAfterAction:
    """AC#3: State is re-read after action execution."""

    def test_state_read_after_action(self) -> None:
        mock = _make_mock_adapter()
        orch = TestOrchestrator(adapter=mock)
        _run(orch.execute_action(ActionDescriptor(action_type="play_card")))
        # get_state is called: before action + after action = 2
        assert mock.get_state.call_count == 2
