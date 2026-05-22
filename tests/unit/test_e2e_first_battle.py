"""Tests for the adaptive first-battle E2E helper logic.

中文说明：该说明保留英文术语，并补充中文语境。

中文说明：该说明保留英文术语，并补充中文语境。"""

from __future__ import annotations

from sts2_autotest.common.spec_models import TestSpec
from sts2_autotest.core.code_generator import CodeGenerator
from tests.e2e_first_battle import (
    _choose_map_coord,
    _choose_play_card_args,
    _is_early_first_battle_state,
    _is_bootstrap_complete_screen,
    _choose_bootstrap_action,
    _choose_event_progress_action,
    _choose_post_embark_progress_action,
    _choose_tri_card_action,
    _choose_unknown_progress_action,
    _choose_grid_card_action,
    _choose_reward_action,
    _is_post_embark_screen,
    _is_recoverable_bootstrap_screen,
)


class TestChooseBootstrapAction:
    def test_main_menu_prefers_abandon_run_when_save_exists(self) -> None:
        assert _choose_bootstrap_action(
            "MAIN_MENU",
            ["new_run", "continue_run", "abandon_run", "choose_game_mode"],
            {"menu": {"has_run_save": True}},
        ) == ("abandon_run", None)

    def test_main_menu_ignores_abandon_run_when_no_save_exists(self) -> None:
        assert _choose_bootstrap_action(
            "MAIN_MENU",
            ["new_run", "continue_run", "abandon_run", "choose_game_mode"],
            {"menu": {"has_run_save": False}},
        ) == ("new_run", None)

    def test_submenu_prefers_choose_game_mode(self) -> None:
        assert _choose_bootstrap_action(
            "MAIN_MENU",
            ["new_run", "continue_run", "abandon_run", "choose_game_mode"],
            {"singleplayer_submenu": {"standard_available": True}},
        ) == ("choose_game_mode", {"mode": "standard"})

    def test_game_over_returns_to_menu(self) -> None:
        assert _choose_bootstrap_action("GAME_OVER", ["return_to_menu"], {}) == (
            "return_to_menu",
            None,
        )

    def test_victory_returns_to_menu(self) -> None:
        assert _choose_bootstrap_action("VICTORY", ["return_to_menu"], {}) == (
            "return_to_menu",
            None,
        )

    def test_main_menu_prefers_choose_game_mode(self) -> None:
        assert _choose_bootstrap_action(
            "MAIN_MENU",
            ["new_run", "choose_game_mode"],
            {"menu": {"has_run_save": False}},
        ) == ("new_run", None)

    def test_main_menu_falls_back_to_new_run(self) -> None:
        assert _choose_bootstrap_action(
            "MAIN_MENU",
            ["new_run"],
            {"menu": {"has_run_save": False}},
        ) == ("new_run", None)

    def test_event_chooses_first_option(self) -> None:
        assert _choose_bootstrap_action("EVENT", ["choose_event"], {}) == (
            "choose_event",
            {"choice": 0},
        )

    def test_unknown_without_actions_returns_none(self) -> None:
        assert _choose_bootstrap_action("UNKNOWN", [], {}) is None

    def test_unknown_grid_select_chooses_a_card(self) -> None:
        assert _choose_bootstrap_action(
            "UNKNOWN",
            [],
            {
                "grid_card_select": {
                    "cards": [
                        {"card_id": "STRIKE_IRONCLAD"},
                        {"card_id": "DEFEND_IRONCLAD"},
                    ]
                }
            },
        ) == ("grid_select_card", {"card_id": "STRIKE_IRONCLAD"})

    def test_unknown_tri_select_chooses_a_card(self) -> None:
        assert _choose_bootstrap_action(
            "UNKNOWN",
            [],
            {
                "tri_select": {
                    "cards": [
                        {"card_id": "DEFEND_IRONCLAD"},
                        {"card_id": "ATTACK_COLORLESS"},
                    ]
                }
            },
        ) == ("tri_select_card", {"card_id": "ATTACK_COLORLESS"})


class TestChooseRewardAction:
    def test_prefers_skip_card(self) -> None:
        assert _choose_reward_action(["reward_skip_card", "reward_claim"]) == (
            "reward_skip_card",
            {"type": "card"},
        )

    def test_uses_relic_skip_when_available(self) -> None:
        assert _choose_reward_action(["relic_skip"]) == ("relic_skip", None)

    def test_returns_none_when_no_skip_exists(self) -> None:
        assert _choose_reward_action(["reward_claim"]) is None

    def test_unknown_rewards_payload_can_skip_card(self) -> None:
        assert _choose_reward_action(
            [],
            {
                "rewards": {
                    "rewards": [
                        {"type": "Gold"},
                        {"type": "Card"},
                    ]
                }
            },
        ) == ("reward_skip_card", {"type": "card"})


class TestChooseEventProgressAction:
    def test_prefers_choose_event(self) -> None:
        assert _choose_event_progress_action(
            "EVENT",
            ["choose_event", "advance_dialogue"],
        ) == ("choose_event", {"choice": 0})

    def test_falls_back_to_advance_dialogue(self) -> None:
        assert _choose_event_progress_action(
            "EVENT",
            ["advance_dialogue"],
        ) == ("advance_dialogue", None)

    def test_non_event_returns_none(self) -> None:
        assert _choose_event_progress_action("UNKNOWN", ["advance_dialogue"]) is None


class TestChoosePostEmbarkProgressAction:
    def test_character_select_retries_embark(self) -> None:
        assert _choose_post_embark_progress_action(
            "CHARACTER_SELECT",
            ["select_character", "embark"],
            {},
        ) == ("embark", None)

    def test_event_delegates_to_event_progress(self) -> None:
        assert _choose_post_embark_progress_action(
            "EVENT",
            ["advance_dialogue"],
            {},
        ) == ("advance_dialogue", None)

    def test_unknown_delegates_to_unknown_progress(self) -> None:
        assert _choose_post_embark_progress_action(
            "UNKNOWN",
            [],
            {"grid_card_select": {"cards": [{"card_id": "STRIKE_IRONCLAD"}]}},
        ) == ("grid_select_card", {"card_id": "STRIKE_IRONCLAD"})

    def test_event_never_returns_embark(self) -> None:
        assert _choose_post_embark_progress_action(
            "EVENT",
            ["choose_event", "advance_dialogue", "embark"],
            {},
        ) == ("choose_event", {"choice": 0})


class TestChooseGridCardAction:
    def test_prefers_strike_card(self) -> None:
        assert _choose_grid_card_action(
            {
                "cards": [
                    {"card_id": "DEFEND_IRONCLAD"},
                    {"card_id": "STRIKE_IRONCLAD"},
                ]
            }
        ) == {"card_id": "STRIKE_IRONCLAD"}

    def test_falls_back_to_first_card(self) -> None:
        assert _choose_grid_card_action(
            {
                "cards": [
                    {"card_id": "BASH"},
                    {"card_id": "DEFEND_IRONCLAD"},
                ]
            }
        ) == {"card_id": "BASH"}

    def test_returns_none_when_no_cards(self) -> None:
        assert _choose_grid_card_action({"cards": []}) is None


class TestChooseTriCardAction:
    def test_prefers_attack_card(self) -> None:
        assert _choose_tri_card_action(
            {
                "cards": [
                    {"card_id": "DEFEND_IRONCLAD"},
                    {"card_id": "ATTACK_COLORLESS"},
                ]
            }
        ) == {"card_id": "ATTACK_COLORLESS"}

    def test_falls_back_to_first_card(self) -> None:
        assert _choose_tri_card_action(
            {
                "cards": [
                    {"id": "SKILL_COLORLESS"},
                    {"id": "POWER_COLORLESS"},
                ]
            }
        ) == {"card_id": "SKILL_COLORLESS"}


class TestChooseUnknownProgressAction:
    def test_grid_select_progress(self) -> None:
        assert _choose_unknown_progress_action(
            {"grid_card_select": {"cards": [{"card_id": "STRIKE_IRONCLAD"}]}}
        ) == ("grid_select_card", {"card_id": "STRIKE_IRONCLAD"})

    def test_tri_select_progress(self) -> None:
        assert _choose_unknown_progress_action(
            {"tri_select": {"cards": [{"card_id": "ATTACK_COLORLESS"}]}}
        ) == ("tri_select_card", {"card_id": "ATTACK_COLORLESS"})

    def test_empty_tri_select_skips(self) -> None:
        assert _choose_unknown_progress_action(
            {"tri_select": {"cards": []}}
        ) == ("tri_select_skip", None)


class TestChooseMapCoord:
    def test_prefers_travelable_monster_node(self) -> None:
        assert _choose_map_coord(
            {
                "travelable_coords": [
                    {"col": 0, "row": 1},
                    {"col": 3, "row": 1},
                ],
                "nodes": [
                    {"col": 0, "row": 1, "type": "REST_SITE", "state": "TRAVELABLE"},
                    {"col": 3, "row": 1, "type": "MONSTER", "state": "TRAVELABLE"},
                ],
            }
        ) == {"col": 3, "row": 1}

    def test_falls_back_to_first_travelable_coord(self) -> None:
        assert _choose_map_coord(
            {
                "travelable_coords": [
                    {"col": 5, "row": 1},
                ],
                "nodes": [
                    {"col": 5, "row": 1, "type": "UNKNOWN", "state": "TRAVELABLE"},
                ],
            }
        ) == {"col": 5, "row": 1}

    def test_returns_none_when_no_travelable_coord(self) -> None:
        assert _choose_map_coord({"travelable_coords": [], "nodes": []}) is None


class TestChoosePlayCardArgs:
    def test_attack_card_adds_target(self) -> None:
        assert _choose_play_card_args(
            {"id": "STRIKE_IRONCLAD", "target_type": "AnyEnemy"},
            {
                "enemies": [
                    {"combat_id": 1, "is_alive": True},
                    {"combat_id": 2, "is_alive": True},
                ]
            },
        ) == {"card_id": "STRIKE_IRONCLAD", "target": 1}

    def test_self_target_card_has_no_enemy_target(self) -> None:
        assert _choose_play_card_args(
            {"id": "DEFEND_IRONCLAD", "target_type": "Self"},
            {"enemies": [{"combat_id": 1, "is_alive": True}]},
        ) == {"card_id": "DEFEND_IRONCLAD"}


class TestRecoverableBootstrapScreen:
    def test_mid_run_map_is_not_recoverable(self) -> None:
        assert _is_recoverable_bootstrap_screen("MAP") is False

    def test_main_menu_is_recoverable(self) -> None:
        assert _is_recoverable_bootstrap_screen("MAIN_MENU") is True


class TestPostEmbarkScreen:
    def test_event_counts_as_started_run(self) -> None:
        assert _is_post_embark_screen("EVENT") is True

    def test_map_counts_as_started_run(self) -> None:
        assert _is_post_embark_screen("MAP") is True

    def test_combat_counts_as_started_run(self) -> None:
        assert _is_post_embark_screen("COMBAT") is True

    def test_character_select_is_not_started_run(self) -> None:
        assert _is_post_embark_screen("CHARACTER_SELECT") is False


class TestBootstrapCompleteScreen:
    def test_character_select_is_complete(self) -> None:
        assert _is_bootstrap_complete_screen("CHARACTER_SELECT") is True

    def test_event_is_complete(self) -> None:
        assert _is_bootstrap_complete_screen("EVENT") is True

    def test_map_is_complete(self) -> None:
        assert _is_bootstrap_complete_screen("MAP") is True

    def test_combat_is_complete(self) -> None:
        assert _is_bootstrap_complete_screen("COMBAT") is True

    def test_unknown_is_not_complete(self) -> None:
        assert _is_bootstrap_complete_screen("UNKNOWN") is False


class TestEarlyFirstBattleState:
    def test_neow_event_is_early_state(self) -> None:
        assert _is_early_first_battle_state(
            "EVENT",
            {"event": {"event_id": "NEOW"}},
        ) is True

    def test_first_map_choice_is_early_state(self) -> None:
        assert _is_early_first_battle_state(
            "MAP",
            {"map": {"act_floor": 1, "current_coord": {"row": 0}}},
        ) is True

    def test_later_map_is_not_early_state(self) -> None:
        assert _is_early_first_battle_state(
            "MAP",
            {"map": {"act_floor": 3, "current_coord": {"row": 5}}},
        ) is False

    def test_early_combat_is_early_state(self) -> None:
        assert _is_early_first_battle_state(
            "COMBAT",
            {"combat": {"turn_number": 1, "player": {"deck_count": 9}}},
        ) is True

    def test_neow_bonus_card_combat_is_still_early_state(self) -> None:
        assert _is_early_first_battle_state(
            "COMBAT",
            {"combat": {"turn_number": 1, "player": {"deck_count": 11}}},
        ) is True

    def test_large_deck_combat_is_not_early_state(self) -> None:
        assert _is_early_first_battle_state(
            "COMBAT",
            {"combat": {"turn_number": 5, "player": {"deck_count": 20}}},
        ) is False


class TestFirstBattleDslGeneration:
    def test_first_battle_steps_use_combat_policy_primitive(self) -> None:
        spec = TestSpec(
            id="TC-FIRST-BATTLE-POLICY",
            title="首战基础策略",
            steps=["进入首次战斗", "按基础策略完成战斗"],
        )

        code = CodeGenerator().generate_case_test(spec)

        assert "enter_combat()" in code
        assert "combat_basic_policy()" in code
