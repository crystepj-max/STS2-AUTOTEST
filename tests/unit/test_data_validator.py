"""Tests for core/data_validator.py — semantic GameState validation (Story 4.4, AC2)."""

from __future__ import annotations

from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.data_validator import validate_game_state


class TestValidateGameState:
    """AC2: validate_game_state detects semantically impossible data."""

    def test_valid_state_no_extra(self) -> None:
        """A plain state with no extra fields should pass."""
        state = GameState(screen=GameScreen.MAIN_MENU)
        violations = validate_game_state(state)
        assert violations == []

    def test_valid_combat_state(self) -> None:
        """COMBAT with proper combat data should pass."""
        state = GameState(
            screen=GameScreen.COMBAT,
            combat={"player_hp": 50, "hand": ["strike", "defend"], "deck": ["card1"]},
        )
        violations = validate_game_state(state)
        assert violations == []

    def test_negative_hp_in_combat(self) -> None:
        """Negative combat.player_hp should be flagged."""
        state = GameState(
            screen=GameScreen.COMBAT,
            combat={"player_hp": -5, "hand": ["strike"], "deck": ["card1"]},
        )
        violations = validate_game_state(state)
        assert any("player_hp" in v and "negative" in v for v in violations)

    def test_negative_hp_top_level(self) -> None:
        """Negative top-level player_hp should be flagged."""
        state = GameState(screen=GameScreen.COMBAT, player_hp=-10)
        violations = validate_game_state(state)
        assert any("player_hp" in v and "negative" in v for v in violations)

    def test_empty_hand_in_combat(self) -> None:
        """Empty hand list during COMBAT should be flagged."""
        state = GameState(
            screen=GameScreen.COMBAT,
            combat={"player_hp": 50, "hand": [], "deck": ["card1"]},
        )
        violations = validate_game_state(state)
        assert any("hand" in v and "empty" in v for v in violations)

    def test_empty_deck_in_combat(self) -> None:
        """Empty deck list during COMBAT should be flagged."""
        state = GameState(
            screen=GameScreen.COMBAT,
            combat={"player_hp": 50, "hand": ["strike"], "deck": []},
        )
        violations = validate_game_state(state)
        assert any("deck" in v and "empty" in v for v in violations)

    def test_combat_no_combat_data(self) -> None:
        """COMBAT screen without combat data or player_hp should be flagged."""
        state = GameState(screen=GameScreen.COMBAT)
        violations = validate_game_state(state)
        assert any("no combat data" in v for v in violations)

    def test_unknown_screen_no_violations(self) -> None:
        """UNKNOWN screen should always pass."""
        state = GameState(screen=GameScreen.UNKNOWN, some_extra="value")
        violations = validate_game_state(state)
        assert violations == []

    def test_terminal_screens_no_violations(self) -> None:
        """Terminal screens (GAME_OVER, VICTORY, CRASHED) should pass even without data."""
        for screen in (GameScreen.GAME_OVER, GameScreen.VICTORY, GameScreen.CRASHED):
            state = GameState(screen=screen)
            violations = validate_game_state(state)
            assert violations == [], f"Expected no violations for {screen}"

    def test_map_without_extra_flagged(self) -> None:
        """MAP screen without any extra data should be flagged."""
        state = GameState(screen=GameScreen.MAP)
        violations = validate_game_state(state)
        assert any("no extra data" in v for v in violations)

    def test_non_combat_without_extra_flagged(self) -> None:
        """Non-terminal, non-COMBAT screen without extra data should be flagged."""
        state = GameState(screen=GameScreen.SHOP)
        violations = validate_game_state(state)
        assert any("no extra data" in v for v in violations)

    def test_valid_non_combat_with_extra(self) -> None:
        """Non-terminal screen with extra data should pass."""
        state = GameState(screen=GameScreen.MAP, floor=3, gold=100)
        violations = validate_game_state(state)
        assert violations == []

    def test_combat_none_player_hp_not_flagged(self) -> None:
        """COMBAT with 'player_hp' set to None should not raise negative HP."""
        state = GameState(
            screen=GameScreen.COMBAT,
            combat={"player_hp": None, "hand": ["strike"]},
        )
        violations = validate_game_state(state)
        assert not any("player_hp" in v and "negative" in v for v in violations)
