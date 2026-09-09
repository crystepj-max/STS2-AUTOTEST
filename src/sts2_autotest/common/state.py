"""Game state enums and models for STS2-AUTOTEST."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from typing import Any


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
    BUNDLE_SELECTION = "BUNDLE_SELECTION"
    TRI_SELECT = "TRI_SELECT"
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
    GameScreen.UNKNOWN,
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
    GameScreen.EVENT: frozenset({
        GameScreen.MAP, GameScreen.COMBAT, GameScreen.CARD_REWARD,
        GameScreen.BUNDLE_SELECTION, GameScreen.TRI_SELECT,
    }),
    GameScreen.CHEST: frozenset({GameScreen.MAP}),
    # Neow 非 MAP 祝福分支（卷轴箱/铅制镇纸/失物盒）是 EVENT 的延展屏：
    # 复合动作收敛时游戏可能直接进入第一场战斗（issue #57），故与 EVENT 一致
    # 放行 COMBAT。
    GameScreen.BUNDLE_SELECTION: frozenset({
        GameScreen.EVENT, GameScreen.MAP, GameScreen.COMBAT,
    }),
    GameScreen.TRI_SELECT: frozenset({
        GameScreen.EVENT, GameScreen.MAP, GameScreen.COMBAT,
    }),
    GameScreen.CARD_REWARD: frozenset({GameScreen.MAP, GameScreen.COMBAT}),
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


def state_fingerprint(state: dict[str, Any]) -> str:
    """去掉易变字段后的状态指纹：比较业务状态，避免把重复读取误判为进展。

    消费方：journeys（进度发布去重）、navigation（推进判定）、
    run_executor（截图前稳定等待）——历史上各自持有一份逐字相同的拷贝。
    """
    import json

    volatile = {"state_version", "request_id", "timestamp", "updated_at"}
    return json.dumps(
        {key: value for key, value in state.items() if key not in volatile},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
