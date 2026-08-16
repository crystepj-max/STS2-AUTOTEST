"""Test Orchestrator — manages test session lifecycle (FR1, FR2, FR4, FR10-12, FR17)."""

import asyncio
import json
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol
from sts2_autotest.common.errors import ErrorCategory, STS2Error
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.common.types import SessionStatus
from sts2_autotest.core.action_model import ActionDescriptor, TestResult
from sts2_autotest.core.data_validator import validate_game_state
from sts2_autotest.core.evidence_hooks import EvidenceHooks, StubEvidenceHooks
from sts2_autotest.core.lock_manager import LockManager
from sts2_autotest.core.navigation import NavigationBlocked, progress_until
from sts2_autotest.core.progress import ProgressRecord, clear_progress, save_progress
from sts2_autotest.core.recovery import (
    DefaultRecoveryStrategy,
    FailureRecord,
    RecoveryAction,
    RecoveryStrategy,
    crash_signature,
    failure_signature,
    is_p0_exception,
)
from sts2_autotest.core.state_engine import StateEngine, StateTransitionError
from sts2_autotest.core.steam import SteamController
from sts2_autotest.core.watchdog import Watchdog

logger = get_logger("core.orchestrator")


def _is_transient_validation_violation(violations: list[str]) -> bool:
    transient = {
        "combat.hand is empty during COMBAT",
        "combat.deck is empty during COMBAT",
    }
    return bool(violations) and all(item in transient for item in violations)


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
        lifecycle: Any = None,
        status_callback: Callable[[str], None] | None = None,
        gui_probe: Callable[[], bool] | None = None,
        max_restart_budget: int = 3,
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
        # 修复五-B：环境事故止损。gui_probe 由能访问截图能力的上层注入；
        # 环境事故（GUI 会话崩溃等）时会话止损为 BLOCKED_ENVIRONMENT 而非重启。
        self._gui_probe = gui_probe
        self._max_restart_budget = max_restart_budget
        self._environment_incident_reason: str = ""
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
        self._lifecycle = lifecycle
        self._traveling_since: float | None = None
        self._status_callback = status_callback
        self._action_trace_hook: Callable[
            [ActionDescriptor, GameState, GameState, ActionResult], None
        ] | None = None

    def set_action_trace_hook(
        self,
        hook: Callable[[ActionDescriptor, GameState, GameState, ActionResult], None] | None,
    ) -> None:
        """Register a per-action trace hook for the current execution window."""
        self._action_trace_hook = hook

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
        if self._lifecycle is not None:
            state_payload = state.model_dump()
            if self._lifecycle.is_phantom_combat(state_payload):
                raise STS2Error(
                    category=ErrorCategory.CRASH_ERROR,
                    message="Phantom combat detected: combat screen has no combat state",
                )
            map_block = state_payload.get("map") or {}
            if (
                str(state_payload.get("screen") or "").upper() == "MAP"
                and isinstance(map_block, dict)
                and map_block.get("is_traveling")
                and not list(map_block.get("available_nodes") or [])
            ):
                import time as _time

                self._traveling_since = self._traveling_since or _time.monotonic()
                if self._lifecycle.travel_hang_expired(
                    state_payload,
                    self._traveling_since,
                    now=_time.monotonic(),
                ):
                    raise STS2Error(
                        category=ErrorCategory.TIMEOUT_ERROR,
                        message="Map travel made no progress within the recovery threshold",
                    )
            else:
                self._traveling_since = None
        violations = validate_game_state(state)

        if violations and _is_transient_validation_violation(violations):
            for _ in range(20):
                await asyncio.sleep(0.5)
                candidate = await self.adapter.get_state()
                candidate_violations = validate_game_state(candidate)
                if not candidate_violations:
                    self._last_valid_state = candidate
                    return candidate
                state = candidate
                violations = candidate_violations

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
                break

            screen = state.screen
            if screen == GameScreen.MAIN_MENU:
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
        hard_state: GameState | None
        try:
            hard_state = await self.adapter.get_state()
        except STS2Error:
            hard_state = None
        if hard_state is not None and hard_state.screen == GameScreen.MAIN_MENU:
            await self.wait_until_actionable(timeout=5.0)
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
                        if state.screen == GameScreen.MAIN_MENU and await self.wait_until_actionable(timeout=5.0):
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
        if not await self.wait_until_actionable(timeout=self._game_startup_timeout):
            logger.error("Game did not become actionable before timeout")
            self._release_lock_if_held()
            return False

        # Checkpoint 5: ensure a fresh start is possible (clear saved runs)
        # If the game has a saved run, start_new_run won't be available;
        # we need to call abandon_run first to clear it.
        fresh_actions = await self.adapter.get_available_actions()
        if state.screen == GameScreen.MAIN_MENU and "start_new_run" not in fresh_actions:
            if "abandon_run" in fresh_actions:
                logger.info("Saved run detected — clearing via abandon_run")
                abandon = await self.adapter.act("abandon_run")
                if abandon.status == "success":
                    # After abandon_run there may be a confirmation modal
                    try:
                        post_abandon_actions = await self.adapter.get_available_actions()
                        if "confirm_modal" in post_abandon_actions or "dismiss_modal" in post_abandon_actions:
                            modal_action = "confirm_modal" if "confirm_modal" in post_abandon_actions else "dismiss_modal"
                            logger.info("Modal detected after abandon_run — dismissing via %s", modal_action)
                            await self.adapter.act(modal_action)
                    except Exception:
                        logger.debug("No modal actions available or modal dismiss failed")
                    # Re-read state after abandon + modal dismiss
                    post_abandon = await self._get_state_validated()
                    if post_abandon.screen == GameScreen.MAIN_MENU:
                        state = post_abandon
                        if not await self.wait_until_actionable(timeout=self._game_startup_timeout):
                            logger.error("Game not actionable after abandon_run")
                            self._release_lock_if_held()
                            return False
                        logger.info("Saved run cleared — start_new_run should now be available")
                else:
                    logger.warning("abandon_run failed: %s", abandon.detail)
            else:
                logger.warning(
                    "start_new_run not available and abandon_run not in actions either — "
                    "actions: %s", fresh_actions,
                )

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
            gui_probe=self._gui_probe,
            on_environment_incident=self._on_environment_incident,
            max_restart_budget=self._max_restart_budget,
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
        except STS2Error as exc:
            logger.warning("Adapter cleanup failed: %s", exc)
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
        """Execute a single test case (协议层 B20).

        Priority:
        1. CaseRegistry — resolve case_id to CaseDefinition,
           dispatch to runner or action sequence.
        2. Fallback — original probe logic for compatibility.
        """
        self.evidence.on_case_start(case_id)
        self._adapter_replaced = False
        logger.info("Executing case: %s", case_id)

        # B20: try CaseRegistry first
        try:
            from sts2_autotest.core.case_registry import CaseRegistry

            definition = CaseRegistry.resolve(case_id)

            # Programmatic runner (complex stateful flow)
            if definition.runner is not None:
                logger.info(
                    "Case %s: dispatching to programmatic runner",
                    case_id,
                )
                runner_result = await definition.runner(self)
                self.evidence.on_case_end(runner_result)
                return runner_result

            # ActionDescriptor sequence (simple linear flow)
            if definition.actions:
                logger.info(
                    "Case %s: executing %d actions from registry",
                    case_id, len(definition.actions),
                )
                await self.execute_action_sequence(definition.actions)
                self.evidence.on_case_end(TestResult(case_id, "pass"))
                return TestResult(case_id, "pass")

        except KeyError:
            # Not in registry — fall through to probe logic
            logger.info(
                "Case %s: not in CaseRegistry, using probe fallback",
                case_id,
            )
        except Exception as exc:
            # Catches non-KeyError exceptions from CaseRegistry itself
            # (e.g. invalid CaseDefinition state, runner instantiation error).
            # These are infrastructure-level crashes, not MOD test failures.
            logger.error(
                "Case %s: CaseRegistry dispatch failed — %s: %s",
                case_id, type(exc).__name__, exc,
            )
            self.evidence.on_crash(case_id, exc)
            result = TestResult(case_id, "crash", str(exc))
            self.evidence.on_case_end(result)
            return result

        # Fallback: original probe logic
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
            transition_result = TestResult(case_id, "fail", str(exc))
            self.evidence.on_case_end(transition_result)
            return transition_result

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
            signature=failure_signature(exc),
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
        if self._status_callback is not None:
            self._status_callback("RECOVERING")
        recovered, new_adapter = await self.recovery.execute(decision.action, self.adapter)

        if recovered and new_adapter is not None:
            self.adapter = new_adapter
            self._adapter_replaced = True
            logger.info(
                "Recovery (action=%s) succeeded for case %s",
                decision.action.value, case_id,
            )

        if recovered:
            if self._status_callback is not None:
                self._status_callback("RUNNING")
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

    def _on_environment_incident(self, incident_reason: str) -> None:
        """修复五-B：watchdog 判定为环境事故时的止损回调。

        环境事故（图形会话崩溃 / 重启预算耗尽）不是产品缺陷，也不应触发重启。
        记录事故原因供上层把任务判为 BLOCKED_ENVIRONMENT；不置 _crashed。
        """
        logger.critical("Environment incident, stopping cleanly: %s", incident_reason)
        self._session_status = SessionStatus.TERMINATED
        self._environment_incident_reason = incident_reason

    @property
    def environment_incident_reason(self) -> str:
        """环境事故原因（EnvironmentIncidentReason 值），未发生则空串。"""
        return self._environment_incident_reason

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

    async def _wait_until_action_available(self, action_type: str, timeout: float) -> bool:
        """Wait until the specific action appears in available_actions."""
        import time

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False

            if action_type == "choose_neow_blessing":
                try:
                    available = await self.adapter.get_available_actions()
                except STS2Error:
                    available = []

                if action_type in available:
                    return True

            if not await self.adapter.wait_until_actionable(min(remaining, 5.0)):
                await asyncio.sleep(min(0.5, remaining))
                continue

            try:
                available = await self.adapter.get_available_actions()
            except STS2Error:
                available = []

            if action_type in available:
                return True

            await asyncio.sleep(min(0.5, remaining))

    # ── action execution (single public path, FR10-FR11) ────

    async def execute_action(
        self,
        action: ActionDescriptor,
        on_pre_state: Callable[[GameState], None] | None = None,
    ) -> ActionResult:
        """Execute an action with full validation and state tracking.

        Read state → check available_actions → execute → check result
        → re-read state → validate transition → check expected_state.
        This is the ONLY action execution path — all code paths use it.

        on_pre_state, if given, receives the pre-action state snapshot —
        lets callers capture a "before" baseline without an extra get_state call.
        """
        if action.action_type == "choose_neow_blessing":
            return await self._execute_choose_neow_blessing(action, on_pre_state)

        # nav_to_screen — 自适应导航，调用框架 navigation.progress_until
        if action.action_type == "nav_to_screen":
            target = (action.params or {}).get("target")
            if not target or not isinstance(target, str):
                raise STS2Error(
                    category=ErrorCategory.ADAPTER_ERROR,
                    message=f"nav_to_screen requires 'target' param, got {action.params}",
                )

            pre_state = await self._get_state_validated()
            self._current_screen = pre_state.screen
            if on_pre_state is not None:
                on_pre_state(pre_state)

            async def _nav_get_state() -> dict[str, Any]:
                gs = await self.adapter.get_state()
                state = gs.model_dump()
                try:
                    state["available_actions"] = await self.adapter.get_available_actions()
                except STS2Error:
                    state["available_actions"] = []
                return state

            try:
                await progress_until(
                    get_state=_nav_get_state,
                    act=lambda name, p: self.adapter.act(name, p),
                    target_screen=target,
                    timeout=action.timeout,
                )
            except NavigationBlocked as exc:
                latest_state = await self.adapter.get_state()
                map_block = getattr(latest_state, "map", {}) or {}
                available = getattr(latest_state, "available_actions", None)
                if available is None:
                    try:
                        available = await self.adapter.get_available_actions()
                    except STS2Error:
                        available = []
                if (
                    latest_state.screen == GameScreen.MAP
                    and isinstance(map_block, dict)
                    and map_block.get("local_vote")
                    and not available
                ):
                    raise STS2Error(
                        category=ErrorCategory.TIMEOUT_ERROR,
                        message="map vote interface missing after combat",
                    )
                raise STS2Error(
                    category=ErrorCategory.TIMEOUT_ERROR,
                    message=str(exc),
                )
            reached_state = await self._get_state_validated()
            self._current_screen = self.state_engine.update_state(
                self._current_screen,
                reached_state.screen.value,
                event=action.action_type,
            )
            if reached_state.screen.value != target.upper():
                raise STS2Error(
                    category=ErrorCategory.GAME_ERROR,
                    message=(
                        f"nav_to_screen expected {target.upper()} "
                        f"but reached {reached_state.screen.value}"
                    ),
                    detail={
                        "action": action.action_type,
                        "expected_state": target.upper(),
                        "actual_state": reached_state.screen.value,
                    },
                )
            return ActionResult(
                status="success",
                state_changed=True,
                detail=f"Navigation to {target} completed",
            )

        # 1. Pre-read state (with validation)
        pre_state = await self._get_state_validated()
        self._current_screen = pre_state.screen
        if on_pre_state is not None:
            on_pre_state(pre_state)

        # 2. Validate action in available set (empty = nothing available)
        available = await self.adapter.get_available_actions()
        if (
            action.action_type == "start_new_run"
            and action.action_type not in available
            and "open_character_select" in available
        ):
            action = ActionDescriptor(
                action_type="open_character_select",
                params={},
                timeout=action.timeout,
                expected_state=action.expected_state,
            )
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
        new_state: GameState | None = None
        if result.status == "timeout":
            await self._record_adapter_result(False)
            raise STS2Error(
                category=ErrorCategory.TIMEOUT_ERROR,
                message=f"Action '{action.action_type}' timed out",
                detail={"action": action.action_type, "result_detail": result.detail},
            )
        if result.status == "failure":
            new_state = await self._recover_from_known_action_false_negative(
                action,
                pre_state,
                result,
            )
            if new_state is None:
                await self._record_adapter_result(False)
                raise STS2Error(
                    category=ErrorCategory.GAME_ERROR,
                    message=f"Action '{action.action_type}' failed: {result.detail}",
                    detail={"action": action.action_type, "result_detail": result.detail},
                )
            result = ActionResult(status="success", state_changed=True, detail=result.detail)

        # Record heartbeat on successful adapter call
        if self._watchdog is not None:
            self._watchdog.record_heartbeat()

        # 5. Re-read state and validate transition (with validation)
        if new_state is None:
            new_state = await self._get_post_action_state(action, pre_state)
        self._current_screen = self.state_engine.update_state(
            self._current_screen,
            new_state.screen.value,
            event=action.action_type,
        )
        if self._action_trace_hook is not None:
            self._action_trace_hook(action, pre_state, new_state, result)

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

    async def _execute_choose_neow_blessing(
        self,
        action: ActionDescriptor,
        on_pre_state: Callable[[GameState], None] | None = None,
    ) -> ActionResult:
        pre_state = await self._get_state_validated()
        self._current_screen = pre_state.screen
        if on_pre_state is not None:
            on_pre_state(pre_state)

        run_state = getattr(pre_state, "run", None) or {}
        character_id = (
            run_state.get("character_id")
            if isinstance(run_state, dict)
            else None
        ) or (action.params or {}).get("character_id") or "IRONCLAD"

        max_attempts = int((action.params or {}).get("max_attempts", 5))
        current_state = pre_state
        for attempt in range(max_attempts):
            option_index = self._find_stable_neow_option(current_state)
            if option_index is not None:
                result = await self.execute_action(
                    ActionDescriptor(
                        action_type="choose_event",
                        params={"index": option_index},
                        timeout=action.timeout,
                    ),
                )
                current_state = await self._get_state_validated()
                if current_state.screen == GameScreen.MAP:
                    return result

            if attempt == max_attempts - 1:
                options = []
                event = getattr(current_state, "event", None) or {}
                if isinstance(event, dict):
                    options = [opt.get("title") for opt in event.get("options", []) if isinstance(opt, dict)]
                raise STS2Error(
                    category=ErrorCategory.GAME_ERROR,
                    message="No stable Neow blessing option available",
                    detail={"options": options, "attempts": max_attempts},
                )

            await self._auto_reset_to_main_menu()
            if not await self.wait_until_actionable(timeout=self._game_startup_timeout):
                raise STS2Error(
                    category=ErrorCategory.TIMEOUT_ERROR,
                    message="Game did not become actionable after reset while retrying Neow blessing",
                    detail={"attempt": attempt + 1, "action": "choose_neow_blessing"},
                )
            if not await self._wait_until_action_available("start_new_run", self._game_startup_timeout):
                raise STS2Error(
                    category=ErrorCategory.TIMEOUT_ERROR,
                    message="start_new_run not available after reset while retrying Neow blessing",
                    detail={"attempt": attempt + 1, "action": "start_new_run"},
                )
            await self.execute_action_sequence(
                [
                    ActionDescriptor(action_type="start_new_run"),
                    ActionDescriptor(
                        action_type="select_character",
                        params={"character_id": character_id},
                    ),
                    ActionDescriptor(action_type="embark"),
                ]
            )
            current_state = await self._get_state_validated()

        raise STS2Error(
            category=ErrorCategory.GAME_ERROR,
            message="Failed to prepare stable Neow blessing run",
        )

    def _find_stable_neow_option(self, state: GameState) -> int | None:
        if state.screen != GameScreen.EVENT:
            return None

        event = getattr(state, "event", None) or {}
        if not isinstance(event, dict):
            return None

        event_id = str(event.get("event_id") or event.get("id") or "").upper()
        if event_id != "NEOW":
            return None

        options = event.get("options")
        if not isinstance(options, list):
            return None

        scored_options: list[tuple[int, int]] = []
        for fallback_index, option in enumerate(options):
            if not isinstance(option, dict) or option.get("is_locked"):
                continue

            option_index = option.get("index", fallback_index)
            if not isinstance(option_index, int):
                continue

            text_key = str(option.get("text_key") or "").upper()
            title = str(option.get("title") or "")
            description = str(option.get("description") or "")
            combined = f"{text_key} {title} {description}"

            score = 0
            if "LEAD_PAPERWEIGHT" in text_key:
                score += 200
            if "选择" in combined and "1张" in combined:
                score += 150
            if "无色牌" in combined:
                score += 40
            if "NEW_LEAF" in text_key:
                score -= 120
            if "ARCANE_SCROLL" in text_key:
                score -= 100
            if "NEOWS_BONES" in text_key:
                score -= 100
            if "随机" in combined and "稀有牌" in combined:
                score -= 80
            if "涅奥遗物" in combined:
                score -= 80
            if "变化" in combined and "1张" in combined and "选择" not in combined:
                score -= 60

            scored_options.append((score, option_index))

        if not scored_options:
            return None

        scored_options.sort(key=lambda item: (-item[0], item[1]))
        _, best_index = scored_options[0]
        return best_index

    async def _recover_from_known_action_false_negative(
        self,
        action: ActionDescriptor,
        pre_state: GameState,
        result: ActionResult,
    ) -> GameState | None:
        detail = str(result.detail or "")
        if action.action_type != "choose_map_node" or "get_IsPlayPhase" not in detail:
            return None

        try:
            candidate = await self._get_state_validated()
        except STS2Error:
            return None

        if pre_state.screen == GameScreen.MAP and candidate.screen != GameScreen.MAP:
            return candidate
        return None

    async def _get_post_action_state(
        self,
        action: ActionDescriptor,
        pre_state: GameState,
        timeout: float = 5.0,
    ) -> GameState:
        import time

        deadline = time.monotonic() + timeout
        while True:
            try:
                return await self._get_state_validated()
            except STS2Error as exc:
                detail = str(exc)
                is_known_map_transition_glitch = (
                    action.action_type == "choose_map_node"
                    and pre_state.screen == GameScreen.MAP
                    and "get_IsPlayPhase" in detail
                )
                if not is_known_map_transition_glitch or time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.5)

    async def execute_action_sequence(
        self,
        actions: list[ActionDescriptor],
        on_first_pre_state: Callable[[GameState], None] | None = None,
    ) -> list[ActionResult]:
        """Execute a sequence of actions with wait + re-read between each.

        on_first_pre_state, if given, receives the pre-action state snapshot
        captured for the first action in the sequence (no extra get_state call).
        """
        results: list[ActionResult] = []
        for i, action in enumerate(actions):
            if action.action_type != "nav_to_screen":
                actionable = await self._wait_until_action_available(
                    action.action_type,
                    action.timeout,
                )
                if not actionable:
                    raise STS2Error(
                        category=ErrorCategory.TIMEOUT_ERROR,
                        message=f"Game not actionable before action {i}: {action.action_type}",
                        detail={"action_index": i, "action": action.action_type},
                    )
            results.append(
                await self.execute_action(
                    action, on_pre_state=on_first_pre_state if i == 0 else None
                )
            )
            if i < len(actions) - 1:
                await self._wait_for_intermediate_settle(action.action_type)
        return results

    async def _wait_for_intermediate_settle(self, action_type: str, timeout: float = 5.0) -> None:
        """Wait past transient turn-transition frames before the next action.

        Setup chains such as "end_turn -> add card -> play card" should not continue
        while the new turn is still materializing, otherwise the next action's
        baseline can accidentally include stale pre-trigger state.
        """
        if action_type != "end_turn":
            return

        import time

        deadline = time.monotonic() + timeout
        last_signature: str | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return

            try:
                state = await self.adapter.get_state()
            except STS2Error:
                await asyncio.sleep(min(0.5, remaining))
                continue

            if self._is_turn_transition_settled(state):
                signature = json.dumps(
                    state.model_dump(mode="python"),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                if signature == last_signature:
                    return
                last_signature = signature
            else:
                last_signature = None

            await asyncio.sleep(min(0.5, remaining))

    def _is_turn_transition_settled(self, state: GameState) -> bool:
        if state.screen != GameScreen.COMBAT:
            return False

        available = getattr(state, "available_actions", None)
        if not isinstance(available, list) or len(available) == 0:
            return False

        combat = getattr(state, "combat", None)
        if not isinstance(combat, dict):
            return False

        hand = combat.get("hand")
        return isinstance(hand, list) and len(hand) > 0

    def _build_summary(self, results: list[TestResult]) -> SessionSummary:
        return SessionSummary(
            session_id="session-1",
            results=results,
            resumed_from=self._resumed_from,
        )
