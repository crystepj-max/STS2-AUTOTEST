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


def return_to_menu() -> ActionDescriptor:
    """Return to the main menu."""
    return ActionDescriptor(action_type="return_to_menu")


def choose_game_mode(mode: str) -> ActionDescriptor:
    """Choose a game mode from the singleplayer submenu."""
    return ActionDescriptor(
        action_type="choose_game_mode",
        params={"mode": mode},
    )


def start_new_run() -> ActionDescriptor:
    """Open the new run flow from the main menu."""
    return ActionDescriptor(action_type="start_new_run")


def select_character(character_id: str) -> ActionDescriptor:
    """Select a playable character."""
    return ActionDescriptor(
        action_type="select_character",
        params={"character_id": character_id},
    )


def embark() -> ActionDescriptor:
    """Confirm character selection and start the run."""
    return ActionDescriptor(action_type="embark")


def choose_event(index: int) -> ActionDescriptor:
    """Choose an event option by zero-based index."""
    return ActionDescriptor(
        action_type="choose_event",
        params={"index": index},
    )


def advance_dialogue(auto: bool = False) -> ActionDescriptor:
    """Advance the current event dialogue."""
    params = {"auto": auto} if auto else {}
    return ActionDescriptor(action_type="advance_dialogue", params=params)


def choose_map_node(col: int, row: int) -> ActionDescriptor:
    """Choose a travelable map node by column and row."""
    return ActionDescriptor(
        action_type="choose_map_node",
        params={"col": col, "row": row},
    )


def skip_card_reward() -> ActionDescriptor:
    """Skip the current card reward."""
    return ActionDescriptor(action_type="skip_card_reward")


def combat_basic_policy() -> ActionDescriptor:
    """Execute the framework's baseline first-battle combat policy."""
    return ActionDescriptor(action_type="combat_basic_policy")


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


def no_crash_detected() -> AssertionFn:
    """Assert the game has not crashed."""

    def check(state: GameState) -> tuple[bool, str]:
        ok = state.screen != GameScreen.CRASHED
        msg = "" if ok else f"Game is in CRASHED state"
        return ok, msg

    return check


def has_travelable_node() -> AssertionFn:
    """Assert there is at least one travelable map node."""

    def check(state: GameState) -> tuple[bool, str]:
        nodes = getattr(state, "travelable_nodes", None)
        if nodes is None:
            return False, "travelable_nodes not in state"
        ok = len(nodes) > 0
        msg = "" if ok else "No travelable nodes available"
        return ok, msg

    return check
