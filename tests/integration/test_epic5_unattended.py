"""Epic 5 unattended runtime validation with a mock adapter."""

from __future__ import annotations

import asyncio
import itertools
import time
from typing import Any
from unittest.mock import MagicMock

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.orchestrator import TestOrchestrator


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_mock_adapter() -> Any:
    states = itertools.cycle([
        GameState(screen=GameScreen.MAIN_MENU),
        GameState(screen=GameScreen.MAP),
        GameState(screen=GameScreen.COMBAT),
    ])
    mock = MagicMock(spec=GameAdapterProtocol)
    mock.health_check.return_value = HealthStatus(healthy=True)
    mock.get_state.side_effect = lambda: next(states)
    mock.get_available_actions.return_value = ["probe", "play_card", "end_turn"]
    mock.act.return_value = ActionResult(status="success", state_changed=True)
    mock.wait_until_actionable.return_value = True
    mock.capture_bug_snapshot.return_value = {}
    return mock


def test_unattended_mock_run_has_no_framework_crash() -> None:
    started = time.monotonic()
    orch = TestOrchestrator(adapter=_make_mock_adapter())

    summary = _run(orch.run_all([f"TC-{i:03d}" for i in range(20)]))
    elapsed = time.monotonic() - started

    assert summary.total == 20
    assert summary.crashed == 0
    assert elapsed < 60.0
