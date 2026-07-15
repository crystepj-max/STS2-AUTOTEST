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


def _resolve_player_field(state: GameState, name: str) -> Any:
    """Read a player resource value (hp, energy, block, hand_count, ...).

    The real CLI adapter nests these under state.combat['player'][name];
    unit tests and other adapters may set a flat top-level attribute of the
    same name directly on GameState. Flat wins so existing fixtures keep
    working unchanged.
    """
    flat = getattr(state, name, None)
    if flat is not None:
        return flat
    combat = getattr(state, "combat", None)
    if isinstance(combat, dict):
        player = combat.get("player")
        if isinstance(player, dict):
            if name == "hp":
                return player.get("hp", player.get("current_hp"))
            if name == "hand_count":
                hand = combat.get("hand")
                if isinstance(hand, list):
                    return len(hand)
            return player.get(name)
    return None


def _resolve_enemy_field(state: GameState, name: str) -> Any:
    """Read the active enemy field value.

    For ``hp`` we use the total HP of all alive enemies so assertions remain stable
    when damage can land on any target. For other fields, we keep the legacy
    "first alive enemy, else first enemy" behavior. Unit tests may also set a flat
    top-level attribute named enemy_<name> directly.
    """
    flat = getattr(state, f"enemy_{name}", None)
    if flat is not None:
        return flat
    combat = getattr(state, "combat", None)
    if isinstance(combat, dict):
        enemies = combat.get("enemies")
        if isinstance(enemies, list) and enemies:
            if name == "hp":
                alive = [
                    e for e in enemies
                    if isinstance(e, dict) and e.get("is_alive", True)
                ]
                pool = alive or [e for e in enemies if isinstance(e, dict)]
                total = 0
                has_value = False
                for enemy in pool:
                    value = enemy.get("hp", enemy.get("current_hp"))
                    if isinstance(value, (int, float)):
                        total += int(value)
                        has_value = True
                if has_value:
                    return total

            target = next(
                (e for e in enemies if isinstance(e, dict) and e.get("is_alive")),
                enemies[0],
            )
            if isinstance(target, dict):
                return target.get(name)
    return None


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
        current = _resolve_enemy_field(state, "hp")
        previous = getattr(state, "previous_enemy_hp", None)
        if previous is None:
            return False, f"previous_enemy_hp not in state, cannot verify decrease"
        actual = previous - current if current is not None else 0
        ok = actual >= amount  # >= because RNG can cause extra damage
        msg = "" if ok else f"Expected enemy HP decrease ≥ {amount}, got {actual}"
        return ok, msg

    return check


def enemy_took_exact_hits(damage: int, hits: int) -> AssertionFn:
    """Assert the last action recorded exact enemy damage events."""

    def check(state: GameState) -> tuple[bool, str]:
        events = getattr(state, "damage_events", None)
        if events is None:
            combat = getattr(state, "combat", None)
            if isinstance(combat, dict):
                events = combat.get("damage_events")
        if not isinstance(events, list):
            return False, "damage_events not in state, cannot verify exact hits"

        amounts: list[int] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            raw_amount = event.get("amount", event.get("damage"))
            if isinstance(raw_amount, int):
                amounts.append(raw_amount)

        expected = [damage] * hits
        ok = amounts == expected
        msg = "" if ok else f"Expected enemy damage events {damage} x {hits}, got {amounts}"
        return ok, msg

    return check


def player_energy_decreased_by(amount: int) -> AssertionFn:
    """Assert player energy decreased by the given amount."""

    def check(state: GameState) -> tuple[bool, str]:
        current = _resolve_player_field(state, "energy")
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
        current = _resolve_player_field(state, "hp")
        previous = getattr(state, "previous_hp", None)
        if previous is None:
            return False, f"previous_hp not in state, cannot verify change"
        actual = current - previous if current is not None else 0
        ok = actual == amount
        msg = "" if ok else f"Expected HP change {amount}, got {actual}"
        return ok, msg

    return check


def player_block_increased_by(amount: int) -> AssertionFn:
    """Assert player block increased by the given amount."""

    def check(state: GameState) -> tuple[bool, str]:
        current = _resolve_player_field(state, "block")
        previous = getattr(state, "previous_block", None)
        if previous is None:
            return False, f"previous_block not in state, cannot verify increase"
        actual = current - previous if current is not None else 0
        ok = actual == amount
        msg = "" if ok else f"Expected block increase {amount}, got {actual}"
        return ok, msg

    return check


def hand_size_changed_by(amount: int) -> AssertionFn:
    """Assert hand card count changed by the given amount (positive=draw, negative=play/discard)."""

    def check(state: GameState) -> tuple[bool, str]:
        current = _resolve_player_field(state, "hand_count")
        previous = getattr(state, "previous_hand_count", None)
        if previous is None:
            return False, f"previous_hand_count not in state, cannot verify change"
        actual = current - previous if current is not None else 0
        ok = actual == amount
        msg = "" if ok else f"Expected hand size change {amount}, got {actual}"
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


def minion_queue_ids_are(expected_ids: list[str]) -> AssertionFn:
    """Assert the current combat minion queue matches the expected id order."""

    def check(state: GameState) -> tuple[bool, str]:
        combat = getattr(state, "combat", None)
        if isinstance(combat, dict):
            queue = combat.get("minion_queue")
            if isinstance(queue, list):
                actual_ids = [str(item.get("id", "")) for item in queue if isinstance(item, dict)]
                ok = actual_ids == expected_ids
                msg = "" if ok else f"Expected minion queue {expected_ids}, got {actual_ids}"
                return ok, msg

        agent_view = getattr(state, "agent_view", None)
        if isinstance(agent_view, dict):
            agent_combat = agent_view.get("combat")
            if isinstance(agent_combat, dict):
                queue = agent_combat.get("minion_queue")
                if isinstance(queue, list):
                    actual_ids = [str(item.get("id", "")) for item in queue if isinstance(item, dict)]
                    ok = actual_ids == expected_ids
                    msg = "" if ok else f"Expected minion queue {expected_ids}, got {actual_ids}"
                    return ok, msg

        return False, "minion_queue not in state, cannot verify summon result"

    return check
