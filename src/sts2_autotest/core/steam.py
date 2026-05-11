"""Steam and game process controller (FR33, FR34, FR35, FR44).

Manages Steam and game process lifecycle: start, stop, restart,
health check, and cleanup. Implements context manager protocol
for guaranteed teardown with 10s timeout enforcement.

FR44 (security sandbox) reserved via _create_job_object /
_assign_to_job stubs — full implementation in Epic 4 Beta.
"""

import subprocess
import time
from pathlib import PureWindowsPath
from typing import Any, cast

import psutil

from sts2_autotest.common.logging import get_logger

logger = get_logger("core.steam")

_STEAM_EXE = "steam.exe"
_GAME_EXE = "SlayTheSpire2.exe"
_DEFAULT_APP_ID = "2719470"


class SteamController:
    """Manage Steam and game process lifecycle.

    Implements context manager protocol: __exit__ terminates
    game first, then Steam, with 10s timeout enforcement.
    """

    def __init__(
        self,
        steam_exe: str = _STEAM_EXE,
        game_exe: str = _GAME_EXE,
        app_id: str = _DEFAULT_APP_ID,
        startup_timeout: float = 60.0,
    ) -> None:
        self.steam_exe = steam_exe
        self.steam_process_name = PureWindowsPath(steam_exe).name
        self.game_exe = game_exe
        self.app_id = app_id
        self.startup_timeout = startup_timeout
        self._steam_pid: int | None = None
        self._game_pid: int | None = None

    # ── public API ──────────────────────────────────────────

    def start_steam(self) -> int:
        """Launch Steam and return its PID."""
        if self.is_process_alive(self._steam_pid, self.steam_process_name):
            logger.info("Steam is already running (PID %s)", self._steam_pid)
            return self._steam_pid  # type: ignore[return-value]
        logger.info("Starting Steam...")
        proc = subprocess.Popen([self.steam_exe])
        self._steam_pid = proc.pid
        logger.info("Steam started (PID %s)", self._steam_pid)
        return self._steam_pid

    def start_game(self) -> int:
        """Launch the game via Steam URI and return its PID."""
        logger.info("Starting game (app %s)...", self.app_id)
        existing_pids = self._find_game_pids()
        subprocess.Popen(
            ["cmd", "/c", "start", "", f"steam://run/{self.app_id}"],
            shell=False,
        )
        # Wait for game process to appear
        start = time.monotonic()
        while time.monotonic() - start < self.startup_timeout:
            pid = self._find_game_pid(exclude_pids=existing_pids)
            if pid is not None:
                self._game_pid = pid
                logger.info("Game started (PID %s)", pid)
                return pid
            time.sleep(0.5)
        raise RuntimeError(
            f"Game did not start within {self.startup_timeout}s"
        )

    def is_process_alive(self, pid: int | None, name: str) -> bool:
        """Check if a process with given PID and name is alive."""
        if pid is None:
            return False
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and self._same_process_name(proc.name(), name)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def restart_game(self) -> int:
        """Terminate current game process, then start fresh.

        Termination: SIGTERM (5s grace) → SIGKILL.
        """
        self._terminate_game()
        return self.start_game()

    def stop_game(self) -> None:
        """Stop the game process."""
        self._terminate_game()

    def stop_steam(self) -> None:
        """Stop the Steam process."""
        self._terminate_process(self._steam_pid, self.steam_process_name, "Steam")
        self._steam_pid = None

    # ── context manager ─────────────────────────────────────

    def __enter__(self) -> "SteamController":
        self.start_steam()
        self.start_game()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        deadline = time.monotonic() + 10.0
        try:
            self._terminate_game(deadline=deadline)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.TimeoutExpired) as exc:
            logger.warning("Game cleanup failed during context exit: %s", exc)
        finally:
            self._terminate_process(
                self._steam_pid, self.steam_process_name, "Steam", deadline
            )

    # ── Job Object stubs (FR44, Epic 4 Beta) ────────────────

    def _create_job_object(self) -> Any:
        """Reserved: create Windows Job Object for sandboxing."""
        return None

    def _assign_to_job(self, pid: int) -> None:
        """Reserved: assign process to Job Object."""
        pass

    # ── internals ───────────────────────────────────────────

    @staticmethod
    def _same_process_name(actual: str | None, expected: str) -> bool:
        return (actual or "").casefold() == PureWindowsPath(expected).name.casefold()

    def _find_game_pids(self) -> set[int]:
        """Return all currently running game process PIDs."""
        pids: set[int] = set()
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if self._same_process_name(cast(str | None, proc.info["name"]), self.game_exe):
                    pids.add(cast(int, proc.info["pid"]))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return pids

    def _find_game_pid(self, exclude_pids: set[int] | None = None) -> int | None:
        """Scan running processes for the game executable."""
        exclude_pids = exclude_pids or set()
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pid = cast(int, proc.info["pid"])
                if pid not in exclude_pids and self._same_process_name(
                    cast(str | None, proc.info["name"]), self.game_exe
                ):
                    return pid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    def _terminate_game(self, deadline: float | None = None) -> None:
        self._terminate_process(self._game_pid, self.game_exe, "Game", deadline)
        self._game_pid = None

    def _terminate_process(
        self,
        pid: int | None,
        name: str,
        label: str,
        deadline: float | None = None,
    ) -> None:
        if pid is None or not self.is_process_alive(pid, name):
            return
        proc: psutil.Process | None = None
        try:
            proc = psutil.Process(pid)
            logger.info("Terminating %s (PID %s)...", label, pid)
            proc.terminate()
            proc.wait(timeout=self._remaining_timeout(deadline, 5.0))
            logger.info("%s terminated gracefully", label)
        except psutil.TimeoutExpired:
            logger.warning("%s did not exit, killing...", label)
            try:
                if proc is not None and self._same_process_name(proc.name(), name):
                    proc.kill()
                    proc.wait(timeout=self._remaining_timeout(deadline, 2.0))
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            logger.warning("Could not terminate %s (PID %s): %s", label, pid, exc)
        finally:
            if deadline is not None and time.monotonic() < deadline:
                while time.monotonic() < deadline:
                    time.sleep(0.1)
                    try:
                        followup = psutil.Process(pid)
                        if not self._same_process_name(followup.name(), name):
                            break
                        followup.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        break

    @staticmethod
    def _remaining_timeout(deadline: float | None, fallback: float) -> float:
        if deadline is None:
            return fallback
        return max(0.0, min(fallback, deadline - time.monotonic()))
