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
from datetime import datetime, timezone
from typing import Any

from sts2_autotest.adapters.base import ActionResult, HealthStatus
from sts2_autotest.adapters.discovery import discover_sts2_cli
from sts2_autotest.common.errors import AdapterErrorSubType, ErrorCategory, STS2Error
from sts2_autotest.common.state import GameScreen, GameState

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
    "TREASURE": GameScreen.CHEST,
    "CHEST": GameScreen.CHEST,
    "BOSS_REWARD": GameScreen.BOSS_REWARD,
    "CARD_REWARD": GameScreen.CARD_REWARD,
    "RELIC_REWARD": GameScreen.RELIC_REWARD,
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
        data = self._parse_response(raw)

        # Map CLI screen name to GameScreen enum
        screen_raw = data.get("screen", "UNKNOWN")
        screen = self._map_screen(screen_raw)

        # Build GameState with screen + extra fields from CLI
        state = GameState(screen=screen, **_filter_state_extra(data))
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

        actions = _screen_to_actions(state.screen)
        self._available_actions_cache = actions
        return actions

    def _act_sync(
        self, action: str, args: dict[str, Any] | None = None
    ) -> ActionResult:
        """Execute a game action via CLI subprocess."""
        # probe is a synthetic no-op used by the orchestrator to verify
        # adapter responsiveness — no CLI command needed.
        if action == "probe":
            return ActionResult(status="success", state_changed=False)
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

    def _wait_until_actionable_sync(self, timeout: float) -> bool:
        """Poll until health_check passes and actions are available."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            health = self._health_check_sync()
            if health.healthy:
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
        GameScreen.MAIN_MENU: ["new_run", "continue_run", "abandon_run", "choose_game_mode", "probe"],
        GameScreen.CHARACTER_SELECT: ["select_character", "set_ascension", "embark", "probe"],
        GameScreen.MAP: ["choose_map_node", "proceed", "probe"],
        GameScreen.COMBAT: ["play_card", "end_turn", "use_potion", "probe"],
        GameScreen.SHOP: ["shop_buy_card", "shop_buy_relic", "shop_buy_potion", "shop_remove_card", "probe"],
        GameScreen.REST: ["choose_rest_option", "probe"],
        GameScreen.EVENT: ["choose_event", "advance_dialogue", "probe"],
        GameScreen.CHEST: ["open_chest", "pick_relic", "probe"],
        GameScreen.BOSS_REWARD: ["reward_claim", "relic_select", "relic_skip", "probe"],
        GameScreen.CARD_REWARD: ["reward_choose_card", "reward_skip_card", "reward_claim", "probe"],
        GameScreen.RELIC_REWARD: ["reward_claim", "relic_select", "relic_skip", "probe"],
        GameScreen.GAME_OVER: ["return_to_menu", "probe"],
        GameScreen.VICTORY: ["return_to_menu", "probe"],
    }
    return _ACTIONS.get(screen, [])


_POSITIONAL_ARG_KEYS: dict[str, tuple[str, ...]] = {
    "choose_game_mode": ("mode",),
    "select_character": ("character_id",),
    "choose_map_node": ("col", "row"),
    "choose_event": ("index",),
    "grid_card_select": ("index",),
    "hand_select_card": ("card_ids",),
    "grid_select_card": ("card_id", "card_ids"),
    "tri_select_card": ("card_id", "card_ids"),
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
