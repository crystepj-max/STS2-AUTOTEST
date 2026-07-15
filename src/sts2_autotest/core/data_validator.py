"""Semantic game state validation — checks GameState for logically impossible data (AC2)."""

from __future__ import annotations

from typing import Any

from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.state import GameScreen, GameState

logger = get_logger("core.data_validator")


def validate_game_state(state: GameState) -> list[str]:
    """Validate GameState for semantically impossible data.

    Checks:
    - HP must not be negative (combat.player_hp or player_hp extra field)
    - During COMBAT, hand/cards should not be empty (combat.hand extra field)
    - Screen-to-data consistency: COMBAT screen must have combat-related extra fields

    Returns a list of violation messages. Empty list means valid.
    """
    violations: list[str] = []

    extra = state.model_extra or {}

    combat = extra.get("combat")
    if isinstance(combat, dict):
        _check_combat_data(state.screen, combat, violations)

    # Check for top-level player_hp field if no combat dict
    hp = extra.get("player_hp")
    if isinstance(hp, (int, float)) and hp < 0:
        violations.append(f"player_hp is negative: {hp}")

    # Screen-to-data consistency
    _check_screen_consistency(state.screen, extra, violations)

    return violations


def _check_combat_data(screen: GameScreen, combat: dict[str, Any], violations: list[str]) -> None:
    """Validate combat-specific data fields."""
    hp = combat.get("player_hp")
    if isinstance(hp, (int, float)) and hp < 0:
        violations.append(f"combat.player_hp is negative: {hp}")

    if screen != GameScreen.COMBAT:
        return

    hand = combat.get("hand")
    if isinstance(hand, list) and len(hand) == 0:
        violations.append("combat.hand is empty during COMBAT")

    deck = combat.get("deck")
    if isinstance(deck, list) and len(deck) == 0:
        violations.append("combat.deck is empty during COMBAT")


def _check_screen_consistency(
    screen: GameScreen, extra: dict[str, Any], violations: list[str],
) -> None:
    """Validate screen value is consistent with extra data."""
    if screen == GameScreen.COMBAT:
        if "combat" not in extra and "player_hp" not in extra:
            violations.append(
                f"screen is {screen.value} but no combat data or player_hp found in state"
            )
    elif screen in (GameScreen.UNKNOWN, GameScreen.MAIN_MENU, GameScreen.CHARACTER_SELECT):
        pass  # These screens are valid with or without extra data
    elif screen in (GameScreen.GAME_OVER, GameScreen.VICTORY, GameScreen.CRASHED):
        pass  # Terminal states may have no extra data
    else:
        # Non-terminal, non-COMBAT screens should have some extra data
        if not extra:
            violations.append(
                f"screen is {screen.value} but no extra data available"
            )
