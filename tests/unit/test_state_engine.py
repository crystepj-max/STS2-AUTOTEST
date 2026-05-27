"""Tests for core/state_engine.py — transition validation, parsing, errors."""

import pytest

from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.state_engine import StateEngine, StateTransitionError


@pytest.fixture
def engine() -> StateEngine:
    return StateEngine()


class TestTransitionValidation:
    """validate_transition() tests (AC#1, AC#2)."""

    def test_legal_transition(self, engine: StateEngine) -> None:
        assert engine.validate_transition(
            GameScreen.MAIN_MENU, GameScreen.CHARACTER_SELECT
        ) is True

    def test_character_select_can_enter_neow_event(self, engine: StateEngine) -> None:
        assert engine.validate_transition(
            GameScreen.CHARACTER_SELECT, GameScreen.EVENT
        ) is True

    def test_event_can_enter_combat_after_recovery_action(
        self, engine: StateEngine
    ) -> None:
        assert engine.validate_transition(GameScreen.EVENT, GameScreen.COMBAT) is True

    def test_illegal_transition(self, engine: StateEngine) -> None:
        assert engine.validate_transition(
            GameScreen.MAIN_MENU, GameScreen.COMBAT
        ) is False

    def test_all_terminal_allow_nothing(self, engine: StateEngine) -> None:
        for state in (GameScreen.GAME_OVER, GameScreen.VICTORY, GameScreen.CRASHED):
            for target in GameScreen:
                assert engine.validate_transition(state, target) is (target == state)

    def test_map_to_all_legal_targets(self, engine: StateEngine) -> None:
        legal = {GameScreen.COMBAT, GameScreen.SHOP, GameScreen.REST,
                 GameScreen.EVENT, GameScreen.CHEST, GameScreen.GAME_OVER}
        for target in GameScreen:
            expected = target == GameScreen.MAP or target in legal
            assert engine.validate_transition(GameScreen.MAP, target) == expected

    def test_unknown_has_no_transitions(self, engine: StateEngine) -> None:
        for target in GameScreen:
            assert engine.validate_transition(GameScreen.UNKNOWN, target) is (
                target == GameScreen.UNKNOWN
            )


class TestUpdateState:
    """update_state() integrated validation (AC#1, AC#2)."""

    def test_legal_update(self, engine: StateEngine) -> None:
        result = engine.update_state(GameScreen.MAIN_MENU, "CHARACTER_SELECT")
        assert result == GameScreen.CHARACTER_SELECT

    def test_illegal_update_raises(self, engine: StateEngine) -> None:
        with pytest.raises(StateTransitionError, match="MAIN_MENU → COMBAT"):
            engine.update_state(GameScreen.MAIN_MENU, "COMBAT")

    def test_update_with_event_in_error(self, engine: StateEngine) -> None:
        with pytest.raises(StateTransitionError) as exc_info:
            engine.update_state(
                GameScreen.MAIN_MENU, "COMBAT", event="start_combat"
            )
        assert exc_info.value.event == "start_combat"

    def test_update_to_same_state(self, engine: StateEngine) -> None:
        """Repeated observations of the same screen are valid no-op updates."""
        result = engine.update_state(GameScreen.MAP, "MAP")
        assert result == GameScreen.MAP


class TestParseState:
    """parse_state() dictionary and string handling (AC#3)."""

    def test_parse_string(self, engine: StateEngine) -> None:
        assert engine.parse_state("MAIN_MENU") == GameScreen.MAIN_MENU

    def test_parse_dict(self, engine: StateEngine) -> None:
        assert engine.parse_state({"screen": "COMBAT"}) == GameScreen.COMBAT

    def test_parse_dict_missing_screen(self, engine: StateEngine) -> None:
        result = engine.parse_state({"other": "data"})
        assert result == GameScreen.UNKNOWN

    def test_parse_unknown_string(self, engine: StateEngine) -> None:
        result = engine.parse_state("NONEXISTENT_SCREEN")
        assert result == GameScreen.UNKNOWN

    def test_parse_unknown_dict_value(self, engine: StateEngine) -> None:
        result = engine.parse_state({"screen": "WEIRD_MODE"})
        assert result == GameScreen.UNKNOWN

    def test_parse_all_known_values(self, engine: StateEngine) -> None:
        for screen in GameScreen:
            assert engine.parse_state(screen.value) == screen

    def test_parse_game_state_model(self, engine: StateEngine) -> None:
        state = GameState(screen=GameScreen.COMBAT)
        assert engine.parse_state(state) == GameScreen.COMBAT

    def test_update_with_game_state_model(self, engine: StateEngine) -> None:
        state = GameState(screen=GameScreen.CHARACTER_SELECT)
        result = engine.update_state(GameScreen.MAIN_MENU, state)
        assert result == GameScreen.CHARACTER_SELECT

    def test_parse_update_unknown_does_not_crash(self, engine: StateEngine) -> None:
        """Unknown adapter states map to UNKNOWN without aborting the update path."""
        result = engine.update_state(GameScreen.MAIN_MENU, "NONEXISTENT_SCREEN")
        assert result == GameScreen.UNKNOWN


class TestStateTransitionError:
    """StateTransitionError context capture tests."""

    def test_error_contains_source_and_target(self) -> None:
        err = StateTransitionError(GameScreen.MAIN_MENU, GameScreen.COMBAT, "test")
        assert err.source == GameScreen.MAIN_MENU
        assert err.target == GameScreen.COMBAT
        assert err.event == "test"

    def test_error_contains_stack(self) -> None:
        err = StateTransitionError(GameScreen.MAP, GameScreen.CHEST)
        assert len(err.stack) > 0
        assert any("test_state_engine" in f for f in err.stack)

    def test_error_message_readable(self) -> None:
        err = StateTransitionError(GameScreen.MAIN_MENU, GameScreen.COMBAT)
        msg = str(err)
        assert "MAIN_MENU" in msg
        assert "COMBAT" in msg
        assert "CHARACTER_SELECT" in msg  # the only allowed transition

    def test_error_message_no_allowed(self) -> None:
        err = StateTransitionError(GameScreen.GAME_OVER, GameScreen.MAIN_MENU, "restart")
        msg = str(err)
        assert "GAME_OVER" in msg
        assert "MAIN_MENU" in msg
        assert "restart" in msg
        assert "none" in msg


class TestFullTransitionMatrix:
    """Validate every legal and illegal transition in the matrix."""

    def test_all_legal_transitions_validate_true(self, engine: StateEngine) -> None:
        for source in GameScreen:
            for target in source.allowed_transitions:
                assert engine.validate_transition(source, target) is True

    def test_all_illegal_transitions_validate_false(self, engine: StateEngine) -> None:
        for source in GameScreen:
            illegal = set(GameScreen) - source.allowed_transitions - {source}
            for target in illegal:
                assert engine.validate_transition(source, target) is False


class TestForceTransition:
    """force_transition() bypasses validation for recovery paths."""

    def test_crashed_to_main_menu(self, engine: StateEngine) -> None:
        result = engine.force_transition(GameScreen.CRASHED, GameScreen.MAIN_MENU)
        assert result == GameScreen.MAIN_MENU

    def test_game_over_to_main_menu(self, engine: StateEngine) -> None:
        result = engine.force_transition(GameScreen.GAME_OVER, GameScreen.MAIN_MENU)
        assert result == GameScreen.MAIN_MENU

    def test_any_to_any(self, engine: StateEngine) -> None:
        """force_transition works between any two states."""
        for source in GameScreen:
            for target in GameScreen:
                result = engine.force_transition(source, target)
                assert result == target

    def test_logs_warning(self, engine: StateEngine, caplog: pytest.LogCaptureFixture) -> None:
        """force_transition should log a WARNING (auditable recovery path)."""
        import logging

        with caplog.at_level(logging.WARNING, logger="core.state_engine"):
            engine.force_transition(GameScreen.CRASHED, GameScreen.MAIN_MENU)
        assert "FORCE transition" in caplog.text
        assert "CRASHED" in caplog.text
        assert "MAIN_MENU" in caplog.text
