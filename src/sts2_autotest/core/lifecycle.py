"""Game lifecycle management for autotest (self-healing runtime).

背景（STS2 v0.107.1 真实缺陷，已在 drive_m1d.py 实测确认）：
- 游戏会崩溃、旅行挂起（is_traveling=True 且 available_nodes=0）、以及
  phantom COMBAT（screen=COMBAT 但 combat=null / in_combat=False），这些状态
  没有任何 in-game 动作能退出，只能重启游戏进程。
- 框架的 ``SteamController`` 能启动/停止进程，但默认的 ``open steam://run/...``
  启动方式**不会注入调试 API 的环境变量**，重启后 8080 API 与调试动作
  （set_hp / win_combat / abandon_run 等）都不可用，自动测试因此卡死。

本模块填补该缺口，提供 ``GameLifecycleManager``：
- 直接 Popen 游戏可执行文件并注入 ``STS2_API_PORT`` / ``STS2_ENABLE_DEBUG_ACTIONS`` /
  ``STS2_GAME_DIR``，使 8080 API 与调试动作随进程起来；
- ``wait_for_api`` / ``is_api_up`` 轮询直到游戏可被控制；
- ``relaunch_run`` 触发崩溃并拉起一个全新对局；
- 纯函数检测器 ``is_phantom_combat`` / ``travel_hang_expired`` 让驱动判定
  当前 run 是否已卡死、必须重启。

所有 in-game 调用走 ``AgentAdapter``，进程控制走 ``subprocess`` + ``psutil``。
不修改 ``steam.py``（其单测已存在），仅在需要时复用其 ``SteamController`` 实例。
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import IO, Any, Callable
from urllib.parse import urlparse

import psutil

from sts2_autotest.common.errors import EnvironmentBlockReason
from sts2_autotest.common.logging import get_logger

# 就绪探测瞬态抖动重试间隔（秒）。测试可 monkeypatch 为 0 以保持快速。
_PROBE_RETRY_GAP_SECONDS = 2.0

logger = get_logger("core.lifecycle")

_IS_MACOS = sys.platform == "darwin"
_IS_WINDOWS = sys.platform == "win32"

_DEFAULT_APP_ID = "2868840"
_DEFAULT_PORT = "8080"


def _port_from_endpoint(endpoint: str) -> str:
    """Extract the port from an endpoint URL like http://127.0.0.1:8080."""
    try:
        port = urlparse(endpoint).port
        if port:
            return str(port)
    except Exception:
        pass
    return _DEFAULT_PORT


@dataclass(frozen=True)
class EnvironmentReadiness:
    """Result of a pre-run environment readiness check + bounded recovery.

    ``ready`` True means the game control API is healthy, state and actions are
    readable and the screen is not UNKNOWN. When not ready, ``reason`` explains
    why (always an environment block, never a product/platform failure).
    """

    ready: bool
    reason: EnvironmentBlockReason | None = None
    recovered: bool = False
    recovery_attempts: int = 0
    pre_process_present: bool = False
    pre_control_ready: bool = False
    checks: dict[str, Any] = field(default_factory=dict)
    actions_taken: list[str] = field(default_factory=list)
    duration_ms: int = 0
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "reason": str(self.reason) if self.reason is not None else None,
            "recovered": self.recovered,
            "recovery_attempts": self.recovery_attempts,
            "pre_process_present": self.pre_process_present,
            "pre_control_ready": self.pre_control_ready,
            "checks": self.checks,
            "actions_taken": list(self.actions_taken),
            "duration_ms": self.duration_ms,
            "detail": self.detail,
        }


def _default_game_exe(game_dir: str | None) -> str | None:
    """Best-effort default game executable path from a game directory."""
    if not game_dir:
        return None
    if _IS_MACOS:
        return os.path.join(
            game_dir, "SlayTheSpire2.app", "Contents", "MacOS", "Slay the Spire 2"
        )
    if _IS_WINDOWS:
        return os.path.join(game_dir, "SlayTheSpire2.exe")
    return os.path.join(game_dir, "SlayTheSpire2")


class GameLifecycleManager:
    """Manage the game process lifecycle for autotest, with debug API enabled.

    Typical driver loop::

        mgr = GameLifecycleManager(adapter, character_id="IRONCLAD")
        await mgr.ensure_game_up()
        for step in range(STEP_CAP):
            state = (await adapter.get_state()).model_dump()
            if mgr.is_phantom_combat(state):
                await mgr.relaunch_run()
                continue
            if mgr.travel_hang_expired(state, traveling_since, time.time()):
                await mgr.relaunch_run()
                continue
            ...  # normal navigation / progress_until
    """

    def __init__(
        self,
        adapter: Any,
        *,
        game_exe: str | None = None,
        game_dir: str | None = None,
        game_cwd: str | None = None,
        game_env: dict[str, str] | None = None,
        steam_controller: Any = None,
        app_id: str = _DEFAULT_APP_ID,
        hang_threshold: float = 18.0,
        max_relaunches: int = 15,
        api_timeout: float = 150.0,
        launch_timeout: float = 120.0,
        poll_interval: float = 3.0,
        down_polls: int = 20,
        down_poll_interval: float = 3.0,
        character_id: str = "IRONCLAD",
        game_log: str | None = None,
        port_release_timeout: float = 15.0,
    ) -> None:
        """Initialize.

        Args:
            adapter: an ``AgentAdapter`` instance (debug_actions=True recommended).
            game_exe: absolute path to the game executable. Falls back to env
                ``STS2_GAME_EXE`` then ``<game_dir>/<platform default>``.
            game_dir: game install directory; also exported as ``STS2_GAME_DIR``.
                Falls back to env ``STS2_GAME_DIR``.
            game_cwd: working dir for the launched process (defaults to exe dir).
            game_env: extra environment variables merged into the launch env.
                ``STS2_API_PORT`` / ``STS2_ENABLE_DEBUG_ACTIONS`` are always set.
            steam_controller: optional ``SteamController`` for process discovery.
            app_id: Steam app id (informational; direct launch is used).
            hang_threshold: seconds a travel-hang may persist before ``travel_hang_expired``.
            max_relaunches: safety cap on ``relaunch_run`` calls in a driver loop.
            api_timeout: max seconds to wait for the API after a launch.
            launch_timeout: (reserved) max seconds for a single launch.
            poll_interval: seconds between API readiness polls.
            down_polls / down_poll_interval: how long to wait for API-down after crash.
            character_id: character used by ``start_run``.
            game_log: optional file path for the launched process stdout/stderr.
        """
        self.adapter = adapter
        self._game_exe_arg = game_exe
        self._game_dir_arg = game_dir
        self._game_cwd_arg = game_cwd
        self._extra_env = dict(game_env or {})
        self.steam_controller = steam_controller
        self.app_id = app_id
        self.hang_threshold = float(hang_threshold)
        self.max_relaunches = int(max_relaunches)
        self.api_timeout = float(api_timeout)
        self.launch_timeout = float(launch_timeout)
        self.poll_interval = float(poll_interval)
        self.down_polls = int(down_polls)
        self.down_poll_interval = float(down_poll_interval)
        self.character_id = character_id
        self.game_log = game_log
        self.port_release_timeout = float(port_release_timeout)

        # resolved at init
        self.game_exe, self.game_cwd, self.game_dir, self.game_env = self._resolve()
        self._proc: subprocess.Popen[Any] | None = None
        self._game_log_handle: IO[str] | None = None
        self._pid: int | None = None
        self.relaunch_count = 0

    # ── resolution ──────────────────────────────────────────

    def _resolve(self) -> tuple[str, str, str | None, dict[str, str]]:
        exe = self._game_exe_arg or os.environ.get("STS2_GAME_EXE")
        gdir = self._game_dir_arg or os.environ.get("STS2_GAME_DIR")
        if not exe and gdir:
            exe = _default_game_exe(gdir)
        if not exe:
            raise ValueError(
                "GameLifecycleManager requires game_exe (or env STS2_GAME_EXE / STS2_GAME_DIR)"
            )
        cwd = self._game_cwd_arg or os.path.dirname(exe)
        endpoint = getattr(self.adapter, "endpoint", None) or "http://127.0.0.1:8080"
        port = _port_from_endpoint(endpoint)
        env = dict(self._extra_env)
        env.setdefault("STS2_API_PORT", port)
        env.setdefault("STS2_ENABLE_DEBUG_ACTIONS", "1")
        if gdir:
            env.setdefault("STS2_GAME_DIR", gdir)
        return exe, cwd, gdir, env

    # ── process control ────────────────────────────────────

    def _app_bundle_path(self) -> str | None:
        """Best-effort locate the ``.app`` bundle containing ``game_exe``.

        Launching via ``open <bundle>`` (macOS) is far more reliable than
        ``Popen`` of the inner binary, which frequently comes up with an
        unresponsive control API (screen stuck at ``UNKNOWN``). The bundle path
        is derived by walking up from ``game_exe`` to the first ``*.app`` segment.
        """
        exe = self.game_exe or ""
        if not exe:
            return None
        parts = os.path.normpath(exe).split(os.sep)
        for i in range(len(parts) - 1, -1, -1):
            if parts[i].endswith(".app"):
                return os.sep + os.path.join(*parts[: i + 1])
        return None

    def _discover_game_pid(self) -> int | None:
        """Find the externally-launched game pid (best-effort, never raises)."""
        if not self.game_exe:
            return None
        needle = os.path.basename(self.game_exe).lower()
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    cmd = " ".join(proc.info.get("cmdline") or []).lower()
                    if (needle and needle in name) or (
                        self.game_exe.lower() in cmd
                    ):
                        return int(proc.info.get("pid"))  # type: ignore[arg-type]
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass
        return None

    def _launch_via_open(self) -> int:
        """Launch the game as a macOS GUI app via ``open``.

        Unlike ``Popen`` of the inner binary, this starts the game the same way
        a normal user/sandbox launch does — with a stable control API. The debug
        API env (``STS2_API_PORT`` / ``STS2_ENABLE_DEBUG_ACTIONS`` /
        ``STS2_GAME_DIR``) is passed explicitly so the relaunched instance keeps
        the 8080 debug actions working. The process is owned by ``launchd``, not
        this Python process, so no child handle is tracked.
        """
        env = dict(os.environ)
        env.update(self.game_env)
        self._close_game_log()
        bundle = self._app_bundle_path()
        if bundle and os.path.exists(bundle):
            cmd: list[str] = ["open", bundle]
        else:
            # Last-resort: open by application name.
            cmd = ["open", "-a", "Slay the Spire 2"]
        logger.info("launch game via open: %s", " ".join(cmd))
        subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._proc = None
        self._pid = None
        # Best-effort: return the launched game pid for logging/diagnostics.
        return self._discover_game_pid() or 0

    def launch(self) -> int:
        """Launch the game with debug-API env. Returns the game PID (best-effort).

        On macOS the game is launched via ``open <bundle>`` (stable control API);
        on other platforms the inner binary is Popen'd directly. A double-launch
        guard prevents spawning a second instance (which would trigger Steam's
        "already running" error).
        """
        # Guard: never launch a second instance.
        if self._game_process_present():
            logger.info("launch skipped: game process already present")
            return self._discover_game_pid() or 0
        if _IS_MACOS:
            return self._launch_via_open()
        # Non-macOS: original Popen launch of the inner binary.
        env = dict(os.environ)
        env.update(self.game_env)
        self._close_game_log()
        stdout: Any = subprocess.DEVNULL
        if self.game_log:
            self._game_log_handle = open(self.game_log, "a", encoding="utf-8")
            stdout = self._game_log_handle
        self._proc = subprocess.Popen(
            [self.game_exe],
            cwd=self.game_cwd,
            env=env,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._pid = self._proc.pid
        logger.info("launch game pid=%s exe=%s", self._pid, self.game_exe)
        return self._pid

    def _close_game_log(self) -> None:
        if self._game_log_handle is not None:
            try:
                self._game_log_handle.close()
            except OSError:
                pass
            self._game_log_handle = None

    def terminate(self) -> None:
        """Terminate the managed game process (best-effort).

        Covers three launch origins:
        * framework-launched (``self._proc`` / ``self._pid``),
        * Steam-managed (via ``SteamController``),
        * externally launched — e.g. a macOS ``.app`` bundle opened directly by
          the user or a harness. These have no tracked handle/pid, so we also
          match by executable path / process name (see
          ``_terminate_external_game_processes``). Without this, an externally
          launched game survives ``terminate()`` and the controlled-restart
          fallback can never reach a clean MAIN_MENU.
        """
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
        # also try by pid in case proc handle is stale
        if self._pid is not None:
            try:
                p = psutil.Process(self._pid)
                if p.is_running():
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            except Exception:
                pass
        # Kill any externally-launched game process (no tracked handle). This is
        # what makes the cancel-cleanup controlled restart work when the game was
        # not spawned by the framework (common on macOS app-bundle setups).
        self._terminate_external_game_processes()
        # optionally use SteamController to catch externally-launched processes
        if self.steam_controller is not None:
            try:
                self.steam_controller.stop_game()
            except Exception as exc:
                logger.warning("steam_controller.stop_game failed: %s", exc)
        self._proc = None
        self._pid = None
        self._close_game_log()

    def _terminate_external_game_processes(self) -> None:
        """Kill game processes not tracked by ``self._proc`` / ``self._pid``.

        Used when the game was launched externally (e.g. a macOS ``.app`` bundle
        opened by the user or a harness). Matches by executable path or by the
        basename of the configured game executable. Never raises.
        """
        try:
            needle = os.path.basename(self.game_exe or "").lower()
        except Exception:
            needle = ""
        if not needle and not self.game_exe:
            return
        own_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info.get("pid") == own_pid:
                    continue
                name = (proc.info.get("name") or "").lower()
                cmd = " ".join(proc.info.get("cmdline") or []).lower()
                matched = False
                if needle and needle in name:
                    matched = True
                if self.game_exe and self.game_exe.lower() in cmd:
                    matched = True
                if matched:
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                continue

    # ── API readiness ──────────────────────────────────────

    @staticmethod
    def _as_dict(state: Any) -> dict[str, Any]:
        if isinstance(state, dict):
            return state
        if hasattr(state, "model_dump"):
            payload = state.model_dump()
            return payload if isinstance(payload, dict) else {}
        return {}

    async def is_api_up(self) -> bool:
        """Return True if the game API answers with a known screen."""
        try:
            st = self._as_dict(await self.adapter.get_state())
            return st.get("screen") is not None
        except Exception:
            return False

    async def wait_for_api(self, timeout: float | None = None) -> bool:
        """Poll until the game API answers (screen present, even UNKNOWN)."""
        timeout = self.api_timeout if timeout is None else float(timeout)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if await self.is_api_up():
                logger.info("wait_for_api ok")
                return True
            await asyncio.sleep(self.poll_interval)
        logger.warning("wait_for_api timeout after %.0fs", timeout)
        return False

    async def wait_for_controllable(self, timeout: float | None = None) -> bool:
        """Poll until the game reaches a controllable (non-``UNKNOWN``) screen.

        A freshly launched game sits at ``UNKNOWN`` for a while while it
        initializes; ``wait_for_api`` would return immediately on that first
        ``UNKNOWN`` reading and the caller would wrongly conclude the launch
        failed. This waits for an actual, actionable screen before declaring
        the game ready.
        """
        timeout = self.api_timeout if timeout is None else float(timeout)
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                st = self._as_dict(await self.adapter.get_state())
                screen = str(st.get("screen") or "").upper()
            except Exception:
                screen = ""
            if screen and screen != "UNKNOWN":
                logger.info("wait_for_controllable ok screen=%s", screen)
                return True
            await asyncio.sleep(self.poll_interval)
        logger.warning("wait_for_controllable timeout after %.0fs", timeout)
        return False

    async def ensure_game_up(self, api_timeout: float | None = None) -> bool:
        """Ensure the game is up and controllable; launch if needed."""
        if await self.wait_for_controllable(api_timeout):
            return True
        self.launch()
        return await self.wait_for_controllable(api_timeout)

    # ── pre-run readiness + bounded auto-recovery ──────────

    def _api_port(self) -> int:
        endpoint = getattr(self.adapter, "endpoint", None) or "http://127.0.0.1:8080"
        return int(_port_from_endpoint(endpoint))

    def _wait_port_released(self, timeout: float | None = None) -> bool:
        """Block until the control API port stops accepting connections.

        Returns True once the port is released (connection refused), False on
        timeout. Used after a controlled terminate so a relaunch does not race a
        still-listening stale server.
        """
        timeout = self.port_release_timeout if timeout is None else float(timeout)
        port = self._api_port()
        t0 = time.time()
        while time.time() - t0 < timeout:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            try:
                sock.connect(("127.0.0.1", port))
            except OSError:
                sock.close()
                return True  # refused -> released
            else:
                sock.close()
                time.sleep(0.3)  # still listening
        return False

    def _wait_game_gone(self, timeout: float | None = None) -> bool:
        """Block until no game process is detected. Returns True once gone.

        Used after a controlled terminate so the subsequent ``open`` relaunch
        starts a genuinely fresh instance (a still-dying process would make
        ``open`` merely focus the old one).
        """
        timeout = self.port_release_timeout if timeout is None else float(timeout)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if not self._game_process_present():
                return True
            time.sleep(0.3)
        return not self._game_process_present()

    def _game_process_present(self) -> bool:
        """Best-effort detection of a running game process (managed or external)."""
        if self._proc is not None and self._proc.poll() is None:
            return True
        if self.steam_controller is not None:
            try:
                pids = self.steam_controller._find_game_pids()  # noqa: SLF001
                if pids:
                    return True
            except Exception:
                pass
        try:
            needle = os.path.basename(self.game_exe or "").lower()
            if needle:
                for proc in psutil.process_iter(["name", "cmdline"]):
                    try:
                        name = (proc.info.get("name") or "").lower()
                        if needle in name:
                            return True
                        cmd = " ".join(proc.info.get("cmdline") or []).lower()
                        if self.game_exe and self.game_exe.lower() in cmd:
                            return True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
        except Exception:
            pass
        return False

    async def _probe_ready(self) -> tuple[bool, EnvironmentBlockReason | None, dict[str, Any]]:
        """Run the three control checks: health, state, actions + screen sanity."""
        checks: dict[str, Any] = {
            "health": False,
            "state": False,
            "actions": False,
            "screen": None,
        }
        # 1) health
        try:
            hs = await self.adapter.health_check()
            healthy = bool(getattr(hs, "healthy", hs))
            checks["health"] = healthy
        except Exception as exc:
            return False, EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE, {**checks, "error": repr(exc)}
        if not checks["health"]:
            return False, EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE, checks
        # 2) state
        try:
            st = self._as_dict(await self.adapter.get_state())
        except Exception as exc:
            return False, EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE, {**checks, "error": repr(exc)}
        screen = str(st.get("screen") or "").upper()
        checks["state"] = True
        checks["screen"] = screen or None
        # 3) actions
        try:
            actions = await self.adapter.get_available_actions()
            checks["actions"] = actions is not None
        except Exception as exc:
            return False, EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE, {**checks, "error": repr(exc)}
        # 4) screen sanity — readable but UNKNOWN/empty is not "ready"
        if not screen or screen == "UNKNOWN":
            return False, EnvironmentBlockReason.GAME_PROCESS_STALE, checks
        # 可操作判定（V11 实测）：主菜单动作列表为空 = 控制模组仍在加载
        # （画面先显示主菜单、模组加载可达数分钟），此时不算真就绪——
        # 否则下游会把「菜单空动作」误判成需要再一次重启。
        if screen == "MAIN_MENU" and not actions:
            checks["actions"] = False
            return False, EnvironmentBlockReason.GAME_PROCESS_STALE, checks
        return True, None, checks

    async def ensure_environment_ready(
        self,
        *,
        max_recoveries: int = 1,
        port_release_timeout: float | None = None,
        gui_check: Callable[[], bool] | None = None,
    ) -> EnvironmentReadiness:
        """Verify the game is controllable; if not, perform bounded recovery.

        Success criteria: health readable AND state readable AND actions readable
        AND screen != UNKNOWN. On failure, attempt at most ``max_recoveries``
        (default 1) recovery cycles: a stale process is controlled-terminated,
        the port is drained, then a single relaunch is performed; no process is
        launched fresh. Still-not-ready returns ``ready=False`` with a reason —
        which the caller maps to BLOCKED_ENVIRONMENT. Never loops indefinitely.
        """
        t0 = time.time()
        actions_taken: list[str] = []

        # Optional GUI/graphics session precondition (macOS WindowServer, etc.).
        if gui_check is not None:
            try:
                gui_ok = bool(gui_check())
            except Exception:
                gui_ok = False
            if not gui_ok:
                return EnvironmentReadiness(
                    ready=False,
                    reason=EnvironmentBlockReason.GUI_SESSION_UNAVAILABLE,
                    checks={"gui_session": False},
                    actions_taken=actions_taken,
                    duration_ms=int((time.time() - t0) * 1000),
                    detail="graphics session unavailable",
                )

        ok, reason, checks = await self._probe_ready()
        if not ok:
            # 瞬态抖动重试（V11 实测）：单次探测抖动（HTTP 瞬断、模组瞬时未
            # 就绪）不应触发破坏性重启——快速重试，连续失败才进入有界恢复。
            for _ in range(2):
                await asyncio.sleep(_PROBE_RETRY_GAP_SECONDS)
                ok, reason, checks = await self._probe_ready()
                if ok:
                    break
        pre_process = self._game_process_present()
        if ok:
            return EnvironmentReadiness(
                ready=True,
                recovered=False,
                pre_process_present=pre_process,
                pre_control_ready=True,
                checks=checks,
                actions_taken=actions_taken,
                duration_ms=int((time.time() - t0) * 1000),
            )

        attempts = 0
        while attempts < max_recoveries:
            attempts += 1
            # If a game process exists but control is unavailable -> stale:
            # controlled exit + drain the port before relaunch.
            if pre_process:
                actions_taken.append("controlled_terminate")
                self.terminate()
                gone = self._wait_game_gone(port_release_timeout)
                actions_taken.append("game_gone" if gone else "game_gone_timeout")
                released = self._wait_port_released(port_release_timeout)
                actions_taken.append("port_released" if released else "port_release_timeout")
            # Launch a single fresh instance with debug API env injected.
            try:
                actions_taken.append("launch")
                self.launch()
            except Exception as exc:
                return EnvironmentReadiness(
                    ready=False,
                    reason=EnvironmentBlockReason.GAME_START_FAILED,
                    recovered=True,
                    recovery_attempts=attempts,
                    pre_process_present=pre_process,
                    checks=checks,
                    actions_taken=actions_taken,
                    duration_ms=int((time.time() - t0) * 1000),
                    detail=f"launch failed: {exc!r}",
                )
            # Wait for the game to reach a controllable screen (not just "up").
            if not await self.wait_for_controllable():
                reason = (
                    EnvironmentBlockReason.GAME_PROCESS_STALE
                    if pre_process
                    else EnvironmentBlockReason.GAME_READINESS_TIMEOUT
                )
                continue
            # Re-verify the three checks.
            ok, reason, checks = await self._probe_ready()
            if ok:
                return EnvironmentReadiness(
                    ready=True,
                    recovered=True,
                    recovery_attempts=attempts,
                    pre_process_present=pre_process,
                    checks=checks,
                    actions_taken=actions_taken,
                    duration_ms=int((time.time() - t0) * 1000),
                )
            reason = (
                EnvironmentBlockReason.GAME_PROCESS_STALE
                if pre_process
                else EnvironmentBlockReason.GAME_READINESS_TIMEOUT
            )

        return EnvironmentReadiness(
            ready=False,
            reason=reason or EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE,
            recovered=attempts > 0,
            recovery_attempts=attempts,
            pre_process_present=pre_process,
            checks=checks,
            actions_taken=actions_taken,
            duration_ms=int((time.time() - t0) * 1000),
        )

    # ── run lifecycle ──────────────────────────────────────

    async def start_run(self, character_id: str | None = None) -> bool:
        """Start a fresh run from MAIN_MENU/CHARACTER_SELECT. Returns success."""
        character_id = character_id or self.character_id
        try:
            await self.adapter.act("start_new_run")
        except Exception as exc:
            logger.warning("start_new_run err %r", exc)
        await asyncio.sleep(2.0)
        scr = str(self._as_dict(await self.adapter.get_state()).get("screen") or "").upper()
        if scr == "CHARACTER_SELECT":
            for cand in ({"character_id": character_id}, {"character": character_id}):
                try:
                    await self.adapter.act("select_character", cand)
                except Exception as exc:
                    logger.warning("select_character %s err %r", cand, exc)
                await asyncio.sleep(1.5)
                s2 = str(
                    self._as_dict(await self.adapter.get_state()).get("screen") or ""
                ).upper()
                if s2 not in ("CHARACTER_SELECT", "MAIN_MENU"):
                    break
            try:
                await self.adapter.act("embark", {})
            except Exception:
                pass
            await asyncio.sleep(2.0)
        final = str(
            self._as_dict(await self.adapter.get_state()).get("screen") or ""
        ).upper()
        return final not in ("MAIN_MENU", "CHARACTER_SELECT")

    async def relaunch_run(self, *, api_timeout: float | None = None) -> bool:
        """Crash the current run and bring up a fresh one. Returns success.

        Steps: set_hp 0 -> wait for API-down -> terminate -> launch ->
        wait_for_api -> start_run.
        """
        if self.relaunch_count >= self.max_relaunches:
            logger.error("max_relaunches (%s) reached", self.max_relaunches)
            return False
        self.relaunch_count += 1
        logger.warning("relaunch_run #%s", self.relaunch_count)
        # 1) trigger a hard crash
        try:
            await self.adapter.act("set_hp", {"value": 0})
        except Exception as exc:
            logger.warning("set_hp err %r", exc)
        # 2) wait for API-down (process exited)
        down = False
        for _ in range(self.down_polls):
            await asyncio.sleep(self.down_poll_interval)
            if not await self.is_api_up():
                down = True
                break
        logger.info("game down=%s", down)
        # 3) terminate any lingering process
        self.terminate()
        self._wait_game_gone(self.port_release_timeout)
        # 4) relaunch
        self.launch()
        ok = await self.wait_for_controllable(api_timeout)
        if not ok:
            logger.error("relaunch_run: API did not come up")
            return False
        # 5) start a fresh run
        return await self.start_run()

    # ── adversarial-state detectors (pure) ─────────────────

    @staticmethod
    def is_phantom_combat(state: Any) -> bool:
        """True when screen=COMBAT but no real combat exists (unexitable).

        In this state ``win_combat`` returns "This doesn't appear to be a
        combat!" and no action can leave the screen — only a process restart
        recovers.
        """
        st = GameLifecycleManager._as_dict(state)
        if str(st.get("screen") or "").upper() != "COMBAT":
            return False
        combat = st.get("combat")
        in_combat = bool(st.get("in_combat"))
        return combat is None and not in_combat

    def travel_hang_expired(
        self,
        state: Any,
        traveling_since: float | None,
        now: float | None = None,
    ) -> bool:
        """True when the map is wedged in an unexitable travel state.

        Condition: screen=MAP, map.is_traveling=True, available_nodes empty,
        and it has persisted >= ``hang_threshold`` seconds since *traveling_since*.
        """
        if traveling_since is None:
            return False
        st = self._as_dict(state)
        if str(st.get("screen") or "").upper() != "MAP":
            return False
        mb = st.get("map") or {}
        if not mb.get("is_traveling"):
            return False
        if len(mb.get("available_nodes") or []) != 0:
            return False
        now = time.time() if now is None else now
        return (now - float(traveling_since)) >= self.hang_threshold
