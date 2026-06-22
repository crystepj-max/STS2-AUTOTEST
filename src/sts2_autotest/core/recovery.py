"""Recovery strategy — decide and execute recovery on adapter/game failures (FR5).

RecoveryAction: FAST_PATH (reconnect <2s) / RECREATE (new instance ≤10s) / TERMINATE.
DefaultRecoveryStrategy: pure-function decide() + async execute().
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Callable, Protocol

from sts2_autotest.common.errors import ErrorCategory, STS2Error
from sts2_autotest.common.logging import get_logger
from sts2_autotest.core.popup_disposal import PopupDisposition

if TYPE_CHECKING:
    from sts2_autotest.adapters.base import GameAdapterProtocol

logger = get_logger("core.recovery")


class RecoveryAction(StrEnum):
    """Recovery action decided by RecoveryStrategy."""

    FAST_PATH = "FAST_PATH"
    RECREATE = "RECREATE"
    GAME_RESTART = "GAME_RESTART"  # kill game → start game → recreate adapter
    FULL_RESTART = "FULL_RESTART"  # kill game+Steam → start Steam → start game → recreate adapter
    TERMINATE = "TERMINATE"


@dataclass(frozen=True)
class RecoveryDecision:
    """Result of a recovery strategy decision with classification context.

    action: which recovery action to take.
    is_p0: True if the failure is a P0 session-level fatal (e.g. version_mismatch,
           FileNotFoundError) that must always terminate the session.
           False for non-P0 TERMINATE triggered by consecutive threshold.
    """

    action: RecoveryAction
    is_p0: bool = False


@dataclass(frozen=True)
class FailureRecord:
    """Record of a single adapter/crash failure."""

    error_type: str
    message: str
    timestamp: str
    exit_code: int | None = None


# P0 exceptions: session-level — framework/environment cannot proceed
_P0_EXCEPTION_TYPES: frozenset[str] = frozenset({
    "FileNotFoundError",
    "OSError",
})

# Fast-path exceptions: single-call level, reconnect may resolve
_FAST_PATH_CATEGORIES: frozenset[ErrorCategory] = frozenset({
    ErrorCategory.TIMEOUT_ERROR,
})


def crash_signature(error: Exception, exit_code: int | None = None) -> str:
    """Generate a deterministic crash signature from exception type + exit code.

    Based on exception type name + exit code, not stack trace matching.
    Same type + same exit_code → same signature.
    """
    type_name = type(error).__name__
    code = exit_code if exit_code is not None else "none"
    return f"{type_name}:{code}"


def is_p0_exception(exc: Exception) -> bool:
    """Check if an exception is a P0 session-level fatal.

    P0 exceptions must always terminate the session — they cannot be
    downgraded to deterministic_fail by consecutive history.
    """
    if type(exc).__name__ in _P0_EXCEPTION_TYPES:
        return True
    if isinstance(exc, STS2Error):
        if exc.category == ErrorCategory.ADAPTER_ERROR:
            detail = exc.detail or {}
            sub_type = str(detail.get("sub_type", ""))
            if sub_type == "version_mismatch":
                return True
    return False


class RecoveryStrategy(Protocol):
    """Decide recovery action based on failure + history."""

    def decide(
        self,
        failure: Exception,
        history: list[FailureRecord],
        *,
        max_consecutive: int = 3,
    ) -> RecoveryDecision: ...

    async def execute(
        self,
        action: RecoveryAction,
        adapter: GameAdapterProtocol,
    ) -> tuple[bool, GameAdapterProtocol | None]:
        """Execute recovery action.

        Returns (success, new_adapter_or_None).
        RECREATE returns the new adapter on success.
        """
        ...


class StubRecoveryStrategy:
    """MVP implementation — always terminates on failure."""

    def decide(
        self,
        failure: Exception,
        history: list[FailureRecord],
        *,
        max_consecutive: int = 3,
    ) -> RecoveryDecision:
        return RecoveryDecision(
            action=RecoveryAction.TERMINATE,
            is_p0=is_p0_exception(failure),
        )

    async def execute(
        self,
        action: RecoveryAction,
        adapter: GameAdapterProtocol,
    ) -> tuple[bool, GameAdapterProtocol | None]:
        return False, None


class DefaultRecoveryStrategy:
    """Full recovery strategy: decide() + execute().

    decide() is a pure function (no side effects).
    execute() is async (has side effects: reconnect/recreate adapter).
    """

    def __init__(
        self,
        *,
        adapter_factory: Callable[[], GameAdapterProtocol] | None = None,
        game_startup_timeout: float = 60.0,
        steam_controller: Any = None,
        popup_handler: Callable[[], PopupDisposition] | None = None,
    ) -> None:
        self._adapter_factory = adapter_factory
        self._game_startup_timeout = game_startup_timeout
        self._steam_controller = steam_controller
        self._popup_handler = popup_handler

    def decide(
        self,
        failure: Exception,
        history: list[FailureRecord],
        *,
        max_consecutive: int = 3,
    ) -> RecoveryDecision:
        """Decide recovery action based on exception type + failure history.

        Decision logic:
        1. P0 exceptions (FileNotFoundError, OSError, VERSION_MISMATCH) → TERMINATE (is_p0=True)
        2. CRASH_ERROR → progressive levels: GAME_RESTART → FULL_RESTART → TERMINATE
        3. Timeout → FAST_PATH (with consecutive escalation)
        4. Other adapter errors → FAST_PATH (with consecutive escalation)
        """
        # P0: session-level fatal
        p0 = is_p0_exception(failure)
        if p0:
            return RecoveryDecision(action=RecoveryAction.TERMINATE, is_p0=True)

        # Extract error type from the current failure
        current_type = (
            failure.category.value
            if isinstance(failure, STS2Error)
            else type(failure).__name__
        )

        # Crashes get progressive recovery levels
        if isinstance(failure, STS2Error) and failure.category == ErrorCategory.CRASH_ERROR:
            return self._decide_crash(history, max_consecutive)

        # Timeouts → fast path (with consecutive escalation)
        if isinstance(failure, STS2Error) and failure.category in _FAST_PATH_CATEGORIES:
            action = self._check_consecutive(history, max_consecutive, current_type)
            return RecoveryDecision(action=action, is_p0=False)

        # Other adapter errors
        action = self._check_consecutive(history, max_consecutive, current_type)
        return RecoveryDecision(action=action, is_p0=False)

    async def execute(
        self,
        action: RecoveryAction,
        adapter: GameAdapterProtocol,
    ) -> tuple[bool, GameAdapterProtocol | None]:
        """Execute the recovery action.

        FAST_PATH: health_check + reconnect → (bool, None)
        RECREATE: destroy old + create new + version handshake + health_check → (bool, new_adapter|None)
        TERMINATE: record artifacts (caller handles) → (False, None)
        """
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

    # ── private helpers ─────────────────────────────────────

    def _check_consecutive(
        self,
        history: list[FailureRecord],
        max_consecutive: int,
        current_error_type: str | None = None,
    ) -> RecoveryAction:
        """Check consecutive same-type failures against threshold.

        History does NOT include the current failure (it's appended
        after decide()). The caller MUST pass current_error_type so
        counts use the current error's type, not the previous entry.

        The +1 accounts for the current failure not being in history,
        matching the original semantics where history included it.
        """
        if not history:
            return RecoveryAction.FAST_PATH

        if current_error_type is None:
            current_error_type = history[-1].error_type

        # +1 for current failure not in history
        consecutive = self._consecutive_count(history, current_error_type) + 1
        if consecutive >= max_consecutive:
            return RecoveryAction.TERMINATE
        if consecutive >= max_consecutive - 1:
            return RecoveryAction.RECREATE
        return RecoveryAction.FAST_PATH

    def _decide_crash(
        self,
        history: list[FailureRecord],
        max_consecutive: int,
    ) -> RecoveryDecision:
        """Three-level progressive crash recovery.

        1st crash → GAME_RESTART (restart game process)
        2nd consecutive crash → FULL_RESTART (restart Steam + game)
        3rd+ consecutive crash → TERMINATE.
        Note: history does NOT include the current crash
        (appended after decide()), so we add +1 for the current.
        """
        if not history:
            return RecoveryDecision(action=RecoveryAction.GAME_RESTART)
        # Count consecutive crashes from history, +1 for current crash
        consecutive = self._consecutive_count(history, ErrorCategory.CRASH_ERROR.value) + 1
        if consecutive >= max_consecutive:
            return RecoveryDecision(action=RecoveryAction.TERMINATE)
        if consecutive >= max_consecutive - 1:
            return RecoveryDecision(action=RecoveryAction.FULL_RESTART)
        return RecoveryDecision(action=RecoveryAction.GAME_RESTART)

    @staticmethod
    def _consecutive_count(history: list[FailureRecord], error_type: str) -> int:
        """Count consecutive failures of the same type from the end of history."""
        count = 0
        for record in reversed(history):
            if record.error_type == error_type:
                count += 1
            else:
                break
        return count

    async def _execute_fast_path(self, adapter: GameAdapterProtocol) -> bool:
        """FAST_PATH: health_check + attempt reconnect."""
        try:
            health = await adapter.health_check()
            if health.healthy:
                logger.info("FAST_PATH recovery: adapter healthy after health_check")
                return True
            logger.warning("FAST_PATH recovery: adapter unhealthy — %s", health.message)
            return False
        except Exception as exc:
            logger.warning("FAST_PATH recovery failed: %s", exc)
            return False

    async def _execute_recreate(
        self, adapter: GameAdapterProtocol,
    ) -> tuple[bool, GameAdapterProtocol | None]:
        """RECREATE: destroy old adapter + create new instance + health_check.

        Returns (True, new_adapter) on success, (False, None) on failure.
        The caller replaces the old adapter with the returned new one.
        """
        if self._adapter_factory is None:
            logger.warning("RECREATE: no adapter_factory configured — cannot recreate")
            return False, None

        # Destroy old adapter
        try:
            await adapter.cleanup()
        except Exception as exc:
            logger.warning("RECREATE: old adapter cleanup failed: %s", exc)

        # Create new adapter via factory
        try:
            new_adapter = self._adapter_factory()
        except Exception as exc:
            logger.error("RECREATE: factory failed to create new adapter: %s", exc)
            return False, None

        # Version handshake + health check on new adapter
        try:
            health = await new_adapter.health_check()
            if not health.healthy:
                logger.error("RECREATE: new adapter unhealthy: %s", health.message)
                return False, None
        except Exception as exc:
            logger.error("RECREATE: new adapter health check failed: %s", exc)
            return False, None

        logger.info("RECREATE recovery: new adapter created and healthy")
        return True, new_adapter

    async def _execute_game_restart(
        self, adapter: GameAdapterProtocol,
    ) -> tuple[bool, GameAdapterProtocol | None]:
        """Level 1: restart game → recreate adapter → health check."""
        if not self._prepare_restart_popup("GAME_RESTART"):
            return False, None
        if self._steam_controller is None:
            logger.warning("GAME_RESTART: no steam_controller — falling back to RECREATE")
            return await self._execute_recreate(adapter)
        logger.info("GAME_RESTART: restarting game process...")
        try:
            self._steam_controller.restart_game()
        except Exception as exc:
            logger.error("GAME_RESTART: restart_game failed: %s", exc)
            return False, None
        return await self._execute_recreate(adapter)

    async def _execute_full_restart(
        self, adapter: GameAdapterProtocol,
    ) -> tuple[bool, GameAdapterProtocol | None]:
        """Level 2: stop game+Steam → start Steam+game → recreate adapter."""
        if not self._prepare_restart_popup("FULL_RESTART"):
            return False, None
        if self._steam_controller is None:
            logger.warning("FULL_RESTART: no steam_controller — falling back to RECREATE")
            return await self._execute_recreate(adapter)
        logger.info("FULL_RESTART: restarting Steam and game...")
        try:
            self._steam_controller.stop_game()
            self._steam_controller.stop_steam()
            self._steam_controller.start_steam()
            self._steam_controller.start_game()
        except Exception as exc:
            logger.error("FULL_RESTART: restart failed: %s", exc)
            return False, None
        return await self._execute_recreate(adapter)

    def _prepare_restart_popup(self, action_name: str) -> bool:
        if self._popup_handler is None:
            return True
        try:
            disposition = self._popup_handler()
        except Exception as exc:
            logger.warning("%s: popup handler failed: %s", action_name, exc)
            return False

        if disposition == PopupDisposition.MANUAL_INTERVENTION:
            logger.warning("%s: popup requires manual intervention", action_name)
            return False
        if disposition == PopupDisposition.PRESERVE:
            logger.info("%s: preserving popup evidence before recovery", action_name)
        return True
