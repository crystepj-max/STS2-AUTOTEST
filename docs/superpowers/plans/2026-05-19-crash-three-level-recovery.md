# B1 崩溃三级恢复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade MVP crash handling (terminate on first crash) to Beta three-level recovery: restart game → restart Steam+game → stop retry with deterministic fail, then continue to next case.

**Architecture:** Extend `DefaultRecoveryStrategy` by adding `GAME_RESTART` and `FULL_RESTART` actions to `RecoveryAction` enum, and wire a `SteamController` into the strategy so `execute()` can restart actual processes. In the orchestrator, route crash errors through the same recover-or-terminate path as adapter errors (instead of immediately terminating), and clear the `_crashed` flag on successful recovery.

**Tech Stack:** Python 3.11+, core/recovery.py, core/steam.py, core/orchestrator.py, core/progress.py

---

### Task 1: Extend RecoveryAction enum and DefaultRecoveryStrategy

**Files:**
- Modify: `src/sts2_autotest/core/recovery.py`
- Test: `tests/unit/test_recovery_strategy.py`

- [ ] **Step 1.1: Add GAME_RESTART and FULL_RESTART to RecoveryAction**

```python
class RecoveryAction(StrEnum):
    """Recovery action decided by RecoveryStrategy."""

    FAST_PATH = "FAST_PATH"       # health_check + reconnect (<2s)
    RECREATE = "RECREATE"         # new adapter instance (≤10s)
    GAME_RESTART = "GAME_RESTART" # kill game → start game → recreate adapter
    FULL_RESTART = "FULL_RESTART" # kill game+Steam → start Steam → start game → recreate adapter
    TERMINATE = "TERMINATE"       # stop retry, mark deterministic fail
```

- [ ] **Step 1.2: Wire SteamController into DefaultRecoveryStrategy constructor**

Add `steam_controller` parameter:

```python
class DefaultRecoveryStrategy:
    def __init__(
        self,
        *,
        adapter_factory: Callable[[], GameAdapterProtocol] | None = None,
        game_startup_timeout: float = 60.0,
        steam_controller: Any = None,  # core.steam.SteamController, typed as Any to avoid import violation
    ) -> None:
        self._adapter_factory = adapter_factory
        self._game_startup_timeout = game_startup_timeout
        self._steam_controller = steam_controller
```

Note: Use `Any` type to avoid importing `core.steam` (sibling package import violation). The caller injects the SteamController at construction time.

- [ ] **Step 1.3: Update `decide()` for crash progressive levels**

The current decide() treats crash errors generically. Add explicit progressive level logic: 1st crash → GAME_RESTART, 2nd consecutive → FULL_RESTART, 3rd → TERMINATE.

```python
def decide(
    self,
    failure: Exception,
    history: list[FailureRecord],
    *,
    max_consecutive: int = 3,
) -> RecoveryDecision:
    # P0: session-level fatal
    p0 = is_p0_exception(failure)
    if p0:
        return RecoveryDecision(action=RecoveryAction.TERMINATE, is_p0=True)

    # Progressive levels for CRASH_ERROR
    if isinstance(failure, STS2Error) and failure.category == ErrorCategory.CRASH_ERROR:
        return self._decide_crash(history, max_consecutive)

    # Fast-path categories
    if isinstance(failure, STS2Error):
        if failure.category in _FAST_PATH_CATEGORIES:
            action = self._check_consecutive(history, max_consecutive)
            return RecoveryDecision(action=action, is_p0=False)

    # Other adapter errors
    action = self._check_consecutive(history, max_consecutive)
    return RecoveryDecision(action=action, is_p0=False)


def _decide_crash(
    self,
    history: list[FailureRecord],
    max_consecutive: int,
) -> RecoveryDecision:
    """Decide crash recovery level based on consecutive crash history.

    1st crash → GAME_RESTART
    2nd consecutive crash → FULL_RESTART
    3rd consecutive crash → TERMINATE
    """
    if not history:
        return RecoveryDecision(action=RecoveryAction.GAME_RESTART)
    last_type = history[-1].error_type
    consecutive = self._consecutive_count(history, last_type)
    if consecutive >= max_consecutive:
        return RecoveryDecision(action=RecoveryAction.TERMINATE)
    if consecutive >= max_consecutive - 1:
        return RecoveryDecision(action=RecoveryAction.FULL_RESTART)
    return RecoveryDecision(action=RecoveryAction.GAME_RESTART)
```

- [ ] **Step 1.4: Update `execute()` to handle GAME_RESTART and FULL_RESTART**

The `execute()` method currently handles FAST_PATH, RECREATE, and TERMINATE. Add cases for GAME_RESTART and FULL_RESTART:

```python
async def execute(
    self,
    action: RecoveryAction,
    adapter: GameAdapterProtocol,
) -> tuple[bool, GameAdapterProtocol | None]:
    if action == RecoveryAction.FAST_PATH:
        return await self._execute_fast_path(adapter), None
    if action == RecoveryAction.RECREATE:
        return await self._execute_recreate(adapter)
    if action == RecoveryAction.GAME_RESTART:
        return await self._execute_game_restart(adapter)
    if action == RecoveryAction.FULL_RESTART:
        return await self._execute_full_restart(adapter)
    # TERMINATE
    logger.info("Recovery action: TERMINATE — recording artifacts")
    return False, None


async def _execute_game_restart(
    self, adapter: GameAdapterProtocol,
) -> tuple[bool, GameAdapterProtocol | None]:
    """Level 1: restart game → recreate adapter → health check."""
    if self._steam_controller is None:
        logger.warning("GAME_RESTART: no steam_controller — falling back to RECREATE")
        return await self._execute_recreate(adapter)

    logger.info("GAME_RESTART: terminating game and restarting...")
    try:
        self._steam_controller.restart_game()
    except Exception as exc:
        logger.error("GAME_RESTART: restart_game failed: %s", exc)
        return False, None

    # Wait for game to be ready, then recreate adapter
    return await self._execute_recreate(adapter)


async def _execute_full_restart(
    self, adapter: GameAdapterProtocol,
) -> tuple[bool, GameAdapterProtocol | None]:
    """Level 2: restart Steam + game → recreate adapter → health check."""
    if self._steam_controller is None:
        logger.warning("FULL_RESTART: no steam_controller — falling back to RECREATE")
        return await self._execute_recreate(adapter)

    logger.info("FULL_RESTART: terminating Steam and game, then restarting...")
    try:
        self._steam_controller.stop_game()
        self._steam_controller.stop_steam()
        self._steam_controller.start_steam()
        self._steam_controller.start_game()
    except Exception as exc:
        logger.error("FULL_RESTART: restart failed: %s", exc)
        return False, None

    return await self._execute_recreate(adapter)
```

- [ ] **Step 1.5: Write tests for crash recovery levels**

Add to `tests/unit/test_recovery_strategy.py`:

```python
class TestDecideCrashLevels:
    """Progressive crash recovery: GAME_RESTART → FULL_RESTART → TERMINATE."""

    def make_history(self, error_type: str, count: int) -> list[FailureRecord]:
        return [
            FailureRecord(error_type=error_type, message="test", timestamp="now")
            for _ in range(count)
        ]

    def test_first_crash_returns_game_restart(self) -> None:
        strategy = DefaultRecoveryStrategy()
        error = STS2Error(category=ErrorCategory.CRASH_ERROR, message="game crashed")
        decision = strategy.decide(error, [])
        assert decision.action == RecoveryAction.GAME_RESTART
        assert decision.is_p0 is False

    def test_second_consecutive_crash_returns_full_restart(self) -> None:
        strategy = DefaultRecoveryStrategy()
        error = STS2Error(category=ErrorCategory.CRASH_ERROR, message="game crashed again")
        history = self.make_history("crash_error", 1)
        decision = strategy.decide(error, history)
        assert decision.action == RecoveryAction.FULL_RESTART

    def test_third_consecutive_crash_returns_terminate(self) -> None:
        strategy = DefaultRecoveryStrategy()
        error = STS2Error(category=ErrorCategory.CRASH_ERROR, message="game crashed x3")
        history = self.make_history("crash_error", 2)
        decision = strategy.decide(error, history)
        assert decision.action == RecoveryAction.TERMINATE
```

```python
class TestExecuteGameRestart:
    """GAME_RESTART calls steam_controller.restart_game() then recreates adapter."""

    async def test_game_restart_success(self) -> None:
        mock_steam = MagicMock()
        mock_steam.restart_game = AsyncMock(return_value=12345)
        mock_factory = MagicMock(return_value=MagicMock(spec=GameAdapterProtocol))
        strategy = DefaultRecoveryStrategy(
            adapter_factory=mock_factory,
            steam_controller=mock_steam,
        )
        old_adapter = MagicMock(spec=GameAdapterProtocol)

        success, new_adapter = await strategy.execute(RecoveryAction.GAME_RESTART, old_adapter)

        assert success is True
        assert new_adapter is not None
        mock_steam.restart_game.assert_called_once()

    async def test_game_restart_no_steam_controller_falls_back(self) -> None:
        mock_factory = MagicMock(return_value=MagicMock(spec=GameAdapterProtocol))
        strategy = DefaultRecoveryStrategy(adapter_factory=mock_factory)
        old_adapter = MagicMock(spec=GameAdapterProtocol)

        success, new_adapter = await strategy.execute(RecoveryAction.GAME_RESTART, old_adapter)

        assert success is True  # Falls back to RECREATE
        assert new_adapter is not None
```

```python
class TestExecuteFullRestart:
    """FULL_RESTART stops game+Steam, starts Steam+game, recreates adapter."""

    async def test_full_restart_success(self) -> None:
        mock_steam = MagicMock()
        mock_steam.stop_game = MagicMock()
        mock_steam.stop_steam = MagicMock()
        mock_steam.start_steam = MagicMock(return_value=9999)
        mock_steam.start_game = MagicMock(return_value=12345)
        mock_factory = MagicMock(return_value=MagicMock(spec=GameAdapterProtocol))
        strategy = DefaultRecoveryStrategy(
            adapter_factory=mock_factory,
            steam_controller=mock_steam,
        )
        old_adapter = MagicMock(spec=GameAdapterProtocol)

        success, new_adapter = await strategy.execute(RecoveryAction.FULL_RESTART, old_adapter)

        assert success is True
        mock_steam.stop_game.assert_called_once()
        mock_steam.stop_steam.assert_called_once()
        mock_steam.start_steam.assert_called_once()
        mock_steam.start_game.assert_called_once()
```

- [ ] **Step 1.6: Run tests and commit**

```bash
python -m pytest tests/unit/test_recovery_strategy.py -v --tb=short
lint-imports
mypy src/sts2_autotest --strict
git add src/sts2_autotest/core/recovery.py tests/unit/test_recovery_strategy.py
git commit -m "feat: add GAME_RESTART/FULL_RESTART recovery actions for crash recovery"
```

---

### Task 2: Update orchestrator to route crashes through recovery

**Files:**
- Modify: `src/sts2_autotest/core/orchestrator.py`
- Test: `tests/unit/test_orchestrator.py`

- [ ] **Step 2.1: Update `_handle_failure` to route crashes through recovery**

The key change: instead of immediately returning crash result for CRASH_ERROR, route through the same recovery path as other errors.

```python
async def _handle_failure(self, case_id: str, exc: STS2Error) -> TestResult:
    """Centralized failure handler: record → decide → execute recovery.

    Beta: crash errors go through recovery (GAME_RESTART → FULL_RESTART → TERMINATE).
    """
    logger.error("Case %s: %s — %s", case_id, exc.category.value, exc)

    record = FailureRecord(
        error_type=exc.category.value,
        message=exc.message,
        timestamp=exc.timestamp.isoformat(),
        exit_code=exc.detail.get("exit_code") if exc.detail else None,
    )
    self._failure_history.append(record)

    # P0 session-level fatal — always crash, never downgrade
    if is_p0_exception(exc):
        self._handle_crash(case_id, exc)
        sig = crash_signature(exc, record.exit_code)
        result = TestResult(case_id, "crash", exc.message, crash_signature=sig)
        self.evidence.on_case_end(result)
        return result

    # Decide recovery action (includes crash levels via decide())
    decision = self.recovery.decide(
        exc,
        self._failure_history,
        max_consecutive=self._max_consecutive_failures,
    )

    # TERMINATE from decision
    if decision.action == RecoveryAction.TERMINATE:
        if decision.is_p0:
            self._handle_crash(case_id, exc)
            result = TestResult(case_id, "crash", exc.message)
        else:
            sig = crash_signature(exc, record.exit_code)
            result = TestResult(
                case_id, "deterministic_fail", exc.message,
                crash_signature=sig,
            )
        self.evidence.on_case_end(result)
        return result

    # Attempt recovery (FAST_PATH, RECREATE, GAME_RESTART, or FULL_RESTART)
    recovered, new_adapter = await self.recovery.execute(decision.action, self.adapter)

    if recovered and new_adapter is not None:
        self.adapter = new_adapter
        self._adapter_replaced = True
        logger.info("Recovery succeeded for case %s (action=%s)", case_id, decision.action.value)

    if recovered:
        # Clear crash flag if we recovered
        if self._crashed:
            self._crashed = False
            logger.info("Crash flag cleared after successful recovery")

        # Reset state through CRASHED → MAIN_MENU
        self._current_screen = self.state_engine.force_transition(
            self._current_screen, GameScreen.CRASHED,
        )
        await self.wait_until_actionable(timeout=self._game_startup_timeout)
        self._current_screen = self.state_engine.force_transition(
            GameScreen.CRASHED, GameScreen.MAIN_MENU,
        )
        logger.info("Recovery succeeded — state reset to MAIN_MENU")

    # Check consecutive failures for deterministic fail
    consecutive = self._consecutive_same_type(record.error_type)
    if consecutive >= self._max_consecutive_failures:
        sig = crash_signature(exc, record.exit_code)
        result = TestResult(
            case_id, "deterministic_fail", exc.message,
            crash_signature=sig,
        )
        self.evidence.on_case_end(result)
        return result

    result = TestResult(case_id, "fail", exc.message)
    self.evidence.on_case_end(result)
    return result
```

Key differences from current code:
1. Removed the early return for `is_crash` (lines 470-474 in original)
2. All errors (including CRASH_ERROR) now go through `recovery.decide()`
3. After successful recovery, clear `_crashed` flag
4. The `_decide_crash` method handles progressive levels

- [ ] **Step 2.2: Pass SteamController from CLI to orchestrator**

In `src/sts2_autotest/cli/main.py`, the `_run_orchestrator_with_adapter` function creates the orchestrator. We need to create a `SteamController` and pass it through to the `DefaultRecoveryStrategy`:

```python
def _run_orchestrator_with_adapter(
    adapter: GameAdapterProtocol,
    case_ids: list[str],
    timeout: int,
    *,
    progress_path: str | None = None,
    resumed_from: str | None = None,
) -> int:
    from sts2_autotest.core.orchestrator import TestOrchestrator
    from sts2_autotest.core.steam import SteamController

    steam = SteamController(startup_timeout=60.0)
    recovery = DefaultRecoveryStrategy(
        adapter_factory=lambda: CliModAdapter(),
        game_startup_timeout=60.0,
        steam_controller=steam,
    )

    orch = TestOrchestrator(
        adapter=adapter,
        recovery=recovery,
        progress_path=progress_path,
        resumed_from=resumed_from,
    )
    ...
```

Add import for `DefaultRecoveryStrategy` and `SteamController` at the top of the function (local imports to avoid circular deps).

- [ ] **Step 2.3: Write tests for crash recovery in orchestrator**

Add to `tests/unit/test_orchestrator.py` in `TestCrashHandling` class:

```python
class TestCrashHandling:
    """Beta: crash errors go through recovery instead of immediately terminating."""

    async def test_crash_routes_through_recovery(self) -> None:
        """CRASH_ERROR should attempt recovery, not immediately terminate."""
        mock_adapter = MagicMock(spec=GameAdapterProtocol)
        mock_adapter.get_available_actions = AsyncMock(return_value=["probe"])
        mock_adapter.act = AsyncMock(return_value=ActionResult("success", True))
        mock_adapter.health_check = AsyncMock(return_value=HealthStatus(True))
        mock_adapter.get_state = AsyncMock(return_value=GameState(screen=GameScreen.COMBAT))
        mock_adapter.wait_until_actionable = AsyncMock(return_value=True)

        # Use a real recovery strategy that responds to crashes
        strategy = DefaultRecoveryStrategy()
        orch = TestOrchestrator(
            adapter=mock_adapter,
            recovery=strategy,
        )

        crash = STS2Error(category=ErrorCategory.CRASH_ERROR, message="game crashed")
        result = await orch._handle_failure("TC-001", crash)

        # Should NOT be immediately crash — goes through decide()
        # First crash → GAME_RESTART → execute returns (False, None) since no steam_controller
        assert result.status in ("fail", "deterministic_fail")
```

- [ ] **Step 2.4: Run full verification**

```bash
python -m pytest tests/unit/test_orchestrator.py tests/unit/test_recovery_strategy.py -v --tb=short
lint-imports
mypy src/sts2_autotest --strict
```

- [ ] **Step 2.5: Commit**

```bash
git add src/sts2_autotest/core/orchestrator.py src/sts2_autotest/cli/main.py tests/unit/test_orchestrator.py
git commit -m "feat: route crash errors through progressive three-level recovery"
```

---

### Task 3: Integration verification — crash recovery loop

**Files:**
- Modify: None (verification only)

- [ ] **Step 3.1: Run complete verification**

```bash
lint-imports
mypy src/sts2_autotest --strict
python -m pytest tests/unit/ -q --tb=no --no-header
```

Expected:
- lint-imports: 0 violations
- mypy: 0 errors
- Unit tests: all existing + new tests pass

- [ ] **Step 3.2: Manual verification checklist**
- [ ] `DefaultRecoveryStrategy` accepts steam_controller and uses it for GAME_RESTART/FULL_RESTART
- [ ] `decide()` returns GAME_RESTART → FULL_RESTART → TERMINATE for consecutive crashes
- [ ] Orchestrator does NOT immediately terminate on CRASH_ERROR — routes through recovery
- [ ] After successful recovery, `_crashed` flag is cleared and next cases continue
- [ ] After all 3 levels exhausted, remaining cases skip with deterministic_fail
- [ ] No steam_controller → GAME_RESTART/FULL_RESTART fall back to RECREATE gracefully

- [ ] **Step 3.3: Commit if any final fixes**

```bash
git add -A
git commit -m "fix: final fixes before crash recovery merge"
```

---

## Summary of Files

| Action | File | Purpose |
|--------|------|---------|
| Modify | `src/sts2_autotest/core/recovery.py` | Add GAME_RESTART/FULL_RESTART actions, _decide_crash, execute handlers |
| Modify | `src/sts2_autotest/core/orchestrator.py` | Route crashes through recovery path, clear _crashed flag on success |
| Modify | `src/sts2_autotest/cli/main.py` | Wire SteamController into DefaultRecoveryStrategy |
| Modify | `tests/unit/test_recovery_strategy.py` | Tests for new recovery levels |
| Modify | `tests/unit/test_orchestrator.py` | Tests for crash recovery in orchestrator |
