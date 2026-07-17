from sts2_autotest.core.navigation import choose_progress_action
from sts2_autotest.core.navigation import progress_until


def test_choose_progress_action_discards_potion_on_post_combat_map() -> None:
    state = {
        "screen": "MAP",
        "available_actions": ["discard_potion"],
        "run": {
            "potions": [{"index": 0, "occupied": True, "can_discard": True}],
        },
        "map": {"local_vote": {"row": 0, "col": 3}, "available_nodes": []},
    }

    assert choose_progress_action(state) == ("discard_potion", {"option_index": 0})


def test_choose_progress_action_returns_none_for_vote_blocked_map() -> None:
    state = {
        "screen": "MAP",
        "available_actions": [],
        "map": {"local_vote": {"row": 0, "col": 3}, "available_nodes": []},
    }

    assert choose_progress_action(state) is None


def test_progress_until_requires_stable_map_arrival() -> None:
    """screen=MAP 但仍在旅行时不能被判定为已到达。"""
    import asyncio

    states = iter([
        {"screen": "MAP", "available_actions": [], "map": {"is_traveling": True}},
        {"screen": "MAP", "available_actions": [], "map": {"is_traveling": False}},
    ])

    async def get_state() -> dict:
        return next(states)

    async def act(action: str, params: dict) -> object:
        raise AssertionError("unstable map should not receive an action")

    result = asyncio.run(
        progress_until(
            get_state,
            act,
            "MAP",
            timeout=1.0,
            delay=0.0,
            arrival_predicate=lambda state: not bool(
                (state.get("map") or {}).get("is_traveling")
            ),
        )
    )
    assert result["map"]["is_traveling"] is False


def test_choose_progress_action_waits_for_travel_transition_before_picking_node() -> None:
    state = {
        "screen": "MAP",
        "available_actions": ["choose_map_node", "choose_map_node_by_type"],
        "map": {
            "is_traveling": True,
            "local_vote": {"row": 3, "col": 1},
            "available_nodes": [{"index": 0, "node_type": "Monster"}],
        },
    }

    assert choose_progress_action(state, target_screen="COMBAT") is None


def test_choose_progress_action_proceeds_from_chest_to_keep_advancing() -> None:
    state = {
        "screen": "CHEST",
        "available_actions": ["proceed"],
    }

    assert choose_progress_action(state, target_screen="COMBAT") == ("proceed", {})


def test_choose_progress_action_opens_chest_before_proceeding() -> None:
    state = {
        "screen": "CHEST",
        "available_actions": ["open_chest"],
        "chest": {"is_opened": False, "has_relic_been_claimed": False},
    }

    assert choose_progress_action(state, target_screen="COMBAT") == ("open_chest", {})


def test_choose_progress_action_prefers_event_option_over_synthetic_confirm_modal() -> None:
    state = {
        "screen": "EVENT",
        "available_actions": ["choose_event_option", "confirm_modal"],
        "event": {
            "options": [
                {"index": 0, "text_key": "OPTION_A", "is_locked": False},
                {"index": 1, "text_key": "OPTION_B", "is_locked": False},
            ]
        },
    }

    assert choose_progress_action(state) == (
        "choose_event_option",
        {"option_index": 0},
    )


def test_choose_progress_action_uses_cli_choose_event_when_present() -> None:
    """cli 适配器的 choose_event(index) 必须被识别（开局 Neow 祝福页）。"""
    state = {
        "screen": "EVENT",
        "available_actions": ["choose_event", "advance_dialogue"],
        "event": {
            "options": [
                {"index": 0, "text_key": "OPTION_A", "is_locked": False},
                {"index": 1, "text_key": "OPTION_B", "is_locked": False},
            ]
        },
    }

    assert choose_progress_action(state) == ("choose_event", {"index": 0})


def test_choose_progress_action_event_skips_locked_options() -> None:
    """事件选项含锁定项时，应跳过并选第一个可用项。"""
    state = {
        "screen": "EVENT",
        "available_actions": ["choose_event_option"],
        "event": {
            "options": [
                {"index": 0, "text_key": "LOCKED", "is_locked": True},
                {"index": 1, "text_key": "OPTION_B", "is_locked": False},
            ]
        },
    }

    assert choose_progress_action(state) == (
        "choose_event_option",
        {"option_index": 1},
    )


def test_choose_progress_action_event_without_options_advances_dialogue() -> None:
    """事件页没有可选选项（纯对话）时，推进对话而非空转。"""
    state = {
        "screen": "EVENT",
        "available_actions": ["advance_dialogue"],
        "event": {"options": []},
    }

    assert choose_progress_action(state) == ("advance_dialogue", {})


def test_choose_progress_action_handles_bundle_selection() -> None:
    state = {
        "screen": "BUNDLE_SELECTION",
        "available_actions": ["choose_bundle"],
        "bundles": [{"index": 0}, {"index": 1}],
    }

    assert choose_progress_action(state) == (
        "choose_bundle",
        {"option_index": 0},
    )


def test_choose_progress_action_skips_disabled_rest_option() -> None:
    state = {
        "screen": "REST",
        "available_actions": ["choose_rest_option"],
        "run": {"current_hp": 80, "max_hp": 80},
        "rest": {
            "options": [
                {"index": 0, "option_id": "HEAL", "is_enabled": True},
                {"index": 1, "option_id": "SMITH", "is_enabled": False},
            ]
        },
    }

    assert choose_progress_action(state) == (
        "choose_rest_option",
        {"option_index": 0},
    )


def test_choose_progress_action_does_not_pick_advance_dialogue_on_map() -> None:
    """MAP 屏幕即使暴露 advance_dialogue，也不能误选它而迷失在地图。"""
    state = {
        "screen": "MAP",
        "available_actions": [
            "advance_dialogue",
            "choose_map_node",
            "choose_map_node_by_type",
        ],
        "map": {
            "available_nodes": [
                {"index": 0, "node_type": "Monster", "state": "Travelable"},
            ]
        },
    }

    result = choose_progress_action(state, target_screen="COMBAT")
    assert result is not None
    assert result[0] in ("choose_map_node_by_type", "choose_map_node")
    assert result[0] != "advance_dialogue"


def test_choose_progress_action_prefers_targeted_combat_node_type() -> None:
    state = {
        "screen": "MAP",
        "available_actions": ["choose_map_node", "choose_map_node_by_type"],
        "map": {
            "available_nodes": [
                {"index": 0, "node_type": "RestSite"},
                {"index": 1, "node_type": "Monster"},
            ]
        },
    }

    assert choose_progress_action(state, target_screen="COMBAT") == (
        "choose_map_node_by_type",
        {"node_type": "Monster"},
    )


def test_choose_progress_action_falls_back_to_first_unknown_node_for_combat() -> None:
    state = {
        "screen": "MAP",
        "available_actions": ["choose_map_node", "choose_map_node_by_type"],
        "map": {
            "available_nodes": [
                {"index": 0, "node_type": "Unknown", "state": "Travelable"},
            ]
        },
    }

    assert choose_progress_action(state, target_screen="COMBAT") == (
        "choose_map_node",
        {"option_index": 0},
    )


def test_choose_progress_action_prefers_map_progress_over_synthetic_confirm_modal() -> None:
    state = {
        "screen": "MAP",
        "available_actions": ["choose_map_node", "confirm_modal"],
        "map": {"available_nodes": [{"row": 0, "col": 0}]},
    }

    assert choose_progress_action(state) == (
        "choose_map_node",
        {"option_index": 0},
    )
