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

import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from sts2_autotest.adapters.base import ActionResult, HealthStatus
from sts2_autotest.common.errors import AdapterErrorSubType, ErrorCategory, STS2Error
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.common.types import Capabilities

# STS2-Agent screen name → GameScreen enum mapping (same semantics as cli_mod.py)
_SCREEN_MAP: dict[str, GameScreen] = {
    "MENU": GameScreen.MAIN_MENU,
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
        supported_version: int | None = None,
        health_path: str = "health",
        state_path: str = "game_state",
        actions_path: str = "available_actions",
        act_path: str = "act",
        wait_path: str = "wait_until_actionable",
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.tool_profile = tool_profile
        self.debug_actions = debug_actions
        self._health_path = health_path
        self._state_path = state_path
        self._actions_path = actions_path
        self._act_path = act_path
        self._wait_path = wait_path
        self._client = client
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
        except httpx.RequestError as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"HTTP request failed: {exc}",
                detail={"subtype": AdapterErrorSubType.PROCESS_EXIT, "url": url, "method": method},
            )

        # Check HTTP status directly (avoids httpx raise_for_status which
        # requires the request attribute to be set on the response object)
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

        status = data.get("status", "")
        return HealthStatus(healthy=(status == "ok"))

    async def get_state(self) -> GameState:
        """POST {endpoint}/game_state.

        Returns a frozen GameState snapshot built from the agent's
        screen name and any extra fields in the response.
        Raises STS2Error on transport or server errors.
        """
        data = await self._request("POST", self._state_path)

        screen_raw = data.get("screen", "UNKNOWN")
        screen = _map_screen(screen_raw)

        return GameState(screen=screen, **_filter_state_extra(data))

    async def get_available_actions(self) -> list[str]:
        """POST {endpoint}/available_actions.

        Returns the list of action names the agent reports as available.
        """
        data = await self._request("POST", self._actions_path)
        actions_result: list[str] = data.get("actions", [])
        return actions_result

    async def act(self, action: str, args: dict[str, Any] | None = None) -> ActionResult:
        """POST {endpoint}/act.

        Sends the action name, optional args dict, and tool profile.
        Translates transport errors into ActionResult status codes:
          timeout → ActionResult(status="timeout")
          other   → ActionResult(status="failure")
        """
        payload: dict[str, Any] = {
            "action": action,
            "profile": self.tool_profile,
        }
        if args:
            payload["args"] = args

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

    async def wait_until_actionable(self, timeout: float) -> bool:
        """POST {endpoint}/wait_until_actionable in a polling loop.

        Polls the agent at 0.5s intervals until either:
        - The agent responds with actionable=True → returns True
        - The timeout is reached → returns False
        Transient STS2Errors during polling are swallowed.
        """
        import asyncio
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data = await self._request("POST", self._wait_path)
                if data.get("actionable") or data.get("ready"):
                    return True
            except STS2Error:
                pass
            await asyncio.sleep(0.5)
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
        """Close the HTTP client session. Idempotent — safe to call multiple times."""
        if self._client is not None:
            await self._client.aclose()
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


def _filter_state_extra(data: dict[str, Any]) -> dict[str, Any]:
    """Extract extra fields from agent state response for GameState model.

    GameState(screen=..., extra="allow") accepts arbitrary fields,
    but we skip the 'screen' key (already consumed) and 'error' key
    (not a state field).
    """
    skip_keys = {"screen", "error"}
    return {k: v for k, v in data.items() if k not in skip_keys}
