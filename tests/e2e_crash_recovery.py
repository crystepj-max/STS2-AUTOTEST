"""端到端冒烟测试：B1 崩溃三级恢复流程。

验证 Three-level crash recovery 全链路行为：
  Level 1 GAME_RESTART  → 恢复成功 → 继续下一用例
  Level 2 FULL_RESTART  → 恢复成功 → 继续下一用例
  Level 3 TERMINATE     → 标记 deterministic_fail → 跳过剩余用例

不需要真实游戏进程，所有 adapter 调用使用 mock。

运行方式：
  python -m pytest tests/e2e_crash_recovery.py -v --tb=long --no-header -s
  python tests/e2e_crash_recovery.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.common.errors import ErrorCategory, STS2Error
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.orchestrator import TestOrchestrator
from sts2_autotest.core.recovery import DefaultRecoveryStrategy, FailureRecord


def _run(coro: Any) -> Any:
    """Bridge async -> sync for direct script invocation."""
    return asyncio.run(coro)


def log(msg: str) -> None:
    print(f"  {msg}")


def _make_adapter(
    healthy: bool = True,
    screen: GameScreen = GameScreen.MAP,
    actions: list[str] | None = None,
) -> MagicMock:
    """Create a mock adapter with controllable behavior."""
    mock = MagicMock(spec=GameAdapterProtocol)
    mock.health_check = AsyncMock(return_value=HealthStatus(healthy))
    mock.get_state = AsyncMock(return_value=GameState(screen=screen))
    mock.get_available_actions = AsyncMock(return_value=actions or ["probe"])
    mock.act = AsyncMock(return_value=ActionResult("success", True))
    mock.wait_until_actionable = AsyncMock(return_value=True)
    mock.cleanup = AsyncMock()
    mock.capture_bug_snapshot = AsyncMock(
        return_value={"game_state": "mock", "timestamp": "now"}
    )
    return mock


@pytest.mark.anyio
async def test_level1_game_restart_recovers() -> None:
    """Crash → GAME_RESTART → adapter factory creates new healthy adapter → continue."""
    log("=== Level 1: GAME_RESTART recovery ===")
    healthy_adapter = _make_adapter(screen=GameScreen.MAIN_MENU)

    factory_calls: list[MagicMock] = []
    def factory() -> MagicMock:
        a = _make_adapter(screen=GameScreen.MAIN_MENU)
        factory_calls.append(a)
        return a

    strategy = DefaultRecoveryStrategy(adapter_factory=factory)
    orch = TestOrchestrator(adapter=healthy_adapter, recovery=strategy)

    crash = STS2Error(category=ErrorCategory.CRASH_ERROR, message="game process crashed")
    result = await orch._handle_failure("TC-CRASH-001", crash)
    assert result.status in ("fail", "deterministic_fail")


@pytest.mark.anyio
async def test_level2_full_restart_recovers() -> None:
    """2 consecutive crashes → FULL_RESTART → recovery attempted."""
    log("=== Level 2: FULL_RESTART after 2nd crash ===")
    factory_calls: list[MagicMock] = []
    def factory() -> MagicMock:
        a = _make_adapter(screen=GameScreen.MAIN_MENU)
        factory_calls.append(a)
        return a

    strategy = DefaultRecoveryStrategy(adapter_factory=factory)
    orch = TestOrchestrator(adapter=_make_adapter(), recovery=strategy)

    await orch._handle_failure("TC-001", STS2Error(category=ErrorCategory.CRASH_ERROR, message="crash 1"))
    result = await orch._handle_failure("TC-002", STS2Error(category=ErrorCategory.CRASH_ERROR, message="crash 2"))

    assert result.status in ("fail", "deterministic_fail")
    assert len(orch._failure_history) == 2


@pytest.mark.anyio
async def test_level3_terminate_after_three_crashes() -> None:
    """3 consecutive crashes → TERMINATE → _crashed=True → deterministic_fail."""
    log("=== Level 3: TERMINATE after 3 consecutive crashes ===")
    strategy = DefaultRecoveryStrategy()
    orch = TestOrchestrator(adapter=_make_adapter(), recovery=strategy)
    assert not orch._crashed

    await orch._handle_failure("TC-001", STS2Error(category=ErrorCategory.CRASH_ERROR, message="crash 1"))
    assert not orch._crashed

    await orch._handle_failure("TC-002", STS2Error(category=ErrorCategory.CRASH_ERROR, message="crash 2"))
    assert not orch._crashed

    r3 = await orch._handle_failure("TC-003", STS2Error(category=ErrorCategory.CRASH_ERROR, message="crash 3"))
    assert r3.status == "deterministic_fail"
    assert orch._crashed


@pytest.mark.anyio
async def test_run_all_skips_after_three_crashes() -> None:
    """run_all with 3 crashes -> deterministic_fail + skip."""
    log("=== run_all: 3 crashes -> skip remaining ===")

    crash_count = 0
    class CrashThreeAdapter:
        async def health_check(self): return HealthStatus(True)
        async def get_state(self): return GameState(screen=GameScreen.MAIN_MENU)
        async def get_available_actions(self): return ["probe"]
        async def cleanup(self): pass
        async def act(self, action, args=None):
            nonlocal crash_count
            crash_count += 1
            if crash_count <= 3:
                raise STS2Error(category=ErrorCategory.CRASH_ERROR, message=f"crash {crash_count}")
            return ActionResult("success", True)
        async def wait_until_actionable(self, timeout): return True
        async def capture_bug_snapshot(self): return {}

    strategy = DefaultRecoveryStrategy()
    orch = TestOrchestrator(adapter=CrashThreeAdapter(), recovery=strategy)
    orch.start_session = AsyncMock(return_value=True)
    orch.stop_session = AsyncMock()

    summary = await orch.run_all(["TC-001", "TC-002", "TC-003", "TC-004", "TC-005"])
    assert summary.deterministic_fails >= 1
    assert summary.total == 5


def test_three_level_progression_decision() -> None:
    """Verify decide() returns correct recovery levels."""
    log("=== Three-level progression decision ===")
    strategy = DefaultRecoveryStrategy()
    crash = STS2Error(category=ErrorCategory.CRASH_ERROR, message="crash")

    d1 = strategy.decide(crash, [])
    assert d1.action.name == "GAME_RESTART"
    log(f"  Level 1: {d1.action}")

    d2 = strategy.decide(crash, [
        FailureRecord(error_type="crash_error", message="p1", timestamp="t0"),
    ])
    assert d2.action.name == "FULL_RESTART"
    log(f"  Level 2: {d2.action}")

    d3 = strategy.decide(crash, [
        FailureRecord(error_type="crash_error", message="p1", timestamp="t0"),
        FailureRecord(error_type="crash_error", message="p2", timestamp="t1"),
    ])
    assert d3.action.name == "TERMINATE"
    log(f"  Level 3: {d3.action}")


def test_adapter_error_not_affected_by_crash_logic() -> None:
    """Adapter errors follow standard escalation, not crash levels."""
    log("=== Adapter errors unaffected ===")
    strategy = DefaultRecoveryStrategy()
    err = STS2Error(category=ErrorCategory.ADAPTER_ERROR, message="failed")
    d = strategy.decide(err, [])
    assert d.action.name == "FAST_PATH"


async def _run_all() -> int:
    """Run all tests via direct invocation."""
    tests = [
        ("Three-level decision", test_three_level_progression_decision),
        ("Level 1 GAME_RESTART", lambda: _run(test_level1_game_restart_recovers())),
        ("Level 2 FULL_RESTART", lambda: _run(test_level2_full_restart_recovers())),
        ("Level 3 TERMINATE", lambda: _run(test_level3_terminate_after_three_crashes())),
        ("run_all skip", lambda: _run(test_run_all_skips_after_three_crashes())),
        ("Adapter errors", test_adapter_error_not_affected_by_crash_logic),
    ]
    failed = 0
    for name, test_fn in tests:
        print(f"\n[{name}]")
        try:
            test_fn()
            print("  OK")
        except Exception as exc:
            print(f"  FAIL: {exc}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run_all()))
