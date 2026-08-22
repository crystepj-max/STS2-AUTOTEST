"""Tests for dsl/fluent.py and dsl/assertions.py: Fluent API."""

import asyncio
import itertools
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.action_model import ActionDescriptor, TestResult
from sts2_autotest.core.orchestrator import TestOrchestrator
from sts2_autotest.dsl import define
from sts2_autotest.dsl.assertions import (
    choose_event,
    end_turn,
    enemy_hp_decreased_by,
    enemy_took_exact_hits,
    enter_combat,
    game_reached_state,
    give_card,
    hand_size_changed_by,
    minion_queue_ids_are,
    play_card,
    player_block_increased_by,
    player_energy_decreased_by,
    player_hp_changed_by,
    set_hp,
    set_seed,
    start_game,
    start_new_run,
)


def _make_adapt() -> Any:
    mock = MagicMock(spec=GameAdapterProtocol)
    states = itertools.cycle(
        [
            GameState(screen=GameScreen.MAP),
            GameState(screen=GameScreen.COMBAT),
        ]
    )
    mock.health_check.return_value = HealthStatus(healthy=True)
    mock.get_state.side_effect = lambda: next(states)
    mock.get_available_actions.return_value = [
        "play_card",
        "end_turn",
        "enter_combat",
        "start_game",
        "probe",
    ]
    mock.act.return_value = ActionResult(status="success", state_changed=True)
    mock.wait_until_actionable.return_value = True
    return mock


@pytest.fixture
def orch() -> TestOrchestrator:
    return TestOrchestrator(adapter=_make_adapt())


class TestFluentBuilder:
    """FluentBuilder chainable API tests."""

    def test_setup_returns_self(self, orch: TestOrchestrator) -> None:
        builder = define("TC-001", orch)
        result = builder.setup(start_game())
        assert result is builder

    def test_execute_returns_self(self, orch: TestOrchestrator) -> None:
        builder = define("TC-001", orch)
        result = builder.execute(play_card("Strike"))
        assert result is builder

    def test_full_chain(self, orch: TestOrchestrator) -> None:
        loop = asyncio.new_event_loop()
        result = (
            define("card-damage", orch, loop)
            .setup(start_game(), enter_combat("JawWorm"))
            .execute(play_card("VoidSlash", target=0))
            .assert_that(game_reached_state(GameScreen.MAP))
        )
        loop.close()
        assert isinstance(result, TestResult)
        assert result.status in ("pass", "fail")

    def test_require_start_state_fails_before_action_execution(
        self, orch: TestOrchestrator
    ) -> None:
        loop = asyncio.new_event_loop()
        result = (
            define("TC-START-GUARD", orch, loop)
            .require_start_state("- current screen is MAIN_MENU")
            .execute(play_card("Strike"))
            .assert_that()
        )
        loop.close()
        assert result.status == "fail"
        assert any("start state" in failure for failure in result.failures)
        assert orch.adapter.act.call_count == 0

    def test_require_start_state_accepts_allowed_screen_list(
        self, orch: TestOrchestrator
    ) -> None:
        loop = asyncio.new_event_loop()
        result = (
            define("TC-START-ALLOWED-LIST", orch, loop)
            .require_start_state(
                "- resumable state\n"
                "- current screen may be MAIN_MENU / CHARACTER_SELECT / MAP / COMBAT / UNKNOWN"
            )
            .execute(play_card("Strike"))
            .assert_that()
        )
        loop.close()
        assert result.status == "pass"
        assert orch.adapter.act.call_count == 1

    def test_neow_event_start_state_accepts_already_resolved_map(
        self, orch: TestOrchestrator
    ) -> None:
        loop = asyncio.new_event_loop()
        builder = define("TC-NEOW-ALREADY-RESOLVED", orch, loop).require_start_state(
            "- 已进入新 run\n- 当前位于开局事件界面，且事件可交互"
        )
        failures = builder._check_start_state(loop)
        loop.close()
        assert failures == []

    def test_neow_event_start_state_accepts_already_resolved_combat(
        self, orch: TestOrchestrator
    ) -> None:
        orch.adapter.get_state.side_effect = None
        orch.adapter.get_state.return_value = GameState(screen=GameScreen.COMBAT)
        loop = asyncio.new_event_loop()
        builder = define("TC-NEOW-ALREADY-IN-COMBAT", orch, loop).require_start_state(
            "- 已进入新 run\n- 当前位于开局事件界面，且事件可交互"
        )
        failures = builder._check_start_state(loop)
        loop.close()
        assert failures == []

    def test_map_node_start_state_accepts_already_entered_combat(
        self, orch: TestOrchestrator
    ) -> None:
        orch.adapter.get_state.side_effect = None
        orch.adapter.get_state.return_value = GameState(screen=GameScreen.COMBAT)
        orch.adapter.get_available_actions.return_value = ["play_card", "end_turn"]
        loop = asyncio.new_event_loop()
        builder = define("TC-FIRST-BATTLE-ALREADY-IN-COMBAT", orch, loop).require_start_state(
            "- 当前位于地图界面\n- 存在至少一个可到达的普通战斗节点"
        )
        failures = builder._check_start_state(loop)
        loop.close()
        assert failures == []

    def test_first_battle_start_state_accepts_pending_event(
        self, orch: TestOrchestrator
    ) -> None:
        orch.adapter.get_state.side_effect = None
        orch.adapter.get_state.return_value = GameState(screen=GameScreen.EVENT)
        orch.adapter.get_available_actions.return_value = [
            "choose_event",
            "advance_dialogue",
        ]
        loop = asyncio.new_event_loop()
        builder = define("TC-FIRST-BATTLE-PENDING-EVENT", orch, loop).require_start_state(
            "- 当前位于地图界面\n- 存在至少一个可到达的普通战斗节点"
        )
        failures = builder._check_start_state(loop)
        loop.close()
        assert failures == []

    def test_assert_that_without_loop_creates_temporary_loop(
        self, orch: TestOrchestrator
    ) -> None:
        asyncio.set_event_loop(None)
        try:
            result = define("TC-NO-LOOP", orch).execute(start_game()).assert_that()
        finally:
            asyncio.set_event_loop(None)
        assert isinstance(result, TestResult)
        assert result.status in ("pass", "fail")

    def test_writes_per_step_behavior_logs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = MagicMock(spec=GameAdapterProtocol)
        adapter.health_check.return_value = HealthStatus(healthy=True)
        adapter.get_available_actions.return_value = ["play_card", "end_turn"]
        adapter.wait_until_actionable.return_value = True
        adapter.act.return_value = ActionResult(status="success", state_changed=True)
        adapter.get_state.side_effect = [
            GameState(
                screen=GameScreen.COMBAT,
                combat={
                    "player": {"current_hp": 70, "block": 0, "energy": 3},
                    "hand": [{"card_id": "GAWAINMOD-STRIKE_GAWAIN", "card_name": "打击"}],
                    "enemies": [{"current_hp": 42, "intent": "Attack", "intent_damage": 6}],
                },
            ),
            GameState(
                screen=GameScreen.COMBAT,
                combat={
                    "player": {"current_hp": 70, "block": 0, "energy": 2},
                    "hand": [{"card_id": "GAWAINMOD-DEFEND_GAWAIN", "card_name": "防御"}],
                    "enemies": [{"current_hp": 36, "intent": "Attack", "intent_damage": 6}],
                },
            ),
            GameState(
                screen=GameScreen.COMBAT,
                combat={
                    "player": {"current_hp": 70, "block": 0, "energy": 2},
                    "hand": [{"card_id": "GAWAINMOD-DEFEND_GAWAIN", "card_name": "防御"}],
                    "enemies": [{"current_hp": 36, "intent": "Attack", "intent_damage": 6}],
                },
            ),
        ]

        monkeypatch.setenv("STS2_CASE_TRACE_ROOT", str(tmp_path))
        monkeypatch.setenv(
            "PYTEST_CURRENT_TEST",
            "tests/unit/test_fluent_api.py::test_trace_suite (call)",
        )

        orch = TestOrchestrator(adapter=adapter)
        loop = asyncio.new_event_loop()
        try:
            result = (
                define("TC-TRACE", orch, loop)
                .execute(play_card("gawain:strike"))
                .assert_that(game_reached_state(GameScreen.COMBAT))
            )
        finally:
            loop.close()

        case_log = tmp_path / "test_fluent_api-test_trace_suite" / "TC-TRACE" / "case.log"
        step_log = tmp_path / "test_fluent_api-test_trace_suite" / "TC-TRACE" / "step-01.log"
        assert result.passed is True
        assert os.path.exists(case_log)
        assert os.path.exists(step_log)
        text = case_log.read_text(encoding="utf-8")
        assert "打出卡牌 打击" in text
        assert "gawain:strike → GAWAINMOD-STRIKE_GAWAIN" in text
        assert "敌方总生命减少 6" in text

    def test_writes_case_log_when_no_atomic_action_is_recorded(
        self,
        orch: TestOrchestrator,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """零步骤用例也必须留下明确结果，不能只在汇总中声称通过。"""
        monkeypatch.setenv("STS2_CASE_TRACE_ROOT", str(tmp_path))
        monkeypatch.setenv(
            "PYTEST_CURRENT_TEST",
            "tests/unit/test_fluent_api.py::test_zero_step_suite (call)",
        )

        result = define("TC-ZERO-STEP", orch).assert_that()

        case_log = (
            tmp_path
            / "test_fluent_api-test_zero_step_suite"
            / "TC-ZERO-STEP"
            / "case.log"
        )
        assert result.passed is True
        assert case_log.is_file()
        text = case_log.read_text(encoding="utf-8")
        assert "步骤记录：0" in text
        assert "结果：passed" in text


class TestAssertionFunctions:
    """Game semantic assertion functions."""

    def test_game_reached_state_match(self) -> None:
        fn = game_reached_state(GameScreen.MAP)
        ok, _ = fn(GameState(screen=GameScreen.MAP))
        assert ok is True

    def test_game_reached_state_mismatch(self) -> None:
        fn = game_reached_state(GameScreen.MAIN_MENU)
        ok, msg = fn(GameState(screen=GameScreen.COMBAT))
        assert ok is False
        assert "MAIN_MENU" in msg

    def test_enemy_hp_decreased_by_with_previous(self) -> None:
        fn = enemy_hp_decreased_by(15)
        state = GameState(screen=GameScreen.COMBAT, enemy_hp=85, previous_enemy_hp=100)
        ok, _ = fn(state)
        assert ok is True

    def test_enemy_hp_decreased_by_insufficient(self) -> None:
        fn = enemy_hp_decreased_by(15)
        state = GameState(screen=GameScreen.COMBAT, enemy_hp=95, previous_enemy_hp=100)
        ok, msg = fn(state)
        assert ok is False
        assert "decrease" in msg

    def test_enemy_took_exact_hits_from_top_level_damage_events(self) -> None:
        fn = enemy_took_exact_hits(5, 2)
        state = GameState(
            screen=GameScreen.COMBAT,
            damage_events=[
                {"amount": 5, "target": 0},
                {"amount": 5, "target": 0},
            ],
        )
        ok, _ = fn(state)
        assert ok is True

    def test_enemy_took_exact_hits_rejects_wrong_hit_count(self) -> None:
        fn = enemy_took_exact_hits(5, 2)
        state = GameState(
            screen=GameScreen.COMBAT,
            damage_events=[{"amount": 10, "target": 0}],
        )
        ok, msg = fn(state)
        assert ok is False
        assert "5 x 2" in msg

    def test_enemy_took_exact_hits_falls_back_to_hp_delta(self) -> None:
        # Agent 路径无 damage_events：回退到 previous_enemy_hp 差值等价校验（5×2=10）。
        fn = enemy_took_exact_hits(5, 2)
        state = GameState(screen=GameScreen.COMBAT, enemy_hp=29, previous_enemy_hp=39)
        ok, _ = fn(state)
        assert ok is True

    def test_enemy_took_exact_hits_fallback_insufficient_delta(self) -> None:
        fn = enemy_took_exact_hits(5, 2)
        state = GameState(screen=GameScreen.COMBAT, enemy_hp=35, previous_enemy_hp=39)
        ok, msg = fn(state)
        assert ok is False
        assert "HP decrease" in msg

    def test_enemy_took_exact_hits_fallback_without_previous_hp_fails(self) -> None:
        fn = enemy_took_exact_hits(5, 2)
        state = GameState(screen=GameScreen.COMBAT, enemy_hp=29)
        ok, msg = fn(state)
        assert ok is False
        assert "damage_events not in state" in msg

    def test_player_energy_decreased_by(self) -> None:
        fn = player_energy_decreased_by(1)
        state = GameState(screen=GameScreen.COMBAT, energy=2, previous_energy=3)
        ok, _ = fn(state)
        assert ok is True

    def test_player_hp_changed_by_damage(self) -> None:
        fn = player_hp_changed_by(-10)
        state = GameState(screen=GameScreen.COMBAT, hp=90, previous_hp=100)
        ok, _ = fn(state)
        assert ok is True

    def test_player_hp_changed_by_reads_nested_current_hp_shape(self) -> None:
        fn = player_hp_changed_by(1)
        state = GameState(
            screen=GameScreen.COMBAT,
            combat={"player": {"current_hp": 75}},
            previous_hp=74,
        )
        ok, _ = fn(state)
        assert ok is True

    def test_player_hp_changed_by_missing_previous(self) -> None:
        fn = player_hp_changed_by(10)
        state = GameState(screen=GameScreen.COMBAT, hp=100)
        ok, msg = fn(state)
        assert ok is False
        assert "previous_hp" in msg

    def test_player_block_increased_by_flat_fields(self) -> None:
        fn = player_block_increased_by(5)
        state = GameState(screen=GameScreen.COMBAT, block=5, previous_block=0)
        ok, _ = fn(state)
        assert ok is True

    def test_player_block_increased_by_missing_previous(self) -> None:
        fn = player_block_increased_by(5)
        state = GameState(screen=GameScreen.COMBAT, block=5)
        ok, msg = fn(state)
        assert ok is False
        assert "previous_block" in msg

    def test_hand_size_changed_by_flat_fields(self) -> None:
        fn = hand_size_changed_by(-1)
        state = GameState(screen=GameScreen.COMBAT, hand_count=4, previous_hand_count=5)
        ok, _ = fn(state)
        assert ok is True

    def test_hand_size_changed_by_reads_nested_hand_list(self) -> None:
        fn = hand_size_changed_by(-1)
        state = GameState(
            screen=GameScreen.COMBAT,
            combat={"player": {}, "hand": [{"card_id": "A"}, {"card_id": "B"}]},
            previous_hand_count=3,
        )
        ok, _ = fn(state)
        assert ok is True

    def test_player_block_increased_by_reads_nested_combat_player_shape(self) -> None:
        """Real CLI adapter nests resources under state.combat['player'][...]."""
        fn = player_block_increased_by(5)
        state = GameState(
            screen=GameScreen.COMBAT,
            combat={"player": {"block": 5}},
            previous_block=0,
        )
        ok, _ = fn(state)
        assert ok is True

    def test_enemy_hp_decreased_by_reads_nested_combat_enemies_shape(self) -> None:
        """Real adapter enemy HP assertions should work on nested combat enemy state."""
        fn = enemy_hp_decreased_by(6)
        state = GameState(
            screen=GameScreen.COMBAT,
            combat={"enemies": [{"current_hp": 36, "is_alive": True}]},
            previous_enemy_hp=42,
        )
        ok, _ = fn(state)
        assert ok is True

    def test_enemy_hp_decreased_by_sums_multi_enemy_hp(self) -> None:
        fn = enemy_hp_decreased_by(3)
        state = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "enemies": [
                    {"current_hp": 26, "is_alive": True},
                    {"current_hp": 18, "is_alive": True},
                ]
            },
            previous_enemy_hp=47,
        )
        ok, _ = fn(state)
        assert ok is True

    def test_minion_queue_ids_are_reads_agent_view_shape(self) -> None:
        fn = minion_queue_ids_are(["cecil_militia"])
        state = GameState(
            screen=GameScreen.COMBAT,
            agent_view={
                "combat": {
                    "minion_queue": [
                        {"id": "cecil_militia", "type": "Defense"},
                    ]
                }
            },
        )
        ok, _ = fn(state)
        assert ok is True


class TestSetupFunctions:
    """Setup functions return ActionDescriptors."""

    def test_start_game(self) -> None:
        d = start_game()
        assert d.action_type == "start_game"

    def test_start_new_run_uses_semantic_action_name(self) -> None:
        d = start_new_run()
        assert d.action_type == "start_new_run"

    def test_enter_combat(self) -> None:
        d = enter_combat("JawWorm")
        assert d.action_type == "enter_combat"
        assert d.params["enemy"] == "JawWorm"

    def test_play_card(self) -> None:
        d = play_card("Strike", target=1)
        assert d.action_type == "play_card"
        assert d.params["card_id"] == "Strike"
        assert d.params["target"] == 1

    def test_set_seed(self) -> None:
        d = set_seed(42)
        assert d.params["seed"] == 42

    def test_give_card(self) -> None:
        d = give_card("VoidSlash")
        assert d.params["card_id"] == "VoidSlash"

    def test_set_hp(self) -> None:
        d = set_hp(50)
        assert d.params["hp"] == 50

    def test_end_turn(self) -> None:
        d = end_turn()
        assert d.action_type == "end_turn"
        assert d.params == {}


class TestDslExports:
    """DSL exports assertion and setup functions."""

    def test_define_available(self) -> None:
        from sts2_autotest.dsl import define

        assert callable(define)

    def test_assertion_functions_available(self) -> None:
        from sts2_autotest.dsl import enemy_hp_decreased_by, game_reached_state

        assert callable(game_reached_state)
        assert callable(enemy_hp_decreased_by)

    def test_handler_functions_available(self) -> None:
        from sts2_autotest.dsl import capture_screenshot, log_state

        assert callable(capture_screenshot)
        assert callable(log_state)

    def test_fixture_loader_available(self) -> None:
        from sts2_autotest.dsl import FixtureLoader

        assert FixtureLoader is not None

    def test_handler_fn_type_available(self) -> None:
        from sts2_autotest.dsl import HandlerFn

        assert HandlerFn is not None


class TestUnifiedTestResult:
    """TestResult is the single result model for both Orchestrator and Fluent API."""

    def test_passed_property(self) -> None:
        r = TestResult(case_id="c1", status="pass")
        assert r.passed is True

    def test_failed_not_passed(self) -> None:
        r = TestResult(case_id="c1", status="fail")
        assert r.passed is False

    def test_assert_that_returns_test_result(self, orch: TestOrchestrator) -> None:
        loop = asyncio.new_event_loop()
        result = define("TC-UNI", orch, loop).assert_that()
        loop.close()
        assert isinstance(result, TestResult)
        assert result.case_id == "TC-UNI"

    def test_on_error_handler_called_on_failure(self, orch: TestOrchestrator) -> None:
        calls: list[str] = []

        def handler(o: TestOrchestrator, cid: str) -> None:
            calls.append(cid)

        loop = asyncio.new_event_loop()
        result = (
            define("TC-ERR", orch, loop)
            .execute(start_game())
            .on_error(handler)
            .assert_that(game_reached_state(GameScreen.MAIN_MENU))
        )
        loop.close()
        assert isinstance(result, TestResult)
        if result.status == "fail":
            assert calls == ["TC-ERR"]

    def test_on_error_rejects_handler_with_wrong_arity(
        self, orch: TestOrchestrator
    ) -> None:
        builder = define("TC-BAD-HANDLER", orch)

        with pytest.raises(TypeError, match="on_error handler"):
            builder.on_error(lambda case_id: None)

    def test_on_error_rejects_non_callable(self, orch: TestOrchestrator) -> None:
        builder = define("TC-BAD-HANDLER", orch)

        with pytest.raises(TypeError, match="on_error handler"):
            builder.on_error("not callable")  # type: ignore[arg-type]


class TestFluentBuilderSettle:
    """FluentBuilder polls when get_state returns UNKNOWN after execute (screen settle)."""

    def _make_adapter(
        self,
        states: list[GameState] | None = None,
        fixed_state: GameState | None = None,
    ) -> Any:
        mock = MagicMock(spec=GameAdapterProtocol)
        mock.health_check.return_value = HealthStatus(healthy=True)
        mock.get_available_actions.return_value = ["start_game"]
        mock.act.return_value = ActionResult(status="success", state_changed=True)
        mock.wait_until_actionable.return_value = True
        if states is not None:
            mock.get_state.side_effect = states
        else:
            mock.get_state.return_value = fixed_state
        return mock

    def test_polls_until_unknown_screen_settles(self) -> None:
        """When get_state returns UNKNOWN after execute, polls until real screen."""
        # execute_action calls get_state twice (pre + post); assertion calls it once more.
        # post=UNKNOWN triggers settle; next poll returns EVENT.
        mock_adapter = self._make_adapter(states=[
            GameState(screen=GameScreen.MAP),      # pre_action (execute_action line 802)
            GameState(screen=GameScreen.UNKNOWN),  # post_action (execute_action line 840) — transitioning
            GameState(screen=GameScreen.UNKNOWN),  # assertion initial → triggers settle polling
            GameState(screen=GameScreen.EVENT),    # settle poll → settled
        ])
        orch = TestOrchestrator(adapter=mock_adapter)
        loop = asyncio.new_event_loop()
        try:
            result = (
                define("TC-SETTLE", orch, loop, settle_timeout=1.0, settle_poll_interval=0.001)
                .execute(start_game())
                .assert_that(game_reached_state(GameScreen.EVENT))
            )
        finally:
            loop.close()
        assert result.passed, f"Expected pass after settle; failures={result.failures}"
        assert mock_adapter.get_state.call_count == 4

    def test_no_polling_when_assertion_state_is_not_unknown(self) -> None:
        """If first get_state after execute returns a non-UNKNOWN screen, no settle polling."""
        mock_adapter = self._make_adapter(states=[
            GameState(screen=GameScreen.MAP),    # pre_action
            GameState(screen=GameScreen.EVENT),  # post_action (MAP→EVENT valid)
            GameState(screen=GameScreen.EVENT),  # assertion → EVENT ≠ UNKNOWN → pass immediately
        ])
        orch = TestOrchestrator(adapter=mock_adapter)
        loop = asyncio.new_event_loop()
        try:
            result = (
                define("TC-NO-POLL", orch, loop, settle_timeout=5.0, settle_poll_interval=0.001)
                .execute(start_game())
                .assert_that(game_reached_state(GameScreen.EVENT))
            )
        finally:
            loop.close()
        assert result.passed
        assert mock_adapter.get_state.call_count == 3  # no settle polls

    def test_transition_actions_wait_for_repeated_settled_state(self) -> None:
        """Cross-screen actions like choose_event should wait for one repeated settled snapshot."""
        mock_adapter = self._make_adapter(states=[
            GameState(screen=GameScreen.EVENT),  # pre_action
            GameState(screen=GameScreen.MAP, available_actions=["choose_map_node"]),
            GameState(screen=GameScreen.MAP, available_actions=["choose_map_node"]),
            GameState(screen=GameScreen.MAP, available_actions=["choose_map_node"]),
            GameState(screen=GameScreen.MAP, available_actions=["choose_map_node"]),
            GameState(screen=GameScreen.MAP, available_actions=["choose_map_node"]),
        ])
        mock_adapter.get_available_actions.return_value = ["choose_event"]
        orch = TestOrchestrator(adapter=mock_adapter)
        loop = asyncio.new_event_loop()
        try:
            result = (
                define("TC-TRANSITION-SETTLE", orch, loop, settle_timeout=1.0, settle_poll_interval=0.001)
                .execute(choose_event(0))
                .assert_that(game_reached_state(GameScreen.MAP))
            )
        finally:
            loop.close()
        assert result.passed, f"Expected transition settle to pass; failures={result.failures}"
        assert mock_adapter.get_state.call_count >= 4

    def test_choose_map_node_by_type_waits_for_combat_transition(self) -> None:
        """Map-node transitions should wait until the battle screen really loads."""
        mock_adapter = self._make_adapter(states=[
            GameState(screen=GameScreen.MAP, available_actions=["choose_map_node"]),
            GameState(screen=GameScreen.MAP, available_actions=["choose_map_node"]),
            GameState(screen=GameScreen.UNKNOWN),
            GameState(screen=GameScreen.COMBAT, available_actions=["play_card", "end_turn"]),
            GameState(screen=GameScreen.COMBAT, available_actions=["play_card", "end_turn"]),
        ])
        mock_adapter.get_available_actions.return_value = ["choose_map_node_by_type"]
        orch = TestOrchestrator(adapter=mock_adapter)
        loop = asyncio.new_event_loop()
        try:
            result = (
                define("TC-MAP-NODE-BY-TYPE-SETTLE", orch, loop, settle_timeout=1.0, settle_poll_interval=0.001)
                .execute(ActionDescriptor(action_type="choose_map_node_by_type", params={"node_type": "Monster"}))
                .assert_that(game_reached_state(GameScreen.COMBAT))
            )
        finally:
            loop.close()
        assert result.passed, f"Expected combat transition settle to pass; failures={result.failures}"
        assert mock_adapter.get_state.call_count >= 4

    def test_settle_timeout_uses_last_unknown_state_for_failure(self) -> None:
        """When UNKNOWN persists past settle_timeout, FAIL is reported with last UNKNOWN state."""
        mock_adapter = self._make_adapter(fixed_state=GameState(screen=GameScreen.UNKNOWN))
        orch = TestOrchestrator(adapter=mock_adapter)
        loop = asyncio.new_event_loop()
        try:
            result = (
                define("TC-TIMEOUT", orch, loop, settle_timeout=0.01, settle_poll_interval=0.001)
                .execute(start_game())
                .assert_that(game_reached_state(GameScreen.EVENT))
            )
        finally:
            loop.close()
        assert not result.passed
        assert result.state_snapshot is not None
        assert result.state_snapshot.screen == GameScreen.UNKNOWN

    def test_known_wrong_state_fails_immediately_without_settle_polling(self) -> None:
        """If screen is a real but wrong state (not UNKNOWN), no settle polling occurs."""
        mock_adapter = self._make_adapter(states=[
            GameState(screen=GameScreen.MAP),     # pre_action
            GameState(screen=GameScreen.COMBAT),  # post_action (MAP→COMBAT valid)
            GameState(screen=GameScreen.COMBAT),  # assertion → COMBAT ≠ UNKNOWN → no poll
        ])
        orch = TestOrchestrator(adapter=mock_adapter)
        loop = asyncio.new_event_loop()
        try:
            result = (
                define("TC-WRONG", orch, loop, settle_timeout=5.0, settle_poll_interval=0.001)
                .execute(start_game())
                .assert_that(game_reached_state(GameScreen.EVENT))
            )
        finally:
            loop.close()
        assert not result.passed
        # If settle polling occurred, side_effect would be exhausted (StopIteration → test error).
        # call_count == 3 proves no extra polling happened.
        assert mock_adapter.get_state.call_count == 3

    def test_polls_until_transition_combat_state_stabilizes(self) -> None:
        """Turn transitions should wait past empty-hand frames until the settled result repeats."""
        mock_adapter = self._make_adapter(states=[
            GameState(
                screen=GameScreen.COMBAT,
                combat={"player": {"block": 3}, "hand": [{"card_id": "A"}]},
                available_actions=["end_turn", "play_card"],
            ),
            GameState(
                screen=GameScreen.COMBAT,
                combat={"player": {"block": 3}, "hand": []},
                available_actions=[],
            ),
            GameState(
                screen=GameScreen.COMBAT,
                combat={"player": {"block": 3}, "hand": []},
                available_actions=[],
            ),
            GameState(
                screen=GameScreen.COMBAT,
                combat={"player": {"block": 3}, "hand": [{"card_id": "B"}]},
                available_actions=["end_turn", "play_card"],
            ),
            GameState(
                screen=GameScreen.COMBAT,
                combat={"player": {"block": 6}, "hand": [{"card_id": "B"}, {"card_id": "C"}]},
                available_actions=["end_turn", "play_card"],
            ),
            GameState(
                screen=GameScreen.COMBAT,
                combat={"player": {"block": 6}, "hand": [{"card_id": "B"}, {"card_id": "C"}]},
                available_actions=["end_turn", "play_card"],
            ),
        ])
        mock_adapter.get_available_actions.return_value = ["end_turn"]
        orch = TestOrchestrator(adapter=mock_adapter)
        loop = asyncio.new_event_loop()
        try:
            result = (
                define("TC-COMBAT-SETTLE", orch, loop, settle_timeout=1.0, settle_poll_interval=0.001)
                .execute(end_turn())
                .assert_that(player_block_increased_by(3))
            )
        finally:
            loop.close()
        assert result.passed, f"Expected settled transition to pass; failures={result.failures}"
        assert mock_adapter.get_state.call_count == 6

    def test_end_turn_waits_until_hand_refills_before_asserting(self) -> None:
        """A stable-looking new-turn frame with a short hand should still wait for draw completion."""
        mock_adapter = self._make_adapter(states=[
            GameState(
                screen=GameScreen.COMBAT,
                combat={"player": {"block": 0}, "hand": [{"card_id": str(i)} for i in range(5)]},
                available_actions=["end_turn", "play_card"],
            ),
            GameState(
                screen=GameScreen.COMBAT,
                combat={"player": {"block": 0}, "hand": [{"card_id": str(i)} for i in range(4)]},
                available_actions=["end_turn", "play_card"],
            ),
            GameState(
                screen=GameScreen.COMBAT,
                combat={"player": {"block": 3}, "hand": [{"card_id": str(i)} for i in range(5)]},
                available_actions=["end_turn", "play_card"],
            ),
            GameState(
                screen=GameScreen.COMBAT,
                combat={"player": {"block": 3}, "hand": [{"card_id": str(i)} for i in range(5)]},
                available_actions=["end_turn", "play_card"],
            ),
        ])
        mock_adapter.get_available_actions.return_value = ["end_turn"]
        orch = TestOrchestrator(adapter=mock_adapter)
        loop = asyncio.new_event_loop()
        try:
            result = (
                define("TC-END-TURN-DRAW", orch, loop, settle_timeout=1.0, settle_poll_interval=0.001)
                .execute(end_turn())
                .assert_that(player_block_increased_by(3))
            )
        finally:
            loop.close()
        assert result.passed, f"Expected draw-complete settle to pass; failures={result.failures}"


class TestPreviousSnapshotMerge:
    """assert_that() captures a pre-execute snapshot so resource-delta assertions
    (player_block_increased_by, player_hp_changed_by, ...) work without the adapter
    itself tracking history — see FluentBuilder._merge_previous_snapshot."""

    def _make_adapter(self, states: list[GameState]) -> Any:
        mock = MagicMock(spec=GameAdapterProtocol)
        mock.health_check.return_value = HealthStatus(healthy=True)
        mock.get_available_actions.return_value = ["play_card"]
        mock.act.return_value = ActionResult(status="success", state_changed=True)
        mock.wait_until_actionable.return_value = True
        mock.get_state.side_effect = states
        return mock

    def test_no_setup_actions_still_captures_before_state(self) -> None:
        """Common GAWAIN case shape: no .setup(), block assertion off bare .execute()."""
        mock_adapter = self._make_adapter(states=[
            GameState(screen=GameScreen.COMBAT, combat={"player": {"block": 0}}),  # pre_action
            GameState(screen=GameScreen.COMBAT, combat={"player": {"block": 5}}),  # post_action
            GameState(screen=GameScreen.COMBAT, combat={"player": {"block": 5}}),  # assertion read
        ])
        orch = TestOrchestrator(adapter=mock_adapter)
        loop = asyncio.new_event_loop()
        try:
            result = (
                define("TC-DEFEND", orch, loop)
                .execute(play_card("gawain:defend"))
                .assert_that(player_block_increased_by(5))
            )
        finally:
            loop.close()
        assert result.passed, f"failures={result.failures}"
        # Zero extra get_state calls beyond the natural pre/post (execute_action)
        # + one assertion read — proves the snapshot riding the existing pre-read works.
        assert mock_adapter.get_state.call_count == 3

    def test_setup_actions_baseline_excludes_setup_side_effects(self) -> None:
        """set_hp in .setup() must not corrupt the heal-delta baseline for .execute()."""
        mock_adapter = self._make_adapter(states=[
            GameState(screen=GameScreen.COMBAT, hp=50),   # pre_action (set_hp)
            GameState(screen=GameScreen.COMBAT, hp=50),    # post_action (set_hp applied)
            GameState(screen=GameScreen.COMBAT, hp=50),    # pre_action (play_card) == before snapshot
            GameState(screen=GameScreen.COMBAT, hp=51),    # post_action (play_card)
            GameState(screen=GameScreen.COMBAT, hp=51),    # assertion read
        ])
        mock_adapter.get_available_actions.return_value = ["set_hp", "play_card"]
        orch = TestOrchestrator(adapter=mock_adapter)
        loop = asyncio.new_event_loop()
        try:
            result = (
                define("TC-MEDIC", orch, loop)
                .setup(set_hp(50))
                .execute(play_card("gawain:cecil_mage"))
                .assert_that(player_hp_changed_by(1))
            )
        finally:
            loop.close()
        assert result.passed, f"failures={result.failures}"
