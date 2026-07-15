"""验证会话中枢会把假战斗和旅行无进展交给恢复层。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from sts2_autotest.adapters.base import GameAdapterProtocol, HealthStatus
from sts2_autotest.common.errors import ErrorCategory, STS2Error
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.orchestrator import TestOrchestrator


class _Lifecycle:
    def __init__(self, *, phantom: bool = False, hung: bool = False) -> None:
        self.phantom = phantom
        self.hung = hung

    def is_phantom_combat(self, state: dict[str, object]) -> bool:
        return self.phantom

    def travel_hang_expired(
        self, state: dict[str, object], traveling_since: float | None, now: float | None = None
    ) -> bool:
        return self.hung


def _adapter(state: GameState) -> MagicMock:
    adapter = MagicMock(spec=GameAdapterProtocol)
    adapter.get_state = AsyncMock(return_value=state)
    adapter.health_check = AsyncMock(return_value=HealthStatus(healthy=True))
    return adapter


def test_phantom_combat_is_classified_as_crash_signal() -> None:
    orch = TestOrchestrator(
        adapter=_adapter(GameState(screen=GameScreen.COMBAT)),
        lifecycle=_Lifecycle(phantom=True),
    )
    with pytest.raises(STS2Error) as caught:
        asyncio.run(orch._get_state_validated())
    assert caught.value.category == ErrorCategory.CRASH_ERROR


def test_travel_hang_is_classified_as_timeout_signal() -> None:
    orch = TestOrchestrator(
        adapter=_adapter(
            GameState(
                screen=GameScreen.MAP,
                map={"is_traveling": True, "available_nodes": []},
            )
        ),
        lifecycle=_Lifecycle(hung=True),
    )
    with pytest.raises(STS2Error) as caught:
        asyncio.run(orch._get_state_validated())
    assert caught.value.category == ErrorCategory.TIMEOUT_ERROR
