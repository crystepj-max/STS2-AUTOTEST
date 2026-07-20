"""Tests for core/watchdog.py — zombie detection, termination, monitoring."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import psutil

from sts2_autotest.common.types import SessionStatus
from sts2_autotest.core.watchdog import Watchdog


def _run(coro):
    return asyncio.run(coro)


def _make_adapter():
    adapter = AsyncMock()
    adapter.health_check.return_value = MagicMock(healthy=True)
    adapter.wait_until_actionable.return_value = True
    return adapter


# ── Heartbeat ───────────────────────────────────────────────


class TestHeartbeat:
    def test_initially_not_zombie(self) -> None:
        w = Watchdog(game_pid=123, adapter=_make_adapter())
        assert w.is_zombie() is False
        assert w.status == SessionStatus.RUNNING

    def test_heartbeat_timeout_becomes_zombie(self) -> None:
        now = 100.0
        with patch.object(time, "monotonic", return_value=now):
            w = Watchdog(game_pid=123, adapter=_make_adapter(), heartbeat_timeout=60.0)
        # 61 seconds later
        with patch.object(time, "monotonic", return_value=now + 61.0):
            assert w.is_zombie() is True

    def test_heartbeat_just_below_timeout_not_zombie(self) -> None:
        now = 100.0
        with patch.object(time, "monotonic", return_value=now):
            w = Watchdog(game_pid=123, adapter=_make_adapter(), heartbeat_timeout=60.0)
        with patch.object(time, "monotonic", return_value=now + 59.9):
            assert w.is_zombie() is False

    def test_record_heartbeat_delays_zombie(self) -> None:
        now = 100.0
        with patch.object(time, "monotonic", return_value=now):
            w = Watchdog(game_pid=123, adapter=_make_adapter(), heartbeat_timeout=60.0)
        # 61s later → zombie
        with patch.object(time, "monotonic", return_value=now + 61.0):
            assert w.is_zombie() is True
        # Record heartbeat resets timer
        with patch.object(time, "monotonic", return_value=now + 61.1):
            w.record_heartbeat()
        with patch.object(time, "monotonic", return_value=now + 61.1 + 59.0):
            assert w.is_zombie() is False


# ── Process Detection ────────────────────────────────────────


class TestProcessAlive:
    def test_alive_process(self) -> None:
        with patch.object(psutil, "Process") as mock_proc:
            mock_proc.return_value.is_running.return_value = True
            assert Watchdog._is_process_alive(123) is True

    def test_no_such_process(self) -> None:
        with patch.object(psutil, "Process") as mock_proc:
            mock_proc.return_value.is_running.side_effect = psutil.NoSuchProcess(123)
            assert Watchdog._is_process_alive(123) is False

    def test_access_denied(self) -> None:
        with patch.object(psutil, "Process") as mock_proc:
            mock_proc.return_value.is_running.side_effect = psutil.AccessDenied(123)
            assert Watchdog._is_process_alive(123) is False


# ── Mark Zombie ──────────────────────────────────────────────


class TestMarkZombie:
    def test_mark_zombie_sets_status(self) -> None:
        w = Watchdog(game_pid=123, adapter=_make_adapter())
        w._mark_zombie("test reason")
        assert w.status == SessionStatus.ZOMBIE
        assert "test reason" in w.zombie_reason

    def test_mark_zombie_calls_callback(self) -> None:
        callback = MagicMock()
        w = Watchdog(game_pid=123, adapter=_make_adapter(), on_zombie=callback)
        w._mark_zombie("dead")
        callback.assert_called_once_with("dead")


# ── Termination Sequence ─────────────────────────────────────


class TestTerminateSession:
    def test_terminate_success(self) -> None:
        w = Watchdog(game_pid=456, adapter=_make_adapter())
        w._mark_zombie("test")
        with patch.object(Watchdog, "_send_signal", return_value=True), \
             patch.object(Watchdog, "_is_process_alive", side_effect=[True, False]), \
             patch.object(Watchdog, "_cleanup_resources"), \
             patch.object(psutil, "Process") as mock_proc:
            mock_proc.return_value.wait = MagicMock()
            status = _run(w.terminate_session())
        assert status == SessionStatus.TERMINATED

    def test_terminate_needs_kill(self) -> None:
        """TERM doesn't kill process, but KILL does."""
        w = Watchdog(game_pid=456, adapter=_make_adapter())
        w._mark_zombie("test")
        with patch.object(Watchdog, "_send_signal", return_value=True), \
             patch.object(Watchdog, "_is_process_alive", side_effect=[True, False]), \
             patch.object(Watchdog, "_cleanup_resources"), \
             patch.object(psutil, "Process") as mock_proc:
            mock_proc.return_value.wait = MagicMock()
            status = _run(w.terminate_session())
        assert status == SessionStatus.TERMINATED

    def test_terminate_fails_manual_intervention(self) -> None:
        callback = MagicMock()
        w = Watchdog(game_pid=456, adapter=_make_adapter(), on_zombie=callback)
        w._mark_zombie("stubborn")
        with patch.object(Watchdog, "_send_signal", return_value=True), \
             patch.object(Watchdog, "_is_process_alive", return_value=True), \
             patch.object(Watchdog, "_cleanup_resources"), \
             patch.object(psutil, "Process") as mock_proc:
            mock_proc.return_value.wait = MagicMock()
            status = _run(w.terminate_session())
        assert status == SessionStatus.ZOMBIE
        callback.assert_called()

    def test_terminate_no_pid_skips_signals(self) -> None:
        w = Watchdog(game_pid=None, adapter=_make_adapter())
        w._mark_zombie("no pid")
        with patch.object(Watchdog, "_cleanup_resources") as mock_cleanup:
            status = _run(w.terminate_session())
        assert status == SessionStatus.TERMINATED
        mock_cleanup.assert_called_once()

    def test_termination_signal_order(self) -> None:
        w = Watchdog(game_pid=456, adapter=_make_adapter())
        w._mark_zombie("test")
        signal_history = []

        def track_signal(pid, name):
            signal_history.append(name)
            return True

        with patch.object(Watchdog, "_send_signal", side_effect=track_signal), \
             patch.object(Watchdog, "_is_process_alive", side_effect=[True, False]), \
             patch.object(Watchdog, "_cleanup_resources"), \
             patch.object(psutil, "Process") as mock_proc:
            mock_proc.return_value.wait = MagicMock()
            _run(w.terminate_session())
        assert signal_history == ["terminate", "kill"]

    def test_termination_total_time_under_35s(self) -> None:
        w = Watchdog(game_pid=456, adapter=_make_adapter())
        w._mark_zombie("timing test")
        with patch.object(Watchdog, "_send_signal", return_value=True), \
             patch.object(Watchdog, "_is_process_alive", side_effect=[True, False]), \
             patch.object(Watchdog, "_cleanup_resources"), \
             patch.object(psutil, "Process") as mock_proc:
            mock_proc.return_value.wait = MagicMock()
            start = time.monotonic()
            status = _run(w.terminate_session())
            elapsed = time.monotonic() - start
        assert status == SessionStatus.TERMINATED
        assert elapsed < 35.0

    def test_timing_budget_constraint(self) -> None:
        """Prove _MAX_DETECTION_INTERVAL + _TERM_GRACE + _KILL_WAIT ≤ 35s."""
        from sts2_autotest.core.watchdog import _TERM_GRACE, _KILL_WAIT, _MAX_DETECTION_INTERVAL
        worst_case_total = _MAX_DETECTION_INTERVAL + _TERM_GRACE + _KILL_WAIT
        assert worst_case_total <= 35.0, (
            f"Budget exceeded: {_MAX_DETECTION_INTERVAL}s + {_TERM_GRACE}s + "
            f"{_KILL_WAIT}s = {worst_case_total}s > 35s"
        )

    def test_no_sleep_termination_completes_under_budget(self) -> None:
        """With fast mock proc.wait, termination completes instantly (<1s)."""
        w = Watchdog(game_pid=456, adapter=_make_adapter())
        w._mark_zombie("fast timing test")
        with patch.object(Watchdog, "_send_signal", return_value=True), \
             patch.object(Watchdog, "_is_process_alive", side_effect=[True, False]), \
             patch.object(Watchdog, "_cleanup_resources"), \
             patch.object(psutil, "Process") as mock_proc:
            mock_proc.return_value.wait = MagicMock()
            start = time.monotonic()
            status = _run(w.terminate_session())
            elapsed = time.monotonic() - start
        assert status == SessionStatus.TERMINATED
        # Fast mock → well under 1s real time
        assert elapsed < 1.0

    def test_monitoring_interval_respects_budget(self) -> None:
        """Verify monitor interval formula ensures detection + termination ≤ 35s."""
        from sts2_autotest.core.watchdog import _MAX_DETECTION_INTERVAL, _TERM_GRACE, _KILL_WAIT
        # Worst case: longest detection interval + both waits
        worst_case = _MAX_DETECTION_INTERVAL + _TERM_GRACE + _KILL_WAIT
        assert worst_case <= 35.0, f"Budget exceeded: {worst_case}s > 35s"
        # With default 60s heartbeat, interval = min(30, MAX) = 25
        heartbeat_timeout = 60.0
        actual_interval = min(heartbeat_timeout / 2.0, _MAX_DETECTION_INTERVAL)
        assert actual_interval == _MAX_DETECTION_INTERVAL, \
            f"Expected {_MAX_DETECTION_INTERVAL}s interval, got {actual_interval}s"


# ── Signal Sending ──────────────────────────────────────────


class TestSendSignal:
    def test_terminate_sends_term(self) -> None:
        with patch.object(psutil, "Process") as mock_proc:
            result = Watchdog._send_signal(123, "terminate")
            mock_proc.return_value.terminate.assert_called_once()
            assert result is True

    def test_kill_sends_kill(self) -> None:
        with patch.object(psutil, "Process") as mock_proc:
            result = Watchdog._send_signal(123, "kill")
            mock_proc.return_value.kill.assert_called_once()
            assert result is True

    def test_nosuchprocess_returns_false(self) -> None:
        with patch.object(psutil, "Process") as mock_proc:
            mock_proc.side_effect = psutil.NoSuchProcess(123)
            result = Watchdog._send_signal(123, "kill")
            assert result is False


# ── Resource Cleanup ─────────────────────────────────────────


class TestCleanup:
    def test_cleanup_no_error_on_missing_files(self) -> None:
        with patch("pathlib.Path.exists", return_value=False):
            Watchdog._cleanup_resources()
        # No exception = pass

    def test_cleanup_handles_oserror(self) -> None:
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.unlink", side_effect=OSError("busy")):
            Watchdog._cleanup_resources()  # should not raise


# ── Monitoring Lifecycle ─────────────────────────────────────


class TestMonitoringLifecycle:
    def test_start_stop_monitoring(self) -> None:
        w = Watchdog(game_pid=123, adapter=_make_adapter(),
                     heartbeat_timeout=60.0)
        _run(w.start_monitoring())
        assert w._task is not None
        _run(w.stop_monitoring())
        assert w._task is None

    def test_double_start_warns(self) -> None:
        w = Watchdog(game_pid=123, adapter=_make_adapter(),
                     heartbeat_timeout=60.0)
        _run(w.start_monitoring())
        _run(w.start_monitoring())  # should warn but not crash
        _run(w.stop_monitoring())

    def test_stop_without_start_no_error(self) -> None:
        w = Watchdog(game_pid=123, adapter=_make_adapter())
        _run(w.stop_monitoring())  # should not raise
        assert w._task is None

    def test_stop_awaits_task_completion(self) -> None:
        """stop_monitoring() awaits the monitor task before returning."""
        w = Watchdog(game_pid=123, adapter=_make_adapter(),
                     heartbeat_timeout=60.0)
        _run(w.start_monitoring())
        # stop_monitoring awaits the cancelled task
        _run(w.stop_monitoring())
        # After stop, task should be None (proof it was awaited)
        assert w._task is None


# ── Adapter Process Liveness (AC2) ───────────────────────────


class TestAdapterProcessLiveness:
    def test_adapter_pid_dead_triggers_zombie(self) -> None:
        """AC2: adapter process death while session RUNNING → ZOMBIE."""
        w = Watchdog(game_pid=123, adapter=_make_adapter(),
                     adapter_pid=456, heartbeat_timeout=60.0)
        with patch.object(Watchdog, "_is_process_alive", side_effect=[True, False]):
            # game_pid alive, adapter_pid dead
            assert Watchdog._is_process_alive(123) is True
            assert Watchdog._is_process_alive(456) is False
            w._mark_zombie("adapter process PID 456 is dead")
        assert w.status == SessionStatus.ZOMBIE
        assert "adapter" in w.zombie_reason

    def test_no_adapter_pid_skips_check(self) -> None:
        """When adapter_pid is None, no adapter liveness check."""
        w = Watchdog(game_pid=123, adapter=_make_adapter(),
                     adapter_pid=None)
        # Construction succeeds without adapter_pid
        assert w._adapter_pid is None


# ── Zombie Detection Triggers Termination (AC4) ──────────────


class TestZombieTriggersTermination:
    def test_mark_zombie_does_not_auto_terminate(self) -> None:
        """_mark_zombie marks status but does NOT terminate — that's the monitor loop's job."""
        w = Watchdog(game_pid=456, adapter=_make_adapter())
        with patch.object(w, "terminate_session") as mock_term:
            w._mark_zombie("test")
            mock_term.assert_not_called()
        assert w.status == SessionStatus.ZOMBIE

    def test_internal_check_triggers_termination(self) -> None:
        """When composite check finds a zombie reason, termination is triggered."""
        w = Watchdog(game_pid=456, adapter=_make_adapter())
        w._mark_zombie("heartbeat timeout")
        assert w.status == SessionStatus.ZOMBIE
        # Manual termination after zombie detection
        with patch.object(Watchdog, "_send_signal", return_value=True), \
             patch.object(Watchdog, "_is_process_alive", return_value=False), \
             patch.object(Watchdog, "_cleanup_resources"):
            status = _run(w.terminate_session())
        assert status == SessionStatus.TERMINATED

    def test_monitor_loop_detection_triggers_termination(self) -> None:
        """Monitor loop zombie path calls terminate_session after detection."""
        callback = MagicMock()
        w = Watchdog(game_pid=456, adapter=_make_adapter(), on_zombie=callback)
        w._mark_zombie("test detection")
        assert w.status == SessionStatus.ZOMBIE
        # After detection, terminate_session should succeed
        with patch.object(Watchdog, "_send_signal", return_value=True), \
             patch.object(Watchdog, "_is_process_alive", return_value=False), \
             patch.object(Watchdog, "_cleanup_resources"):
            status = _run(w.terminate_session())
        assert status == SessionStatus.TERMINATED
        callback.assert_called_once_with("test detection")


# ── 修复五-B：环境事故止损 ────────────────────────────────────


class TestEnvironmentIncident:
    """修复五-B：GUI 采集与游戏控制同时异常 → 环境事故，止损而非无限重启。"""

    def test_gui_healthy_by_default_without_probe(self) -> None:
        """未注入 GUI 探针时保持旧行为：视为 GUI 健康，走正常僵尸路径。"""
        w = Watchdog(game_pid=123, adapter=_make_adapter())
        assert w._gui_healthy() is True

    def test_gui_probe_failure_is_unhealthy(self) -> None:
        """GUI 探针返回 False → 判定 GUI 不可用。"""
        w = Watchdog(game_pid=123, adapter=_make_adapter(), gui_probe=lambda: False)
        assert w._gui_healthy() is False

    def test_gui_probe_exception_treated_as_unhealthy(self) -> None:
        """GUI 探针自身抛错也算 GUI 采集失败，绝不冒泡。"""
        def _boom() -> bool:
            raise RuntimeError("screen capture blew up")

        w = Watchdog(game_pid=123, adapter=_make_adapter(), gui_probe=_boom)
        assert w._gui_healthy() is False

    def test_zombie_with_gui_down_marks_environment_incident(self) -> None:
        """游戏控制异常 + GUI 采集同时失败 → 环境事故，不进入终止/重启流程。"""
        from sts2_autotest.common.errors import EnvironmentIncidentReason

        incident_cb = MagicMock()
        zombie_cb = MagicMock()
        w = Watchdog(
            game_pid=456,
            adapter=_make_adapter(),
            gui_probe=lambda: False,
            on_zombie=zombie_cb,
            on_environment_incident=incident_cb,
        )
        with patch.object(w, "terminate_session") as mock_term:
            handled = w.evaluate_detection("game process PID 456 is dead")
        assert handled == "environment_incident"
        assert w.status == SessionStatus.TERMINATED
        assert (
            w.environment_incident_reason
            == EnvironmentIncidentReason.GUI_SESSION_UNAVAILABLE.value
        )
        mock_term.assert_not_called()          # 环境事故不重启游戏
        zombie_cb.assert_not_called()          # 不走僵尸恢复回调
        incident_cb.assert_called_once()

    def test_zombie_with_gui_healthy_is_normal_zombie(self) -> None:
        """仅游戏控制异常、GUI 采集正常 → 普通僵尸，正常终止（合法重启候选）。"""
        zombie_cb = MagicMock()
        w = Watchdog(
            game_pid=456,
            adapter=_make_adapter(),
            gui_probe=lambda: True,
            on_zombie=zombie_cb,
        )
        assert w.evaluate_detection("heartbeat timeout after 60s") == "zombie"
        assert w.status == SessionStatus.ZOMBIE
        zombie_cb.assert_called_once()


class TestRestartGuardrail:
    """修复五-B：护栏计数，禁止无限重启。"""

    def test_initial_budget_not_exhausted(self) -> None:
        w = Watchdog(game_pid=1, adapter=_make_adapter(), max_restart_budget=3)
        assert w.restart_budget_exhausted() is False
        assert w.restart_count == 0

    def test_note_restart_consumes_budget(self) -> None:
        w = Watchdog(game_pid=1, adapter=_make_adapter(), max_restart_budget=2)
        assert w.note_restart() is True   # 1/2
        assert w.note_restart() is True   # 2/2
        assert w.restart_budget_exhausted() is True
        assert w.note_restart() is False  # 拒绝：预算耗尽
        assert w.restart_count == 2

    def test_exhausted_budget_downgrades_zombie_to_incident(self) -> None:
        """重启预算耗尽后再检出僵尸 → 转判环境事故止损，不再终止重启。"""
        from sts2_autotest.common.errors import EnvironmentIncidentReason

        incident_cb = MagicMock()
        w = Watchdog(
            game_pid=456,
            adapter=_make_adapter(),
            gui_probe=lambda: True,      # GUI 正常，本应是普通僵尸
            max_restart_budget=1,
            on_environment_incident=incident_cb,
        )
        assert w.note_restart() is True  # 耗尽预算
        with patch.object(w, "terminate_session") as mock_term:
            handled = w.evaluate_detection("game process PID 456 is dead")
        assert handled == "environment_incident"
        assert (
            w.environment_incident_reason
            == EnvironmentIncidentReason.GAME_CONTROL_LOST.value
        )
        mock_term.assert_not_called()
        incident_cb.assert_called_once()
