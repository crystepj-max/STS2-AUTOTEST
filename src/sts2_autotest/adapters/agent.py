"""AgentAdapter -- STS2-Agent HTTP adapter (FR8, FR9, FR25, FR50).

Communicates with the STS2-Agent HTTP service to drive game interactions.
Implements the GameAdapterProtocol via async HTTP calls to the agent endpoint.

Communication protocol:
  AI Agent -- HTTP (GET/POST) --> STS2-Agent -- AI-driven --> Game

Response format (shared with STS2-Agent):
  Health:   {"status": "ok"|"degraded"|..., "version": "MAJOR.MINOR.PATCH"}
  State:    {"ok": true, "data": {"screen": "COMBAT", ...extra fields...}}
  Actions:  {"ok": true, "data": {"actions": [{"name": "play_card"}, ...]}}
  Act:      {"ok": true} or {"ok": false, "error": "CODE"}
  Wait:     {"actionable": true|false}
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

import httpx

from sts2_autotest.adapters.base import ActionResult, HealthStatus
from sts2_autotest.common.errors import AdapterErrorSubType, ErrorCategory, STS2Error
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.common.types import Capabilities

logger = get_logger("adapters.agent")

# STS2-Agent screen name → GameScreen enum mapping (same semantics as cli_mod.py)
_SCREEN_MAP: dict[str, GameScreen] = {
    "MENU": GameScreen.MAIN_MENU,
    "MAIN_MENU": GameScreen.MAIN_MENU,
    "MODAL": GameScreen.MAIN_MENU,
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
    "CRASHED": GameScreen.CRASHED,
}


class AgentMcpClientProtocol(Protocol):
    """Agent MCP 客户端的最小结构接口。"""

    async def request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发送一次 MCP 请求并返回 JSON 风格字典。"""
        ...

    async def aclose(self) -> None:
        """释放客户端资源。"""
        ...


class FastMcpAgentClient:
    """轻量 MCP 适配层，避免引入 fastmcp 运行时依赖。"""

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:8765/mcp",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self._client = client

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "method": method,
            "path": path,
            "json": json_data or {},
        }
        resp = await self._get_client().post(self.endpoint, json=payload)
        if resp.status_code >= 400:
            resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class AgentAdapter:
    """STS2-Agent HTTP adapter implementing the GameAdapterProtocol.

    Communicates with the game via the STS2-Agent HTTP API.
    Designed for dependency injection (testing) and real usage.

    Constructor params:
        endpoint:        Base URL of the STS2-Agent HTTP service.
        timeout:         HTTP request timeout in seconds.
        tool_profile:    Agent tool usage profile ("balanced", "aggressive", etc.).
        debug_actions:   Enable debug action capabilities.
        client:          Pre-configured httpx.AsyncClient (for testing injection).
        transport:       Transport backend ("http" or "mcp").
        mcp_client:      Pre-configured MCP client (for testing or real MCP use).
        supported_version: Expected major version for version handshake.
    """

    SUPPORTED_MAJOR_VERSION = 0

    def __init__(
        self,
        endpoint: str = "http://localhost:8080",
        timeout: float = 30.0,
        tool_profile: str = "guided",
        debug_actions: bool = False,
        client: httpx.AsyncClient | None = None,
        supported_version: int | None = None,
        health_path: str = "health",
        state_path: str = "state",
        actions_path: str = "actions/available",
        act_path: str = "action",
        wait_path: str = "wait_until_actionable",
        transport: Literal["http", "mcp"] = "http",
        mcp_client: AgentMcpClientProtocol | None = None,
    ) -> None:
        if transport not in ("http", "mcp"):
            raise ValueError("transport must be 'http' or 'mcp'")
        if endpoint is None:
            endpoint = "http://localhost:8080"
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.tool_profile = tool_profile
        self.debug_actions = debug_actions
        self.transport = transport
        self._health_path = health_path
        self._state_path = state_path
        self._actions_path = actions_path
        self._act_path = act_path
        self._wait_path = wait_path
        self._client = client
        self._mcp_client = mcp_client
        self._version_checked = False
        self._supported_version = supported_version if supported_version is not None else self.SUPPORTED_MAJOR_VERSION

    # ── capabilities ─────────────────────────────────────────

    @property
    def capabilities(self) -> Capabilities:
        """Dynamic adapter capability discovery."""
        return Capabilities(
            supports_multiplayer=True,
            supports_metadata=True,
            supports_debug_actions=self.debug_actions,
        )

    # ── HTTP client management ──────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init httpx.AsyncClient.

        Returns the injected client if one was provided at construction,
        otherwise creates a new one with the configured timeout.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _get_mcp_client(self) -> AgentMcpClientProtocol:
        """Lazy-init MCP client."""
        if self._mcp_client is None:
            self._mcp_client = FastMcpAgentClient(endpoint=self.endpoint, timeout=self.timeout)
        return self._mcp_client

    # ── core HTTP request method ────────────────────────────

    async def _request(self, method: str, path: str, json_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Core HTTP request handler.

        Builds URL, dispatches GET or POST, and translates all transport
        errors into STS2Error with appropriate category and subtype.

        Error mapping:
          httpx.TimeoutException  → ADAPTER_ERROR / TIMEOUT
          httpx.ConnectError      → ADAPTER_ERROR / PROCESS_EXIT
          HTTP 408/504            → TIMEOUT_ERROR / TIMEOUT
          Other HTTP errors       → ADAPTER_ERROR / NONZERO_EXIT_CODE
          JSON decode failure     → ADAPTER_ERROR / JSON_PARSE_FAILURE
        """
        if self.transport == "mcp":
            return await self._mcp_request(method, path, json_data)

        url = f"{self.endpoint}/{path}"
        try:
            client = self._get_client()
            if method == "GET":
                resp = await client.get(url)
            else:
                resp = await client.post(url, json=json_data or {})
        except httpx.TimeoutException:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Request timed out: {method} {path}",
                detail={"subtype": AdapterErrorSubType.TIMEOUT, "url": url, "method": method},
            )
        except httpx.ConnectError:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Connection refused: {self.endpoint}/{path}",
                detail={
                    "subtype": AdapterErrorSubType.PROCESS_EXIT,
                    "url": f"{self.endpoint}/{path}",
                    "method": method,
                },
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (408, 504):
                raise STS2Error(
                    category=ErrorCategory.TIMEOUT_ERROR,
                    message=f"HTTP timeout: {status}",
                    detail={"subtype": AdapterErrorSubType.TIMEOUT, "url": url, "status": status},
                )
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"HTTP error: {status}",
                detail={"subtype": AdapterErrorSubType.NONZERO_EXIT_CODE, "url": url, "status": status},
            )
        except httpx.RequestError as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"HTTP request failed: {exc}",
                detail={
                    "subtype": AdapterErrorSubType.PROCESS_EXIT,
                    "url": f"{self.endpoint}/{path}",
                    "method": method,
                },
            )

        # Raise HTTPStatusError for 4xx/5xx responses not caught by httpx
        # (httpx only raises HTTPStatusError when raise_for_status is called)
        if resp.status_code >= 400:
            if resp.status_code in (408, 504):
                raise STS2Error(
                    category=ErrorCategory.TIMEOUT_ERROR,
                    message=f"HTTP timeout: {resp.status_code}",
                    detail={
                        "subtype": AdapterErrorSubType.TIMEOUT,
                        "url": url,
                        "status": resp.status_code,
                    },
                )
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"HTTP error: {resp.status_code}",
                detail={
                    "subtype": AdapterErrorSubType.NONZERO_EXIT_CODE,
                    "url": url,
                    "status": resp.status_code,
                },
            )

        try:
            resp_data: dict[str, Any] = resp.json()
            return resp_data
        except json.JSONDecodeError as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Invalid JSON response: {exc}",
                detail={"subtype": AdapterErrorSubType.JSON_PARSE_FAILURE, "url": url},
            )

    # ── public async interface (GameAdapterProtocol) ────────

    async def _mcp_request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """通过 MCP client 发送请求，并复用适配器错误分类。"""
        try:
            return await self._get_mcp_client().request(method, path, json_data)
        except httpx.TimeoutException:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"MCP request timed out: {method} {path}",
                detail={"subtype": AdapterErrorSubType.TIMEOUT, "path": path, "method": method},
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (408, 504):
                raise STS2Error(
                    category=ErrorCategory.TIMEOUT_ERROR,
                    message=f"MCP HTTP timeout: {status}",
                    detail={"subtype": AdapterErrorSubType.TIMEOUT, "path": path, "status": status},
                )
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"MCP HTTP error: {status}",
                detail={"subtype": AdapterErrorSubType.NONZERO_EXIT_CODE, "path": path, "status": status},
            )
        except httpx.RequestError as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"MCP request failed: {exc}",
                detail={"subtype": AdapterErrorSubType.PROCESS_EXIT, "path": path, "method": method},
            )
        except json.JSONDecodeError as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Invalid MCP JSON response: {exc}",
                detail={"subtype": AdapterErrorSubType.JSON_PARSE_FAILURE, "path": path, "method": method},
            )

    async def health_check(self) -> HealthStatus:
        """GET {endpoint}/health.

        Returns HealthyStatus(healthy=True) when the agent responds with
        status="ok". On the first successful call, performs a version
        handshake if a version field is present in the response.
        """
        try:
            data = await self._request("GET", self._health_path)
        except STS2Error:
            return HealthStatus(healthy=False)

        # Version handshake on first health_check response
        if not self._version_checked and "version" in data:
            self._check_version(data["version"])

        payload = _unwrap_data_envelope(data)
        status = payload.get("status", "")
        return HealthStatus(healthy=(status in ("ok", "ready")))

    async def get_state(self) -> GameState:
        """GET {endpoint}/state.

        Returns a frozen GameState snapshot built from the agent's
        screen name and any extra fields in the response.
        Raises STS2Error on transport or server errors.
        """
        data = _unwrap_data_envelope(await self._request("GET", self._state_path))

        screen_raw = data.get("screen", "UNKNOWN")
        screen = _map_screen(screen_raw)

        return GameState(screen=screen, **_filter_state_extra(data))

    async def get_available_actions(self) -> list[str]:
        """GET {endpoint}/actions/available.

        Returns the list of action names the agent reports as available.
        """
        data = _unwrap_data_envelope(await self._request("GET", self._actions_path))
        actions_result = _normalize_actions(data.get("actions", []))
        if ("open_character_select" in actions_result or "abandon_run" in actions_result) and "start_new_run" not in actions_result:
        	actions_result.append("start_new_run")
        if self.debug_actions and "give_card" not in actions_result:
            actions_result.append("give_card")
        return actions_result

    async def act(self, action: str, args: dict[str, Any] | None = None) -> ActionResult:
        """POST {endpoint}/act.

        Sends the action name, optional args dict, and tool profile.
        Translates transport errors into ActionResult status codes:
          timeout → ActionResult(status="timeout")
          other   → ActionResult(status="failure")
        """
        if action == "start_new_run":
            return await self._start_new_run()
        if action == "give_card":
            if not self.debug_actions:
                return ActionResult(
                    status="failure",
                    state_changed=False,
                    detail="give_card requires AgentAdapter(debug_actions=True)",
                )
            card_id = str((args or {}).get("card_id", "")).strip()
            if not card_id:
                return ActionResult(
                    status="failure",
                    state_changed=False,
                    detail="give_card requires card_id",
                )
            console_payload = {
                "action": "run_console_command",
                "command": f"card {card_id} hand",
            }
            try:
                data = await self._request("POST", self._act_path, console_payload)
            except STS2Error as exc:
                if exc.category == ErrorCategory.TIMEOUT_ERROR or exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                    return ActionResult(status="timeout", state_changed=False, detail=exc.message)
                return ActionResult(status="failure", state_changed=False, detail=exc.message)

            if data.get("ok", False):
                return ActionResult(status="success", state_changed=True)
            return ActionResult(
                status="failure",
                state_changed=False,
                detail=data.get("error", "Unknown error"),
            )

        payload: dict[str, Any] = {"action": action}
        if args:
            payload.update(_normalize_action_args(action, await self._resolve_agent_action_args(action, args)))

        try:
            data = await self._request("POST", self._act_path, payload)
        except STS2Error as exc:
            if exc.category == ErrorCategory.TIMEOUT_ERROR or exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                return ActionResult(status="timeout", state_changed=False, detail=exc.message)
            return ActionResult(status="failure", state_changed=False, detail=exc.message)

        if data.get("ok", False):
            return ActionResult(status="success", state_changed=True)
        return ActionResult(
            status="failure",
            state_changed=False,
            detail=data.get("error", "Unknown error"),
        )

    
    async def _start_new_run(self) -> ActionResult:
        """Open character select, clearing an existing saved run when necessary."""
        try:
            available = await self.get_available_actions()
        except STS2Error as exc:
            if exc.category == ErrorCategory.TIMEOUT_ERROR or exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                return ActionResult(status="timeout", state_changed=False, detail=exc.message)
            return ActionResult(status="failure", state_changed=False, detail=exc.message)

        if "open_character_select" in available:
            return await self.act("open_character_select")

        if "abandon_run" not in available:
            return ActionResult(
                status="failure",
                state_changed=False,
                detail="start_new_run requires open_character_select or abandon_run",
            )

        abandon_result = await self.act("abandon_run")
        if abandon_result.status != "success":
            return abandon_result

        try:
            available = await self.get_available_actions()
        except STS2Error as exc:
            if exc.category == ErrorCategory.TIMEOUT_ERROR or exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                return ActionResult(status="timeout", state_changed=True, detail=exc.message)
            return ActionResult(status="failure", state_changed=True, detail=exc.message)

        if "confirm_modal" in available:
            confirm_result = await self.act("confirm_modal")
            if confirm_result.status != "success":
                return confirm_result
            try:
                available = await self.get_available_actions()
            except STS2Error as exc:
                if exc.category == ErrorCategory.TIMEOUT_ERROR or exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                    return ActionResult(status="timeout", state_changed=True, detail=exc.message)
                return ActionResult(status="failure", state_changed=True, detail=exc.message)

        if "open_character_select" in available:
            return await self.act("open_character_select")

        return ActionResult(
            status="failure",
            state_changed=True,
            detail="start_new_run could not reach open_character_select",
        )

    async def _resolve_agent_action_args(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        """Translate AUTOTEST action args to STS2-Agent HTTP action fields."""
        if action == "select_character":
            return await self._resolve_select_character_args(args)
        if action != "play_card":
            return args
        if "card_index" in args or "card_id" not in args:
            return args

        card_id = str(args["card_id"])
        state = await self.get_state()
        combat = getattr(state, "combat", {})
        hand = combat.get("hand", []) if isinstance(combat, dict) else []
        if not isinstance(hand, list):
            return args

        for card in hand:
            if not isinstance(card, dict):
                continue
            if str(card.get("card_id") or card.get("id")).upper() != card_id.upper():
                continue
            index = card.get("index")
            if isinstance(index, int):
                resolved = dict(args)
                resolved["card_index"] = index
                return resolved
        return args

    async def _resolve_select_character_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Resolve select_character args: character_id → option_index.

        STS2-Agent v0.7.2+ returns CHARACTER_SELECT options with character_id
        that may omit the MOD prefix (e.g. 'GAWAIN' instead of 'GAWAINMOD-GAWAIN').
        We match by:
        1. Exact character_id match (case-insensitive)
        2. Fuzzy match: if the requested ID contains a mod prefix, try matching
           the suffix only (e.g. 'GAWAINMOD-GAWAIN' → match 'GAWAIN')
        3. Name-based fallback using the 'name' field
        """
        if "option_index" in args:
            return args  # Already resolved, pass through

        character_id = str(args.get("character_id", "")).strip()
        if not character_id:
            return args

        state = await self.get_state()
        if state.screen != GameScreen.CHARACTER_SELECT:
            return args

        cs = getattr(state, "character_select", None) or {}
        characters = cs.get("characters", []) if isinstance(cs, dict) else []
        if not isinstance(characters, list):
            return args

        target_upper = character_id.upper()
        # Extract the part after the last '-' or '/' — handles "GAWAINMOD-GAWAIN" → "GAWAIN"
        suffix = target_upper.split("-")[-1] if "-" in target_upper else target_upper

        for char in characters:
            if not isinstance(char, dict):
                continue
            char_id = str(char.get("character_id") or char.get("id") or "").upper()
            char_name = str(char.get("name") or "").upper()

            if char_id == target_upper:
                index = char.get("index")
                if isinstance(index, int):
                    return {"option_index": index}
                return args

            # Fuzzy match: suffix matches (e.g. "GAWAINMOD-GAWAIN" → "GAWAIN" matches "GAWAIN")
            if suffix and suffix in char_id:
                index = char.get("index")
                if isinstance(index, int):
                    return {"option_index": index}
                return args

            # Name-based fuzzy match (e.g. "gawain" matches "GAWAIN") — skip empty names
            if char_name and (character_id.upper() in char_name or char_name in target_upper):
                index = char.get("index")
                if isinstance(index, int):
                    return {"option_index": index}
                return args

        # No match found — let the agent try with the raw character_id
        return args

    async def wait_until_actionable(self, timeout: float) -> bool:
        """Poll real Agent health/actions endpoints until the game is actionable.

        Polls the agent at 0.5s intervals until either:
        - Health is ready and at least one action is available → returns True
        - The timeout is reached → returns False
        Transient STS2Errors during polling are swallowed.

        Each iteration directly calls health_check() and get_available_actions()
        without an additional asyncio.wait_for wrapper — the httpx.AsyncClient
        already enforces its own timeout (self.timeout), so nesting a second
        wait_for is redundant and can cause subtle task-cancellation interference
        in pytest event-loop environments (Python 3.12+).
        """
        import asyncio
        import time

        deadline = time.monotonic() + timeout
        last_logged_hp: float = 0.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False

            # Diagnostic log every 10s to help debug adapter readiness
            elapsed = timeout - remaining
            if elapsed - last_logged_hp >= 10.0:
                logger.info(
                    "wait_until_actionable: %.0fs elapsed (remaining=%.0fs, timeout=%.0fs)",
                    elapsed, remaining, timeout,
                )
                last_logged_hp = elapsed

            try:
                health = await self.health_check()
                if health.healthy:
                    actions = await self.get_available_actions()
                    if actions:
                        return True
            except STS2Error:
                # Transient error — continue polling
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False

            await asyncio.sleep(min(0.5, remaining))
        return False

    async def capture_bug_snapshot(self) -> dict[str, Any]:
        """Compose get_state() + get_available_actions() into a snapshot dict.

        Returns a dict with keys: game_state, available_actions, timestamp.
        Falls back to UNKNOWN / empty list if the adapter raises.
        """
        try:
            state = await self.get_state()
            actions = await self.get_available_actions()
        except STS2Error:
            state = GameState(screen=GameScreen.UNKNOWN)
            actions = []

        return {
            "game_state": state,
            "available_actions": actions,
            "timestamp": datetime.now(timezone.utc),
        }

    async def cleanup(self) -> None:
        """Close the HTTP client session. Idempotent — safe to call multiple times.
        Closing wraps in asyncio.wait_for(..., timeout=10) per project resource
        cleanup standard (CLAUDE.md: __exit__ must include 10s timeout logic).
        """
        import asyncio

        if self._client is not None:
            try:
                await asyncio.wait_for(self._client.aclose(), timeout=10)
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("Error closing HTTP client: %s", exc)
            self._client = None
        if self._mcp_client is not None:
            try:
                await asyncio.wait_for(self._mcp_client.aclose(), timeout=10)
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("Error closing MCP client: %s", exc)
            self._mcp_client = None

    # ── version handshake ────────────────────────────────────

    def _check_version(self, version_str: str) -> None:
        """Parse 'MAJOR.MINOR.PATCH' and verify major version (FR50).

        Raises STS2Error(ADAPTER_ERROR) on parse failure or major mismatch.
        """
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version_str.strip())
        if not match:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Cannot parse version from: {version_str!r}",
                detail={
                    "subtype": AdapterErrorSubType.JSON_PARSE_FAILURE,
                    "raw_output": version_str,
                },
            )
        major = int(match.group(1))
        if major != self._supported_version:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=(
                    f"Adapter major version {major} is incompatible "
                    f"(supported: {self._supported_version}). "
                    f"Please upgrade STS2-Agent."
                ),
                detail={
                    "subtype": AdapterErrorSubType.VERSION_MISMATCH,
                    "raw_output": version_str,
                },
            )
        self._version_checked = True


# ── module-level helpers ────────────────────────────────────


def _map_screen(screen_raw: str) -> GameScreen:
    """Map STS2-Agent screen name to GameScreen enum, falling back to UNKNOWN."""
    return _SCREEN_MAP.get(screen_raw, GameScreen.UNKNOWN)


def _unwrap_data_envelope(data: dict[str, Any]) -> dict[str, Any]:
    """Return the payload under STS2-Agent's {ok, data} envelope when present."""
    payload = data.get("data")
    if data.get("ok") is True and isinstance(payload, dict):
        return payload
    return data


def _normalize_actions(actions: Any) -> list[str]:
    """Normalize Agent action payloads from strings or action descriptor objects."""
    if not isinstance(actions, list):
        return []

    result: list[str] = []
    for item in actions:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and item.get("name"):
            result.append(str(item["name"]))
    return result


def _normalize_action_args(action: str, resolved: dict[str, Any]) -> dict[str, Any]:
    """Convert framework arg names to STS2-Agent HTTP action field names."""
    payload = dict(resolved)
    if action == "play_card":
        payload.pop("card_id", None)
        if "target" in payload and "target_index" not in payload:
            payload["target_index"] = payload.pop("target")
    return payload


def _filter_state_extra(data: dict[str, Any]) -> dict[str, Any]:
    """Extract extra fields from agent state response for GameState model.

    GameState(screen=..., extra="allow") accepts arbitrary fields,
    but we skip the 'screen' key (already consumed) and 'error' key
    (not a state field).
    """
    skip_keys = {"screen", "error"}
    return {k: v for k, v in data.items() if k not in skip_keys}
