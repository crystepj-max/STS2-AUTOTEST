"""Tests for core/steam.py — SteamController process management."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import psutil
import pytest

from sts2_autotest.core.steam import _GAME_EXE, _IS_MACOS, SteamController


@pytest.fixture
def sc() -> SteamController:
    return SteamController()


def test_default_app_id_matches_sts2() -> None:
    assert SteamController().app_id == "2868840"


class TestStartSteam:
    """AC#1: Steam process start."""

    def test_start_steam_returns_pid(self, sc: SteamController) -> None:
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            if _IS_MACOS:
                # macOS: start_steam polls for actual process, not Popen PID
                with patch.object(sc, "_poll_for_process", return_value=12345):
                    pid = sc.start_steam()
                assert pid == 12345
                assert sc._steam_pid == 12345
            else:
                pid = sc.start_steam()
                assert pid == 12345
                assert sc._steam_pid == 12345

    def test_start_steam_skips_if_already_running(self, sc: SteamController) -> None:
        sc._steam_pid = 99999
        with patch.object(sc, "is_process_alive", return_value=True):
            with patch("subprocess.Popen") as mock_popen:
                pid = sc.start_steam()
                assert pid == 99999
                mock_popen.assert_not_called()

    def test_start_steam_checks_custom_executable_name(self) -> None:
        sc = SteamController(steam_exe=r"C:\Steam\CustomSteam.exe")
        sc._steam_pid = 99999
        with patch.object(sc, "is_process_alive", return_value=True) as mock_alive:
            pid = sc.start_steam()
        assert pid == 99999
        mock_alive.assert_called_once_with(99999, "CustomSteam.exe")


class TestStartGame:
    """AC#2: Game process start via Steam URI."""

    def test_start_game_finds_existing_process(self, sc: SteamController) -> None:
        sc.startup_timeout = 1.0
        with patch("subprocess.Popen"):
            with patch.object(sc, "_find_game_pids", return_value=set()):
                with patch.object(sc, "_find_game_pid", return_value=54321):
                    pid = sc.start_game(reuse_existing=True)
                    assert pid == 54321
                    assert sc._game_pid == 54321

    def test_start_game_reuses_existing_process_without_relaunch(self, sc: SteamController) -> None:
        with patch("subprocess.Popen") as mock_popen:
            with patch.object(sc, "_find_game_pids", return_value={54321}):
                with patch.object(sc, "_find_game_pid") as mock_find_new:
                    pid = sc.start_game(reuse_existing=True)

        assert pid == 54321
        assert sc._game_pid == 54321
        mock_find_new.assert_not_called()
        mock_popen.assert_not_called()

    def test_start_game_uses_steam_applaunch(self) -> None:
        if _IS_MACOS:
            sc = SteamController()
            sc.startup_timeout = 1.0
            with patch("subprocess.Popen") as mock_popen:
                with patch.object(sc, "_find_game_pids", return_value=set()):
                    with patch.object(sc, "_find_game_pid", return_value=54321):
                        pid = sc.start_game()
            assert pid == 54321
            args, kwargs = mock_popen.call_args
            assert args[0][0] == "open"
            assert args[0][1].endswith("SlayTheSpire2.app") or args[0][1].startswith("steam://run/")
            assert kwargs["env"]["STS2_ENABLE_DEBUG_ACTIONS"] == "1"
            assert kwargs["env"]["STS2_API_PORT"] == "8080"
        else:
            sc = SteamController(steam_exe=r"C:\Program Files (x86)\Steam\steam.exe")
            sc.startup_timeout = 1.0
            with patch("subprocess.Popen") as mock_popen:
                with patch.object(sc, "_find_game_pids", return_value=set()):
                    with patch.object(sc, "_find_game_pid", return_value=54321):
                        pid = sc.start_game()
            assert pid == 54321
            args, kwargs = mock_popen.call_args
            assert args[0] == [
                r"C:\Program Files (x86)\Steam\steam.exe",
                "-applaunch",
                "2868840",
            ]
            assert kwargs["env"]["STS2_ENABLE_DEBUG_ACTIONS"] == "1"

    def test_start_game_injects_debug_env_on_macos(self) -> None:
        """macOS 启动必须注入调试 API 环境（硬重启后调试控制台不丢失）。"""
        if not _IS_MACOS:
            return
        sc = SteamController()
        sc.startup_timeout = 1.0
        with patch("subprocess.Popen") as mock_popen:
            with patch.object(sc, "_find_game_pids", return_value=set()):
                with patch.object(sc, "_find_game_pid", return_value=54321):
                    sc.start_game()
        _, kwargs = mock_popen.call_args
        assert kwargs["env"]["STS2_ENABLE_DEBUG_ACTIONS"] == "1"

    def test_find_game_bundle_prefers_constructor_game_dir(self, tmp_path, monkeypatch) -> None:
        """构造时传入的安装目录优先于环境变量与默认路径（自定义 Steam 库）。"""
        if not _IS_MACOS:
            return
        custom_dir = tmp_path / "custom-library"
        bundle = custom_dir / "SlayTheSpire2.app"
        bundle.mkdir(parents=True)
        monkeypatch.delenv("STS2_GAME_DIR", raising=False)
        sc = SteamController(game_dir=str(custom_dir))

        assert sc._find_game_bundle() == bundle

    def test_find_game_bundle_none_when_nothing_exists(self, tmp_path, monkeypatch) -> None:
        """构造目录、环境变量与默认路径均不存在时如实返回 None（回退 steam URI）。"""
        if not _IS_MACOS:
            return
        monkeypatch.delenv("STS2_GAME_DIR", raising=False)
        sc = SteamController(game_dir=str(tmp_path / "no-such-dir"))

        with patch("pathlib.Path.is_dir", return_value=False):
            assert sc._find_game_bundle() is None

    def test_start_game_timeout(self, sc: SteamController) -> None:
        sc.startup_timeout = 0.1
        with patch("subprocess.Popen"):
            with patch.object(sc, "_find_game_pids", return_value=set()):
                with patch.object(sc, "_find_game_pid", return_value=None):
                    with patch.object(sc, "_start_game_direct") as mock_direct:
                        with pytest.raises(RuntimeError, match="Steam launch did not start"):
                            sc.start_game()
        mock_direct.assert_not_called()

    def test_start_game_does_not_use_direct_fallback_by_default(self, sc: SteamController) -> None:
        sc.startup_timeout = 0.1
        with patch("subprocess.Popen"):
            with patch("time.sleep"):
                with patch.object(sc, "_find_game_pids", return_value=set()):
                    with patch.object(sc, "_find_game_pid", return_value=None):
                        with patch.object(sc, "_start_game_direct") as mock_direct:
                            with pytest.raises(RuntimeError, match="Steam launch did not start"):
                                sc.start_game()
        mock_direct.assert_not_called()

    def test_start_game_can_use_direct_fallback_when_enabled(self, sc: SteamController) -> None:
        sc.startup_timeout = 0.1
        with patch("subprocess.Popen"):
            with patch.object(sc, "_find_game_pids", return_value=set()):
                with patch.object(sc, "_find_game_pid", return_value=None):
                    with patch.object(sc, "_start_game_direct", return_value=54321) as mock_direct:
                        pid = sc.start_game(allow_direct_fallback=True)
        assert pid == 54321
        mock_direct.assert_called_once()

    def test_start_game_requires_game_dir_for_direct_fallback(self, sc: SteamController) -> None:
        sc.startup_timeout = 0.1
        with patch("subprocess.Popen"):
            with patch.object(sc, "_find_game_pids", return_value=set()):
                with patch.object(sc, "_find_game_pid", return_value=None):
                    with pytest.raises(RuntimeError, match="Direct game launch requires"):
                        sc.start_game(allow_direct_fallback=True)

    def test_start_game_direct_launch_sets_cwd_and_appid(self, tmp_path: Path) -> None:
        sc = SteamController(game_dir=str(tmp_path))
        sc.startup_timeout = 1.0
        # macOS needs the app bundle directory to use `open` command
        if _IS_MACOS:
            app_bundle = tmp_path / "SlayTheSpire2.app"
            app_bundle.mkdir()
        with patch("subprocess.Popen") as mock_popen:
            with patch("time.sleep"):
                with patch.object(sc, "_find_game_pid", return_value=54321):
                    pid = sc._start_game_direct(set())
        assert pid == 54321
        assert sc._game_pid == 54321
        assert mock_popen.call_count == 1
        if _IS_MACOS:
            call_args = mock_popen.call_args_list[0]
            assert call_args[0][0][0] == "open"
        else:
            _, direct_kwargs = mock_popen.call_args_list[0]
            assert direct_kwargs["cwd"] == str(tmp_path)
        assert (tmp_path / "steam_appid.txt").read_text(encoding="utf-8") == "2868840\n"

    def test_start_game_direct_accepts_existing_appid_without_trailing_newline(
        self, tmp_path: Path
    ) -> None:
        sc = SteamController(game_dir=str(tmp_path))
        sc.startup_timeout = 1.0
        appid_path = tmp_path / "steam_appid.txt"
        appid_path.write_text("2868840", encoding="utf-8")

        with patch("subprocess.Popen") as mock_popen:
            with patch("time.sleep"):
                with patch.object(sc, "_find_game_pid", return_value=54321):
                    pid = sc._start_game_direct(set())

        assert pid == 54321
        assert mock_popen.call_count == 1
        assert appid_path.read_text(encoding="utf-8") == "2868840"

class TestIsProcessAlive:
    """AC#3: Process alive detection."""

    def test_none_pid_returns_false(self, sc: SteamController) -> None:
        assert sc.is_process_alive(None, "any.exe") is False

    def test_process_running_correct_name(self, sc: SteamController) -> None:
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.is_running.return_value = True
            mock_proc.return_value.name.return_value = "steam.exe"
            assert sc.is_process_alive(100, "steam.exe") is True

    def test_process_running_wrong_name(self, sc: SteamController) -> None:
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.is_running.return_value = True
            mock_proc.return_value.name.return_value = "other.exe"
            assert sc.is_process_alive(100, "steam.exe") is False

    def test_process_name_matching_is_case_insensitive(self, sc: SteamController) -> None:
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.is_running.return_value = True
            mock_proc.return_value.name.return_value = "STEAM.EXE"
            assert sc.is_process_alive(100, "steam.exe") is True

    def test_process_not_found(self, sc: SteamController) -> None:
        with patch("psutil.Process", side_effect=psutil.NoSuchProcess(100)):
            assert sc.is_process_alive(100, "steam.exe") is False


class TestRestartGame:
    """AC#4: Game restart with terminate → start."""

    def test_restart_terminates_then_starts(self, sc: SteamController) -> None:
        sc._game_pid = 54321
        with patch.object(sc, "_terminate_game") as mock_stop:
            with patch.object(sc, "start_game", return_value=12345) as mock_start:
                pid = sc.restart_game()
                mock_stop.assert_called_once()
                mock_start.assert_called_once()
                assert pid == 12345


class TestContextManager:
    """AC#5: Context manager protocol with cleanup order."""

    def test_enter_starts_steam_and_game(self, sc: SteamController) -> None:
        with patch.object(sc, "start_steam", return_value=111) as mock_steam:
            with patch.object(sc, "start_game", return_value=222) as mock_game:
                result = sc.__enter__()
                assert result is sc
                mock_steam.assert_called_once()
                mock_game.assert_called_once()

    def test_exit_terminates_game_then_steam(self, sc: SteamController) -> None:
        sc._game_pid = 222
        sc._steam_pid = 111
        calls: list[str] = []

        def track_game(deadline: Any = None) -> None:
            calls.append("game_stopped")

        def track_steam(pid: Any, name: str, label: str, deadline: Any = None) -> None:
            calls.append("steam_stopped")

        with patch.object(sc, "_terminate_game", side_effect=track_game):
            with patch.object(sc, "_terminate_process", side_effect=track_steam):
                sc.__exit__(None, None, None)
        assert calls == ["game_stopped", "steam_stopped"]

    def test_exit_force_cleanup_on_timeout(self, sc: SteamController) -> None:
        sc._game_pid = 222
        sc._steam_pid = 111
        with patch.object(sc, "_terminate_game") as mock_game:
            with patch.object(sc, "_terminate_process") as mock_steam:
                sc.__exit__(None, None, None)
                mock_game.assert_called_once()
                # _terminate_process receives deadline parameter
                assert mock_steam.call_args.kwargs.get(
                    "deadline"
                ) is not None or len(mock_steam.call_args.args) >= 4

    def test_exit_passes_deadline_to_game_cleanup(self, sc: SteamController) -> None:
        with patch.object(sc, "_terminate_game") as mock_game:
            with patch.object(sc, "_terminate_process"):
                sc.__exit__(None, None, None)
        assert mock_game.call_args.kwargs.get("deadline") is not None

    def test_exit_still_attempts_steam_cleanup_if_game_cleanup_raises(
        self, sc: SteamController
    ) -> None:
        sc._steam_pid = 111
        with patch.object(sc, "_terminate_game", side_effect=psutil.AccessDenied(222)):
            with patch.object(sc, "_terminate_process") as mock_steam:
                sc.__exit__(None, None, None)
        mock_steam.assert_called_once()


class TestJobObject:
    """AC#6: Job Object stubs reserved for Beta."""

    def test_create_job_object_returns_value_or_none(self, sc: SteamController) -> None:
        """On Windows returns a kernel handle, on other platforms None."""
        result = sc._create_job_object()
        if sc._IS_WINDOWS:
            # On Windows, result may be a valid handle or None on failure;
            # either is acceptable — the contract is "does not raise".
            pass
        else:
            assert result is None

    def test_assign_to_job_does_not_raise(self, sc: SteamController) -> None:
        """Assign a non-existent PID — should not raise."""
        sc._assign_to_job(12345)  # should not raise


class TestTerminateProcess:
    """Graceful termination → force kill."""

    def test_terminate_graceful(self, sc: SteamController) -> None:
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.is_running.return_value = True
            mock_proc.return_value.name.return_value = "game.exe"
            sc._terminate_process(100, "game.exe", "Game")
            mock_proc.return_value.terminate.assert_called_once()
            mock_proc.return_value.wait.assert_called_once_with(timeout=5)

    def test_terminate_timeout_triggers_kill(self, sc: SteamController) -> None:
        with patch("psutil.Process") as mock_proc_cls:
            mock_proc = mock_proc_cls.return_value
            mock_proc.is_running.return_value = True
            mock_proc.name.return_value = "game.exe"
            mock_proc.wait.side_effect = [
                psutil.TimeoutExpired(5),  # first wait raises
                None,  # kill succeeds, second wait is OK
            ]
            with patch("time.sleep"):  # skip sleeps in finally block
                with patch("psutil.pid_exists", return_value=False):  # process gone after kill
                    sc._terminate_process(100, "game.exe", "Game")
            mock_proc.kill.assert_called_once()

    def test_terminate_access_denied_does_not_raise(self, sc: SteamController) -> None:
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.is_running.return_value = True
            mock_proc.return_value.name.return_value = "game.exe"
            mock_proc.return_value.terminate.side_effect = psutil.AccessDenied(100)
            sc._terminate_process(100, "game.exe", "Game")

    def test_terminate_kill_wait_timeout_does_not_raise(self, sc: SteamController) -> None:
        with patch("psutil.Process") as mock_proc_cls:
            mock_proc = mock_proc_cls.return_value
            mock_proc.is_running.return_value = True
            mock_proc.name.return_value = "game.exe"
            mock_proc.wait.side_effect = [
                psutil.TimeoutExpired(5),
                psutil.TimeoutExpired(2),
                None,
            ]
            with patch("time.sleep"):
                with patch("psutil.pid_exists", return_value=False):
                    sc._terminate_process(100, "game.exe", "Game")

    def test_deadline_cleanup_does_not_kill_reused_pid(self, sc: SteamController) -> None:
        with patch("psutil.Process") as mock_proc_cls:
            original_proc = MagicMock()
            original_proc.is_running.return_value = True
            original_proc.name.return_value = "game.exe"
            reused_proc = MagicMock()
            reused_proc.name.return_value = "other.exe"
            mock_proc_cls.side_effect = [original_proc, original_proc, reused_proc]
            original_proc.wait.side_effect = psutil.TimeoutExpired(5)
            with patch("time.sleep"):
                with patch("time.monotonic", side_effect=[0.0, 0.5, 1.0, 1.1, 1.2]):
                    sc._terminate_process(100, "game.exe", "Game", deadline=2.0)
        reused_proc.kill.assert_not_called()


class TestFindGamePid:
    """Process scanning for game executable."""

    def test_finds_game_process(self, sc: SteamController) -> None:
        mock_proc = MagicMock()
        mock_proc.info = {"pid": 7777, "name": _GAME_EXE}
        with patch("psutil.process_iter", return_value=[mock_proc]):
            pid = sc._find_game_pid()
            assert pid == 7777

    def test_returns_none_when_not_found(self, sc: SteamController) -> None:
        with patch("psutil.process_iter", return_value=[]):
            pid = sc._find_game_pid()
            assert pid is None

    def test_permission_error_is_skipped(self, sc: SteamController) -> None:
        mock_proc = MagicMock()
        mock_proc.info = MagicMock(side_effect=PermissionError("not permitted"))
        with patch("psutil.process_iter", return_value=[mock_proc]):
            pid = sc._find_game_pid()
            assert pid is None

    def test_find_game_pids_permission_error_is_skipped(self, sc: SteamController) -> None:
        mock_proc = MagicMock()
        mock_proc.info = MagicMock(side_effect=PermissionError("not permitted"))
        with patch("psutil.process_iter", return_value=[mock_proc]):
            pids = sc._find_game_pids()
            assert pids == set()

    def test_start_game_ignores_existing_game_process(self, sc: SteamController) -> None:
        sc.startup_timeout = 1.0
        old_proc = MagicMock()
        old_proc.info = {"pid": 100, "name": _GAME_EXE}
        new_proc = MagicMock()
        new_proc.info = {"pid": 200, "name": _GAME_EXE}
        with patch("subprocess.Popen"):
            with patch("time.sleep"):
                with patch("psutil.process_iter", side_effect=[[old_proc], [old_proc, new_proc]]):
                    pid = sc.start_game()
        assert pid == 200

    def test_stop_steam_uses_custom_executable_name(self) -> None:
        sc = SteamController(steam_exe=r"C:\Steam\CustomSteam.exe")
        sc._steam_pid = 111
        with patch.object(sc, "_terminate_process") as mock_terminate:
            sc.stop_steam()
        mock_terminate.assert_called_once_with(111, "CustomSteam.exe", "Steam")
