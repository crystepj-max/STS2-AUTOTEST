"""Tests for core/precheck.py — PrecheckRunner five-layer fail-fast validation."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil

from sts2_autotest.core.precheck import PrecheckReport, PrecheckResult, PrecheckRunner


def _make_settings(**overrides):
    """Build a minimal PrecheckSettings stub for testing."""
    defaults = {
        "disk_threshold_mb": 100,
        "lock_file": ".sts2-autotest.lock",
        "screenshot_dir": "./screenshots",
        "evidence_dir": "./evidence",
        "adapter_cli_path": "",
        "adapter_timeout": 30.0,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


# ── PrecheckResult / PrecheckReport ──────────────────────────


class TestPrecheckResult:
    def test_fields(self) -> None:
        r = PrecheckResult(layer="env", passed=True, message="ok")
        assert r.layer == "env"
        assert r.passed is True
        assert r.message == "ok"

    def test_detail_defaults_empty(self) -> None:
        r = PrecheckResult(layer="x", passed=False, message="bad")
        assert r.detail == {}


class TestPrecheckReport:
    def test_passed_aggregate(self) -> None:
        r = PrecheckReport(passed=True, results=[
            PrecheckResult(layer="a", passed=True, message="ok"),
        ])
        assert r.passed is True

    def test_failed_layers(self) -> None:
        r = PrecheckReport(passed=False, results=[
            PrecheckResult(layer="a", passed=True, message="ok"),
            PrecheckResult(layer="b", passed=False, message="fail"),
            PrecheckResult(layer="c", passed=True, message="ok"),
            PrecheckResult(layer="d", passed=False, message="fail2"),
        ])
        assert r.failed_layers == ["b", "d"]


# ── Helpers ──────────────────────────────────────────────────


class TestSafeProcessIter:
    def test_skips_no_such_process(self) -> None:
        """psutil.NoSuchProcess is swallowed."""
        mock_proc = MagicMock()
        type(mock_proc).info = property(lambda self: (_ for _ in ()).throw(psutil.NoSuchProcess(123)))
        with patch.object(psutil, "process_iter", return_value=[mock_proc]):
            result = list(PrecheckRunner._safe_process_iter(["name"]))
        assert result == []

    def test_skips_access_denied(self) -> None:
        """psutil.AccessDenied is swallowed."""
        mock_proc = MagicMock()
        type(mock_proc).info = property(lambda self: (_ for _ in ()).throw(psutil.AccessDenied(123)))
        with patch.object(psutil, "process_iter", return_value=[mock_proc]):
            result = list(PrecheckRunner._safe_process_iter(["name"]))
        assert result == []

    def test_yields_valid_processes(self) -> None:
        mock_proc = MagicMock()
        mock_proc.info = {"name": "steam.exe"}
        with patch.object(psutil, "process_iter", return_value=[mock_proc]):
            result = list(PrecheckRunner._safe_process_iter(["name"]))
        assert len(result) == 1
        assert result[0]["name"] == "steam.exe"


# ── Environment Layer ────────────────────────────────────────


class TestCheckEnvironment:
    def test_steam_not_running_fails(self) -> None:
        settings = _make_settings()
        with patch.object(PrecheckRunner, "_safe_process_iter", return_value=[]):
            runner = PrecheckRunner(settings)
            result = runner._check_environment()
        assert result.passed is False
        assert result.layer == "environment"
        assert "steam" in result.message.lower()

    def test_cli_not_discoverable_fails(self) -> None:
        settings = _make_settings()
        with patch.object(PrecheckRunner, "_safe_process_iter",
                          return_value=[{"name": "steam.exe"}]):
            runner = PrecheckRunner(settings, cli_discover=lambda: None)
            result = runner._check_environment()
        assert result.passed is False
        assert "cli" in result.message.lower()

    def test_game_not_installed_fails(self) -> None:
        settings = _make_settings()
        with patch.object(PrecheckRunner, "_safe_process_iter",
                          return_value=[{"name": "steam.exe"}]):
            runner = PrecheckRunner(settings, cli_discover=lambda: "/valid/cli")
            with patch.object(Path, "is_file", return_value=True), \
                 patch.object(PrecheckRunner, "_find_game_exe", return_value=None):
                result = runner._check_environment()
        assert result.passed is False
        assert "game" in result.message.lower()

    def test_mod_not_found_fails(self) -> None:
        settings = _make_settings()
        mock_exe = Path("/fake/SlayTheSpire2.exe")
        with patch.object(PrecheckRunner, "_safe_process_iter",
                          return_value=[{"name": "steam.exe"}]):
            runner = PrecheckRunner(settings, cli_discover=lambda: "/valid/cli")
            with patch.object(Path, "is_file", return_value=True), \
                 patch.object(PrecheckRunner, "_find_game_exe", return_value=mock_exe), \
                 patch.object(PrecheckRunner, "_find_mod_dir", return_value=None):
                result = runner._check_environment()
        assert result.passed is False
        assert "mod" in result.message.lower()

    def test_cli_discover_exception_handled(self) -> None:
        settings = _make_settings()
        with patch.object(PrecheckRunner, "_safe_process_iter",
                          return_value=[{"name": "steam.exe"}]):
            runner = PrecheckRunner(settings, cli_discover=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            result = runner._check_environment()
        assert result.passed is False
        assert result.detail.get("cli_path") is None

    def test_all_environment_ok(self) -> None:
        settings = _make_settings()
        mock_exe = Path("/fake/SlayTheSpire2.exe")
        mock_mod = Path("/fake/mods")
        with patch.object(PrecheckRunner, "_safe_process_iter",
                          return_value=[{"name": "steam.exe"}]):
            runner = PrecheckRunner(settings, cli_discover=lambda: "/valid/cli")
            with patch.object(Path, "is_file", return_value=True), \
                 patch.object(PrecheckRunner, "_find_game_exe", return_value=mock_exe), \
                 patch.object(PrecheckRunner, "_find_mod_dir", return_value=mock_mod):
                result = runner._check_environment()
        assert result.passed is True
        assert result.detail.get("steam_detected") is True


# ── Config Layer ─────────────────────────────────────────────


class TestCheckConfig:
    def test_cli_path_invalid_fails(self) -> None:
        settings = _make_settings(adapter_cli_path="/bad/path")
        runner = PrecheckRunner(settings)
        result = runner._check_config()
        assert result.passed is False
        assert result.layer == "config"

    def test_timeout_zero_or_negative_fails(self) -> None:
        settings = _make_settings(adapter_timeout=0)
        runner = PrecheckRunner(settings)
        result = runner._check_config()
        assert result.passed is False
        assert "timeout" in result.message.lower()

    def test_empty_cli_path_passes(self) -> None:
        """Empty cli_path skips CLI validation."""
        settings = _make_settings(adapter_cli_path="")
        runner = PrecheckRunner(settings)
        result = runner._check_config()
        assert result.passed is True

    def test_cli_help_succeeds(self) -> None:
        """CLI --help communication check passes."""
        settings = _make_settings(adapter_cli_path="/valid/cli")
        runner = PrecheckRunner(settings)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = b""
        with patch.object(Path, "is_file", return_value=True), \
             patch("subprocess.run", return_value=mock_result):
            result = runner._check_config()
        assert result.passed is True

    def test_cli_help_nonzero_fails(self) -> None:
        """CLI --help returns non-zero → communication failure."""
        settings = _make_settings(adapter_cli_path="/valid/cli")
        runner = PrecheckRunner(settings)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"error"
        with patch.object(Path, "is_file", return_value=True), \
             patch("subprocess.run", return_value=mock_result):
            result = runner._check_config()
        assert result.passed is False

    def test_cli_help_timeout_fails(self) -> None:
        """CLI timeout → config failure."""
        settings = _make_settings(adapter_cli_path="/valid/cli")
        runner = PrecheckRunner(settings)
        import subprocess
        with patch.object(Path, "is_file", return_value=True), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            result = runner._check_config()
        assert result.passed is False
        assert "timed out" in result.message.lower()


# ── Resources Layer ──────────────────────────────────────────


class TestCheckResources:
    def test_disk_below_threshold_fails(self) -> None:
        settings = _make_settings(disk_threshold_mb=100, evidence_dir="/ev")
        with patch("shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(free=50 * 1024 * 1024)
            with patch.object(Path, "exists", return_value=True):
                runner = PrecheckRunner(settings)
                result = runner._check_resources()
        assert result.passed is False
        assert "disk" in result.message.lower()

    def test_disk_check_oserror_fails(self) -> None:
        settings = _make_settings(evidence_dir="/ev")
        with patch("shutil.disk_usage", side_effect=OSError("access denied")):
            runner = PrecheckRunner(settings)
            result = runner._check_resources()
        assert result.passed is False

    def test_memory_critical_fails(self) -> None:
        settings = _make_settings()
        mock_mem = MagicMock()
        mock_mem.percent = 97
        with patch.object(psutil, "virtual_memory", return_value=mock_mem), \
             patch("shutil.disk_usage") as mock_du, \
             patch.object(Path, "exists", return_value=True):
            mock_du.return_value = MagicMock(free=500 * 1024 * 1024)
            runner = PrecheckRunner(settings)
            result = runner._check_resources()
        assert result.passed is False
        assert "memory" in result.message.lower()

    def test_resources_ok(self) -> None:
        settings = _make_settings(disk_threshold_mb=100, evidence_dir="/ev")
        mock_mem = MagicMock()
        mock_mem.percent = 45
        with patch.object(psutil, "virtual_memory", return_value=mock_mem), \
             patch("shutil.disk_usage") as mock_du, \
             patch.object(Path, "exists", return_value=True):
            mock_du.return_value = MagicMock(free=500 * 1024 * 1024)
            runner = PrecheckRunner(settings)
            result = runner._check_resources()
        assert result.passed is True


# ── Permissions Layer ────────────────────────────────────────


class TestCheckPermissions:
    def test_admin_check_fails_when_not_admin(self) -> None:
        """Non-admin on Windows fails permissions."""
        settings = _make_settings()
        runner = PrecheckRunner(settings)
        with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=0):
            result = runner._check_permissions()
        assert result.passed is False
        assert "admin" in result.message.lower()

    def test_admin_check_succeeds_when_admin(self, tmp_path) -> None:
        ss_dir = tmp_path / "screenshots"
        ev_dir = tmp_path / "evidence"
        settings = _make_settings(
            screenshot_dir=str(ss_dir),
            evidence_dir=str(ev_dir),
        )
        runner = PrecheckRunner(settings)
        with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=1):
            result = runner._check_permissions()
        assert result.passed is True

    def test_screenshot_dir_not_writable_fails(self) -> None:
        settings = _make_settings(screenshot_dir="/this/will/fail/on/write")
        runner = PrecheckRunner(settings)
        with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=1), \
             patch.object(Path, "mkdir"), \
             patch.object(Path, "write_text", side_effect=OSError(13, "Permission denied")):
            result = runner._check_permissions()
        assert result.passed is False
        assert "screenshot" in result.message.lower()

    def test_lock_dir_not_writable_fails(self) -> None:
        """Lock directory write test fails → permissions fail."""
        settings = _make_settings(lock_file="/readonly/lock/.sts2.lock")
        runner = PrecheckRunner(settings)
        lock_result = PrecheckResult(
            layer="permissions", passed=False,
            message="Lock file directory not writable: /readonly/lock",
        )
        with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=1), \
             patch.object(runner, "_check_environment",
                          return_value=PrecheckResult(layer="environment", passed=True, message="ok")), \
             patch.object(runner, "_check_config",
                          return_value=PrecheckResult(layer="config", passed=True, message="ok")), \
             patch.object(runner, "_check_resources",
                          return_value=PrecheckResult(layer="resources", passed=True, message="ok")), \
             patch.object(runner, "_check_permissions", return_value=lock_result):
            report = runner.run()
        assert report.passed is False
        assert "lock" in report.results[3].message.lower()


# ── Concurrency Layer ────────────────────────────────────────


class TestCheckConcurrency:
    def test_game_running_fails(self) -> None:
        settings = _make_settings()
        with patch.object(PrecheckRunner, "_safe_process_iter",
                          return_value=[{"name": "SlayTheSpire2.exe"}]):
            runner = PrecheckRunner(settings)
            result = runner._check_concurrency()
        assert result.passed is False
        assert result.detail.get("game_running") is True

    def test_lock_file_with_alive_holder_fails(self) -> None:
        settings = _make_settings(lock_file="/tmp/.lock")
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value="12345"), \
             patch.object(psutil, "pid_exists", return_value=True), \
             patch.object(PrecheckRunner, "_safe_process_iter", return_value=[]):
            mock_proc = MagicMock()
            mock_proc.is_running.return_value = True
            mock_proc.name.return_value = "python.exe"
            with patch.object(psutil, "Process", return_value=mock_proc):
                runner = PrecheckRunner(settings)
                result = runner._check_concurrency()
        assert result.passed is False
        assert result.detail.get("holder_alive") is True

    def test_lock_file_recycled_pid_ignored(self) -> None:
        """PID recycled to non-test process → treated as stale."""
        settings = _make_settings(lock_file="/tmp/.lock")
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value="12345"), \
             patch.object(psutil, "pid_exists", return_value=True), \
             patch.object(PrecheckRunner, "_safe_process_iter", return_value=[]):
            mock_proc = MagicMock()
            mock_proc.is_running.return_value = True
            mock_proc.name.return_value = "chrome.exe"
            with patch.object(psutil, "Process", return_value=mock_proc):
                runner = PrecheckRunner(settings)
                result = runner._check_concurrency()
        # In new impl, non-test process name → not treated as holder → stale lock
        assert result.passed is False
        assert result.detail.get("holder_alive") is False

    def test_lock_file_dead_holder_fails(self) -> None:
        settings = _make_settings(lock_file="/tmp/.lock")
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value="99999"), \
             patch.object(psutil, "pid_exists", return_value=False), \
             patch.object(PrecheckRunner, "_safe_process_iter", return_value=[]):
            runner = PrecheckRunner(settings)
            result = runner._check_concurrency()
        assert result.passed is False
        assert result.detail.get("holder_alive") is False

    def test_lock_file_invalid_content_fails(self) -> None:
        settings = _make_settings(lock_file="/tmp/.lock")
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value="not-a-pid"), \
             patch.object(PrecheckRunner, "_safe_process_iter", return_value=[]):
            runner = PrecheckRunner(settings)
            result = runner._check_concurrency()
        assert result.passed is False
        assert result.detail.get("holder_alive") is False

    def test_concurrency_ok(self) -> None:
        settings = _make_settings(lock_file="/tmp/.lock")
        with patch.object(Path, "exists", return_value=False), \
             patch.object(PrecheckRunner, "_safe_process_iter", return_value=[]):
            runner = PrecheckRunner(settings)
            result = runner._check_concurrency()
        assert result.passed is True


# ── Fail-Fast Behavior ───────────────────────────────────────


class TestRunFailFast:
    def test_environment_failure_stops_immediately(self) -> None:
        settings = _make_settings()
        runner = PrecheckRunner(settings, cli_discover=lambda: None)
        with patch.object(PrecheckRunner, "_safe_process_iter", return_value=[]):
            report = runner.run()
        assert report.passed is False
        assert len(report.results) == 1
        assert report.results[0].layer == "environment"

    def test_config_failure_after_env_passes(self) -> None:
        settings = _make_settings()
        runner = PrecheckRunner(settings)
        with patch.object(runner, "_check_environment",
                          return_value=PrecheckResult(layer="environment", passed=True, message="ok")), \
             patch.object(runner, "_check_config",
                          return_value=PrecheckResult(layer="config", passed=False, message="bad config")):
            report = runner.run()
        assert report.passed is False
        assert len(report.results) == 2
        assert report.results[0].layer == "environment"
        assert report.results[1].layer == "config"

    def test_all_pass(self) -> None:
        runner = PrecheckRunner(_make_settings(), cli_discover=lambda: "/valid/cli")
        with patch.object(runner, "_check_environment",
                          return_value=PrecheckResult(layer="environment", passed=True, message="ok")), \
             patch.object(runner, "_check_config",
                          return_value=PrecheckResult(layer="config", passed=True, message="ok")), \
             patch.object(runner, "_check_resources",
                          return_value=PrecheckResult(layer="resources", passed=True, message="ok")), \
             patch.object(runner, "_check_permissions",
                          return_value=PrecheckResult(layer="permissions", passed=True, message="ok")), \
             patch.object(runner, "_check_concurrency",
                          return_value=PrecheckResult(layer="concurrency", passed=True, message="ok")):
            report = runner.run()
        assert report.passed is True
        assert len(report.results) == 5
        assert report.failed_layers == []


# ── Disk Threshold Calculation ────────────────────────────────


class TestDiskThreshold:
    def test_threshold_exact_boundary_passes(self) -> None:
        settings = _make_settings(disk_threshold_mb=100, evidence_dir="/ev")
        mock_mem = MagicMock()
        mock_mem.percent = 50
        with patch.object(psutil, "virtual_memory", return_value=mock_mem), \
             patch("shutil.disk_usage") as mock_du, \
             patch.object(Path, "exists", return_value=True):
            mock_du.return_value = MagicMock(free=100 * 1024 * 1024)
            runner = PrecheckRunner(settings)
            result = runner._check_resources()
        assert result.passed is True

    def test_threshold_one_mb_below_fails(self) -> None:
        settings = _make_settings(disk_threshold_mb=100, evidence_dir="/ev")
        mock_mem = MagicMock()
        mock_mem.percent = 50
        with patch.object(psutil, "virtual_memory", return_value=mock_mem), \
             patch("shutil.disk_usage") as mock_du, \
             patch.object(Path, "exists", return_value=True):
            mock_du.return_value = MagicMock(free=99 * 1024 * 1024)
            runner = PrecheckRunner(settings)
            result = runner._check_resources()
        assert result.passed is False
