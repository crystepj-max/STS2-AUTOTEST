"""统一目标场景执行器的纯逻辑检查。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sts2_autotest.adapters.base import ActionResult
from sts2_autotest.core.journeys import GenericJourneys
from sts2_autotest.core.navigation import NavigationBlocked, choose_progress_action, progress_until


def test_leftmost_route_uses_horizontal_coordinate_not_list_order() -> None:
    state = {
        "screen": "MAP",
        "available_actions": ["choose_map_node"],
        "map": {
            "available_nodes": [
                {"index": 0, "col": 4, "row": 1, "type": "MONSTER"},
                {"index": 7, "col": 1, "row": 1, "type": "EVENT"},
            ]
        },
    }

    assert choose_progress_action(state) == (
        "choose_map_node",
        {"option_index": 7},
    )


def test_traversal_combat_uses_first_legal_card_and_first_live_enemy() -> None:
    state = {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn"],
        "combat": {
            "hand": [
                {"id": "unplayable", "can_play": False},
                {"id": "legal", "can_play": True, "requires_target": True},
            ],
            "enemies": [
                {"combat_id": 0, "is_alive": False},
                {"combat_id": 2, "is_alive": True},
            ],
        },
    }

    assert choose_progress_action(state, combat_mode="traversal") == (
        "play_card",
        {"card_id": "legal", "target": 2},
    )


def test_traversal_combat_prefers_platform_fast_end_when_exposed() -> None:
    state = {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn", "win_combat"],
        "combat": {
            "hand": [{"id": "opaque", "can_play": True}],
            "enemies": [{"index": 0, "is_alive": True}],
        },
    }

    assert choose_progress_action(state, combat_mode="traversal") == (
        "win_combat",
        {},
    )


def test_basic_combat_does_not_use_platform_fast_end() -> None:
    state = {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn", "win_combat"],
        "combat": {
            "hand": [{"id": "legal", "can_play": True}],
            "enemies": [{"index": 0, "is_alive": True}],
        },
    }

    assert choose_progress_action(state, combat_mode="basic") == (
        "play_card",
        {"card_id": "legal"},
    )


def test_rest_uses_current_option_index() -> None:
    state = {
        "screen": "REST",
        "available_actions": ["choose_rest_option"],
        "rest": {
            "options": [
                {"index": 0, "option_id": "HEAL", "is_enabled": True},
                {"index": 1, "option_id": "SMITH", "is_enabled": True},
            ]
        },
        "run": {"current_hp": 80, "max_hp": 80},
    }

    assert choose_progress_action(state) == (
        "choose_rest_option",
        {"option_index": 1},
    )


def test_map_discards_first_occupied_potion_slot() -> None:
    state = {
        "screen": "MAP",
        "available_actions": ["choose_map_node", "discard_potion"],
        "run": {
            "potions": [
                {"index": 0, "occupied": False, "can_discard": False},
                {"index": 1, "occupied": True, "can_discard": True},
            ]
        },
        "map": {
            "is_traveling": False,
            "available_nodes": [{"index": 0, "col": 0, "row": 1}],
        },
    }

    assert choose_progress_action(state) == (
        "discard_potion",
        {"option_index": 1},
    )


def test_card_reward_prefers_highest_rarity_then_rightmost() -> None:
    state = {
        "screen": "CARD_REWARD",
        "available_actions": ["reward_choose_card", "reward_skip_card"],
        "reward": {
            "card_options": [
                {"index": 2, "rarity": "RARE"},
                {"index": 5, "rarity": "RARE"},
                {"index": 8, "rarity": "UNCOMMON"},
            ]
        },
    }

    assert choose_progress_action(state) == (
        "reward_choose_card",
        {"option_index": 5},
    )


def test_nested_card_rewards_choose_card_id_and_reward_index() -> None:
    state = {
        "screen": "CARD_REWARD",
        "available_actions": ["reward_choose_card", "reward_skip_card"],
        "rewards": {
            "rewards": [
                {
                    "index": 1,
                    "type": "Card",
                    "card_choices": [
                        {"index": 0, "id": "COMMON_CARD", "rarity": "Common"},
                        {"index": 2, "id": "RARE_CARD", "rarity": "Rare"},
                    ],
                }
            ]
        },
    }

    assert choose_progress_action(state) == (
        "reward_choose_card",
        {"type": "card", "nth": 1, "card_id": "RARE_CARD"},
    )


def test_success_without_state_change_fails_after_short_observation() -> None:
    state = {
        "screen": "EVENT",
        "available_actions": ["choose_event_option"],
        "event": {"options": [{"index": 0, "is_locked": False}]},
    }

    async def get_state() -> dict:
        return state

    async def act(action: str, params: dict) -> ActionResult:
        return ActionResult(status="success", state_changed=False)

    async def recover() -> bool:
        return True

    try:
        asyncio.run(
            progress_until(
                get_state,
                act,
                "MAP",
                timeout=0.3,
                delay=0.01,
                recover=recover,
                no_progress_timeout=0.05,
            )
        )
    except Exception as exc:  # noqa: BLE001
        assert "没有可观察变化" in str(exc)
    else:
        raise AssertionError("expected fast no-progress failure")


def test_stable_map_without_next_nodes_reports_target_unreachable() -> None:
    state = {
        "screen": "MAP",
        "available_actions": [],
        "map": {"is_traveling": False, "available_nodes": []},
    }

    async def get_state() -> dict:
        return state

    async def act(action: str, params: dict) -> ActionResult:
        raise AssertionError(f"unexpected action: {action} {params}")

    try:
        asyncio.run(progress_until(get_state, act, "COMBAT", timeout=1.0, delay=0.01))
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "reason_code", None) == "TARGET_UNREACHABLE"
        assert "不可达" in str(exc)
    else:
        raise AssertionError("expected target-unreachable failure")


def test_game_over_fails_immediately_with_combat_evidence() -> None:
    state = {
        "screen": "GAME_OVER",
        "available_actions": ["return_to_main_menu"],
        "run": {"act_id": "0", "floor": 2},
        "game_over": {"is_victory": False, "can_return_to_main_menu": True},
    }

    async def get_state() -> dict:
        return state

    async def act(action: str, params: dict) -> ActionResult:
        raise AssertionError(f"unexpected action: {action} {params}")

    try:
        asyncio.run(progress_until(get_state, act, "NEXT_ACT", timeout=1.0, delay=0.01))
    except NavigationBlocked as exc:
        assert exc.reason_code == "COMBAT_FAILED"
        assert exc.last_state == state
        assert exc.last_action is None
    else:
        raise AssertionError("expected immediate combat-failed result")


def test_combat_missing_play_card_action_fails_with_structured_reason() -> None:
    state = {
        "screen": "COMBAT",
        "available_actions": ["end_turn"],
        "combat": {"player": {"energy": 3}, "hand": []},
        "agent_view": {"combat": {"draw": [{"line": "opaque card"}]}},
    }

    async def get_state() -> dict:
        return state

    async def act(action: str, params: dict) -> ActionResult:
        raise AssertionError(f"unexpected action: {action} {params}")

    try:
        asyncio.run(progress_until(get_state, act, "MAP", timeout=1.0, delay=0.01))
    except NavigationBlocked as exc:
        assert exc.reason_code == "ACTION_SURFACE_INCOMPLETE"
        assert exc.last_state == state
    else:
        raise AssertionError("expected missing-play-card failure")


@dataclass
class _State:
    screen: str
    available_actions: list[str]
    run: dict | None = None
    map: dict | None = None
    event: dict | None = None

    def model_dump(self) -> dict:
        return {
            "screen": self.screen,
            "available_actions": list(self.available_actions),
            "run": self.run,
            "map": self.map,
            "event": self.event,
        }


class _NextActAdapter:
    def __init__(self) -> None:
        self.state = _State("MAIN_MENU", ["start_new_run"])

    async def health_check(self):
        return type("Health", (), {"healthy": True})()

    async def cleanup(self) -> None:
        return None

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
                run={"act_id": "0"},
                event={"options": [{"index": 0, "is_locked": False}]},
            )
        elif action in {"choose_event", "choose_event_option"}:
            self.state = _State(
                "MAP",
                ["choose_map_node"],
                run={"act_id": "0"},
                map={"is_traveling": False, "available_nodes": [{"index": 0, "col": 0, "row": 1}]},
            )
        elif action == "choose_map_node":
            self.state = _State(
                "MAP",
                [],
                run={"act_id": "1"},
                map={"is_traveling": False, "current_node": {"col": 0, "row": 2}},
            )
        return ActionResult(status="success", state_changed=True)


def test_next_act_requires_chapter_change_and_stable_map() -> None:
    adapter = _NextActAdapter()
    result = asyncio.run(
        GenericJourneys(adapter, timeout=1.0).execute_target(
            character_id="IRONCLAD",
            target_scene="NEXT_ACT",
            route_policy="leftmost",
            combat_mode="traversal",
        )
    )

    assert result["screen"] == "MAP"


def test_preselected_character_is_not_selected_again() -> None:
    class _PreselectedAdapter(_NextActAdapter):
        async def act(self, action: str, params: dict | None = None) -> ActionResult:
            if action == "start_new_run":
                self.state = _State("CHARACTER_SELECT", ["embark"])
            elif action == "embark":
                self.state = _State("EVENT", ["choose_event_option"])
            return ActionResult(status="success", state_changed=True)

        async def get_state(self) -> _State:
            if self.state.screen == "CHARACTER_SELECT":
                self.state = _State(
                    "CHARACTER_SELECT",
                    ["embark"],
                    event=None,
                )
                payload = self.state.model_dump()
                payload["character_select"] = {"selected_character_id": "IRONCLAD"}
                return type("State", (), {"model_dump": lambda _: payload})()
            return self.state

    adapter = _PreselectedAdapter()
    result = asyncio.run(
        GenericJourneys(adapter, timeout=1.0).start_new_run("IRONCLAD", target_screen="EVENT")
    )
    assert result["screen"] == "EVENT"
