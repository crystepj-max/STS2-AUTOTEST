"""Tests for common/state.py — GameScreen enum, GameState model."""

import pytest
from pydantic import ValidationError

from sts2_autotest.common.state import (
    GameScreen,
    GameState,
)


class TestGameScreen:
    """GameScreen StrEnum tests."""

    def test_has_all_required_states(self) -> None:
        expected = {
            "MAIN_MENU", "CHARACTER_SELECT", "MAP", "COMBAT", "SHOP", "REST",
            "EVENT", "CHEST", "BUNDLE_SELECTION", "BOSS_REWARD", "CARD_REWARD",
            "RELIC_REWARD", "GAME_OVER", "VICTORY", "CRASHED", "UNKNOWN",
        }
        actual = {s.name for s in GameScreen}
        assert expected == actual

    def test_enum_values_match_names(self) -> None:
        for state in GameScreen:
            assert state.value == state.name


    def test_is_string_enum(self) -> None:
        assert isinstance(GameScreen.MAIN_MENU, str)


class TestIsTerminal:
    """is_terminal property tests."""

    @pytest.mark.parametrize("state", [
        GameScreen.GAME_OVER,
        GameScreen.VICTORY,
        GameScreen.CRASHED,
        GameScreen.UNKNOWN,
    ])
    def test_terminal_states(self, state: GameScreen) -> None:
        assert state.is_terminal is True

    @pytest.mark.parametrize("state", [
        GameScreen.MAIN_MENU,
        GameScreen.CHARACTER_SELECT,
        GameScreen.MAP,
        GameScreen.COMBAT,
        GameScreen.SHOP,
        GameScreen.REST,
        GameScreen.EVENT,
        GameScreen.CHEST,
        GameScreen.BOSS_REWARD,
        GameScreen.CARD_REWARD,
        GameScreen.RELIC_REWARD,
    ])
    def test_non_terminal_states(self, state: GameScreen) -> None:
        assert state.is_terminal is False


class TestAllowedTransitions:
    """allowed_transitions mapping tests."""

    def test_main_menu_to_character_select(self) -> None:
        transitions = GameScreen.MAIN_MENU.allowed_transitions
        assert transitions == frozenset({GameScreen.CHARACTER_SELECT})

    def test_character_select_to_event_or_map(self) -> None:
        transitions = GameScreen.CHARACTER_SELECT.allowed_transitions
        assert transitions == frozenset({GameScreen.EVENT, GameScreen.MAP})

    def test_map_has_multiple_transitions(self) -> None:
        transitions = GameScreen.MAP.allowed_transitions
        assert GameScreen.COMBAT in transitions
        assert GameScreen.SHOP in transitions
        assert GameScreen.REST in transitions
        assert GameScreen.EVENT in transitions
        assert GameScreen.CHEST in transitions
        assert GameScreen.GAME_OVER in transitions

    def test_combat_has_reward_transitions(self) -> None:
        transitions = GameScreen.COMBAT.allowed_transitions
        assert GameScreen.MAP in transitions
        assert GameScreen.GAME_OVER in transitions
        assert GameScreen.CARD_REWARD in transitions
        assert GameScreen.RELIC_REWARD in transitions
        assert GameScreen.BOSS_REWARD in transitions

    def test_event_can_transition_to_card_reward(self) -> None:
        transitions = GameScreen.EVENT.allowed_transitions
        assert GameScreen.CARD_REWARD in transitions

    def test_terminal_states_have_no_transitions(self) -> None:
        for state in (GameScreen.GAME_OVER, GameScreen.VICTORY, GameScreen.CRASHED):
            assert state.allowed_transitions == frozenset()

    def test_unknown_has_no_transitions(self) -> None:
        assert GameScreen.UNKNOWN.allowed_transitions == frozenset()

    def test_reward_states_return_to_map(self) -> None:
        for state in (
            GameScreen.CARD_REWARD,
            GameScreen.RELIC_REWARD,
            GameScreen.CHEST,
        ):
            assert state.allowed_transitions == frozenset({GameScreen.MAP})

    def test_boss_reward_can_go_to_map_or_victory(self) -> None:
        transitions = GameScreen.BOSS_REWARD.allowed_transitions
        assert transitions == frozenset({GameScreen.MAP, GameScreen.VICTORY})


class TestGameState:
    """GameState pydantic model tests."""

    def test_create_with_screen(self) -> None:
        state = GameState(screen=GameScreen.MAIN_MENU)
        assert state.screen == GameScreen.MAIN_MENU

    def test_frozen_immutable(self) -> None:
        state = GameState(screen=GameScreen.MAIN_MENU)
        with pytest.raises(ValidationError):
            state.screen = GameScreen.COMBAT  # type: ignore[misc]

    def test_accepts_extra_fields(self) -> None:
        state = GameState(screen=GameScreen.COMBAT, hp=100, energy=3)
        assert state.screen == GameScreen.COMBAT
        assert state.hp == 100  # type: ignore[attr-defined]
        assert state.energy == 3  # type: ignore[attr-defined]

    def test_requires_screen(self) -> None:
        with pytest.raises(ValidationError):
            GameState()  # type: ignore[call-arg]

    def test_each_state_creates_independent_snapshot(self) -> None:
        state1 = GameState(screen=GameScreen.MAP, gold=50)
        state2 = GameState(screen=GameScreen.MAP, gold=100)
        assert state1.gold == 50  # type: ignore[attr-defined]
        assert state2.gold == 100  # type: ignore[attr-defined]
