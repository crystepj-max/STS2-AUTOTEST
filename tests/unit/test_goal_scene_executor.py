"""统一目标场景执行器的纯逻辑检查。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sts2_autotest.adapters.base import ActionResult
from sts2_autotest.core.journeys import GenericJourneys, JourneyFailure
from sts2_autotest.core.navigation import (
    NavigationBlocked,
    choose_progress_action,
    progress_until,
)


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


def test_combat_skips_runtime_unplayable_card_after_energy_changes() -> None:
    state = {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn"],
        "combat": {
            "hand": [
                {"id": "spent", "can_play": True, "playable": False},
                {"id": "legal", "can_play": True, "playable": True},
            ],
            "enemies": [{"index": 0, "is_alive": True}],
        },
    }

    assert choose_progress_action(state, combat_mode="basic") == (
        "play_card",
        {"card_id": "legal"},
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
            act_id = "1" if (self.state.run or {}).get("act_id") == "1" else "0"
            self.state = _State(
                "MAP",
                ["choose_map_node"],
                run={"act_id": act_id},
                map={"is_traveling": False, "available_nodes": [{"index": 0, "col": 0, "row": 1}]},
            )
        elif action == "choose_map_node":
            self.state = _State(
                "EVENT",
                ["choose_event_option"],
                run={"act_id": "1"},
                event={
                    "event_id": "ENTRY",
                    "is_finished": False,
                    "options": [{"index": 0, "is_locked": False}],
                },
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


def test_next_act_rejects_chapter_change_without_entry_event() -> None:
    class _PseudoCombatAdapter(_NextActAdapter):
        def __init__(self) -> None:
            super().__init__()
            self._pseudo_seen = False

        async def act(self, action: str, params: dict | None = None) -> ActionResult:
            if action == "choose_map_node":
                self.state = _State("COMBAT", [], run={"act_id": "1"})
                return ActionResult(status="success", state_changed=True)
            return await super().act(action, params)

        async def get_state(self) -> _State:
            if self.state.screen == "COMBAT":
                if not self._pseudo_seen:
                    self._pseudo_seen = True
                else:
                    self.state = _State("MAP", [], run={"act_id": "1"}, map={"is_traveling": False})
            return self.state

    try:
        asyncio.run(
            GenericJourneys(_PseudoCombatAdapter(), timeout=1.0).execute_target(
                character_id="IRONCLAD",
                target_scene="NEXT_ACT",
            )
        )
    except JourneyFailure as exc:
        assert exc.reason_code == "TARGET_UNREACHABLE"
        assert "EVENT" in str(exc)
    else:
        raise AssertionError("chapter change without entry event must not pass")


def test_next_act_processes_a_multi_step_entry_event_before_map() -> None:
    class _MultiStepAdapter(_NextActAdapter):
        def __init__(self) -> None:
            super().__init__()
            self._event_step = 0
            self._in_next_act_event = False

        async def act(self, action: str, params: dict | None = None) -> ActionResult:
            if action == "choose_map_node":
                self._in_next_act_event = True
                self.state = _State(
                    "EVENT",
                    ["choose_event_option"],
                    run={"act_id": "1"},
                    event={
                        "event_id": "ENTRY",
                        "is_finished": False,
                        "options": [{"index": 0, "is_locked": False}],
                    },
                )
            elif action == "choose_event_option" and self._in_next_act_event:
                self._event_step += 1
                if self._event_step == 1:
                    self.state = _State(
                        "EVENT",
                        ["choose_event_option"],
                        run={"act_id": "1"},
                        event={
                            "event_id": "ENTRY",
                            "is_finished": False,
                            "options": [{"index": 1, "is_locked": False}],
                        },
                    )
                else:
                    self.state = _State(
                        "MAP",
                        ["choose_map_node"],
                        run={"act_id": "1"},
                        map={"is_traveling": False, "available_nodes": [{"index": 0}]},
                    )
                return ActionResult(status="success", state_changed=True)
            return await super().act(action, params)

    runner = GenericJourneys(_MultiStepAdapter(), timeout=3.0)
    result = asyncio.run(
        runner.execute_target(character_id="IRONCLAD", target_scene="NEXT_ACT")
    )
    assert result["screen"] == "MAP"
    assert [op["action"] for op in runner.evidence["operations"]].count("choose_event_option") >= 2


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


# ── 死亡测试（combat_mode="death"）与卡牌专测（journey="card_test"）──


def test_death_mode_only_ends_turn_even_when_other_actions_available() -> None:
    state = {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn", "win_combat"],
        "in_combat": True,
        "combat": {
            "hand": [{"id": "legal", "can_play": True}],
            "enemies": [{"index": 0, "is_alive": True}],
        },
    }

    assert choose_progress_action(state, combat_mode="death") == ("end_turn", {})


def test_death_mode_waits_when_end_turn_not_available() -> None:
    state = {
        "screen": "COMBAT",
        "available_actions": ["play_card"],
        "in_combat": True,
        "combat": {
            "hand": [{"id": "legal", "can_play": True}],
            "enemies": [{"index": 0, "is_alive": True}],
        },
    }

    assert choose_progress_action(state, combat_mode="death") is None


def test_game_over_is_success_when_death_mode_targets_it() -> None:
    states = iter(
        [
            {
                "screen": "COMBAT",
                "available_actions": ["end_turn"],
                "in_combat": True,
                "combat": {"enemies": [{"index": 0, "is_alive": True}]},
            },
            {
                "screen": "GAME_OVER",
                "available_actions": ["return_to_main_menu"],
                "game_over": {"is_victory": False},
            },
        ]
    )
    current: dict = {}

    async def get_state() -> dict:
        nonlocal current
        try:
            current = next(states)
        except StopIteration:
            pass
        return current

    played: list[str] = []

    async def act(action: str, params: dict) -> ActionResult:
        played.append(action)
        return ActionResult(status="success", state_changed=True)

    result = asyncio.run(
        progress_until(get_state, act, "GAME_OVER", timeout=2.0, delay=0.01, combat_mode="death")
    )

    assert result["screen"] == "GAME_OVER"
    assert played == ["end_turn"]


@dataclass
class _FullState:
    screen: str
    available_actions: list[str]
    run: dict | None = None
    map: dict | None = None
    event: dict | None = None
    in_combat: bool = False
    combat: dict | None = None

    def model_dump(self) -> dict:
        return {
            "screen": self.screen,
            "available_actions": list(self.available_actions),
            "run": self.run,
            "map": self.map,
            "event": self.event,
            "in_combat": self.in_combat,
            "combat": self.combat,
        }


class _DeathAdapter:
    """主菜单→角色选择→开局事件→地图→首战；每回合状态都变化，两回合后死亡。"""

    def __init__(self) -> None:
        self.state = _FullState("MAIN_MENU", ["start_new_run"])
        self._turns = 0
        self.actions: list[str] = []

    async def health_check(self):
        return type("Health", (), {"healthy": True})()

    async def cleanup(self) -> None:
        return None

    async def get_state(self) -> _FullState:
        return self.state

    async def get_available_actions(self) -> list[str]:
        return list(self.state.available_actions)

    async def act(self, action: str, params: dict | None = None) -> ActionResult:
        self.actions.append(action)
        if action == "start_new_run":
            self.state = _FullState("CHARACTER_SELECT", ["select_character"])
        elif action == "select_character":
            self.state = _FullState("CHARACTER_SELECT", ["embark"])
        elif action == "embark":
            self.state = _FullState(
                "EVENT",
                ["choose_event_option"],
                event={"options": [{"index": 0, "is_locked": False}]},
            )
        elif action == "choose_event_option":
            self.state = _FullState(
                "MAP",
                ["choose_map_node"],
                map={"is_traveling": False, "available_nodes": [{"index": 0, "col": 0, "row": 1, "node_type": "MONSTER"}]},
            )
        elif action in {"choose_map_node", "choose_map_node_by_type"}:
            self.state = _FullState(
                "COMBAT",
                ["play_card", "end_turn", "win_combat"],
                in_combat=True,
                combat={
                    "hand": [{"id": "defend", "can_play": True}],
                    "enemies": [{"index": 0, "is_alive": True}],
                    "turn": 1,
                },
            )
        elif action == "end_turn":
            self._turns += 1
            if self._turns >= 2:
                self.state = _FullState("GAME_OVER", ["return_to_main_menu"])
            else:
                combat = dict(self.state.combat or {})
                combat["turn"] = self._turns + 1
                self.state = _FullState(
                    "COMBAT",
                    ["play_card", "end_turn", "win_combat"],
                    in_combat=True,
                    combat=combat,
                )
        return ActionResult(status="success", state_changed=True)


def test_death_test_reaches_game_over_using_only_end_turn() -> None:
    adapter = _DeathAdapter()
    runner = GenericJourneys(adapter, timeout=5.0)
    result = asyncio.run(
        runner.execute_target(
            character_id="IRONCLAD",
            target_scene="COMBAT",
            combat_mode="death",
        )
    )

    assert result["screen"] == "GAME_OVER"
    combat_phase_actions = adapter.actions[adapter.actions.index("choose_map_node") + 1:]
    assert combat_phase_actions == ["end_turn", "end_turn"]
    assert "play_card" not in adapter.actions
    assert "win_combat" not in adapter.actions


class _CardTestAdapter(_DeathAdapter):
    """give_card 后把目标牌加入手牌；play_card 后该牌离手。

    give_card_effect：
    - "target"：正常把目标牌加入手牌；
    - "none"：控制台返回成功但状态完全不变（假成功）；
    - "wrong"：状态有变化但入手的是别的牌（部分假成功）。
    """

    def __init__(self, *, give_card_effect: str = "target") -> None:
        super().__init__()
        self._give_card_effect = give_card_effect
        self.give_card_params: list[dict] = []
        self.play_card_params: list[dict] = []

    @property
    def capabilities(self):
        return type("Caps", (), {"supports_debug_actions": True})()

    async def get_available_actions(self) -> list[str]:
        # 与真实调试适配器一致：调试动作始终附加在公开动作之后。
        actions = list(self.state.available_actions)
        for debug_action in ("give_card", "win_combat"):
            if debug_action not in actions:
                actions.append(debug_action)
        return actions

    async def act(self, action: str, params: dict | None = None) -> ActionResult:
        if action == "give_card":
            self.give_card_params.append(dict(params or {}))
            if self._give_card_effect != "none":
                combat = dict(self.state.combat or {})
                hand = list(combat.get("hand") or [])
                added = (
                    {"id": "STRIKE", "can_play": True, "requires_target": True}
                    if self._give_card_effect == "target"
                    else {"id": "SUNDER", "can_play": True}
                )
                hand.append(added)
                combat["hand"] = hand
                self.state.combat = combat
            return ActionResult(status="success", state_changed=True)
        if action == "play_card":
            self.play_card_params.append(dict(params or {}))
            combat = dict(self.state.combat or {})
            combat["hand"] = [
                card for card in list(combat.get("hand") or []) if card.get("id") != "STRIKE"
            ]
            self.state.combat = combat
            return ActionResult(status="success", state_changed=True)
        return await super().act(action, params)


def test_card_test_gives_card_then_plays_it() -> None:
    adapter = _CardTestAdapter()
    result = asyncio.run(
        GenericJourneys(adapter, timeout=5.0).card_test("IRONCLAD", "STRIKE")
    )

    assert result["screen"] == "COMBAT"
    assert adapter.give_card_params == [{"card_id": "STRIKE"}]
    assert adapter.play_card_params == [{"card_id": "STRIKE", "target": 0}]


def test_card_test_fails_when_give_card_is_a_noop() -> None:
    adapter = _CardTestAdapter(give_card_effect="none")

    try:
        asyncio.run(GenericJourneys(adapter, timeout=5.0).card_test("IRONCLAD", "STRIKE"))
    except JourneyFailure as exc:
        assert "no observable state change" in str(exc)
    else:
        raise AssertionError("give_card 无效果的假成功必须判失败")


def test_card_test_fails_when_card_never_enters_hand() -> None:
    adapter = _CardTestAdapter(give_card_effect="wrong")

    try:
        asyncio.run(GenericJourneys(adapter, timeout=5.0).card_test("IRONCLAD", "STRIKE"))
    except JourneyFailure as exc:
        assert exc.reason_code == "NO_PROGRESS"
        assert "假成功" in str(exc)
    else:
        raise AssertionError("give_card 假成功必须判失败，不得当作已入手")


def test_card_test_requires_debug_actions() -> None:
    adapter = _DeathAdapter()  # 无 capabilities 属性 → 调试能力不可用

    try:
        asyncio.run(GenericJourneys(adapter, timeout=1.0).card_test("IRONCLAD", "STRIKE"))
    except JourneyFailure as exc:
        assert exc.reason_code == "DEBUG_ACTIONS_UNAVAILABLE"
    else:
        raise AssertionError("未开启调试能力时必须明确失败")


def test_card_test_rejects_blank_card_id() -> None:
    adapter = _CardTestAdapter()

    try:
        asyncio.run(GenericJourneys(adapter, timeout=1.0).card_test("IRONCLAD", "  "))
    except JourneyFailure as exc:
        assert "card_id" in str(exc)
    else:
        raise AssertionError("空 card_id 必须在进入游戏前失败")


def test_async_observation_callback_is_awaited() -> None:
    adapter = _DeathAdapter()
    observed: list[str] = []

    async def on_state(payload: dict) -> None:
        await asyncio.sleep(0)
        observed.append(str(payload.get("screen")))

    runner = GenericJourneys(adapter, timeout=1.0, observation_callback=on_state)
    asyncio.run(runner.snapshot())

    assert observed == ["MAIN_MENU"]
