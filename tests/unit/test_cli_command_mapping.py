"""CliModAdapter 纯函数契约测试。

验证 `_build_cli_args`、`_screen_to_actions` 和 `_SCREEN_MAP`
是否符合 `docs/sts2-cli-mod-reference.md` 中记录的 STS2-Cli-Mod CLI 命令格式。
这些测试不启动真实 CLI 进程。
"""

from sts2_autotest.adapters.cli_mod import (
    _SCREEN_MAP,
    _build_cli_args,
    _filter_state_extra,
    _screen_to_actions,
)
from sts2_autotest.common.state import GameScreen


class TestBuildCliArgs:
    """_build_cli_args generates correct sts2 CLI arguments."""

    def test_action_only(self) -> None:
        assert _build_cli_args("ping") == ["ping"]

    def test_action_with_string_arg(self) -> None:
        result = _build_cli_args("play_card", {"card_id": "VoidSlash"})
        assert result == ["play_card", "VoidSlash"]

    def test_action_with_numeric_arg(self) -> None:
        result = _build_cli_args("set_ascension", {"level": 5})
        assert result == ["set_ascension", "--level", "5"]

    def test_action_with_bool_true(self) -> None:
        result = _build_cli_args("advance_dialogue", {"auto": True})
        assert result == ["advance_dialogue", "--auto"]

    def test_action_with_bool_false(self) -> None:
        result = _build_cli_args("advance_dialogue", {"auto": False})
        assert result == ["advance_dialogue"]

    def test_action_with_list_arg(self) -> None:
        result = _build_cli_args("hand_select_card", {"card_ids": ["Strike", "Defend"]})
        assert result == ["hand_select_card", "Strike", "Defend"]

    def test_action_with_multiple_args(self) -> None:
        result = _build_cli_args("play_card", {
            "card_id": "VoidSlash",
            "nth": 2,
            "target": "enemy_1",
        })
        assert result[0] == "play_card"
        assert result[1] == "VoidSlash"
        assert "--nth" in result
        assert "2" in result
        assert "--target" in result
        assert "enemy_1" in result

    def test_play_card_preserves_unknown_flags(self) -> None:
        result = _build_cli_args("play_card", {"card_id": "Strike", "foo": "bar"})
        assert result == ["play_card", "Strike", "--foo", "bar"]

    def test_choose_map_node_uses_explicit_positional_order(self) -> None:
        result = _build_cli_args("choose_map_node", {"row": 3, "col": 1})
        assert result == ["choose_map_node", "1", "3"]

    def test_grid_select_card_uses_positional_card_id(self) -> None:
        result = _build_cli_args("grid_select_card", {"card_id": "Strike"})
        assert result == ["grid_select_card", "Strike"]

    def test_action_with_none_args(self) -> None:
        assert _build_cli_args("end_turn", None) == ["end_turn"]

    def test_action_with_empty_args(self) -> None:
        assert _build_cli_args("end_turn", {}) == ["end_turn"]


class TestScreenToActions:
    """_screen_to_actions returns valid CLI commands for each game screen.

    Cross-referenced with sts2-cli-mod-reference.md command tables.
    """

    def test_main_menu_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.MAIN_MENU)
        assert "new_run" in actions
        assert "continue_run" in actions
        assert "abandon_run" in actions
        assert "choose_game_mode" in actions

    def test_character_select_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.CHARACTER_SELECT)
        assert "select_character" in actions
        assert "set_ascension" in actions
        assert "embark" in actions

    def test_map_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.MAP)
        assert "choose_map_node" in actions
        assert "proceed" in actions

    def test_combat_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.COMBAT)
        assert "play_card" in actions
        assert "end_turn" in actions
        assert "use_potion" in actions

    def test_shop_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.SHOP)
        assert "shop_buy_card" in actions
        assert "shop_buy_relic" in actions
        assert "shop_buy_potion" in actions
        assert "shop_remove_card" in actions

    def test_rest_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.REST)
        assert "choose_rest_option" in actions

    def test_event_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.EVENT)
        assert "choose_event" in actions
        assert "advance_dialogue" in actions

    def test_chest_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.CHEST)
        assert "open_chest" in actions
        assert "pick_relic" in actions

    def test_boss_reward_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.BOSS_REWARD)
        assert "reward_claim" in actions
        assert "relic_select" in actions
        assert "relic_skip" in actions

    def test_card_reward_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.CARD_REWARD)
        assert "reward_choose_card" in actions
        assert "reward_skip_card" in actions
        assert "reward_claim" in actions

    def test_relic_reward_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.RELIC_REWARD)
        assert "reward_claim" in actions
        assert "relic_select" in actions
        assert "relic_skip" in actions

    def test_game_over_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.GAME_OVER)
        assert "return_to_menu" in actions

    def test_victory_actions(self) -> None:
        actions = _screen_to_actions(GameScreen.VICTORY)
        assert "return_to_menu" in actions

    def test_terminal_screens_have_return(self) -> None:
        for screen in (GameScreen.GAME_OVER, GameScreen.VICTORY):
            assert "return_to_menu" in _screen_to_actions(screen)

    def test_unknown_screen_returns_empty(self) -> None:
        assert _screen_to_actions(GameScreen.UNKNOWN) == []

    def test_crashed_screen_returns_empty(self) -> None:
        assert _screen_to_actions(GameScreen.CRASHED) == []

    def test_all_non_terminal_screens_have_actions(self) -> None:
        terminal = {
            GameScreen.GAME_OVER,
            GameScreen.VICTORY,
            GameScreen.CRASHED,
            GameScreen.UNKNOWN,
        }
        for screen in GameScreen:
            if screen not in terminal:
                assert _screen_to_actions(screen), f"{screen.value} has no actions"


class TestScreenMap:
    """_SCREEN_MAP covers all CLI screen names from reference doc."""

    def test_menu_mapped(self) -> None:
        assert _SCREEN_MAP["MENU"] == GameScreen.MAIN_MENU

    def test_singleplayer_submenu_mapped(self) -> None:
        assert _SCREEN_MAP["SINGLEPLAYER_SUBMENU"] == GameScreen.MAIN_MENU

    def test_character_select_mapped(self) -> None:
        assert _SCREEN_MAP["CHARACTER_SELECT"] == GameScreen.CHARACTER_SELECT

    def test_map_mapped(self) -> None:
        assert _SCREEN_MAP["MAP"] == GameScreen.MAP

    def test_combat_mapped(self) -> None:
        assert _SCREEN_MAP["COMBAT"] == GameScreen.COMBAT

    def test_shop_mapped(self) -> None:
        assert _SCREEN_MAP["SHOP"] == GameScreen.SHOP

    def test_rest_mapped(self) -> None:
        assert _SCREEN_MAP["REST"] == GameScreen.REST
        assert _SCREEN_MAP["REST_SITE"] == GameScreen.REST

    def test_event_mapped(self) -> None:
        assert _SCREEN_MAP["EVENT"] == GameScreen.EVENT

    def test_treasure_and_chest_mapped(self) -> None:
        assert _SCREEN_MAP["TREASURE"] == GameScreen.CHEST
        assert _SCREEN_MAP["CHEST"] == GameScreen.CHEST

    def test_game_over_mapped(self) -> None:
        assert _SCREEN_MAP["GAME_OVER"] == GameScreen.GAME_OVER

    def test_victory_mapped(self) -> None:
        assert _SCREEN_MAP["VICTORY"] == GameScreen.VICTORY

    def test_reward_screens_mapped(self) -> None:
        assert _SCREEN_MAP["BOSS_REWARD"] == GameScreen.BOSS_REWARD
        assert _SCREEN_MAP["CARD_REWARD"] == GameScreen.CARD_REWARD
        assert _SCREEN_MAP["RELIC_REWARD"] == GameScreen.RELIC_REWARD

    def test_all_values_are_valid_game_screens(self) -> None:
        for cli_name, screen in _SCREEN_MAP.items():
            assert isinstance(screen, GameScreen), (
                f"_SCREEN_MAP[{cli_name!r}] = {screen!r} is not a GameScreen"
            )

    def test_all_mapped_screens_have_actions(self) -> None:
        for cli_name, screen in _SCREEN_MAP.items():
            actions = _screen_to_actions(screen)
            if screen not in (GameScreen.UNKNOWN, GameScreen.CRASHED):
                assert actions, f"{cli_name} -> {screen.value} has no actions"


class TestFilterStateExtra:
    """_filter_state_extra strips internal keys and preserves game data."""

    def test_strips_screen_key(self) -> None:
        assert "screen" not in _filter_state_extra({"screen": "MENU", "hp": 50})

    def test_strips_error_key(self) -> None:
        assert "error" not in _filter_state_extra({"error": "timeout", "hp": 50})

    def test_preserves_game_data(self) -> None:
        data = {"combat": {"player_hp": 50}, "timestamp": 123}
        result = _filter_state_extra(data)
        assert result == data

    def test_empty_input(self) -> None:
        assert _filter_state_extra({}) == {}

    def test_only_stripped_keys(self) -> None:
        assert _filter_state_extra({"screen": "MENU", "error": "x"}) == {}
