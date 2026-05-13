"""State engine and transition validation for STS2-AUTOTEST (FR7).

StateEngine is the central authority for game state transitions.
All state changes must pass through it for validation.
"""

import traceback
from typing import Any

from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.state import GameScreen, GameState

logger = get_logger("core.state_engine")


class StateTransitionError(Exception):
    """Raised when an illegal state transition is attempted.

    Records source state, target state, triggering event, and call stack
    for diagnostic purposes.
    """

    def __init__(
        self,
        source: GameScreen,
        target: GameScreen,
        event: str = "",
    ) -> None:
        self.source = source
        self.target = target
        self.event = event
        self.stack = traceback.format_stack()
        allowed = (
            ", ".join(t.value for t in source.allowed_transitions)
            if source.allowed_transitions
            else "none"
        )
        super().__init__(
            f"Illegal state transition: {source.value} → {target.value}"
            + (f" (event: {event})" if event else "")
            + f". Allowed transitions from {source.value}: {allowed}"
        )


class StateEngine:
    """Central state machine authority.

    Validates all game state transitions against the allowed_transitions
    mapping in GameScreen. Unknown state values are mapped to UNKNOWN
    with a warning log rather than crashing.
    """

    def validate_transition(
        self, current: GameScreen, target: GameScreen
    ) -> bool:
        """Check if target is in current's allowed_transitions.

        Returns True if the transition is legal, False otherwise.
        Does NOT raise — callers decide how to handle.
        """
        return target == current or target in current.allowed_transitions

    def parse_state(self, raw_state: str | dict[str, Any] | GameState) -> GameScreen:
        """Parse raw adapter state into a GameScreen enum value.

        Handles both string states (e.g., "MAIN_MENU") and dict
        states (e.g., {"screen": "MAIN_MENU"}).

        Unknown values are mapped to GameScreen.UNKNOWN with a
        warning log — the test is NOT terminated (FR7).
        """
        if isinstance(raw_state, GameState):
            return raw_state.screen
        if isinstance(raw_state, dict):
            screen_val = raw_state.get("screen", "")
            screen_str = screen_val if isinstance(screen_val, str) else ""
        else:
            screen_str = raw_state

        try:
            return GameScreen(screen_str)
        except ValueError:
            logger.warning(
                "Unknown game screen value %r — mapping to UNKNOWN",
                screen_str,
            )
            return GameScreen.UNKNOWN

    def update_state(
        self,
        current: GameScreen,
        new_raw: str | dict[str, Any] | GameState,
        event: str = "",
    ) -> GameScreen:
        """Parse and validate a state transition in one step.

        Args:
            current: Current known GameScreen.
            new_raw: Raw state data from adapter (string or dict).
            event: Optional event name for error context.

        Returns:
            The new GameScreen if the transition is legal.

        Raises:
            StateTransitionError: If the transition is illegal.
        """
        target = self.parse_state(new_raw)
        if target is GameScreen.UNKNOWN:
            logger.warning(
                "State transition from %s to UNKNOWN accepted for compatibility",
                current.value,
            )
            return target
        if not self.validate_transition(current, target):
            raise StateTransitionError(current, target, event)
        logger.info("State transition: %s → %s", current.value, target.value)
        return target

    def force_transition(
        self, current: GameScreen, target: GameScreen
    ) -> GameScreen:
        """Force a state transition bypassing allowed_transitions validation.

        Only used in recovery paths (e.g., CRASHED → MAIN_MENU after
        adapter reconnect). Normal code paths MUST NOT call this.
        Always logs a WARNING to make recovery transitions auditable.
        """
        logger.warning(
            "FORCE transition (recovery path): %s → %s — "
            "bypassing allowed_transitions check",
            current.value,
            target.value,
        )
        return target
