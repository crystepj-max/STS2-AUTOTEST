"""Game state enums and models for STS2-AUTOTEST."""

from enum import StrEnum
from pydantic import BaseModel, ConfigDict


class GameScreen(StrEnum):
    """Complete game state enumeration (PRD FR6).

    Each state represents a distinct game screen/phase.
    Terminal states (GAME_OVER, VICTORY, CRASHED) have no allowed transitions.
    """

    MAIN_MENU = "MAIN_MENU"
    CHARACTER_SELECT = "CHARACTER_SELECT"
    MAP = "MAP"
    COMBAT = "COMBAT"
    SHOP = "SHOP"
    REST = "REST"
    EVENT = "EVENT"
    CHEST = "CHEST"
    BOSS_REWARD = "BOSS_REWARD"
    CARD_REWARD = "CARD_REWARD"
    RELIC_REWARD = "RELIC_REWARD"
    GAME_OVER = "GAME_OVER"
    VICTORY = "VICTORY"
    CRASHED = "CRASHED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_terminal(self) -> bool:
        """Check if this state is terminal (no further transitions possible)."""
        return self in _TERMINAL_STATES

    @property
    def allowed_transitions(self) -> frozenset[GameScreen]:
        """Get the set of allowed target states from this state."""
        return _ALLOWED_TRANSITIONS.get(self, frozenset())


_TERMINAL_STATES: frozenset[GameScreen] = frozenset({
    GameScreen.GAME_OVER,
    GameScreen.VICTORY,
    GameScreen.CRASHED,
    GameScreen.UNKNOWN,  # No outgoing transitions — session must restart
})

_ALLOWED_TRANSITIONS: dict[GameScreen, frozenset[GameScreen]] = {
    GameScreen.MAIN_MENU: frozenset({GameScreen.CHARACTER_SELECT}),
    GameScreen.CHARACTER_SELECT: frozenset({GameScreen.EVENT, GameScreen.MAP}),
    GameScreen.MAP: frozenset({
        GameScreen.COMBAT, GameScreen.SHOP, GameScreen.REST,
        GameScreen.EVENT, GameScreen.CHEST, GameScreen.GAME_OVER,
    }),
    GameScreen.COMBAT: frozenset({
        GameScreen.MAP, GameScreen.GAME_OVER,
        GameScreen.CARD_REWARD, GameScreen.RELIC_REWARD, GameScreen.BOSS_REWARD,
    }),
    GameScreen.SHOP: frozenset({GameScreen.MAP}),
    GameScreen.REST: frozenset({GameScreen.MAP}),
    GameScreen.EVENT: frozenset({GameScreen.MAP}),
    GameScreen.CHEST: frozenset({GameScreen.MAP}),
    GameScreen.CARD_REWARD: frozenset({GameScreen.MAP}),
    GameScreen.RELIC_REWARD: frozenset({GameScreen.MAP}),
    GameScreen.BOSS_REWARD: frozenset({GameScreen.MAP, GameScreen.VICTORY}),
    GameScreen.GAME_OVER: frozenset(),
    GameScreen.VICTORY: frozenset(),
    GameScreen.CRASHED: frozenset(),
    GameScreen.UNKNOWN: frozenset(),
}


class GameState(BaseModel):
    """Immutable snapshot of the current game state.

    frozen=True prevents accidental mutation during test execution.
    extra='allow' tolerates unknown fields from game version changes
    (adapter version buffer pattern).
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    screen: GameScreen
