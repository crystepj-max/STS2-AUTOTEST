"""Tests for dsl/fluent.py and dsl/assertions.py: Fluent API."""

import asyncio
import itertools
from typing import Any
from unittest.mock import MagicMock

import pytest

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.action_model import TestResult
from sts2_autotest.core.orchestrator import TestOrchestrator
from sts2_autotest.dsl import FluentBuilder, define
from sts2_autotest.dsl.assertions import (
    end_turn,
    enemy_hp_decreased_by,
    enter_combat,
    game_reached_state,
    give_card,
    play_card,
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


class TestAssertionFunctions:
    """Game semantic assertion functions."""

    def test_game_reached_state_match(self) -> None:
        fn = game_reached_state(GameScreen.MAP)
        ok, msg = fn(GameState(screen=GameScreen.MAP))
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

    def test_player_hp_changed_by_missing_previous(self) -> None:
        fn = player_hp_changed_by(10)
        state = GameState(screen=GameScreen.COMBAT, hp=100)
        ok, msg = fn(state)
        assert ok is False
        assert "previous_hp" in msg


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
