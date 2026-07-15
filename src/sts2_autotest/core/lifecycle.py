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
import subprocess
import sys
import time
from typing import Any, IO
from urllib.parse import urlparse

import psutil  # type: ignore[import-untyped]

from sts2_autotest.common.logging import get_logger

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

    def launch(self) -> int:
        """Launch the game executable with debug-API env. Returns the PID."""
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
        """Terminate the managed game process (best-effort)."""
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
        # optionally use SteamController to catch externally-launched processes
        if self.steam_controller is not None:
            try:
                self.steam_controller.stop_game()
            except Exception as exc:
                logger.warning("steam_controller.stop_game failed: %s", exc)
        self._proc = None
        self._pid = None
        self._close_game_log()

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
        """Poll until the game API is controllable. Returns success."""
        timeout = self.api_timeout if timeout is None else float(timeout)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if await self.is_api_up():
                logger.info("wait_for_api ok")
                return True
            await asyncio.sleep(self.poll_interval)
        logger.warning("wait_for_api timeout after %.0fs", timeout)
        return False

    async def ensure_game_up(self, api_timeout: float | None = None) -> bool:
        """Ensure the game is up; launch if needed. Returns success."""
        if await self.is_api_up():
            return True
        if self._proc is None or (self._proc.poll() is not None):
            self.launch()
        return await self.wait_for_api(api_timeout)

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
        # 4) relaunch
        self.launch()
        ok = await self.wait_for_api(api_timeout)
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
