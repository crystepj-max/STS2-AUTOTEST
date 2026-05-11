"""Game-semantic assertion and setup functions for the Fluent API (FR14, FR15, FR18).

Setup functions return ActionDescriptors that the FluentBuilder
dispatches to the adapter. Assertion functions return callables
that validate GameState snapshots.
"""

from typing import Any, Callable

from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.action_model import ActionDescriptor

# ── assertion functions ────────────────────────────────────

AssertionFn = Callable[[GameState], tuple[bool, str]]


def game_reached_state(expected: GameScreen) -> AssertionFn:
    """Assert the game is in a specific screen/state."""

    def check(state: GameState) -> tuple[bool, str]:
        ok = state.screen == expected
        msg = "" if ok else f"Expected {expected.value}, got {state.screen.value}"
        return ok, msg

    return check


def enemy_hp_decreased_by(amount: int) -> AssertionFn:
    """Assert enemy HP decreased by the given amount."""

    def check(state: GameState) -> tuple[bool, str]:
        current = getattr(state, "enemy_hp", None)
        previous = getattr(state, "previous_enemy_hp", None)
        if previous is None:
            return False, f"previous_enemy_hp not in state, cannot verify decrease"
        actual = previous - current if current is not None else 0
        ok = actual >= amount  # >= because RNG can cause extra damage
        msg = "" if ok else f"Expected enemy HP decrease ≥ {amount}, got {actual}"
        return ok, msg

    return check


def player_energy_decreased_by(amount: int) -> AssertionFn:
    """Assert player energy decreased by the given amount."""

    def check(state: GameState) -> tuple[bool, str]:
        current = getattr(state, "energy", None)
        previous = getattr(state, "previous_energy", None)
        if previous is None:
            return False, f"previous_energy not in state, cannot verify decrease"
        actual = previous - current if current is not None else 0
        ok = actual == amount
        msg = "" if ok else f"Expected energy decrease {amount}, got {actual}"
        return ok, msg

    return check


def player_hp_changed_by(amount: int) -> AssertionFn:
    """Assert player HP changed by the given amount (positive=heal, negative=damage)."""

    def check(state: GameState) -> tuple[bool, str]:
        current = getattr(state, "hp", None)
        previous = getattr(state, "previous_hp", None)
        if previous is None:
            return False, f"previous_hp not in state, cannot verify change"
        actual = current - previous if current is not None else 0
        ok = actual == amount
        msg = "" if ok else f"Expected HP change {amount}, got {actual}"
        return ok, msg

    return check


# ── setup / execute action descriptors ──────────────────────


def start_game(save: str | None = None) -> ActionDescriptor:
    """Start/load a game save."""
    return ActionDescriptor(
        action_type="start_game",
        params={"save": save} if save else {},
    )


def enter_combat(enemy: str = "") -> ActionDescriptor:
    """Enter combat with the given enemy."""
    return ActionDescriptor(
        action_type="enter_combat",
        params={"enemy": enemy} if enemy else {},
    )


def play_card(card_id: str, target: int = 0) -> ActionDescriptor:
    """Play a card, optionally targeting an enemy index."""
    return ActionDescriptor(
        action_type="play_card",
        params={"card_id": card_id, "target": target},
    )


def end_turn() -> ActionDescriptor:
    """End the current turn."""
    return ActionDescriptor(action_type="end_turn")


def set_seed(seed: int) -> ActionDescriptor:
    """Set the random seed for deterministic testing."""
    return ActionDescriptor(
        action_type="set_seed",
        params={"seed": seed},
    )


def give_card(card_id: str) -> ActionDescriptor:
    """Add a card to the player's hand."""
    return ActionDescriptor(
        action_type="give_card",
        params={"card_id": card_id},
    )


def set_hp(hp: int) -> ActionDescriptor:
    """Set the player's HP."""
    return ActionDescriptor(
        action_type="set_hp",
        params={"hp": hp},
    )
