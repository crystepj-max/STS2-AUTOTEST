"""Watchdog — zombie session detection and termination (FR27, FR28, FR45)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Callable

import psutil

from sts2_autotest.common.errors import EnvironmentIncidentReason
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
        gui_probe: Callable[[], bool] | None = None,
        on_environment_incident: Callable[[str], None] | None = None,
        max_restart_budget: int = 3,
    ) -> None:
        self._game_pid = game_pid
        self._adapter_pid = adapter_pid
        self._adapter = adapter
        self._heartbeat_timeout = heartbeat_timeout
        self._on_zombie = on_zombie
        # 修复五-B：GUI/窗口采集健康探针（可选注入，避免 core 依赖 evidence）。
        self._gui_probe = gui_probe
        self._on_environment_incident = on_environment_incident
        self._max_restart_budget = max_restart_budget
        self._restart_count = 0
        self._environment_incident_reason: str = ""
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

    @property
    def environment_incident_reason(self) -> str:
        """环境事故原因（EnvironmentIncidentReason 值），无则空串。"""
        return self._environment_incident_reason

    @property
    def restart_count(self) -> int:
        """已消耗的重启预算次数。"""
        return self._restart_count

    def restart_budget_exhausted(self) -> bool:
        """重启预算是否已耗尽（护栏：禁止无限重启）。"""
        return self._restart_count >= self._max_restart_budget

    def note_restart(self) -> bool:
        """登记一次重启尝试。

        Returns:
            True 表示还有预算、允许本次重启；False 表示预算已耗尽、必须拒绝，
            改走环境事故止损。预算耗尽时计数不再增加。
        """
        if self.restart_budget_exhausted():
            return False
        self._restart_count += 1
        return True

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
                outcome = self.evaluate_detection(reason)
                if outcome == "zombie":
                    await self.terminate_session()
                # 环境事故：已在 evaluate_detection 中止损（状态置 TERMINATED、
                # 通知 on_environment_incident），不再尝试终止/重启游戏。
                return

    # ── detection classification ────────────────────────────

    def evaluate_detection(self, reason: str) -> str:
        """把一次检出的异常分类为普通僵尸或环境事故（修复五-B）。

        判定规则：
        - 若 GUI/窗口采集同时失败（游戏控制异常 AND 画面采集异常同时发生），
          说明是本机图形会话层面的事故（WindowServer 崩溃 / 锁屏 / 显示休眠），
          重启游戏无意义 → 记为环境事故，止损停止，不再重启。
        - 若重启预算已耗尽（护栏），即便 GUI 正常也转判环境事故，避免无限重启。
        - 否则视为普通僵尸（合法重启候选），走正常终止流程。

        Returns:
            ``"environment_incident"`` 或 ``"zombie"``。副作用已就地应用
            （标记状态、触发对应回调）；本方法不做异步终止。
        """
        if not self._gui_healthy():
            self._mark_environment_incident(
                EnvironmentIncidentReason.GUI_SESSION_UNAVAILABLE.value, reason
            )
            return "environment_incident"
        if self.restart_budget_exhausted():
            self._mark_environment_incident(
                EnvironmentIncidentReason.GAME_CONTROL_LOST.value,
                f"restart budget exhausted after: {reason}",
            )
            return "environment_incident"
        self._mark_zombie(reason)
        return "zombie"

    def _gui_healthy(self) -> bool:
        """探测 GUI/窗口采集是否可用。未注入探针时保守视为健康（旧行为）。

        探针自身抛错也算采集失败——这本身就是图形会话异常的信号。
        """
        if self._gui_probe is None:
            return True
        try:
            return bool(self._gui_probe())
        except Exception as exc:  # noqa: BLE001 - 探针任何异常都视为 GUI 不可用
            logger.warning("GUI probe raised, treating GUI as unavailable: %s", exc)
            return False

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

    def _mark_environment_incident(self, incident_reason: str, detail: str) -> None:
        """止损：标记环境事故并通知编排器（修复五-B）。

        环境事故不是游戏缺陷，重启游戏无意义。直接把会话置为 TERMINATED
        （干净停止），记录事故原因，触发 on_environment_incident，交由上层
        判为 BLOCKED_ENVIRONMENT 而非 FAILED。
        """
        self._status = SessionStatus.TERMINATED
        self._environment_incident_reason = incident_reason
        self._zombie_reason = detail
        logger.critical(
            "Environment incident (%s): %s — stopping cleanly, no restart",
            incident_reason, detail,
        )
        if self._on_environment_incident:
            self._on_environment_incident(incident_reason)

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
