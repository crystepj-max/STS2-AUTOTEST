"""Test Orchestrator — manages test session lifecycle (FR1, FR2, FR4, FR10-12, FR17)."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.common.errors import ErrorCategory, STS2Error
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.state import GameScreen
from sts2_autotest.core.action_model import ActionDescriptor, TestResult
from sts2_autotest.core.evidence_hooks import EvidenceHooks, StubEvidenceHooks
from sts2_autotest.core.recovery import FailureRecord, StubRecoveryStrategy
from sts2_autotest.core.state_engine import StateEngine, StateTransitionError

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
    results: list[TestResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.total = len(self.results)
        self.passed = sum(1 for r in self.results if r.status == "pass")
        self.failed = sum(1 for r in self.results if r.status == "fail")
        self.crashed = sum(1 for r in self.results if r.status == "crash")
        self.skipped = sum(1 for r in self.results if r.status == "skip")

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
        recovery: Any = None,
        evidence: EvidenceHooks | None = None,
    ) -> None:
        self.adapter = adapter
        self.state_engine = state_engine or StateEngine()
        self.recovery = recovery or StubRecoveryStrategy()
        self.evidence = evidence or StubEvidenceHooks()
        self._session_active = False
        self._crashed = False
        self._current_screen: GameScreen = GameScreen.UNKNOWN
        self._failure_history: list[FailureRecord] = []
        self._last_results: list[TestResult] = []

    # ── session lifecycle ───────────────────────────────────

    async def start_session(self) -> bool:
        """Start a test session through observable checkpoints (FR1).

        Checkpoints: health_check → state readable → MAIN_MENU reachable
        → available_actions non-empty (MVP uses stub adapter).
        """
        logger.info("Starting test session...")

        # Checkpoint 1: adapter health
        health = await self.adapter.health_check()
        if not health.healthy:
            logger.error("Adapter health check failed: %s", health.message)
            return False

        # Checkpoint 2: state readable and known
        state = await self.adapter.get_state()
        if state.screen == GameScreen.UNKNOWN:
            logger.error("Game state is UNKNOWN — cannot proceed")
            return False

        # Checkpoint 3: main menu reachable
        if state.screen != GameScreen.MAIN_MENU:
            logger.warning(
                "Not at MAIN_MENU (currently %s) — proceeding anyway (MVP)",
                state.screen.value,
            )

        # Checkpoint 4: game is actionable
        await self.wait_until_actionable(timeout=10.0)

        self._current_screen = state.screen
        self._session_active = True
        self._crashed = False
        logger.info("Session started. Screen: %s", self._current_screen.value)
        return True

    async def stop_session(self) -> None:
        """Stop the session, release resources, and report completion status.

        Release order: game adapter cleanup → evidence finalization.
        """
        self._session_active = False

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
            "is_failed": summary.is_failed,
        })
        logger.info(
            "Session stopped. %d passed, %d failed, %d crashed, %d skipped",
            summary.passed, summary.failed, summary.crashed, summary.skipped,
        )

    # ── execution modes ─────────────────────────────────────

    async def run_all(self, case_ids: list[str]) -> SessionSummary:
        """Run all specified test cases."""
        if not await self.start_session():
            return SessionSummary(session_id="failed-start")
        results = []
        for case_id in case_ids:
            if self._crashed:
                results.append(TestResult(case_id, "skip", "Session crashed"))
                continue
            results.append(await self.execute_case(case_id))
        self._last_results = results
        await self.stop_session()
        return self._build_summary(results)

    async def run_cases(self, case_ids: list[str]) -> SessionSummary:
        """Run specific test cases by ID. Delegates to run_all."""
        return await self.run_all(case_ids)

    async def run_failed(
        self, previous_results: list[TestResult] | None = None
    ) -> SessionSummary:
        """Re-run previously failed cases."""
        prev = previous_results or self._last_results
        failed_ids = [r.case_id for r in prev if r.status == "fail"]
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
            logger.error("Case %s: %s — %s", case_id, exc.category.value, exc)
            self._failure_history.append(FailureRecord(
                error_type=exc.category.value,
                message=exc.message,
                timestamp=str(exc.timestamp),
            ))
            is_crash = exc.category == ErrorCategory.CRASH_ERROR
            if is_crash:
                self._handle_crash(case_id, exc)
                result2: TestResult = TestResult(case_id, "crash", exc.message)
            else:
                result2 = TestResult(case_id, "fail", exc.message)
            self.evidence.on_case_end(result2)
            return result2

        except Exception as exc:
            logger.error("Case %s: unexpected crash — %s", case_id, exc)
            self._crashed = True
            self.evidence.on_crash(case_id, exc)
            result3: TestResult = TestResult(case_id, "crash", str(exc))
            self.evidence.on_case_end(result3)
            return result3

        finally:
            # Per-case adapter cleanup: clear caches, release per-case handles
            try:
                await self.adapter.cleanup()
            except Exception as exc:
                logger.debug("Adapter cleanup after case %s failed: %s", case_id, exc)

    # ── crash handling ──────────────────────────────────────

    def _handle_crash(self, case_id: str, error: Exception) -> None:
        """Handle game crash (MVP: record + stop, FR36)."""
        logger.error("CRASH during case %s: %s", case_id, error)
        self._crashed = True
        self.evidence.on_crash(case_id, error)

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
        # 1. Pre-read state
        self._current_screen = (await self.adapter.get_state()).screen

        # 2. Validate action in available set (empty = nothing available)
        available = await self.adapter.get_available_actions()
        if action.action_type not in available:
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
            raise STS2Error(
                category=ErrorCategory.TIMEOUT_ERROR,
                message=f"Action '{action.action_type}' timed out",
                detail={"action": action.action_type, "result_detail": result.detail},
            )
        if result.status == "failure":
            raise STS2Error(
                category=ErrorCategory.GAME_ERROR,
                message=f"Action '{action.action_type}' failed: {result.detail}",
                detail={"action": action.action_type, "result_detail": result.detail},
            )

        # 5. Re-read state and validate transition
        new_state = await self.adapter.get_state()
        self._current_screen = self.state_engine.update_state(
            self._current_screen,
            new_state.screen.value,
            event=action.action_type,
        )

        # 6. Verify expected_state if specified
        if action.expected_state is not None and self._current_screen != action.expected_state:
            logger.warning(
                "Action '%s': expected state %s but reached %s",
                action.action_type,
                action.expected_state.value,
                self._current_screen.value,
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
        return SessionSummary(session_id="session-1", results=results)
