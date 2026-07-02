from sts2_autotest.core.navigation import choose_progress_action


def test_choose_progress_action_discards_potion_on_post_combat_map() -> None:
    state = {
        "screen": "MAP",
        "available_actions": ["discard_potion"],
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
