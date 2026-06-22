"""Watchdog — zombie session detection and termination (FR27, FR28, FR45)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import psutil

from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.types import SessionStatus

if TYPE_CHECKING:
    from sts2_autotest.adapters.base import GameAdapterProtocol

logger = get_logger("core.watchdog")

_TERM_GRACE = 5.0  # seconds to wait after TERM before KILL
_KILL_WAIT = 2.0   # seconds to wait after KILL before cleanup
_MAX_DETECTION_INTERVAL = 25.0  # max seconds between checks (budget: 25 + 5 + 2 = 32 ≤ 35)


class Watchdog:
    """Zombie session detector and terminator.

    Monitors session heartbeat + process liveness. Notifies Orchestrator
    via callback on zombie detection. Executes graceful termination
    sequence on request.

    Does NOT make recovery decisions — that is the RecoveryStrategy's
    responsibility (separation of concerns).
    """

    def __init__(
        self,
        game_pid: int | None,
        adapter: GameAdapterProtocol,
        *,
        adapter_pid: int | None = None,
        heartbeat_timeout: float = 60.0,
        on_zombie: Callable[[str], None] | None = None,
    ) -> None:
        self._game_pid = game_pid
        self._adapter_pid = adapter_pid
        self._adapter = adapter
        self._heartbeat_timeout = heartbeat_timeout
        self._on_zombie = on_zombie
        self._last_heartbeat = time.monotonic()
        self._status = SessionStatus.RUNNING
        self._task: asyncio.Task[None] | None = None
        self._zombie_reason: str = ""

    # ── public API ──────────────────────────────────────────

    @property
    def status(self) -> SessionStatus:
        return self._status

    @property
    def zombie_reason(self) -> str:
        return self._zombie_reason

    def record_heartbeat(self) -> None:
        """Record a successful communication heartbeat."""
        self._last_heartbeat = time.monotonic()

    def is_zombie(self) -> bool:
        """Check if the session heartbeat has timed out."""
        elapsed = time.monotonic() - self._last_heartbeat
        return elapsed > self._heartbeat_timeout

    async def start_monitoring(self) -> None:
        """Start the periodic monitoring async task."""
        if self._task is not None:
            logger.warning("Monitoring task already running")
            return
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Watchdog monitoring started (interval=%.1fs)", self._heartbeat_timeout / 2)

    async def stop_monitoring(self) -> None:
        """Stop the monitoring task and record final status."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("Watchdog monitoring stopped (final status=%s)", self._status.value)

    # ── monitoring loop ────────────────────────────────────

    async def _monitor_loop(self) -> None:
        """Periodic check: heartbeat timeout + process liveness.

        On zombie detection: marks ZOMBIE, notifies orchestrator, then
        automatically triggers the termination sequence (AC4).
        """
        interval = min(self._heartbeat_timeout / 2.0, _MAX_DETECTION_INTERVAL)
        while True:
            await asyncio.sleep(interval)

            reason: str | None = None

            # Check 1: heartbeat timeout
            if self.is_zombie():
                reason = f"heartbeat timeout after {self._heartbeat_timeout}s"

            # Check 2: game process liveness
            if reason is None and self._game_pid is not None:
                if not self._is_process_alive(self._game_pid):
                    reason = f"game process PID {self._game_pid} is dead"

            # Check 3: adapter process liveness (AC2)
            if reason is None and self._adapter_pid is not None:
                if not self._is_process_alive(self._adapter_pid):
                    reason = f"adapter process PID {self._adapter_pid} is dead"

            if reason is not None:
                self._mark_zombie(reason)
                await self.terminate_session()
                return

    # ── termination ─────────────────────────────────────────

    async def terminate_session(self) -> SessionStatus:
        """Execute the zombie termination sequence.

        Flow:
        1. TERM signal → wait grace period
        2. KILL signal → wait
        3. Cleanup residual resources
        4. Mark TERMINATED

        Total target: ≤35s (detection already done, just termination).
        Returns the termination duration for verification.
        """
        start = time.monotonic()
        logger.warning(
            "Starting termination sequence for PID %s (reason: %s)",
            self._game_pid, self._zombie_reason,
        )

        if self._game_pid is not None:
            # Step 1: TERM signal
            terminated = self._send_signal(self._game_pid, "terminate")
            if terminated:
                try:
                    proc = psutil.Process(self._game_pid)
                    proc.wait(timeout=_TERM_GRACE)
                except (psutil.NoSuchProcess, psutil.TimeoutExpired, psutil.AccessDenied):
                    pass

            # Step 2: KILL signal if still alive
            if self._is_process_alive(self._game_pid):
                self._send_signal(self._game_pid, "kill")
                try:
                    proc = psutil.Process(self._game_pid)
                    proc.wait(timeout=_KILL_WAIT)
                except (psutil.NoSuchProcess, psutil.TimeoutExpired, psutil.AccessDenied):
                    pass

        # Step 3: Cleanup residual resources
        self._cleanup_resources()

        # Step 4: Final status
        if self._game_pid is not None and self._is_process_alive(self._game_pid):
            self._status = SessionStatus.ZOMBIE
            logger.critical("Termination FAILED for PID %s — manual intervention required", self._game_pid)
            msg = f"Zombie session PID {self._game_pid} could not be terminated. Manual intervention required."
            if self._on_zombie:
                self._on_zombie(msg)
        else:
            self._status = SessionStatus.TERMINATED
            logger.info("Termination successful for PID %s", self._game_pid)

        elapsed = time.monotonic() - start
        logger.info("Termination sequence took %.1fs", elapsed)
        return self._status

    # ── internal helpers ────────────────────────────────────

    def _mark_zombie(self, reason: str) -> None:
        """Mark the session as ZOMBIE and notify the orchestrator."""
        self._status = SessionStatus.ZOMBIE
        self._zombie_reason = reason
        logger.warning("Session marked ZOMBIE: %s", reason)
        if self._on_zombie:
            self._on_zombie(reason)

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        """Check if a process with the given PID is alive."""
        try:
            proc = psutil.Process(pid)
            return bool(proc.is_running())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    @staticmethod
    def _send_signal(pid: int, signal_name: str) -> bool:
        """Send a signal to a process. Returns True on success."""
        try:
            proc = psutil.Process(pid)
            if signal_name == "terminate":
                proc.terminate()
            elif signal_name == "kill":
                proc.kill()
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            logger.warning("Cannot %s PID %s: %s", signal_name, pid, exc)
            return False

    @staticmethod
    def _cleanup_resources() -> None:
        """Clean up residual resources (lock files, named pipes, temp files)."""
        patterns = [
            ".sts2-autotest.lock",
            ".sts2-autotest.lock.writetest",
            "sts2_pipe",
        ]
        for pattern in patterns:
            p = Path(pattern)
            if p.exists():
                try:
                    p.unlink()
                    logger.info("Cleaned up residual file: %s", pattern)
                except OSError as exc:
                    logger.warning("Failed to clean up %s: %s", pattern, exc)
