"""Steam and game process controller (FR33, FR34, FR35, FR44).

Manages Steam and game process lifecycle: start, stop, restart,
health check, and cleanup. Implements context manager protocol
for guaranteed teardown with 10s timeout enforcement.

FR44 (security sandbox) reserved via _create_job_object /
_assign_to_job stubs — full implementation in Epic 4 Beta.
"""

import subprocess
import time
from pathlib import Path, PureWindowsPath
from typing import Any, cast

import psutil

from sts2_autotest.common.logging import get_logger

logger = get_logger("core.steam")

_STEAM_EXE = "steam.exe"
_GAME_EXE = "SlayTheSpire2.exe"
_DEFAULT_APP_ID = "2868840"


class SteamController:
    """Manage Steam and game process lifecycle.

    Implements context manager protocol: __exit__ terminates
    game first, then Steam, with 10s timeout enforcement.
    """

    _IS_WINDOWS: bool = hasattr(__import__("os", fromlist=["name"]), "name") and __import__("os", fromlist=["name"]).name == "nt"

    def __init__(
        self,
        steam_exe: str = _STEAM_EXE,
        game_exe: str = _GAME_EXE,
        app_id: str = _DEFAULT_APP_ID,
        startup_timeout: float = 60.0,
        game_dir: str | None = None,
    ) -> None:
        self.steam_exe = steam_exe
        self.steam_process_name = PureWindowsPath(steam_exe).name
        self.game_exe = game_exe
        self.app_id = app_id
        self.startup_timeout = startup_timeout
        self.game_dir = game_dir
        self._steam_pid: int | None = None
        self._game_pid: int | None = None
        self._job_handle: Any = self._create_job_object()

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

    def start_game(self, *, allow_direct_fallback: bool = False) -> int:
        """Launch the game via Steam and return its PID."""
        logger.info("Starting game (app %s)...", self.app_id)
        existing_pids = self._find_game_pids()
        subprocess.Popen([self.steam_exe, "-applaunch", self.app_id])
        # Wait for game process to appear.
        start = time.monotonic()
        while time.monotonic() - start < self.startup_timeout:
            pid = self._find_game_pid(exclude_pids=existing_pids)
            if pid is not None:
                self._game_pid = pid
                self._assign_to_job(pid)
                logger.info("Game started (PID %s)", pid)
                return pid
            time.sleep(0.5)
        if allow_direct_fallback:
            logger.warning(
                "Steam launch did not start game within %ss, trying direct launch fallback",
                self.startup_timeout,
            )
            return self._start_game_direct(existing_pids)
        raise RuntimeError(f"Steam launch did not start game within {self.startup_timeout}s")

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

    # ── Job Object (B15 / Story 4.8) ───────────────────────

    def _create_job_object(self) -> Any:
        """Create a Windows Job Object for process sandboxing.

        Configures KILL_ON_JOB_CLOSE so that when the job handle is
        closed (process exit / cleanup), all assigned processes are
        automatically terminated. Falls back safely on non-Windows.
        Returns the kernel handle, or None on failure.
        """
        if not self._IS_WINDOWS:
            return None
        import ctypes
        try:
            kernel32 = ctypes.windll.kernel32
            # CreateJobObjectW(NULL, name) — name=None creates unnamed
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                logger.warning("CreateJobObjectW failed (error %d)", kernel32.GetLastError())
                return None

            # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
            # https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/ns-jobapi2-jobobject_extended_limit_information
            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", ctypes.c_ulong * 4),
                    ("IoInfo", ctypes.c_ulong * 4),
                    ("ProcessLimitInformation", ctypes.c_ulong * 2),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation[0] = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

            # SetInformationJobObject(job, JobObjectExtendedLimitInformation, info, size)
            rc = kernel32.SetInformationJobObject(
                job,
                9,  # JobObjectExtendedLimitInformation
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not rc:
                kernel32.CloseHandle(job)
                logger.warning("SetInformationJobObject failed (error %d)", kernel32.GetLastError())
                return None

            logger.info("Job object created (KILL_ON_JOB_CLOSE)")
            return job
        except (AttributeError, OSError) as exc:
            logger.warning("Failed to create job object: %s", exc)
            return None

    def _assign_to_job(self, pid: int) -> None:
        """Assign a process to the job object for sandboxing."""
        if self._job_handle is None:
            return
        import ctypes
        try:
            kernel32 = ctypes.windll.kernel32
            # OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, FALSE, pid)
            PROCESS_SET_QUOTA = 0x0100
            PROCESS_TERMINATE = 0x0001
            ACCESS = PROCESS_SET_QUOTA | PROCESS_TERMINATE
            proc_handle = kernel32.OpenProcess(ACCESS, False, pid)
            if not proc_handle:
                logger.warning("OpenProcess(%d) failed (error %d)", pid, kernel32.GetLastError())
                return
            try:
                rc = kernel32.AssignProcessToJobObject(self._job_handle, proc_handle)
                if rc:
                    logger.info("Assigned PID %d to job object", pid)
                else:
                    logger.warning("AssignProcessToJobObject(%d) failed (error %d)", pid, kernel32.GetLastError())
            finally:
                kernel32.CloseHandle(proc_handle)
        except (AttributeError, OSError) as exc:
            logger.warning("Failed to assign PID %d to job: %s", pid, exc)

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

    def _start_game_direct(self, existing_pids: set[int]) -> int:
        """Launch the game executable directly from the game directory."""
        game_dir = self._resolve_game_dir()
        game_path = self._resolve_game_path(game_dir)
        self._ensure_steam_appid(game_dir)
        logger.info("Starting game directly from %s", game_path)
        subprocess.Popen([str(game_path)], cwd=str(game_dir), shell=False)
        start = time.monotonic()
        while time.monotonic() - start < self.startup_timeout:
            pid = self._find_game_pid(exclude_pids=existing_pids)
            if pid is not None:
                self._game_pid = pid
                self._assign_to_job(pid)
                logger.info("Game started via direct fallback (PID %s)", pid)
                return pid
            time.sleep(0.5)
        raise RuntimeError(f"Game did not start within {self.startup_timeout}s")

    def _resolve_game_dir(self) -> Path:
        if self.game_dir:
            return Path(self.game_dir)
        candidate = Path(self.game_exe)
        if candidate.parent != Path("."):
            return candidate.parent
        raise RuntimeError("Direct game launch requires game_dir or absolute game_exe path")

    def _resolve_game_path(self, game_dir: Path) -> Path:
        candidate = Path(self.game_exe)
        if candidate.is_absolute():
            return candidate
        return game_dir / candidate.name

    def _ensure_steam_appid(self, game_dir: Path) -> None:
        appid_path = game_dir / "steam_appid.txt"
        expected = f"{self.app_id}\n"
        try:
            current = appid_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = None
        if current is not None and current.strip() == self.app_id:
            return
        if current != expected:
            appid_path.write_text(expected, encoding="utf-8")

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
