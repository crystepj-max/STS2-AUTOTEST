"""CliModAdapter — STS2-Cli-Mod adapter (FR8, FR9, FR26, FR50).

Wraps synchronous CLI calls with asyncio.to_thread() to satisfy the
async GameAdapterProtocol. Communicates with STS2-Cli-Mod via
subprocess calls to the sts2 CLI executable.

Communication protocol:
  AI Agent → sts2 CLI (subprocess) → Named Pipe → In-Game Mod → Game

Response format:
  Success: {"ok": true, "data": {...}}
  Error:   {"ok": false, "error": "CODE", "message": "..."}

Exit codes: 0=success, 1=connection error, 2=invalid state,
            3=invalid parameter, 4=timeout
"""

import asyncio
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

from sts2_autotest.adapters.base import ActionResult, DebugVerification, HealthStatus
from sts2_autotest.adapters.discovery import discover_sts2_cli
from sts2_autotest.common.errors import AdapterErrorSubType, ErrorCategory, STS2Error
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.state import GameScreen, GameState

logger = get_logger("adapters.cli_mod")

# CLI 命令返回的 screen 值 → GameScreen 枚举映射
_SCREEN_MAP: dict[str, GameScreen] = {
    "MENU": GameScreen.MAIN_MENU,
    "SINGLEPLAYER_SUBMENU": GameScreen.MAIN_MENU,
    "CHARACTER_SELECT": GameScreen.CHARACTER_SELECT,
    "MAP": GameScreen.MAP,
    "COMBAT": GameScreen.COMBAT,
    "SHOP": GameScreen.SHOP,
    "REST": GameScreen.REST,
    "REST_SITE": GameScreen.REST,
    "EVENT": GameScreen.EVENT,
    "GRID_CARD_SELECT": GameScreen.EVENT,
    "TREASURE": GameScreen.CHEST,
    "CHEST": GameScreen.CHEST,
    "BOSS_REWARD": GameScreen.BOSS_REWARD,
    "REWARD": GameScreen.CARD_REWARD,
    "CARD_REWARD": GameScreen.CARD_REWARD,
    # 防御性：STS2-Agent / 新版本可能以 CARD_SELECTION 上报战后选牌子界面
    "CARD_SELECTION": GameScreen.CARD_REWARD,
    "RELIC_REWARD": GameScreen.RELIC_REWARD,
    # 卡包选择页（Scroll Boxes 遗物触发）：CLI 可能以这些名字直接上报。
    # 与 _get_state_sync 的数据驱动重映射（UNKNOWN + bundle_select 载荷）形成双保险。
    "BUNDLE_SELECTION": GameScreen.BUNDLE_SELECTION,
    "BUNDLE_SELECT": GameScreen.BUNDLE_SELECTION,
    "CONFIRM_BUNDLE": GameScreen.BUNDLE_SELECTION,
    # 三选一卡牌事件屏（tri_select_card / tri_select_skip）。CLI 直接以
    # "TRI_SELECT" 上报；不映射会让导航器看到 UNKNOWN + 空动作而空转超时。
    "TRI_SELECT": GameScreen.TRI_SELECT,
    "GAME_OVER": GameScreen.GAME_OVER,
    "VICTORY": GameScreen.VICTORY,
}


class CliModAdapter:
    """STS2-Cli-Mod adapter implementing the GameAdapterProtocol.

    Communicates with the game via the sts2 CLI tool subprocess.
    CLI path is resolved through:
    1. Explicit cli_path parameter
    2. STS2_CLI_PATH environment variable
    3. Automatic discovery (discover_sts2_cli)
    """

    SUPPORTED_MAJOR_VERSION = 0

    def __init__(
        self,
        cli_path: str | None = None,
        timeout: float = 30.0,
        version_output: str | None = None,
    ) -> None:
        self.timeout = timeout
        self._cache_stale = True
        self._cached_state: GameState | None = None
        self._version_checked = False
        self._available_actions_cache: list[str] | None = None

        # Resolve CLI path: explicit → env → discovery
        if cli_path is not None:
            self.cli_path = cli_path
        else:
            discovered = discover_sts2_cli()
            self.cli_path = discovered if discovered is not None else "sts2"

        # Version handshake
        if version_output is not None:
            self._check_version(version_output)

    # ── subprocess helper ─────────────────────────────────────

    def _run_cli(self, *args: str) -> dict[str, Any]:
        """Execute a sts2 CLI command and return parsed JSON response.

        Uses subprocess.Popen directly instead of subprocess.run with
        capture_output to avoid Windows handle inheritance issues when
        called from asyncio.to_thread within an event loop.

        Raises STS2Error(ADAPTER_ERROR) on subprocess failure,
        non-zero exit code, or JSON parse failure.
        """
        cmd = [self.cli_path, *args]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = proc.communicate(timeout=self.timeout)
            result_stdout = stdout_bytes.decode("utf-8", errors="replace")
            result_stderr = stderr_bytes.decode("utf-8", errors="replace")
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"CLI command timed out after {self.timeout}s: {' '.join(cmd)}",
                detail={"subtype": AdapterErrorSubType.TIMEOUT, "command": " ".join(cmd)},
            )
        except FileNotFoundError:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"CLI executable not found: {self.cli_path}",
                detail={
                    "subtype": AdapterErrorSubType.PROCESS_EXIT,
                    "command": " ".join(cmd),
                    "cli_path": self.cli_path,
                },
            )
        except OSError as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"CLI process error: {exc}",
                detail={
                    "subtype": AdapterErrorSubType.PROCESS_EXIT,
                    "command": " ".join(cmd),
                    "os_error": str(exc),
                },
            )

        if returncode != 0:
            self._handle_nonzero_exit(returncode, cmd, result_stdout, result_stderr)

        try:
            data: dict[str, Any] = json.loads(result_stdout)
        except json.JSONDecodeError as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"CLI returned invalid JSON: {exc}",
                detail={
                    "subtype": AdapterErrorSubType.JSON_PARSE_FAILURE,
                    "command": " ".join(cmd),
                    "raw_output": result_stdout[:500],
                },
            )

        return data

    def _handle_nonzero_exit(
        self,
        returncode: int,
        cmd: list[str],
        stdout: str,
        stderr: str,
    ) -> None:
        """Classify non-zero exit codes and raise appropriate STS2Error.

        STS2-Cli-Mod outputs structured JSON errors to stderr on failure.
        We parse that first for better error messages before falling back
        to generic exit-code classification.
        """
        exit_code_map = {
            1: AdapterErrorSubType.PROCESS_EXIT,    # connection error
            2: AdapterErrorSubType.PROCESS_EXIT,    # invalid state
            3: AdapterErrorSubType.PROCESS_EXIT,    # invalid parameter
            4: AdapterErrorSubType.TIMEOUT,          # timeout
        }
        subtype = exit_code_map.get(returncode, AdapterErrorSubType.NONZERO_EXIT_CODE)

        # Try to extract error message from JSON in stderr or stdout
        message = f"CLI exited with code {returncode}"
        error_code: str | None = None
        for output in (stderr, stdout):
            output = output.strip()
            if not output:
                continue
            try:
                error_data = json.loads(output)
                if isinstance(error_data, dict):
                    if "message" in error_data:
                        message = error_data["message"]
                    if "error" in error_data:
                        error_code = error_data["error"]
                    break
            except (json.JSONDecodeError, TypeError):
                # Use raw text as message if it's not JSON
                if not message.startswith("CLI exited"):
                    break
                message = output[:200]

        detail: dict[str, Any] = {
            "subtype": subtype,
            "command": " ".join(cmd),
            "exit_code": returncode,
        }
        if error_code is not None:
            detail["error_code"] = error_code

        raise STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message=message,
            detail=detail,
        )

    def _parse_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Extract data from CLI response envelope.

        Expected format: {"ok": true, "data": {...}}
        Raises STS2Error if ok=false.
        """
        if not raw.get("ok", False):
            error_code = raw.get("error", "UNKNOWN")
            error_msg = raw.get("message", "Unknown CLI error")
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"CLI error [{error_code}]: {error_msg}",
                detail={
                    "subtype": AdapterErrorSubType.NONZERO_EXIT_CODE,
                    "error_code": error_code,
                    "error_message": error_msg,
                },
            )
        result: dict[str, Any] = raw.get("data", {})
        return result

    # ── public async interface ──────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Check adapter health by pinging the CLI mod."""
        return await asyncio.to_thread(self._health_check_sync)

    async def get_state(self) -> GameState:
        """Read current game state via sts2 state command. Cached when not stale."""
        return await asyncio.to_thread(self._get_state_sync)

    async def get_available_actions(self) -> list[str]:
        """List currently legal actions based on current game screen."""
        return await asyncio.to_thread(self._get_available_actions_sync)

    async def act(
        self, action: str, args: dict[str, Any] | None = None
    ) -> ActionResult:
        """Execute a game action via CLI. Marks cache stale after execution."""
        return await asyncio.to_thread(self._act_sync, action, args)

    async def wait_until_actionable(self, timeout: float) -> bool:
        """Wait until health_check passes and actions are available."""
        return await asyncio.to_thread(self._wait_until_actionable_sync, timeout)

    async def capture_bug_snapshot(self) -> dict[str, Any]:
        """Capture a debugging snapshot of current state."""
        return await asyncio.to_thread(self._capture_bug_snapshot_sync)

    async def cleanup(self) -> None:
        """Release resources. Clear cache, mark stale."""
        self._cached_state = None
        self._cache_stale = True
        self._available_actions_cache = None

    async def verify_debug_actions(self) -> DebugVerification:
        """CliMod 路径不暴露调试控制台，因此调试能力永远 NOT_SUPPORTED。

        快速结束战斗（win_combat）等调试命令只通过 AgentAdapter 的 HTTP 调试
        控制台执行；CliMod 走 sts2 CLI 子进程，没有 run_console_command 通道。
        诚实地报告"未支持"，避免只按配置声明能力。此探测无副作用。
        """
        checked_at = datetime.now(timezone.utc).isoformat()
        return DebugVerification(
            configured=False,
            verified=False,
            reason="NOT_SUPPORTED",
            checked_at=checked_at,
        )

    # ── synchronous internals (wrapped by asyncio.to_thread) ──

    def _health_check_sync(self) -> HealthStatus:
        """Ping the CLI mod to verify connectivity."""
        try:
            raw = self._run_cli("ping")
            self._parse_response(raw)
            return HealthStatus(healthy=True, message="CLI mod connected")
        except STS2Error as exc:
            return HealthStatus(healthy=False, message=str(exc.message))

    def _get_state_sync(self) -> GameState:
        """Query game state via sts2 state command."""
        if not self._cache_stale and self._cached_state is not None:
            return self._cached_state

        raw = self._run_cli("state")
        try:
            data = self._parse_response(raw)
        except STS2Error as exc:
            # Game v0.107.1 removed CombatManager.get_IsPlayPhase(), causing
            # "Method not found" when CLI queries combat state.
            # Degrade gracefully: return COMBAT with whatever fields we have.
            error_msg = str(exc.message or "")
            if "get_IsPlayPhase" in error_msg or "Method not found" in error_msg:
                combat_hint = {}
                if isinstance(raw, dict):
                    raw_data = raw.get("data", {})
                    if isinstance(raw_data, dict):
                        combat_hint = {k: v for k, v in raw_data.items() if k != "error"}
                logger.warning(
                    "CombatManager method not found (game v0.107.1+) — "
                    "returning degraded COMBAT state",
                )
                state = GameState(screen=GameScreen.COMBAT, **combat_hint)
                if state.screen != GameScreen.UNKNOWN:
                    self._cached_state = state
                    self._cache_stale = False
                    self._available_actions_cache = None
                return state
            raise  # Re-raise other errors

        # Map CLI screen name to GameScreen enum
        screen_raw = data.get("screen", "UNKNOWN")
        screen = self._map_screen(screen_raw)

        # Data-driven remap for screens whose raw name is not (yet) in _SCREEN_MAP.
        # The game reports the bundle-picker (Scroll Boxes relic) with a screen
        # name that maps to UNKNOWN, but the payload carries a ``bundle_select``
        # block with the available bundles. Without this remap the navigator sees
        # UNKNOWN + no actions and stalls until the journey timeout (observed:
        # a 10-minute silent hang after ``choose_event`` led into a bundle pick).
        if screen == GameScreen.UNKNOWN:
            bundle_block = data.get("bundle_select")
            if isinstance(bundle_block, dict) and bundle_block.get("bundles"):
                logger.warning(
                    "remapped unmapped screen %r -> BUNDLE_SELECTION "
                    "(bundle_select present)",
                    screen_raw,
                )
                screen = GameScreen.BUNDLE_SELECTION
            else:
                tri_block = data.get("tri_select")
                if isinstance(tri_block, dict) and tri_block.get("cards"):
                    logger.warning(
                        "remapped unmapped screen %r -> TRI_SELECT "
                        "(tri_select present)",
                        screen_raw,
                    )
                    screen = GameScreen.TRI_SELECT
                else:
                    # Surface the raw screen name so future unmapped screens can be
                    # diagnosed instead of silently degrading to UNKNOWN.
                    logger.warning("unmapped game screen name: %r", screen_raw)

        # Build GameState with screen + extra fields from CLI
        state = GameState(screen=screen, **_filter_state_extra(data))
        # Don't cache UNKNOWN — the settle loop needs fresh state on every poll
        # during loading transitions (e.g. embark → EVENT).
        if screen != GameScreen.UNKNOWN:
            self._cached_state = state
            self._cache_stale = False
            self._available_actions_cache = None  # screen changed, invalidate actions
        return state

    def _get_available_actions_sync(self) -> list[str]:
        """Derive available actions from current game screen state."""
        if self._available_actions_cache is not None:
            return self._available_actions_cache

        try:
            state = self._get_state_sync()
        except STS2Error:
            return []

        actions = _state_to_actions(state)
        self._available_actions_cache = actions
        return actions

    def _act_sync(
        self, action: str, args: dict[str, Any] | None = None
    ) -> ActionResult:
        """Execute a game action via CLI subprocess."""
        # probe/return_to_menu at MAIN_MENU are synthetic no-ops — no CLI
        # command needed. probe verifies adapter responsiveness;
        # return_to_menu is a setup recovery step that is already satisfied.
        if action == "probe":
            return ActionResult(status="success", state_changed=False)
        if action in {"start_new_run", "select_character", "embark"}:
            cur = self._cached_state
            if cur is not None and cur.screen in {GameScreen.EVENT, GameScreen.MAP, GameScreen.COMBAT, GameScreen.CARD_REWARD}:
                return ActionResult(status="success", state_changed=False)
        # 注：return_to_menu 不再在此短路为「空操作成功」。sts2 CLI 的
        # return_to_menu 仅支持 GAME_OVER/VICTORY 屏；局内（EVENT/MAP/COMBAT/
        # CARD_REWARD）若被当作可用动作调用，必须真正下发 CLI 并由上层
        # reset_to_main_menu 的受控重启兜底处理，不能再假报成功（修复：
        # 「reported success but produced no observable state change」）。
        if action == "advance_dialogue" and self._cached_state is not None:
            cur = self._cached_state
            if cur.screen in {GameScreen.MAP, GameScreen.COMBAT, GameScreen.CARD_REWARD}:
                return ActionResult(status="success", state_changed=False)
            grid = getattr(cur, "grid_card_select", {})
            if cur.screen == GameScreen.EVENT and isinstance(grid, dict):
                cards = grid.get("cards", [])
                if cards:
                    first = cards[0]
                    if isinstance(first, dict) and first.get("card_id"):
                        raw = self._run_cli("grid_select_card", str(first["card_id"]))
                        self._parse_response(raw)
                        self._cache_stale = True
                        self._available_actions_cache = None
                        return ActionResult(status="success", state_changed=True)
            event = getattr(cur, "event", {})
            if (
                cur.screen == GameScreen.EVENT
                and isinstance(event, dict)
                and event.get("options")
                and not event.get("is_in_dialogue", False)
            ):
                return ActionResult(status="success", state_changed=False)
        if action == "choose_event" and self._cached_state is not None:
            if self._cached_state.screen in {GameScreen.MAP, GameScreen.COMBAT, GameScreen.CARD_REWARD}:
                return ActionResult(status="success", state_changed=False)
        if action == "choose_map_node" and self._cached_state is not None:
            if self._cached_state.screen in {GameScreen.COMBAT, GameScreen.CARD_REWARD}:
                return ActionResult(status="success", state_changed=False)
            if self._cached_state.screen == GameScreen.EVENT:
                try:
                    event_result = self._advance_event_until_map_sync()
                except STS2Error as exc:
                    self._cache_stale = True
                    self._available_actions_cache = None
                    if exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                        return ActionResult(status="timeout", state_changed=True, detail=exc.message)
                    return ActionResult(status="failure", state_changed=True, detail=exc.message)
                if event_result.screen in {GameScreen.COMBAT, GameScreen.CARD_REWARD}:
                    return ActionResult(status="success", state_changed=True)
                if event_result.screen != GameScreen.MAP:
                    return ActionResult(
                        status="failure",
                        state_changed=True,
                        detail=f"Expected MAP after event progression, got {event_result.screen.value}",
                    )
            args = _resolve_map_node_args(self._cached_state, args)
        if action == "enter_combat" and self._cached_state is not None:
            if self._cached_state.screen in {GameScreen.COMBAT, GameScreen.CARD_REWARD}:
                return ActionResult(status="success", state_changed=False)
        if action == "bundle_select":
            # Bundle picker (Scroll Boxes relic) is a two-step CLI flow:
            # ``bundle_select <index>`` previews, ``bundle_confirm`` commits.
            # Collapse it into one deterministic compound so the stateless
            # navigator does not need to remember the preview step.
            idx = 0
            if args and args.get("index") is not None:
                try:
                    idx = int(args["index"])
                except (TypeError, ValueError):
                    idx = 0
            return self._bundle_select_and_confirm_sync(idx)
        if action == "combat_basic_policy":
            return self._combat_basic_policy_sync()
        if action == "skip_card_reward":
            if self._cached_state is not None and self._cached_state.screen != GameScreen.CARD_REWARD:
                return ActionResult(status="success", state_changed=False)
            # Try skipping card reward; if no cards, fall back to proceed
            try:
                raw = self._run_cli("reward_skip_card")
                self._parse_response(raw)
                self._cache_stale = True
                self._available_actions_cache = None
                return ActionResult(status="success", state_changed=True)
            except STS2Error:
                try:
                    raw = self._run_cli("proceed")
                    self._parse_response(raw)
                    self._cache_stale = True
                    self._available_actions_cache = None
                    return ActionResult(status="success", state_changed=True)
                except STS2Error as exc:
                    self._cache_stale = True
                    self._available_actions_cache = None
                    return ActionResult(status="failure", state_changed=False, detail=exc.message)
        if action == "return_to_menu" and self._cached_state is not None:
            try:
                cur = self._get_state_sync()
            except STS2Error:
                cur = self._cached_state
            if cur.screen == GameScreen.MAIN_MENU:
                return ActionResult(status="success", state_changed=False)
        if action == "start_new_run":
            return self._start_new_run_sync()
        if action == "embark":
            return self._embark_sync()
        cli_args = _build_cli_args(action, args)
        try:
            raw = self._run_cli(*cli_args)
            self._parse_response(raw)
            self._cache_stale = True
            self._available_actions_cache = None
            return ActionResult(status="success", state_changed=True)
        except STS2Error as exc:
            self._cache_stale = True
            self._available_actions_cache = None
            if exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                return ActionResult(status="timeout", state_changed=False, detail=exc.message)
            return ActionResult(status="failure", state_changed=False, detail=exc.message)

    def _embark_sync(self) -> ActionResult:
        """Embark and wait for the game to leave character select."""
        try:
            raw = self._run_cli("embark")
            self._parse_response(raw)
            self._cache_stale = True
            self._available_actions_cache = None

            deadline = time.monotonic() + min(self.timeout, 10.0)
            state = self._get_state_sync()
            while state.screen == GameScreen.CHARACTER_SELECT and time.monotonic() < deadline:
                time.sleep(0.5)
                self._cache_stale = True
                self._available_actions_cache = None
                state = self._get_state_sync()

            if state.screen == GameScreen.CHARACTER_SELECT:
                return ActionResult(
                    status="failure",
                    state_changed=False,
                    detail="embark did not leave CHARACTER_SELECT before timeout",
                )
            return ActionResult(status="success", state_changed=True)
        except STS2Error as exc:
            self._cache_stale = True
            self._available_actions_cache = None
            if exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                return ActionResult(status="timeout", state_changed=False, detail=exc.message)
            return ActionResult(status="failure", state_changed=False, detail=exc.message)

    def _bundle_select_and_confirm_sync(self, index: int) -> ActionResult:
        """Preview then confirm a bundle (Scroll Boxes relic picker).

        The CLI exposes this as two commands — ``bundle_select <index>`` to
        preview and ``bundle_confirm`` to commit. We run both so a single
        framework action fully resolves the BUNDLE_SELECTION screen and the
        game advances (typically back to MAP/EVENT), instead of leaving the
        navigator stuck on a half-picked bundle.
        """
        try:
            raw = self._run_cli("bundle_select", str(index))
            self._parse_response(raw)
            self._cache_stale = True
            self._available_actions_cache = None
            # Give the preview a moment to register, then confirm.
            time.sleep(0.3)
            raw2 = self._run_cli("bundle_confirm")
            self._parse_response(raw2)
            self._cache_stale = True
            self._available_actions_cache = None
            return ActionResult(status="success", state_changed=True)
        except STS2Error as exc:
            self._cache_stale = True
            self._available_actions_cache = None
            if exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                return ActionResult(status="timeout", state_changed=True, detail=exc.message)
            return ActionResult(status="failure", state_changed=True, detail=exc.message)

    def _combat_basic_policy_sync(self) -> ActionResult:
        deadline = time.monotonic() + min(self.timeout, 60.0)
        steps = 0
        consecutive_play_failures = 0
        try:
            while time.monotonic() < deadline and steps < 80:
                self._cache_stale = True
                self._available_actions_cache = None
                state = self._get_state_sync()
                if state.screen != GameScreen.COMBAT:
                    return ActionResult(status="success", state_changed=True)

                combat = getattr(state, "combat", {})
                if not isinstance(combat, dict):
                    return ActionResult(status="failure", state_changed=False, detail="Combat payload missing")

                if not combat.get("is_player_turn", False) or combat.get("is_player_actions_disabled", False):
                    time.sleep(0.5)
                    continue

                play_args = _choose_basic_combat_card(combat)
                if play_args is not None:
                    try:
                        raw = self._run_cli(*_build_cli_args("play_card", play_args))
                        self._parse_response(raw)
                        consecutive_play_failures = 0
                    except STS2Error:
                        consecutive_play_failures += 1
                        if consecutive_play_failures >= 3:
                            raw = self._run_cli("end_turn")
                            self._parse_response(raw)
                            consecutive_play_failures = 0
                else:
                    raw = self._run_cli("end_turn")
                    self._parse_response(raw)
                    consecutive_play_failures = 0
                self._cache_stale = True
                self._available_actions_cache = None
                steps += 1

            return ActionResult(status="timeout", state_changed=True, detail="combat_basic_policy timed out")
        except STS2Error as exc:
            self._cache_stale = True
            self._available_actions_cache = None
            if exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                return ActionResult(status="timeout", state_changed=True, detail=exc.message)
            return ActionResult(status="failure", state_changed=True, detail=exc.message)

    def _start_new_run_sync(self) -> ActionResult:
        """Execute the semantic new-run flow across menu sub-screens."""
        try:
            state = self._get_state_sync()
            if getattr(state, "singleplayer_submenu", None) is None:
                try:
                    raw = self._run_cli("new_run")
                    self._parse_response(raw)
                except STS2Error as exc:
                    if "saved run" in (exc.message or "").lower() or "abandon" in (exc.message or "").lower():
                        raw = self._run_cli("abandon_run")
                        self._parse_response(raw)
                        self._cache_stale = True
                        self._available_actions_cache = None
                        raw = self._run_cli("new_run")
                        self._parse_response(raw)
                    else:
                        raise
                self._cache_stale = True
                self._available_actions_cache = None
                state = self._poll_state_after_new_run()

            if getattr(state, "singleplayer_submenu", None) is not None:
                raw = self._run_cli("choose_game_mode", "standard")
                self._parse_response(raw)
                self._cache_stale = True
                self._available_actions_cache = None

            return ActionResult(status="success", state_changed=True)
        except STS2Error as exc:
            self._cache_stale = True
            self._available_actions_cache = None
            if exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                return ActionResult(status="timeout", state_changed=False, detail=exc.message)
            return ActionResult(status="failure", state_changed=False, detail=exc.message)

    def _poll_state_after_new_run(self) -> GameState:
        deadline = time.monotonic() + min(self.timeout, 5.0)
        state = self._get_state_sync()
        while (
            state.screen == GameScreen.MAIN_MENU
            and getattr(state, "singleplayer_submenu", None) is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.2)
            self._cache_stale = True
            self._available_actions_cache = None
            state = self._get_state_sync()
        return state

    def _advance_event_until_map_sync(self) -> GameState:
        deadline = time.monotonic() + min(self.timeout, 20.0)
        state = self._cached_state or self._get_state_sync()
        while time.monotonic() < deadline:
            if state.screen in {GameScreen.MAP, GameScreen.COMBAT, GameScreen.CARD_REWARD}:
                return state
            if state.screen != GameScreen.EVENT:
                return state

            raw = self._run_cli(*_event_progress_cli_args(state))
            self._parse_response(raw)
            self._cache_stale = True
            self._available_actions_cache = None
            state = self._get_state_sync()

        return state

    def _wait_until_actionable_sync(self, timeout: float) -> bool:
        """Poll until health_check passes and actions are available."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            health = self._health_check_sync()
            if health.healthy:
                self._cache_stale = True
                self._available_actions_cache = None
                actions = self._get_available_actions_sync()
                if actions:
                    return True
            time.sleep(0.5)
        return False

    def _capture_bug_snapshot_sync(self) -> dict[str, Any]:
        """Capture current state for bug reporting."""
        try:
            state = self._get_state_sync()
            actions = self._get_available_actions_sync()
        except STS2Error:
            state = self._cached_state or GameState(screen=GameScreen.UNKNOWN)
            actions = []

        return {
            "game_state": state,
            "available_actions": actions,
            "timestamp": datetime.now(timezone.utc),
        }

    # ── version handshake ────────────────────────────────────

    def _check_version(self, version_output: str) -> None:
        """Parse 'MAJOR.MINOR.PATCH' and verify major version (FR50).

        Raises STS2Error(ADAPTER_ERROR) on parse failure or major mismatch.
        """
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version_output.strip())
        if not match:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Cannot parse version from: {version_output!r}",
                detail={
                    "subtype": AdapterErrorSubType.JSON_PARSE_FAILURE,
                    "command": "sts2 --version",
                    "raw_output": version_output,
                },
            )
        major = int(match.group(1))
        if major != self.SUPPORTED_MAJOR_VERSION:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=(
                    f"Adapter major version {major} is incompatible "
                    f"(supported: {self.SUPPORTED_MAJOR_VERSION}). "
                    f"Please upgrade STS2-Cli-Mod."
                ),
                detail={
                    "subtype": AdapterErrorSubType.VERSION_MISMATCH,
                    "command": "sts2 --version",
                    "raw_output": version_output,
                },
            )
        self._version_checked = True

    @staticmethod
    def _map_screen(screen_raw: str) -> GameScreen:
        """Map CLI screen name to GameScreen enum with fallback to UNKNOWN."""
        return _SCREEN_MAP.get(screen_raw, GameScreen.UNKNOWN)


def _filter_state_extra(data: dict[str, Any]) -> dict[str, Any]:
    """Extract extra fields from CLI state response for GameState model.

    GameState(screen=..., extra="allow") accepts arbitrary fields,
    but we skip the 'screen' key (already consumed) and 'error' key
    (not a state field).
    """
    skip_keys = {"screen", "error"}
    return {k: v for k, v in data.items() if k not in skip_keys}


def _screen_to_actions(screen: GameScreen) -> list[str]:
    """Derive available actions from the current game screen.

    Maps GameScreen enum values to the sts2 CLI commands that are
    valid in that screen. This is a static derivation based on the
    game's state machine; the actual available actions may vary.
    """
    _ACTIONS: dict[GameScreen, list[str]] = {
        GameScreen.MAIN_MENU: ["start_new_run", "new_run", "continue_run", "abandon_run", "choose_game_mode", "probe", "return_to_menu"],
        GameScreen.CHARACTER_SELECT: ["select_character", "set_ascension", "embark", "probe"],
        GameScreen.MAP: ["start_new_run", "select_character", "embark", "choose_map_node", "proceed", "choose_event", "advance_dialogue", "probe"],
        GameScreen.COMBAT: ["start_new_run", "select_character", "embark", "enter_combat", "choose_map_node", "choose_event", "advance_dialogue", "combat_basic_policy", "give_card", "play_card", "end_turn", "use_potion", "probe"],
        GameScreen.SHOP: ["shop_buy_card", "shop_buy_relic", "shop_buy_potion", "shop_remove_card", "probe"],
        GameScreen.REST: ["choose_rest_option", "probe"],
        GameScreen.EVENT: ["start_new_run", "select_character", "embark", "choose_event", "advance_dialogue", "choose_map_node", "probe"],
        GameScreen.CHEST: ["open_chest", "pick_relic", "probe"],
        GameScreen.BUNDLE_SELECTION: ["bundle_select", "bundle_confirm", "bundle_cancel", "probe"],
        GameScreen.TRI_SELECT: ["tri_select_card", "tri_select_skip", "probe"],
        GameScreen.BOSS_REWARD: ["reward_claim", "relic_select", "relic_skip", "probe"],
        GameScreen.CARD_REWARD: ["start_new_run", "select_character", "embark", "choose_map_node", "enter_combat", "choose_event", "advance_dialogue", "combat_basic_policy", "reward_choose_card", "reward_skip_card", "skip_card_reward", "reward_claim", "probe"],
        GameScreen.RELIC_REWARD: ["reward_claim", "relic_select", "relic_skip", "probe"],
        GameScreen.GAME_OVER: ["return_to_menu", "probe"],
        GameScreen.VICTORY: ["return_to_menu", "probe"],
    }
    return _ACTIONS.get(screen, [])


def _state_to_actions(state: GameState) -> list[str]:
    if getattr(state, "grid_card_select", None) is not None:
        return [
            "start_new_run",
            "select_character",
            "embark",
            "choose_map_node",
            "grid_select_card",
            "grid_select_skip",
            "advance_dialogue",
            "probe",
        ]
    return _screen_to_actions(state.screen)


def _resolve_map_node_args(
    state: GameState,
    args: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if state.screen != GameScreen.MAP:
        return args
    map_payload = getattr(state, "map", None)
    if not isinstance(map_payload, dict):
        return args

    requested = None
    if args is not None:
        col = args.get("col")
        row = args.get("row")
        if isinstance(col, int) and isinstance(row, int):
            requested = (col, row)

    travelable = map_payload.get("travelable_coords", [])
    node_by_coord: dict[tuple[int, int], dict[str, Any]] = {}
    for node in map_payload.get("nodes", []):
        col = node.get("col")
        row = node.get("row")
        if isinstance(col, int) and isinstance(row, int):
            node_by_coord[(col, row)] = node

    travelable_coords: list[tuple[int, int]] = []
    for coord in travelable:
        if not isinstance(coord, dict):
            continue
        col = coord.get("col")
        row = coord.get("row")
        if isinstance(col, int) and isinstance(row, int):
            travelable_coords.append((col, row))

    if requested in travelable_coords and requested in node_by_coord:
        return args

    for coord in travelable_coords:
        node = node_by_coord.get(coord, {})
        if str(node.get("type", "")).upper() == "MONSTER":
            return {"col": coord[0], "row": coord[1]}

    if travelable_coords:
        first = travelable_coords[0]
        return {"col": first[0], "row": first[1]}
    return args


def _event_progress_cli_args(state: GameState) -> list[str]:
    grid = getattr(state, "grid_card_select", {})
    if isinstance(grid, dict):
        cards = grid.get("cards", [])
        if cards:
            first = cards[0]
            if isinstance(first, dict) and first.get("card_id"):
                return ["grid_select_card", str(first["card_id"])]

    event = getattr(state, "event", {})
    if (
        isinstance(event, dict)
        and event.get("options")
        and not event.get("is_in_dialogue", False)
    ):
        first_option = event["options"][0]
        index = 0
        if isinstance(first_option, dict) and isinstance(first_option.get("index"), int):
            index = first_option["index"]
        return ["choose_event", str(index)]

    return ["advance_dialogue"]


def _choose_basic_combat_card(combat: dict[str, Any]) -> dict[str, Any] | None:
    hand = combat.get("hand", [])
    if not isinstance(hand, list):
        return None

    candidates: list[dict[str, Any]] = []
    for preferred_type in ("Attack", "Skill"):
        for card in hand:
            if (
                isinstance(card, dict)
                and card.get("can_play", True)
                and str(card.get("type", "")) == preferred_type
                and card.get("id")
            ):
                candidates.append(card)
        if candidates:
            break

    if not candidates:
        return None

    card = candidates[0]
    args: dict[str, Any] = {"card_id": str(card["id"])}
    if str(card.get("target_type", "")) == "AnyEnemy":
        enemies = combat.get("enemies", [])
        if isinstance(enemies, list):
            for enemy in enemies:
                if (
                    isinstance(enemy, dict)
                    and enemy.get("is_alive")
                    and isinstance(enemy.get("combat_id"), int)
                ):
                    args["target"] = enemy["combat_id"]
                    break
    return args


_POSITIONAL_ARG_KEYS: dict[str, tuple[str, ...]] = {
    "choose_game_mode": ("mode",),
    "select_character": ("character_id",),
    "choose_map_node": ("col", "row"),
    "choose_event": ("index",),
    "bundle_select": ("index",),
    "choose_rest_option": ("index",),
    "grid_card_select": ("index",),
    "hand_select_card": ("card_ids",),
    "grid_select_card": ("card_id", "card_ids"),
    "tri_select_card": ("card_id", "card_ids"),
    "give_card": ("card_id",),
}


def _build_cli_args(action: str, args: dict[str, Any] | None = None) -> list[str]:
    """Convert an action name + args dict into CLI positional + flag arguments.

    Maps framework action names to sts2 CLI commands. Special cases
    for actions that require specific flag formats.
    """
    cmd = [action]

    if args is None:
        return cmd

    if action == "play_card":
        card_id = args.get("card_id")
        if card_id is not None:
            cmd.append(str(card_id))
        consumed = {"card_id"}
        for key, value in args.items():
            if key in consumed:
                continue
            if key in ("nth", "target"):
                cmd.extend([f"--{key}", str(value)])
            else:
                _append_flag_args(cmd, key, value)
        return cmd

    if action == "use_potion":
        potion_id = args.get("potion_id")
        if potion_id is not None:
            cmd.append(str(potion_id))
        consumed = {"potion_id"}
        for key, value in args.items():
            if key in consumed:
                continue
            if key in ("nth", "target"):
                cmd.extend([f"--{key}", str(value)])
            else:
                _append_flag_args(cmd, key, value)
        return cmd

    if action in _POSITIONAL_ARG_KEYS:
        consumed = set()
        for key in _POSITIONAL_ARG_KEYS[action]:
            if key not in args:
                continue
            consumed.add(key)
            value = args[key]
            if isinstance(value, (list, tuple)):
                cmd.extend(str(v) for v in value)
            else:
                cmd.append(str(value))
        for key, value in args.items():
            if key not in consumed:
                _append_flag_args(cmd, key, value)
        return cmd

    for key, value in args.items():
        _append_flag_args(cmd, key, value)

    return cmd


def _append_flag_args(cmd: list[str], key: str, value: Any) -> None:
    """Append a value using generic CLI flag formatting."""
    if isinstance(value, bool):
        if value:
            cmd.append(f"--{key}")
    elif isinstance(value, (list, tuple)):
        for item in value:
            cmd.append(str(item))
    else:
        cmd.extend([f"--{key}", str(value)])
