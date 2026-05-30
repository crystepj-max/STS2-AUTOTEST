"""Test Orchestrator — manages test session lifecycle (FR1, FR2, FR4, FR10-12, FR17)."""

import asyncio
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.common.errors import ErrorCategory, STS2Error
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.action_model import ActionDescriptor, TestResult
from sts2_autotest.core.data_validator import validate_game_state
from sts2_autotest.core.lock_manager import LockManager
from sts2_autotest.core.progress import ProgressRecord, clear_progress, save_progress
from sts2_autotest.core.evidence_hooks import EvidenceHooks, StubEvidenceHooks
from sts2_autotest.common.types import DataValidationSettings, SessionStatus
from sts2_autotest.core.recovery import (
    DefaultRecoveryStrategy,
    FailureRecord,
    RecoveryAction,
    RecoveryDecision,
    RecoveryStrategy,
    crash_signature,
    is_p0_exception,
)
from sts2_autotest.core.steam import SteamController
from sts2_autotest.core.state_engine import StateEngine, StateTransitionError
from sts2_autotest.core.watchdog import Watchdog

logger = get_logger("core.orchestrator")


@dataclass
class SessionSummary:
    """Aggregate results for a completed test session."""

    session_id: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    crashed: int = 0
    skipped: int = 0
    deterministic_fails: int = 0
    results: list[TestResult] = field(default_factory=list)
    resumed_from: str | None = None

    def __post_init__(self) -> None:
        self.total = len(self.results)
        self.passed = sum(1 for r in self.results if r.status == "pass")
        self.failed = sum(1 for r in self.results if r.status == "fail")
        self.crashed = sum(1 for r in self.results if r.status == "crash")
        self.skipped = sum(1 for r in self.results if r.status == "skip")
        self.deterministic_fails = sum(
            1 for r in self.results if r.status == "deterministic_fail"
        )

    @property
    def is_failed(self) -> bool:
        return self.failed > 0 or self.crashed > 0


class TestOrchestrator:
    """Central test session orchestrator.

    Coordinates: SteamController → adapter → StateEngine → test execution.
    All external dependencies are injected for testability.
    """

    __test__ = False

    def __init__(
        self,
        adapter: GameAdapterProtocol,
        state_engine: StateEngine | None = None,
        recovery: RecoveryStrategy | None = None,
        evidence: EvidenceHooks | None = None,
        *,
        adapter_factory: Callable[[], GameAdapterProtocol] | None = None,
        max_consecutive_failures: int = 3,
        game_startup_timeout: float = 60.0,
        heartbeat_timeout: float = 60.0,
        strict_validation: bool = False,
        progress_path: str | None = None,
        resumed_from: str | None = None,
        lock_path: str | None = None,
    ) -> None:
        self.adapter = adapter
        self.state_engine = state_engine or StateEngine()
        self.recovery = recovery or DefaultRecoveryStrategy(
            adapter_factory=adapter_factory,
            game_startup_timeout=game_startup_timeout,
            steam_controller=SteamController(),
        )
        self.evidence = evidence or StubEvidenceHooks()
        self._max_consecutive_failures = max_consecutive_failures
        self._game_startup_timeout = game_startup_timeout
        self._heartbeat_timeout = heartbeat_timeout
        self._session_active = False
        self._crashed = False
        self._session_status = SessionStatus.RUNNING
        self._current_screen: GameScreen = GameScreen.UNKNOWN
        self._failure_history: list[FailureRecord] = []
        self._last_results: list[TestResult] = []
        self._watchdog: Watchdog | None = None
        self._last_valid_state: GameState | None = None
        self._strict_validation = strict_validation
        self._primary_adapter_failures: int = 0
        self._adapter_degraded: bool = False
        self._progress_path: str | None = progress_path
        self._resumed_from: str | None = resumed_from
        self._shutdown_requested: bool = False
        self._pause_requested: bool = False
        self._progress_saved_on_shutdown: bool = False
        self._adapter_replaced: bool = False
        self._lock_path: str | None = lock_path
        self._lock_manager: LockManager | None = (
            LockManager(lock_path) if lock_path else None
        )

    def _release_lock_if_held(self) -> None:
        """Release the process lock if it was acquired."""
        if self._lock_manager is not None:
            self._lock_manager.release_lock()

    # ── state validation ─────────────────────────────────────

    async def _get_state_validated(self) -> GameState:
        """Get game state with semantic validation.

        Wraps adapter.get_state() with validate_game_state().
        In strict mode, violations raise STS2Error.
        In non-strict mode, violations log WARNING and return _last_valid_state.
        """
        state = await self.adapter.get_state()
        violations = validate_game_state(state)

        if not violations:
            self._last_valid_state = state
            return state

        if self._strict_validation:
            raise STS2Error(
                category=ErrorCategory.ASSERTION_ERROR,
                message="Game state validation failed: " + "; ".join(violations),
                detail={"violations": violations, "screen": state.screen.value},
            )

        logger.warning(
            "Game state validation warnings (%s): %s — "
            "returning last valid state",
            state.screen.value, "; ".join(violations),
        )
        if self._last_valid_state is not None:
            return self._last_valid_state
        # No cached state available — return the invalid state anyway
        return state

    async def _auto_reset_to_main_menu(self) -> None:
        """Attempt to reset the game to MAIN_MENU regardless of current state.

        Phase 1: Soft navigation — try return_to_menu first, then screen-specific actions.
        Phase 2: If stuck (e.g. MAP during active run), kill and restart the game.
        """
        # Phase 1: soft navigation
        for attempt in range(5):
            try:
                state = await self.adapter.get_state()
            except STS2Error:
                return

            screen = state.screen
            if screen == GameScreen.MAIN_MENU:
                try:
                    await self.adapter.act("abandon_run")
                except Exception:
                    pass
                return

            # MAP during an active run cannot soft-navigate to MAIN_MENU;
            # skip directly to hard reset to avoid wasting time in loops.
            if screen in {GameScreen.MAP, GameScreen.COMBAT}:
                break

            try:
                actions = await self.adapter.get_available_actions()

                # Always try return_to_menu first if available
                if "return_to_menu" in actions:
                    await self.adapter.act("return_to_menu")
                    await asyncio.sleep(1)
                    continue

                if screen == GameScreen.CHARACTER_SELECT:
                    await self.adapter.act("select_character", {"character_id": "IRONCLAD"})
                    await asyncio.sleep(0.5)
                    await self.adapter.act("embark")
                    await asyncio.sleep(2)
                elif screen == GameScreen.EVENT:
                    event = getattr(state, "event", None)
                    if isinstance(event, dict):
                        proceed_idx = None
                        for opt in event.get("options", []):
                            if isinstance(opt, dict) and opt.get("is_proceed"):
                                proceed_idx = opt.get("index", 0)
                                break
                        if proceed_idx is not None:
                            await self.adapter.act("choose_event", {"index": proceed_idx})
                        else:
                            await self.adapter.act("choose_event", {"index": 0})
                    else:
                        await self.adapter.act("advance_dialogue")
                    await asyncio.sleep(1)
                elif screen == GameScreen.CARD_REWARD:
                    try:
                        await self.adapter.act("reward_claim", {"type": "gold"})
                    except Exception:
                        pass
                    for _ in range(5):
                        try:
                            await self.adapter.act("skip_card_reward")
                        except Exception:
                            break
                        await asyncio.sleep(0.5)
                    try:
                        await self.adapter.act("proceed")
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                elif screen in {
                    GameScreen.RELIC_REWARD, GameScreen.SHOP, GameScreen.REST,
                    GameScreen.CHEST, GameScreen.BOSS_REWARD,
                }:
                    if "proceed" in actions:
                        await self.adapter.act("proceed")
                    elif "combat_basic_policy" in actions:
                        await self.adapter.act("combat_basic_policy")
                    await asyncio.sleep(1)
                else:
                    break
            except Exception:
                await asyncio.sleep(1)

        # Phase 2: hard reset — kill and restart the game
        try:
            state = await self.adapter.get_state()
        except STS2Error:
            return
        if state.screen == GameScreen.MAIN_MENU:
            try:
                await self.adapter.act("abandon_run")
            except Exception:
                pass
            return

        logger.info("Soft navigation failed — restarting game process")
        steam = getattr(self.recovery, "_steam_controller", None)
        if steam is not None:
            try:
                import psutil
                for proc in psutil.process_iter(["pid", "name"]):
                    try:
                        name = proc.info.get("name") or ""
                        if steam.game_exe.lower().replace(" ", "") in name.lower().replace(" ", ""):
                            proc.kill()
                            logger.info("Killed game process PID %s", proc.pid)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                await asyncio.sleep(3)
                steam.start_game()
                for _ in range(30):
                    await asyncio.sleep(2)
                    try:
                        state = await self.adapter.get_state()
                        if state.screen == GameScreen.MAIN_MENU:
                            try:
                                await self.adapter.act("abandon_run")
                            except Exception:
                                pass
                            return
                    except STS2Error:
                        continue
            except Exception as exc:
                logger.warning("Game restart failed: %s", exc)

    async def _record_adapter_result(self, success: bool) -> None:
        """Track adapter call success/failure for degradation detection."""
        if success:
            self._primary_adapter_failures = 0
        else:
            self._primary_adapter_failures += 1
            if self._primary_adapter_failures >= 2 and not self._adapter_degraded:
                self._adapter_degraded = True
                logger.warning(
                    "Primary adapter degraded (%d consecutive failures) — "
                    "degradation logged (MVP: notification only)",
                    self._primary_adapter_failures,
                )

    # ── signal handling ─────────────────────────────────────

    def _register_shutdown_handler(self) -> None:
        """Register SIGINT handler that sets a flag for graceful shutdown.

        The handler only sets a flag — actual cleanup happens in
        the run_all() loop, avoiding I/O in signal context.
        """
        def _on_sigint(signum: int, frame: object) -> None:
            if not self._shutdown_requested:
                logger.warning("SIGINT received — shutting down after current case")
                self._shutdown_requested = True

        try:
            signal.signal(signal.SIGINT, _on_sigint)
        except (ValueError, RuntimeError):
            # Not in main thread or signal already set — skip gracefully
            pass

    async def _save_progress_snapshot(
        self, completed: list[str], pending: list[str],
        current_case: str | None = None,
        *,
        paused: bool = False,
    ) -> None:
        """Save a progress snapshot if progress_path is configured."""
        if self._progress_path is None:
            return
        record = ProgressRecord(
            session_id="session-1",
            completed_cases=completed,
            pending_cases=pending,
            current_case=current_case,
            game_screen=self._current_screen.value,
            paused=paused,
        )
        save_progress(record, Path(self._progress_path))

    def request_pause(self) -> None:
        """Request a safe pause after the current case completes."""
        self._pause_requested = True

    # ── session lifecycle ───────────────────────────────────

    async def start_session(self) -> bool:
        """Start a test session through observable checkpoints (FR1).

        Checkpoints: health_check → state readable → MAIN_MENU reachable
        → available_actions non-empty (MVP uses stub adapter).
        """
        logger.info("Starting test session...")

        # Checkpoint 0: process-level mutex lock
        if self._lock_manager is not None and not self._lock_manager.acquire_lock(timeout=0):
            logger.error("Another session is running — lock file held: %s", self._lock_path)
            return False

        # Checkpoint 1: adapter health (with degradation detection)
        health = await self.adapter.health_check()
        if not health.healthy:
            await self._record_adapter_result(False)
            logger.error("Adapter health check failed: %s", health.message)
            self.evidence.on_session_end({
                "total": 0, "passed": 0, "failed": 0,
                "crashed": 0, "skipped": 0,
                "is_failed": True,
                "degraded": self._adapter_degraded,
            })
            self._release_lock_if_held()
            return False

        # Checkpoint 2: state readable and known (with validation)
        state = await self._get_state_validated()
        if state.screen == GameScreen.UNKNOWN:
            logger.error("Game state is UNKNOWN — cannot proceed")
            self._release_lock_if_held()
            return False

        # Checkpoint 3: ensure clean starting state
        if state.screen != GameScreen.MAIN_MENU:
            logger.warning(
                "Not at MAIN_MENU (currently %s) — attempting auto-reset",
                state.screen.value,
            )
            await self._auto_reset_to_main_menu()
            state = await self._get_state_validated()
            if state.screen == GameScreen.MAIN_MENU:
                logger.info("Auto-reset succeeded — now at MAIN_MENU")
            else:
                logger.error(
                    "Auto-reset did not reach MAIN_MENU (currently %s) — aborting session",
                    state.screen.value,
                )
                self._release_lock_if_held()
                return False

        # Checkpoint 4: game is actionable
        await self.wait_until_actionable(timeout=self._game_startup_timeout)

        self._current_screen = state.screen
        self._session_active = True
        self._crashed = False
        self._session_status = SessionStatus.RUNNING
        self._failure_history.clear()

        # Start watchdog
        game_pid = getattr(self.adapter, 'game_pid', None)
        adapter_pid = getattr(self.adapter, 'adapter_pid', None)
        self._watchdog = Watchdog(
            game_pid=game_pid,
            adapter=self.adapter,
            adapter_pid=adapter_pid,
            heartbeat_timeout=self._heartbeat_timeout,
            on_zombie=self._on_watchdog_zombie,
        )
        await self._watchdog.start_monitoring()

        logger.info("Session started. Screen: %s", self._current_screen.value)
        return True

    async def stop_session(self) -> None:
        """Stop the session, release resources, and report completion status.

        Release order: game adapter cleanup → evidence finalization.
        """
        self._session_active = False

        # Stop watchdog
        if self._watchdog is not None:
            await self._watchdog.stop_monitoring()
            self._watchdog = None

        # Release adapter resources
        try:
            await self.adapter.cleanup()
        except Exception as exc:
            logger.warning("Adapter cleanup failed: %s", exc)

        summary = self._build_summary(self._last_results)
        self.evidence.on_session_end({
            "total": summary.total,
            "passed": summary.passed,
            "failed": summary.failed,
            "crashed": summary.crashed,
            "skipped": summary.skipped,
            "deterministic_fails": summary.deterministic_fails,
            "is_failed": summary.is_failed,
            "resumed_from": self._resumed_from,
        })

        # Release process-level lock
        if self._lock_manager is not None:
            self._lock_manager.release_lock()

        # Clear progress file on normal completion
        if self._progress_path is not None and not self._crashed and not self._progress_saved_on_shutdown:
            clear_progress(Path(self._progress_path))

        logger.info(
            "Session stopped. %d passed, %d failed, %d crashed, %d skipped, "
            "%d deterministic_fail",
            summary.passed, summary.failed, summary.crashed, summary.skipped,
            summary.deterministic_fails,
        )

    # ── execution modes ─────────────────────────────────────

    async def run_all(self, case_ids: list[str]) -> SessionSummary:
        """Run all specified test cases with progress persistence."""
        self._register_shutdown_handler()
        if not await self.start_session():
            return SessionSummary(session_id="failed-start")
        results = []
        completed: list[str] = []
        pending = list(case_ids)

        for case_id in case_ids:
            if self._crashed:
                results.append(TestResult(case_id, "skip", "Session crashed"))
                completed.append(case_id)
                pending = [c for c in pending if c != case_id]
                await self._save_progress_snapshot(completed, pending, case_id)
                continue

            results.append(await self.execute_case(case_id))
            completed.append(case_id)
            pending = [c for c in case_ids if c not in completed]

            # Save progress after each completed case (AC1)
            await self._save_progress_snapshot(completed, pending)

            if self._pause_requested:
                logger.info("Pause requested — saving progress and stopping")
                await self._save_progress_snapshot(completed, pending, paused=True)
                self._progress_saved_on_shutdown = True
                for remaining in pending:
                    results.append(TestResult(remaining, "skip", "Paused by operator"))
                break

            if self._shutdown_requested:
                logger.info("Shutdown requested — saving final progress and stopping")
                await self._save_progress_snapshot(completed, pending)
                self._progress_saved_on_shutdown = True
                logger.warning(
                    "进度已保存，使用 --resume 继续 (Progress saved. Use --resume to continue.)"
                )
                for remaining in pending:
                    results.append(
                        TestResult(remaining, "skip", "Interrupted by SIGINT")
                    )
                break

        self._last_results = results
        await self.stop_session()
        summary = self._build_summary(results)
        summary.resumed_from = getattr(self, "_resumed_from", None)
        return summary

    async def run_cases(self, case_ids: list[str]) -> SessionSummary:
        """Run specific test cases by ID. Delegates to run_all."""
        return await self.run_all(case_ids)

    async def run_failed(
        self, previous_results: list[TestResult] | None = None
    ) -> SessionSummary:
        """Re-run previously failed cases.

        Excludes deterministic_fail cases — their root cause is
        framework/environment issues, re-running is pointless.
        """
        prev = previous_results or self._last_results
        failed_ids = [
            r.case_id for r in prev
            if r.status == "fail" and not r.is_deterministic_fail
        ]
        if not failed_ids:
            logger.info("No failed cases to re-run")
            return SessionSummary(session_id="no-failed")
        return await self.run_all(failed_ids)

    # ── case execution ──────────────────────────────────────

    async def execute_case(self, case_id: str) -> TestResult:
        """Execute a single test case: read state → probe → re-read.

        Per-case cleanup runs in finally to release adapter resources
        between cases (handles, caches, temp state).
        """
        self.evidence.on_case_start(case_id)
        self._adapter_replaced = False
        logger.info("Executing case: %s", case_id)

        try:
            available = await self.adapter.get_available_actions()
            if not available:
                return TestResult(case_id, "fail", "No available actions")

            probe = ActionDescriptor(action_type="probe")
            act_r = await self.execute_action(probe)

            if act_r.status == "failure":
                return TestResult(case_id, "fail", act_r.detail or "Action failed")

            self.evidence.on_case_end(TestResult(case_id, "pass"))
            return TestResult(case_id, "pass")

        except StateTransitionError as exc:
            logger.warning("Case %s: illegal transition — %s", case_id, exc)
            result: TestResult = TestResult(case_id, "fail", str(exc))
            self.evidence.on_case_end(result)
            return result

        except STS2Error as exc:
            return await self._handle_failure(case_id, exc)

        except Exception as exc:
            logger.error("Case %s: unexpected crash — %s", case_id, exc)
            self._crashed = True
            self.evidence.on_crash(case_id, exc)
            result3: TestResult = TestResult(case_id, "crash", str(exc))
            self.evidence.on_case_end(result3)
            return result3

        finally:
            # Per-case adapter cleanup: skip if recovery replaced the adapter
            if not self._adapter_replaced:
                try:
                    await self.adapter.cleanup()
                except Exception as exc:
                    logger.debug("Adapter cleanup after case %s failed: %s", case_id, exc)

    # ── failure handling ────────────────────────────────────

    async def _handle_failure(self, case_id: str, exc: STS2Error) -> TestResult:
        """Centralized failure handler: record → decide → execute recovery.

        Beta: crash errors go through progressive recovery
        (GAME_RESTART -> FULL_RESTART -> TERMINATE) instead of
        immediately terminating.

        Flow:
        1. Record failure in history
        2. Check for P0 session-level exception -> crash immediately
        3. Decide recovery action via RecoveryStrategy
        4. Execute recovery if applicable
        5. On success: clear crash flag + force state CRASHED -> MAIN_MENU
        6. Determine result status (fail/crash/deterministic_fail)
        """
        logger.error(
            "Case %s: %s — %s", case_id, exc.category.value, exc,
        )

        record = FailureRecord(
            error_type=exc.category.value,
            message=exc.message,
            timestamp=exc.timestamp.isoformat(),
            exit_code=exc.detail.get("exit_code") if exc.detail else None,
        )

        # P0 session-level fatal — always crash, never downgrade
        if is_p0_exception(exc):
            self._handle_crash(case_id, exc)
            sig = crash_signature(exc, record.exit_code)
            result = TestResult(case_id, "crash", exc.message, crash_signature=sig)
            self.evidence.on_case_end(result)
            return result

        # Decide recovery action before appending to history,
        # so decide() sees history WITHOUT the current failure
        # and consecutive counts are not off-by-one.
        decision = self.recovery.decide(
            exc,
            self._failure_history,
            max_consecutive=self._max_consecutive_failures,
        )

        # Now append to history — used by _consecutive_same_type below
        self._failure_history.append(record)

        # TERMINATE from decision: P0 → crash, non-P0 → deterministic fail
        if decision.action == RecoveryAction.TERMINATE:
            if decision.is_p0:
                # Safety net: P0 must crash, never become deterministic_fail
                self._handle_crash(case_id, exc)
                result3 = TestResult(case_id, "crash", exc.message)
            else:
                # Non-P0 TERMINATE from consecutive threshold → deterministic fail
                self._crashed = True  # Stop remaining cases
                sig = crash_signature(exc, record.exit_code)
                result3 = TestResult(
                    case_id, "deterministic_fail", exc.message,
                    crash_signature=sig,
                )
            self.evidence.on_case_end(result3)
            return result3

        # Non-P0, non-terminate: attempt recovery (FAST_PATH or RECREATE)
        recovered, new_adapter = await self.recovery.execute(decision.action, self.adapter)

        if recovered and new_adapter is not None:
            self.adapter = new_adapter
            self._adapter_replaced = True
            logger.info(
                "Recovery (action=%s) succeeded for case %s",
                decision.action.value, case_id,
            )

        if recovered:
            # Clear crash flag if previously set
            if self._crashed:
                self._crashed = False
                logger.info("Crash flag cleared after successful recovery")

            # Reset state through CRASHED -> MAIN_MENU
            self._current_screen = self.state_engine.force_transition(
                self._current_screen, GameScreen.CRASHED,
            )
            await self.wait_until_actionable(timeout=self._game_startup_timeout)
            self._current_screen = self.state_engine.force_transition(
                GameScreen.CRASHED, GameScreen.MAIN_MENU,
            )
            logger.info(
                "Recovery succeeded for case %s — state reset to MAIN_MENU", case_id,
            )

        # Check consecutive failures for deterministic fail
        consecutive = self._consecutive_same_type(record.error_type)
        if consecutive >= self._max_consecutive_failures:
            self._crashed = True  # Stop remaining cases
            sig = crash_signature(exc, record.exit_code)
            result4 = TestResult(
                case_id, "deterministic_fail", exc.message,
                crash_signature=sig,
            )
            self.evidence.on_case_end(result4)
            return result4

        result5 = TestResult(case_id, "fail", exc.message)
        self.evidence.on_case_end(result5)
        return result5

    def _consecutive_same_type(self, error_type: str) -> int:
        """Count consecutive failures of the same type from history end."""
        count = 0
        for record in reversed(self._failure_history):
            if record.error_type == error_type:
                count += 1
            else:
                break
        return count

    # ── crash handling ──────────────────────────────────────

    def _handle_crash(self, case_id: str, error: Exception) -> None:
        """Handle game crash (MVP: record + stop, FR36)."""
        logger.error("CRASH during case %s: %s", case_id, error)
        self._crashed = True
        self.evidence.on_crash(case_id, error)

    def _on_watchdog_zombie(self, reason: str) -> None:
        """Callback invoked when watchdog detects a zombie session."""
        logger.critical("Watchdog zombie detected: %s", reason)
        self._session_status = SessionStatus.ZOMBIE
        self._crashed = True
        self.evidence.on_crash("__watchdog__", Exception(f"Zombie: {reason}"))

    # ── wait until actionable ───────────────────────────────

    async def wait_until_actionable(self, timeout: float = 30.0) -> bool:
        """Wait for the game to become fully actionable (FR12).

        Checks: adapter health + available_actions non-empty,
        then delegates to adapter-level wait.
        """
        health = await self.adapter.health_check()
        if not health.healthy:
            logger.warning("Adapter not healthy: %s", health.message)
            return False
        available = await self.adapter.get_available_actions()
        if not available:
            logger.info("No actions available yet, delegating to adapter wait...")
        return await self.adapter.wait_until_actionable(timeout)

    # ── action execution (single public path, FR10-FR11) ────

    async def execute_action(self, action: ActionDescriptor) -> ActionResult:
        """Execute an action with full validation and state tracking.

        Read state → check available_actions → execute → check result
        → re-read state → validate transition → check expected_state.
        This is the ONLY action execution path — all code paths use it.
        """
        # 1. Pre-read state (with validation)
        pre_state = await self._get_state_validated()
        self._current_screen = pre_state.screen

        # 2. Validate action in available set (empty = nothing available)
        available = await self.adapter.get_available_actions()
        if action.action_type not in available:
            await self._record_adapter_result(True)
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Action '{action.action_type}' not available. "
                        f"Available: {available}",
                detail={"action": action.action_type, "available": available},
            )

        # 3. Execute
        result = await self.adapter.act(action.action_type, action.params)

        # 4. Check ActionResult
        if result.status == "timeout":
            await self._record_adapter_result(False)
            raise STS2Error(
                category=ErrorCategory.TIMEOUT_ERROR,
                message=f"Action '{action.action_type}' timed out",
                detail={"action": action.action_type, "result_detail": result.detail},
            )
        if result.status == "failure":
            await self._record_adapter_result(False)
            raise STS2Error(
                category=ErrorCategory.GAME_ERROR,
                message=f"Action '{action.action_type}' failed: {result.detail}",
                detail={"action": action.action_type, "result_detail": result.detail},
            )

        # Record heartbeat on successful adapter call
        if self._watchdog is not None:
            self._watchdog.record_heartbeat()

        # 5. Re-read state and validate transition (with validation)
        new_state = await self._get_state_validated()
        self._current_screen = self.state_engine.update_state(
            self._current_screen,
            new_state.screen.value,
            event=action.action_type,
        )

        # 6. Verify expected_state if specified
        if action.expected_state is not None and self._current_screen != action.expected_state:
            raise STS2Error(
                category=ErrorCategory.GAME_ERROR,
                message=(
                    f"Action '{action.action_type}' expected state "
                    f"{action.expected_state.value} but reached "
                    f"{self._current_screen.value}"
                ),
                detail={
                    "action": action.action_type,
                    "expected_state": action.expected_state.value,
                    "actual_state": self._current_screen.value,
                },
            )

        return result

    async def execute_action_sequence(
        self, actions: list[ActionDescriptor]
    ) -> list[ActionResult]:
        """Execute a sequence of actions with wait + re-read between each."""
        results: list[ActionResult] = []
        for i, action in enumerate(actions):
            actionable = await self.adapter.wait_until_actionable(action.timeout)
            if not actionable:
                raise STS2Error(
                    category=ErrorCategory.TIMEOUT_ERROR,
                    message=f"Game not actionable before action {i}: {action.action_type}",
                    detail={"action_index": i, "action": action.action_type},
                )
            results.append(await self.execute_action(action))
        return results

    def _build_summary(self, results: list[TestResult]) -> SessionSummary:
        return SessionSummary(
            session_id="session-1",
            results=results,
            resumed_from=self._resumed_from,
        )
