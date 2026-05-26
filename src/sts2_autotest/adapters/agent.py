"""AgentAdapter -- STS2-Agent HTTP adapter (FR8, FR9, FR25, FR50).

Communicates with the STS2-Agent HTTP service to drive game interactions.
Implements the GameAdapterProtocol via async HTTP calls to the agent endpoint.

Communication protocol:
  AI Agent -- HTTP (GET/POST) --> STS2-Agent -- AI-driven --> Game

Response format (shared with STS2-Agent):
  Health:   {"status": "ok"|"degraded"|..., "version": "MAJOR.MINOR.PATCH"}
  State:    {"screen": "COMBAT", ...extra fields...}
  Actions:  {"actions": ["play_card", ...]}
  Act:      {"ok": true} or {"ok": false, "error": "CODE"}
  Wait:     {"actionable": true|false}
"""

from __future__ import annotations

import asyncio
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


class AgentMcpClientProtocol(Protocol):
    """Minimal MCP client surface used by AgentAdapter."""

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Call a STS2-Agent MCP tool and return its JSON-like result."""
        ...


class FastMcpAgentClient:
    """MCP client backed by FastMCP for STS2-Agent network MCP."""

    def __init__(self, endpoint: str = "http://127.0.0.1:8765/mcp") -> None:
        self.endpoint = endpoint

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        try:
            from fastmcp import Client
        except ImportError as exc:
            raise RuntimeError(
                "fastmcp is required for STS2-Agent MCP transport"
            ) from exc

        async with Client(self.endpoint) as client:
            result = await client.call_tool(tool_name, arguments)
        return _coerce_mcp_tool_result(result)


# STS2-Agent screen name → GameScreen enum mapping (same semantics as cli_mod.py)
_SCREEN_MAP: dict[str, GameScreen] = {
    "MENU": GameScreen.MAIN_MENU,
    "MAIN_MENU": GameScreen.MAIN_MENU,
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
        mcp_client: AgentMcpClientProtocol | None = None,
        transport: Literal["http", "mcp"] = "http",
        supported_version: int | None = None,
        health_path: str = "health",
        state_path: str = "state",
        actions_path: str = "actions/available",
        act_path: str = "action",
        wait_path: str = "wait_until_actionable",
    ) -> None:
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

    def get_capabilities(self) -> Capabilities:
        """Return runtime capabilities for STS2-Agent."""
        return self.capabilities

    # ── HTTP client management ──────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init httpx.AsyncClient.

        Returns the injected client if one was provided at construction,
        otherwise creates a new one with the configured timeout.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    # ── core HTTP request method ────────────────────────────

    async def _request(self, method: str, path: str, json_data: dict[str, Any] | None = None) -> Any:
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
            return await self._request_mcp(path, json_data)

        url = f"{self.endpoint}/{path}"
        client = self._get_client()

        try:
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
                message=f"Connection refused: {url}",
                detail={"subtype": AdapterErrorSubType.PROCESS_EXIT, "url": url, "method": method},
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
                detail={"subtype": AdapterErrorSubType.PROCESS_EXIT, "url": url, "method": method},
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
            resp_data: Any = resp.json()
            return _unwrap_agent_response(resp_data)
        except json.JSONDecodeError as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Invalid JSON response: {exc}",
                detail={"subtype": AdapterErrorSubType.JSON_PARSE_FAILURE, "url": url},
            )

    async def _request_mcp(
        self,
        path: str,
        json_data: dict[str, Any] | None = None,
    ) -> Any:
        """Call STS2-Agent through an injected MCP client."""
        if self._mcp_client is None:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message="MCP transport selected but no MCP client is configured",
                detail={"subtype": AdapterErrorSubType.PROCESS_EXIT, "transport": "mcp"},
            )

        tool_name = _MCP_TOOL_MAP.get(path, path)
        try:
            return await self._mcp_client.call_tool(tool_name, json_data or {})
        except TimeoutError:
            raise STS2Error(
                category=ErrorCategory.TIMEOUT_ERROR,
                message=f"MCP tool timed out: {tool_name}",
                detail={"subtype": AdapterErrorSubType.TIMEOUT, "tool": tool_name},
            )
        except asyncio.TimeoutError:
            raise STS2Error(
                category=ErrorCategory.TIMEOUT_ERROR,
                message=f"MCP tool timed out: {tool_name}",
                detail={"subtype": AdapterErrorSubType.TIMEOUT, "tool": tool_name},
            )
        except Exception as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"MCP tool failed: {tool_name}: {exc}",
                detail={
                    "subtype": AdapterErrorSubType.PROCESS_EXIT,
                    "tool": tool_name,
                    "transport": "mcp",
                },
            )

    # ── public async interface (GameAdapterProtocol) ────────

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
        if not isinstance(data, dict):
            return HealthStatus(healthy=False)

        version = data.get("version") or data.get("mod_version")
        if not self._version_checked and isinstance(version, str):
            self._check_version(version)

        status = data.get("status", "")
        return HealthStatus(healthy=(status in ("ok", "ready")))

    async def get_state(self) -> GameState:
        """POST {endpoint}/game_state.

        Returns a frozen GameState snapshot built from the agent's
        screen name and any extra fields in the response.
        Raises STS2Error on transport or server errors.
        """
        data = await self._request("GET", self._state_path)
        if not isinstance(data, dict):
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message="Agent state response must be an object",
                detail={"subtype": AdapterErrorSubType.JSON_PARSE_FAILURE},
            )

        screen_raw = data.get("screen", "UNKNOWN")
        screen = _map_screen(screen_raw)

        return GameState(screen=screen, **_filter_state_extra(data))

    async def get_available_actions(self) -> list[str]:
        """POST {endpoint}/available_actions.

        Returns the list of action names the agent reports as available.
        """
        data = await self._request("GET", self._actions_path)
        return _extract_action_names(data)

    async def act(self, action: str, args: dict[str, Any] | None = None) -> ActionResult:
        """POST {endpoint}/act.

        Sends the action name, optional args dict, and tool profile.
        Translates transport errors into ActionResult status codes:
          timeout → ActionResult(status="timeout")
          other   → ActionResult(status="failure")
        """
        payload = _build_action_payload(action, args)

        try:
            data = await self._request("POST", self._act_path, payload)
        except STS2Error as exc:
            if exc.category == ErrorCategory.TIMEOUT_ERROR or exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                return ActionResult(status="timeout", state_changed=False, detail=exc.message)
            return ActionResult(status="failure", state_changed=False, detail=exc.message)

        if _is_successful_action_response(data):
            return ActionResult(
                status="success",
                state_changed=True,
                detail=_action_response_message(data),
            )
        return ActionResult(
            status="failure",
            state_changed=False,
            detail=_action_response_message(data) or "Unknown error",
        )

    async def wait_until_actionable(self, timeout: float) -> bool:
        """POST {endpoint}/wait_until_actionable in a polling loop.

        Polls the agent at 0.5s intervals until either:
        - The agent responds with actionable=True → returns True
        - The timeout is reached → returns False
        Transient STS2Errors during polling are swallowed.
        Each request is capped to the remaining time so that
        callers get a timely response even when self.timeout is large.
        """
        import asyncio
        import time

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                if self.transport == "mcp":
                    data = await asyncio.wait_for(
                        self._request("POST", self._wait_path, {"timeout_seconds": timeout}),
                        timeout=remaining,
                    )
                else:
                    data = await asyncio.wait_for(
                        self._request("GET", self._actions_path),
                        timeout=remaining,
                    )
                if _is_actionable_payload(data):
                    return True
            except (STS2Error, asyncio.TimeoutError):
                # Re-check remaining time before sleeping
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


_MCP_TOOL_MAP: dict[str, str] = {
    "health": "health_check",
    "state": "get_game_state",
    "actions/available": "get_available_actions",
    "action": "act",
    "wait_until_actionable": "wait_until_actionable",
}


def _unwrap_agent_response(data: Any) -> Any:
    """Return STS2-Agent response data, supporting wrapped and legacy payloads."""
    if not isinstance(data, dict) or "ok" not in data:
        return data

    if data.get("ok") is True:
        if "data" in data:
            return data["data"]
        return data

    error = data.get("error")
    message = "STS2-Agent returned an error"
    if isinstance(error, dict):
        raw_message = error.get("message") or error.get("code")
        if isinstance(raw_message, str):
            message = raw_message
    raise STS2Error(
        category=ErrorCategory.ADAPTER_ERROR,
        message=message,
        detail={
            "subtype": AdapterErrorSubType.NONZERO_EXIT_CODE,
            "error": error,
        },
    )


def _coerce_mcp_tool_result(result: Any) -> Any:
    """Convert common FastMCP tool result wrappers into JSON-like data."""
    data_value = getattr(result, "data", None)
    if data_value is not None:
        return data_value

    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    if structured is not None:
        return structured

    content = getattr(result, "content", None)
    if isinstance(content, list) and len(content) == 1:
        text = getattr(content[0], "text", None)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text

    return result


def _extract_action_names(data: Any) -> list[str]:
    """Normalize STS2-Agent action payloads into action names."""
    if isinstance(data, dict):
        raw_actions = data.get("actions")
    else:
        raw_actions = data

    if not isinstance(raw_actions, list):
        return []

    names: list[str] = []
    for item in raw_actions:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def _build_action_payload(action: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Build the flat STS2-Agent action request shape."""
    payload: dict[str, Any] = {"action": action}
    if not args:
        return payload

    for key in ("card_index", "target_index", "option_index", "command", "client_context"):
        if key in args:
            payload[key] = args[key]
    return payload


def _is_successful_action_response(data: Any) -> bool:
    """Return whether an action response represents accepted execution."""
    if not isinstance(data, dict):
        return False
    if data.get("ok") is True:
        return True
    status = data.get("status")
    return status in ("completed", "pending", "success")


def _action_response_message(data: Any) -> str | None:
    """Extract a short message or error from an action response."""
    if not isinstance(data, dict):
        return None
    message = data.get("message") or data.get("error")
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        raw = message.get("message") or message.get("code")
        if isinstance(raw, str):
            return raw
    return None


def _is_actionable_payload(data: Any) -> bool:
    """Return whether a wait/action payload indicates available user action."""
    if not isinstance(data, dict):
        return bool(_extract_action_names(data))
    if data.get("actionable") is True or data.get("ready") is True:
        return True
    if _extract_action_names(data):
        return True
    state = data.get("state")
    if isinstance(state, dict):
        return bool(_extract_action_names({"actions": state.get("available_actions")}))
    return False


def _filter_state_extra(data: dict[str, Any]) -> dict[str, Any]:
    """Extract extra fields from agent state response for GameState model.

    GameState(screen=..., extra="allow") accepts arbitrary fields,
    but we skip the 'screen' key (already consumed) and 'error' key
    (not a state field).
    """
    skip_keys = {"screen", "error"}
    return {k: v for k, v in data.items() if k not in skip_keys}
