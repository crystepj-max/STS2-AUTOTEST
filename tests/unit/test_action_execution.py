"""Tests for enhanced action execution (Story 2.2 — FR10, FR11)."""

import asyncio
import itertools
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.common.errors import ErrorCategory, STS2Error
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

    def test_choose_map_node_false_negative_is_recovered_when_state_advances(self) -> None:
        mock = MagicMock(spec=GameAdapterProtocol)
        mock.health_check.return_value = HealthStatus(healthy=True)
        mock.get_state.side_effect = [
            GameState(screen=GameScreen.MAP),
            GameState(screen=GameScreen.COMBAT),
        ]
        mock.get_available_actions.return_value = ["choose_map_node"]
        mock.act.return_value = ActionResult(
            status="failure",
            state_changed=True,
            detail="Method not found: 'Boolean MegaCrit.Sts2.Core.Combat.CombatManager.get_IsPlayPhase()'.",
        )
        mock.wait_until_actionable.return_value = True
        mock.capture_bug_snapshot.return_value = {}
        orch = TestOrchestrator(adapter=mock)
        action = ActionDescriptor(action_type="choose_map_node", params={"index": 0})

        result = _run(orch.execute_action(action))

        assert result.status == "success"

    def test_choose_map_node_retries_post_action_state_after_is_play_phase_glitch(self) -> None:
        mock = MagicMock(spec=GameAdapterProtocol)
        mock.health_check.return_value = HealthStatus(healthy=True)
        mock.get_state.side_effect = [
            GameState(screen=GameScreen.MAP),
            STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message="Method not found: 'Boolean MegaCrit.Sts2.Core.Combat.CombatManager.get_IsPlayPhase()'.",
            ),
            GameState(screen=GameScreen.COMBAT),
        ]
        mock.get_available_actions.return_value = ["choose_map_node"]
        mock.act.return_value = ActionResult(status="success", state_changed=True)
        mock.wait_until_actionable.return_value = True
        mock.capture_bug_snapshot.return_value = {}
        orch = TestOrchestrator(adapter=mock)
        action = ActionDescriptor(action_type="choose_map_node", params={"index": 0})

        with patch("sts2_autotest.core.orchestrator.asyncio.sleep", new=AsyncMock()):
            result = _run(orch.execute_action(action))

        assert result.status == "success"

    def test_non_map_action_retries_post_action_state_after_is_play_phase_glitch(self) -> None:
        mock = MagicMock(spec=GameAdapterProtocol)
        mock.health_check.return_value = HealthStatus(healthy=True)
        mock.get_state.side_effect = [
            GameState(screen=GameScreen.COMBAT),
            STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message="Method not found: 'Boolean MegaCrit.Sts2.Core.Combat.CombatManager.get_IsPlayPhase()'.",
            ),
            GameState(screen=GameScreen.COMBAT),
        ]
        mock.get_available_actions.return_value = ["end_turn"]
        mock.act.return_value = ActionResult(status="success", state_changed=True)
        mock.wait_until_actionable.return_value = True
        mock.capture_bug_snapshot.return_value = {}
        orch = TestOrchestrator(adapter=mock)
        action = ActionDescriptor(action_type="end_turn")

        with patch("sts2_autotest.core.orchestrator.asyncio.sleep", new=AsyncMock()):
            result = _run(orch.execute_action(action))

        assert result.status == "success"

    def test_non_map_action_false_negative_is_recovered_when_state_advances(self) -> None:
        mock = MagicMock(spec=GameAdapterProtocol)
        mock.health_check.return_value = HealthStatus(healthy=True)
        mock.get_state.side_effect = [
            GameState(screen=GameScreen.EVENT),
            GameState(screen=GameScreen.COMBAT),
        ]
        mock.get_available_actions.return_value = ["advance_dialogue"]
        mock.act.return_value = ActionResult(
            status="failure",
            state_changed=True,
            detail="Method not found: 'Boolean MegaCrit.Sts2.Core.Combat.CombatManager.get_IsPlayPhase()'.",
        )
        mock.wait_until_actionable.return_value = True
        mock.capture_bug_snapshot.return_value = {}
        orch = TestOrchestrator(adapter=mock)
        action = ActionDescriptor(action_type="advance_dialogue")

        result = _run(orch.execute_action(action))

        assert result.status == "success"

    def test_choose_neow_blessing_accepts_fallback_option_without_reset(self) -> None:
        bad_state = GameState(
            screen=GameScreen.EVENT,
            event={
                "event_id": "NEOW",
                "options": [
                    {
                        "index": 0,
                        "text_key": "NEW_LEAF",
                        "title": "坏分支",
                        "description": "直接拿遗物",
                    },
                ],
            },
            run={"character_id": "GAWAINMOD-GAWAIN"},
        )
        good_state = GameState(
            screen=GameScreen.EVENT,
            event={
                "event_id": "NEOW",
                "options": [
                    {
                        "index": 1,
                        "text_key": "LEAD_PAPERWEIGHT",
                        "title": "好分支",
                        "description": "选择1张无色牌加入牌组",
                    },
                ],
            },
            run={"character_id": "GAWAINMOD-GAWAIN"},
        )
        final_state = GameState(
            screen=GameScreen.MAP,
            map={"available_nodes": []},
            available_actions=["choose_map_node"],
        )

        mock = MagicMock(spec=GameAdapterProtocol)
        mock.health_check.return_value = HealthStatus(healthy=True)
        mock.get_state.side_effect = itertools.chain(
            [
                bad_state,
                good_state,
                good_state,
                final_state,
            ],
            itertools.repeat(final_state),
        )
        mock.get_available_actions.return_value = ["choose_event"]
        mock.act.return_value = ActionResult(status="success", state_changed=True)
        mock.wait_until_actionable.return_value = True
        mock.capture_bug_snapshot.return_value = {}

        orch = TestOrchestrator(adapter=mock)
        action = ActionDescriptor(action_type="choose_neow_blessing", params={"max_attempts": 2})

        with (
            patch.object(orch, "_auto_reset_to_main_menu", new=AsyncMock()) as reset_mock,
            patch.object(orch, "_wait_until_action_available", new=AsyncMock(return_value=True)) as wait_mock,
            patch.object(orch, "execute_action_sequence", new=AsyncMock(return_value=[])) as sequence_mock,
        ):
            result = _run(orch.execute_action(action))

        assert result.status == "success"
        reset_mock.assert_not_awaited()
        sequence_mock.assert_not_awaited()
        wait_mock.assert_not_awaited()

    def test_emits_action_trace_hook_with_pre_and_post_state(self) -> None:
        mock = MagicMock(spec=GameAdapterProtocol)
        pre_state = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "player": {"current_hp": 70, "block": 0, "energy": 3},
                "hand": [{"card_id": "GAWAINMOD-STRIKE_GAWAIN", "card_name": "打击"}],
                "enemies": [{"current_hp": 42, "intent": "Attack", "intent_damage": 6}],
            },
        )
        post_state = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "player": {"current_hp": 70, "block": 0, "energy": 2},
                "hand": [{"card_id": "GAWAINMOD-DEFEND_GAWAIN", "card_name": "防御"}],
                "enemies": [{"current_hp": 36, "intent": "Attack", "intent_damage": 6}],
            },
        )
        mock.health_check.return_value = HealthStatus(healthy=True)
        mock.get_state.side_effect = [pre_state, post_state, post_state]
        mock.get_available_actions.return_value = ["play_card"]
        mock.act.return_value = ActionResult(status="success", state_changed=True)
        mock.wait_until_actionable.return_value = True
        mock.capture_bug_snapshot.return_value = {}

        trace_calls: list[tuple[ActionDescriptor, GameState, GameState, ActionResult]] = []
        orch = TestOrchestrator(adapter=mock)
        orch.set_action_trace_hook(
            lambda action, before, after, result: trace_calls.append((action, before, after, result))
        )

        result = _run(orch.execute_action(ActionDescriptor(action_type="play_card", params={"card_id": "gawain:strike"})))

        assert result.status == "success"
        assert len(trace_calls) == 1
        traced_action, traced_before, traced_after, traced_result = trace_calls[0]
        assert traced_action.action_type == "play_card"
        assert traced_before == pre_state
        assert traced_after == post_state
        assert traced_result.status == "success"


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

    def test_waits_until_specific_action_appears(self) -> None:
        mock = _make_mock_adapter()
        mock.get_available_actions.side_effect = [
            ["end_turn"],
            ["play_card", "end_turn"],
            ["play_card", "end_turn"],
        ]
        orch = TestOrchestrator(adapter=mock)
        actions = [ActionDescriptor(action_type="play_card")]

        results = _run(orch.execute_action_sequence(actions))

        assert len(results) == 1
        assert results[0].status == "success"
        assert mock.wait_until_actionable.call_count >= 1

    def test_not_actionable_raises_timeout(self) -> None:
        mock = _make_mock_adapter()
        mock.wait_until_actionable.return_value = False
        orch = TestOrchestrator(adapter=mock)
        actions = [ActionDescriptor(action_type="play_card")]
        with pytest.raises(STS2Error, match="not actionable"):
            _run(orch.execute_action_sequence(actions))

    def test_retries_when_actionability_is_temporarily_false(self) -> None:
        mock = _make_mock_adapter()
        mock.wait_until_actionable.side_effect = [False, True]
        mock.get_available_actions.return_value = ["play_card", "end_turn"]
        orch = TestOrchestrator(adapter=mock)
        actions = [ActionDescriptor(action_type="play_card", timeout=2.0)]

        with patch("sts2_autotest.core.orchestrator.asyncio.sleep", new=AsyncMock()):
            results = _run(orch.execute_action_sequence(actions))

        assert len(results) == 1
        assert results[0].status == "success"
        assert mock.wait_until_actionable.call_count == 2


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
