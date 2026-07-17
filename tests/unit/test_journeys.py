"""通用新局、开局事件和地图稳定性测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sts2_autotest.adapters.base import ActionResult
from sts2_autotest.core.journeys import GenericJourneys


@dataclass
class _State:
    screen: str
    available_actions: list[str]
    event: dict | None = None
    map: dict | None = None
    in_combat: bool = False
    combat: dict | None = None

    def model_dump(self) -> dict:
        return {
            "screen": self.screen,
            "available_actions": list(self.available_actions),
            "event": self.event,
            "map": self.map,
            "in_combat": self.in_combat,
            "combat": self.combat,
        }


class _Adapter:
    def __init__(self) -> None:
        self.state = _State("MAIN_MENU", ["start_new_run"])

    async def get_state(self) -> _State:
        return self.state

    async def get_available_actions(self) -> list[str]:
        return list(self.state.available_actions)

    async def act(self, action: str, params: dict | None = None) -> ActionResult:
        if action == "start_new_run":
            self.state = _State("CHARACTER_SELECT", ["select_character"])
        elif action == "select_character":
            self.state = _State("CHARACTER_SELECT", ["embark"])
        elif action == "embark":
            self.state = _State(
                "EVENT",
                ["choose_event_option"],
                event={"options": [{"index": 0, "is_locked": False}]},
            )
        elif action == "choose_event_option":
            self.state = _State(
                "MAP",
                [],
                map={"is_traveling": False, "available_nodes": [{"index": 0}]},
            )
        return ActionResult(status="success", state_changed=True)


def test_start_new_run_handles_character_and_opening_event() -> None:
    adapter = _Adapter()
    result = asyncio.run(GenericJourneys(adapter).start_new_run("IRONCLAD"))
    assert result["screen"] == "MAP"
    assert result["map"]["is_traveling"] is False


def test_resume_run_keeps_an_already_playable_run() -> None:
    adapter = _Adapter()
    adapter.state = _State("COMBAT", ["end_turn"])

    result = asyncio.run(GenericJourneys(adapter).resume_run())

    assert result["screen"] == "COMBAT"


def test_reset_from_combat_uses_unadvertised_abandon_recovery_action() -> None:
    adapter = _Adapter()
    adapter.state = _State("COMBAT", ["end_turn", "play_card"])

    async def act(action: str, params: dict | None = None) -> ActionResult:
        if action == "abandon_run":
            adapter.state = _State("MAIN_MENU", ["start_new_run"])
        return ActionResult(status="success", state_changed=True)

    adapter.act = act  # type: ignore[method-assign]

    result = asyncio.run(GenericJourneys(adapter).reset_to_main_menu())

    assert result["screen"] == "MAIN_MENU"


def test_reset_from_game_over_uses_agent_return_to_main_menu_action() -> None:
    adapter = _Adapter()
    adapter.state = _State("GAME_OVER", ["return_to_main_menu"])

    async def act(action: str, params: dict | None = None) -> ActionResult:
        if action == "return_to_main_menu":
            adapter.state = _State("MAIN_MENU", ["start_new_run"])
        return ActionResult(status="success", state_changed=True)

    adapter.act = act  # type: ignore[method-assign]

    result = asyncio.run(GenericJourneys(adapter).reset_to_main_menu())

    assert result["screen"] == "MAIN_MENU"


def test_reset_from_card_reward_collects_and_returns_to_main_menu() -> None:
    adapter = _Adapter()
    adapter.state = _State("CARD_REWARD", ["collect_rewards_and_proceed"])

    async def act(action: str, params: dict | None = None) -> ActionResult:
        if action == "collect_rewards_and_proceed":
            adapter.state = _State("MAP", ["abandon_run"])
        elif action == "abandon_run":
            adapter.state = _State("MAIN_MENU", ["start_new_run"])
        return ActionResult(status="success", state_changed=True)

    adapter.act = act  # type: ignore[method-assign]

    result = asyncio.run(GenericJourneys(adapter).reset_to_main_menu())

    assert result["screen"] == "MAIN_MENU"


def test_reset_from_event_resolves_first_unlocked_option_before_abandoning() -> None:
    adapter = _Adapter()
    adapter.state = _State(
        "EVENT",
        ["choose_event_option"],
        event={"options": [{"index": 0, "is_locked": False}]},
    )

    async def act(action: str, params: dict | None = None) -> ActionResult:
        if action in {"choose_event", "choose_event_option"}:
            adapter.state = _State("MAP", ["abandon_run"])
        elif action == "abandon_run":
            adapter.state = _State("MAIN_MENU", ["start_new_run"])
        return ActionResult(status="success", state_changed=True)

    adapter.act = act  # type: ignore[method-assign]

    result = asyncio.run(GenericJourneys(adapter).reset_to_main_menu())

    assert result["screen"] == "MAIN_MENU"


def test_reset_from_map_uses_debug_abandon_fallback_when_no_normal_action() -> None:
    adapter = _Adapter()
    adapter.state = _State("MAP", ["choose_map_node"])
    adapter.capabilities = type("Capabilities", (), {"supports_debug_actions": True})()

    async def act(action: str, params: dict | None = None) -> ActionResult:
        if action == "abandon_run":
            adapter.state = _State("GAME_OVER", ["return_to_main_menu"])
        elif action == "return_to_main_menu":
            adapter.state = _State("MAIN_MENU", ["start_new_run"])
        return ActionResult(status="success", state_changed=True)

    adapter.act = act  # type: ignore[method-assign]

    result = asyncio.run(GenericJourneys(adapter).reset_to_main_menu())

    assert result["screen"] == "MAIN_MENU"


def test_reset_from_map_tries_unadvertised_abandon_before_debug_capability() -> None:
    adapter = _Adapter()
    adapter.state = _State("MAP", ["choose_map_node"])

    async def act(action: str, params: dict | None = None) -> ActionResult:
        if action == "abandon_run":
            adapter.state = _State("MAIN_MENU", ["start_new_run"])
        return ActionResult(status="success", state_changed=True)

    adapter.act = act  # type: ignore[method-assign]

    result = asyncio.run(GenericJourneys(adapter).reset_to_main_menu())

    assert result["screen"] == "MAIN_MENU"


class _TrajectoryAdapter:
    """走完 主菜单→角色选择→开局事件→地图→首战 的适配器，用于验证轨迹。"""

    def __init__(self) -> None:
        self.state = _State("MAIN_MENU", ["start_new_run"])

    async def get_state(self) -> _State:
        return self.state

    async def get_available_actions(self) -> list[str]:
        return list(self.state.available_actions)

    async def act(self, action: str, params: dict | None = None) -> ActionResult:
        if action == "start_new_run":
            self.state = _State("CHARACTER_SELECT", ["select_character"])
        elif action == "select_character":
            self.state = _State(
                "EVENT",
                ["choose_event_option"],
                event={"options": [{"index": 0, "is_locked": False}]},
            )
        elif action == "embark":
            self.state = _State(
                "MAP",
                ["choose_map_node"],
                map={"is_traveling": False, "available_nodes": [{"index": 0, "node_type": "Monster"}]},
            )
        elif action == "choose_event_option":
            self.state = _State(
                "MAP",
                ["choose_map_node"],
                map={"is_traveling": False, "available_nodes": [{"index": 0, "node_type": "Monster"}]},
            )
        elif action == "choose_map_node":
            self.state = _State(
                "COMBAT",
                ["end_turn"],
                in_combat=True,
                combat={"enemies": [{"index": 0, "is_alive": True}]},
            )
        return ActionResult(status="success", state_changed=True)


def test_enter_first_battle_records_full_trajectory() -> None:
    """状态轨迹应覆盖 主菜单→角色选择→开局事件→地图→首战，相邻不合并。"""
    adapter = _TrajectoryAdapter()
    run = GenericJourneys(adapter, timeout=10.0)
    result = asyncio.run(run.enter_first_battle(character_id="IRONCLAD"))

    assert result["screen"] == "COMBAT"
    assert run.trajectory == [
        "MAIN_MENU",
        "CHARACTER_SELECT",
        "EVENT",
        "MAP",
        "COMBAT",
    ]


class _StuckAdapter:
    """从主菜单走到开局事件页后始终不推进，触发导航超时。"""

    def __init__(self) -> None:
        self.state = _State("MAIN_MENU", ["start_new_run"])

    async def get_state(self) -> _State:
        return self.state

    async def get_available_actions(self) -> list[str]:
        return list(self.state.available_actions)

    async def act(self, action: str, params: dict | None = None) -> ActionResult:
        if action == "start_new_run":
            self.state = _State("CHARACTER_SELECT", ["select_character"])
        elif action == "select_character":
            self.state = _State(
                "EVENT",
                ["choose_event_option"],
                event={"options": [{"index": 0, "is_locked": False}]},
            )
        # choose_event_option 返回成功但不前进 → 开局事件页卡死
        return ActionResult(status="success", state_changed=False)


def test_enter_first_battle_translates_blocked_to_failure_with_last_state() -> None:
    """卡在开局事件页时，应给出带卡屏页面/最后操作/状态快照的失败。"""
    adapter = _StuckAdapter()
    run = GenericJourneys(adapter, timeout=0.3)

    try:
        asyncio.run(run.enter_first_battle(character_id="IRONCLAD"))
        raise AssertionError("expected JourneyFailure")
    except Exception as exc:  # noqa: BLE001
        from sts2_autotest.core.journeys import JourneyFailure

        assert isinstance(exc, JourneyFailure)
        assert exc.last_action == "choose_event_option"
        assert isinstance(exc.last_state, dict)
        assert exc.last_state.get("screen") == "EVENT"
        assert "EVENT" in run.trajectory
