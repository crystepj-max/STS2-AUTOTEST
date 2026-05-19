# B7 AgentAdapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement AgentAdapter — an HTTP-based adapter connecting STS2-AUTOTEST to [STS2-Agent](https://github.com/CharTyr/STS2-Agent) via its REST API, enabling async-native game control, multiplayer support, and metadata querying.

**Architecture:** AgentAdapter implements the existing `GameAdapterProtocol` (7 async methods) using `httpx.AsyncClient`. Unlike CliModAdapter which wraps synchronous CLI calls via `asyncio.to_thread()`, AgentAdapter makes native async HTTP calls — no thread pool needed. The adapter handles STS2-Agent's response format, maps errors to `STS2Error` categories, and supports configurable endpoint paths and tool profiles. The config (`AgentAdapterConfig`) already exists in `config/schema.py` with mutual exclusion against CliModAdapter.

**Tech Stack:** Python 3.11+, `httpx` (async HTTP client), STS2-Agent REST API, existing `GameAdapterProtocol` + `GameState` + `STS2Error` from common/

---

### Task 1: Add httpx dependency & finalize AgentAdapterConfig

**Files:**
- Modify: `pyproject.toml` (add httpx dependency)
- Modify: `src/sts2_autotest/config/schema.py` (extend AgentAdapterConfig)
- Read: `src/sts2_autotest/config/loader.py`

- [ ] **Step 1.1: Add httpx to core dependencies**

Add `httpx>=0.27.0` to `pyproject.toml`:

```toml
dependencies = [
    "pytest>=7.0",
    "pydantic>=2.0,<3.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
    "psutil>=5.9",
    "mss>=9.0",
    "httpx>=0.27.0",
]
```

- [ ] **Step 1.2: Extend AgentAdapterConfig with endpoint path and profile fields**

Modify `src/sts2_autotest/config/schema.py:20-28` to add tool profile and path configs:

```python
class AgentAdapterConfig(BaseModel):
    """STS2-Agent adapter configuration (Beta, disabled by default)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    endpoint: str = "http://localhost:8080"
    timeout: float = Field(default=30.0, gt=0)
    tool_profile: str = Field(
        default="guided",
        pattern=r"^(guided|layered|full)$",
    )
    health_path: str = "health"
    state_path: str = "game_state"
    actions_path: str = "available_actions"
    act_path: str = "act"
    wait_path: str = "wait_until_actionable"
    debug_actions: bool = False
```

- [ ] **Step 1.3: Run lint-imports and existing tests**

```bash
lint-imports
python -m pytest tests/unit/test_config_schema.py -v
```

Expected: config tests pass with existing mutual exclusion test covering `agent.enabled=True` + `cli.enabled=True`.

- [ ] **Step 1.4: Commit**

```bash
git add pyproject.toml src/sts2_autotest/config/schema.py
git commit -m "feat: add httpx dep, extend AgentAdapterConfig with profile and path fields"
```

---

### Task 2: Implement AgentAdapter core (all 7 Protocol methods)

**Files:**
- Create: `src/sts2_autotest/adapters/agent.py`
- Test: `tests/unit/test_agent_adapter.py`
- Read: `src/sts2_autotest/adapters/base.py` (Protocol definition)
- Read: `src/sts2_autotest/adapters/cli_mod.py` (for patterns to follow)

This is the largest task — the full AgentAdapter class with all Protocol methods, error mapping, screen mapping, and version handshake.

- [ ] **Step 2.1: Write failing unit tests for AgentAdapter**

Create `tests/unit/test_agent_adapter.py`:

```python
"""Unit tests for AgentAdapter — all HTTP calls mocked with respx or httpx mock."""

from __future__ import annotations

import pytest
import httpx
from sts2_autotest.adapters.agent import AgentAdapter
from sts2_autotest.adapters.base import HealthStatus, ActionResult
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.common.errors import STS2Error, ErrorCategory


class MockAsyncClient:
    """Minimal mock for httpx.AsyncClient to avoid respx dependency."""

    def __init__(self) -> None:
        self.responses: list[httpx.Response] = []
        self._closed = False
        self._requests: list[dict] = []

    def add_response(self, status: int = 200, json_data: dict | None = None) -> None:
        self.responses.append(httpx.Response(status, json=json_data or {}))

    async def get(self, url: str, **kwargs: object) -> httpx.Response:
        self._requests.append({"method": "GET", "url": url, "kwargs": kwargs})
        return self.responses.pop(0) if self.responses else httpx.Response(200, json={})

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        self._requests.append({"method": "POST", "url": url, "kwargs": kwargs})
        return self.responses.pop(0) if self.responses else httpx.Response(200, json={})

    async def aclose(self) -> None:
        self._closed = True


class TestAgentAdapterHealthCheck:
    """health_check() maps to GET {endpoint}/health"""

    async def test_healthy(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"status": "ok"})
        adapter = AgentAdapter(client=mock)
        result = await adapter.health_check()
        assert result.healthy is True
        assert mock._requests[0]["url"] == "http://localhost:8080/health"

    async def test_unhealthy(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"status": "degraded"})
        adapter = AgentAdapter(client=mock)
        result = await adapter.health_check()
        assert result.healthy is False

    async def test_connection_refused(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(503, {})
        adapter = AgentAdapter(client=mock)
        result = await adapter.health_check()
        assert result.healthy is False

    async def test_request_error(self) -> None:
        adapter = AgentAdapter(client=None)  # Will create real client but fail
        adapter._client = MockAsyncClient()
        # Simulate a grandchild mock that fails on aclose
        result = await adapter.health_check()
        assert result.healthy is False


class TestAgentAdapterGetState:
    """get_state() maps to POST {endpoint}/game_state"""

    async def test_returns_game_state(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"screen": "MENU", "hp": 80})
        adapter = AgentAdapter(client=mock)
        state = await adapter.get_state()
        assert isinstance(state, GameState)
        assert state.screen == GameScreen.MAIN_MENU

    async def test_unknown_screen_maps_to_unknown(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"screen": "SOME_NEW_SCREEN"})
        adapter = AgentAdapter(client=mock)
        state = await adapter.get_state()
        assert state.screen == GameScreen.UNKNOWN

    async def test_server_error_raises_sts2error(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(500, {})
        adapter = AgentAdapter(client=mock)
        with pytest.raises(STS2Error) as exc:
            await adapter.get_state()
        assert exc.value.category == ErrorCategory.ADAPTER_ERROR


class TestAgentAdapterAvailableActions:
    """get_available_actions() maps to POST {endpoint}/available_actions"""

    async def test_returns_list(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actions": ["play_card", "end_turn"]})
        adapter = AgentAdapter(client=mock)
        actions = await adapter.get_available_actions()
        assert actions == ["play_card", "end_turn"]

    async def test_empty_list(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actions": []})
        adapter = AgentAdapter(client=mock)
        actions = await adapter.get_available_actions()
        assert actions == []


class TestAgentAdapterAct:
    """act() maps to POST {endpoint}/act"""

    async def test_success(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)
        result = await adapter.act("play_card", {"card_id": "strike"})
        assert isinstance(result, ActionResult)
        assert result.status == "success"

    async def test_failure(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": False, "error": "CARD_NOT_FOUND"})
        adapter = AgentAdapter(client=mock)
        result = await adapter.act("play_card", {"card_id": "nonexistent"})
        assert result.status == "failure"

    async def test_timeout_from_http(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(408, {})
        adapter = AgentAdapter(client=mock)
        result = await adapter.act("play_card")
        assert result.status == "timeout"


class TestAgentAdapterWaitUntilActionable:
    """wait_until_actionable() polls via POST {endpoint}/wait_until_actionable"""

    async def test_returns_true_when_ready(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actionable": True})
        adapter = AgentAdapter(client=mock)
        result = await adapter.wait_until_actionable(10.0)
        assert result is True

    async def test_returns_false_on_timeout(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actionable": False})
        adapter = AgentAdapter(client=mock)
        result = await adapter.wait_until_actionable(0.1)
        assert result is False


class TestAgentAdapterCaptureBugSnapshot:
    """capture_bug_snapshot() composes from get_state + get_available_actions"""

    async def test_returns_snapshot(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"screen": "COMBAT", "hp": 50})
        mock.add_response(200, {"actions": ["play_card", "end_turn"]})
        adapter = AgentAdapter(client=mock)
        snapshot = await adapter.capture_bug_snapshot()
        assert "game_state" in snapshot
        assert "available_actions" in snapshot
        assert "timestamp" in snapshot


class TestAgentAdapterCleanup:
    """cleanup() closes the HTTP client session"""

    async def test_closes_client(self) -> None:
        mock = MockAsyncClient()
        adapter = AgentAdapter(client=mock)
        await adapter.cleanup()
        assert mock._closed is True

    async def test_idempotent(self) -> None:
        adapter = AgentAdapter()
        await adapter.cleanup()
        await adapter.cleanup()  # Should not raise


class TestAgentAdapterVersionHandshake:
    """Version handshake on first health_check response"""

    async def test_version_mismatch_raises_error(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"version": "2.0.0"})
        adapter = AgentAdapter(client=mock, supported_version=1)
        with pytest.raises(STS2Error) as exc:
            await adapter.health_check()
        assert "version" in str(exc.value).lower()
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_agent_adapter.py -v --tb=short
```

Expected: All tests fail with `ModuleNotFoundError` or `ImportError` since `agent.py` doesn't exist yet.

- [ ] **Step 2.3: Write the AgentAdapter implementation**

Create `src/sts2_autotest/adapters/agent.py`:

```python
"""AgentAdapter — STS2-Agent HTTP adapter (B7/Beta).

Implements GameAdapterProtocol via async HTTP calls to STS2-Agent's
REST API. Unlike CliModAdapter (sync CLI wrapped via asyncio.to_thread),
AgentAdapter uses native async HTTP via httpx.AsyncClient.

STS2-Agent API endpoints (configurable via AgentAdapterConfig):
  GET  {endpoint}/health              → health check
  POST {endpoint}/game_state          → full game state JSON
  POST {endpoint}/available_actions   → list of legal action names
  POST {endpoint}/act                 → execute a game action
  POST {endpoint}/wait_until_actionable → wait until game is ready

Tool profiles (guided / layered / full):
  guided  (default) — high-level actions, strongest guardrails
  layered           — multi-layer agent control
  full              — raw tool access, debug actions available
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from sts2_autotest.adapters.base import ActionResult, HealthStatus
from sts2_autotest.common.errors import AdapterErrorSubType, ErrorCategory, STS2Error
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.common.types import Capabilities

logger = get_logger("adapters.agent")

# STS2-Agent screen names → GameScreen enum mapping
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


class AgentAdapter:
    """STS2-Agent HTTP adapter implementing GameAdapterProtocol.

    Communicates with the game via STS2-Agent's HTTP API.
    Endpoint resolved from:
      1. Explicit endpoint parameter
      2. STS2_AGENT_ENDPOINT environment variable
      3. AgentAdapterConfig.endpoint (default: http://localhost:8080)

    All 7 Protocol methods are async-native (httpx), no thread pool needed.
    """

    SUPPORTED_MAJOR_VERSION = 0

    def __init__(
        self,
        endpoint: str | None = None,
        timeout: float = 30.0,
        tool_profile: str = "guided",
        debug_actions: bool = False,
        client: httpx.AsyncClient | None = None,
        health_path: str = "health",
        state_path: str = "game_state",
        actions_path: str = "available_actions",
        act_path: str = "act",
        wait_path: str = "wait_until_actionable",
        supported_version: int | None = None,
        version_output: str | None = None,
    ) -> None:
        self.endpoint = (endpoint or "").rstrip("/")
        self.timeout = timeout
        self.tool_profile = tool_profile
        self.debug_actions = debug_actions
        self._health_path = health_path
        self._state_path = state_path
        self._actions_path = actions_path
        self._act_path = act_path
        self._wait_path = wait_path
        self._version_checked = False
        self._supported_version = supported_version if supported_version is not None else self.SUPPORTED_MAJOR_VERSION
        self._client: httpx.AsyncClient | None = client

        if version_output is not None:
            self._check_version(version_output)

    @property
    def capabilities(self) -> Capabilities:
        """AgentAdapter supports multiplayer, metadata; debug gated."""
        return Capabilities(
            supports_multiplayer=True,
            supports_metadata=True,
            supports_debug_actions=self.debug_actions,
        )

    # ── HTTP client ────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init the HTTP client with configured timeout."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout))
        return self._client

    async def _request(
        self, method: str, path: str, json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request and return parsed JSON response.

        Raises STS2Error(ADAPTER_ERROR) on connection/timeout/server errors.
        """
        url = f"{self.endpoint}/{path}"
        client = await self._get_client()

        try:
            if method == "GET":
                response = await client.get(url, params=json_data)
            else:
                response = await client.post(url, json=json_data or {})
        except httpx.TimeoutException:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Agent HTTP request timed out after {self.timeout}s: {method} {path}",
                detail={"subtype": AdapterErrorSubType.TIMEOUT, "path": path, "method": method},
            )
        except httpx.ConnectError as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Cannot connect to STS2-Agent at {self.endpoint}: {exc}",
                detail={
                    "subtype": AdapterErrorSubType.PROCESS_EXIT,
                    "path": path,
                    "method": method,
                    "endpoint": self.endpoint,
                },
            )
        except httpx.HTTPError as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Agent HTTP error: {exc}",
                detail={"subtype": AdapterErrorSubType.PROCESS_EXIT, "path": path, "error": str(exc)},
            )

        # Non-2xx responses
        if response.is_error:
            body = ""
            try:
                body = response.text[:500]
            except Exception:
                pass

            # 408 / 504 → timeout
            if response.status_code in (408, 504):
                raise STS2Error(
                    category=ErrorCategory.TIMEOUT_ERROR,
                    message=f"Agent request timed out (HTTP {response.status_code}): {path}",
                    detail={"subtype": AdapterErrorSubType.TIMEOUT, "status_code": response.status_code},
                )

            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Agent returned HTTP {response.status_code}: {body[:200]}",
                detail={
                    "subtype": AdapterErrorSubType.NONZERO_EXIT_CODE,
                    "status_code": response.status_code,
                    "path": path,
                },
            )

        # Parse JSON body
        try:
            data: dict[str, Any] = response.json()
        except json.JSONDecodeError as exc:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Agent returned invalid JSON: {exc}",
                detail={
                    "subtype": AdapterErrorSubType.JSON_PARSE_FAILURE,
                    "raw_response": response.text[:500],
                },
            )

        return data

    # ── Protocol implementation ────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """GET {endpoint}/health → HealthStatus."""
        try:
            data = await self._request("GET", self._health_path)

            # Version handshake on first call
            if not self._version_checked and "version" in data:
                self._check_version(data["version"])
                self._version_checked = True

            status = data.get("status", data.get("healthy", "ok"))
            if status in ("ok", "healthy", True):
                return HealthStatus(healthy=True, message="Agent connected")
            return HealthStatus(healthy=False, message=str(status))
        except STS2Error as exc:
            return HealthStatus(healthy=False, message=str(exc.message))

    async def get_state(self) -> GameState:
        """POST {endpoint}/game_state → GameState snapshot."""
        data = await self._request("POST", self._state_path)

        # Agent might wrap in a data envelope
        game_data = data.get("data", data.get("game_state", data))

        screen_raw = str(game_data.get("screen", "UNKNOWN"))
        screen = _map_screen(screen_raw)

        extra = {k: v for k, v in game_data.items() if k != "screen"}
        return GameState(screen=screen, **extra)

    async def get_available_actions(self) -> list[str]:
        """POST {endpoint}/available_actions → list[str]."""
        data = await self._request("POST", self._actions_path)
        actions: list[str] = data.get("actions", data.get("available_actions", []))
        return actions

    async def act(
        self, action: str, args: dict[str, Any] | None = None,
    ) -> ActionResult:
        """POST {endpoint}/act → ActionResult.

        Request body: {"action": "...", "args": {...}, "profile": "..."}
        """
        payload: dict[str, Any] = {
            "action": action,
            "profile": self.tool_profile,
        }
        if args:
            payload["args"] = args

        try:
            data = await self._request("POST", self._act_path, json_data=payload)
        except STS2Error as exc:
            # Timeout → ActionResult.timeout
            if exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT:
                return ActionResult(status="timeout", state_changed=False, detail=str(exc.message))
            # Non-adapter errors (e.g. TIMEOUT_ERROR from 408)
            if exc.category == ErrorCategory.TIMEOUT_ERROR:
                return ActionResult(status="timeout", state_changed=False, detail=str(exc.message))
            return ActionResult(status="failure", state_changed=False, detail=str(exc.message))

        # Check ok flag in response
        if not data.get("ok", True):
            error_msg = data.get("error", data.get("message", "Unknown action error"))
            return ActionResult(status="failure", state_changed=False, detail=error_msg)

        return ActionResult(status="success", state_changed=True)

    async def wait_until_actionable(self, timeout: float) -> bool:
        """POST {endpoint}/wait_until_actionable → bool.

        Agent-side waits until health_check passes + actions available.
        """
        import asyncio

        deadline = datetime.now(timezone.utc).timestamp() + timeout
        payload = {"timeout": timeout, "profile": self.tool_profile}

        while datetime.now(timezone.utc).timestamp() < deadline:
            try:
                data = await self._request("POST", self._wait_path, json_data=payload)
                if data.get("actionable", data.get("ready", False)):
                    return True
            except STS2Error:
                # Transient errors don't stop waiting — retry
                pass
            await asyncio.sleep(0.5)

        return False

    async def capture_bug_snapshot(self) -> dict[str, Any]:
        """Capture current state + actions for bug reporting.

        Tries agent-side snapshot endpoint if available, otherwise
        composes from get_state + get_available_actions.
        """
        try:
            state = await self.get_state()
            actions = await self.get_available_actions()
        except STS2Error:
            state = GameState(screen=GameScreen.UNKNOWN)
            actions = []

        return {
            "game_state": state.model_dump() if hasattr(state, "model_dump") else str(state),
            "available_actions": actions,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def cleanup(self) -> None:
        """Close the HTTP client session. Safe to call multiple times."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:
                logger.debug("Error closing HTTP client: %s", exc)
            self._client = None

    # ── version handshake ─────────────────────────────────────

    def _check_version(self, version_output: str) -> None:
        """Parse 'MAJOR.MINOR.PATCH' and verify major version.

        Raises STS2Error(ADAPTER_ERROR) on parse failure or major mismatch.
        """
        import re

        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version_output.strip())
        if not match:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Cannot parse version from: {version_output!r}",
                detail={"subtype": AdapterErrorSubType.JSON_PARSE_FAILURE, "raw_output": version_output},
            )
        major = int(match.group(1))
        if major != self._supported_version:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=(
                    f"Agent major version {major} is incompatible "
                    f"(supported: {self._supported_version}). "
                    f"Please upgrade STS2-Agent."
                ),
                detail={
                    "subtype": AdapterErrorSubType.VERSION_MISMATCH,
                    "raw_output": version_output,
                },
            )
        self._version_checked = True


def _map_screen(screen_raw: str) -> GameScreen:
    """Map STS2-Agent screen name to GameScreen enum with fallback."""
    return _SCREEN_MAP.get(screen_raw, GameScreen.UNKNOWN)
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_agent_adapter.py -v --tb=short
```

Expected: All tests PASS.

- [ ] **Step 2.5: Run full lint + mypy check**

```bash
lint-imports
mypy src/sts2_autotest --strict
python -m pytest tests/unit/ -v --tb=no --no-header -q
```

Expected: lint-imports passes, mypy 0 errors, all existing tests still pass (no regressions).

- [ ] **Step 2.6: Commit**

```bash
git add src/sts2_autotest/adapters/agent.py tests/unit/test_agent_adapter.py
git commit -m "feat: implement AgentAdapter with all 7 Protocol methods"
```

---

### Task 3: Integrate AgentAdapter into CLI and Orchestrator

**Files:**
- Modify: `src/sts2_autotest/cli/main.py` (wire AgentAdapter creation)
- Modify: `src/sts2_autotest/core/orchestrator.py` (optional — adapter params flow)
- Test: No new test — existing CLI tests cover this

- [ ] **Step 3.1: Wire AgentAdapter into CLI `run` command**

Modify `src/sts2_autotest/cli/main.py` to support `--adapter` flag:

```python
@click.option(
    "--adapter",
    type=click.Choice(["cli", "agent"]),
    default=None,
    help="Adapter type to use (default: cli, or agent if configured in YAML)",
)
```

The `_create_adapter` helper function reads config and instantiates the correct adapter:

```python
async def _create_adapter(config: STS2Config) -> GameAdapterProtocol:
    """Create the appropriate adapter based on config."""
    if config.adapter.agent.enabled:
        agent_cfg = config.adapter.agent
        return AgentAdapter(
            endpoint=agent_cfg.endpoint,
            timeout=agent_cfg.timeout,
            tool_profile=agent_cfg.tool_profile,
            debug_actions=agent_cfg.debug_actions,
            health_path=agent_cfg.health_path,
            state_path=agent_cfg.state_path,
            actions_path=agent_cfg.actions_path,
            act_path=agent_cfg.act_path,
            wait_path=agent_cfg.wait_path,
        )
    else:
        cli_cfg = config.adapter.cli
        return CliModAdapter(cli_path=cli_cfg.cli_path, timeout=cli_cfg.timeout)
```

And in `run` command, accept the explicit `--adapter` flag that overrides config:

```python
if adapter == "agent":
    # Force agent adapter regardless of config
    actual_adapter = AgentAdapter(...)
elif adapter == "cli":
    actual_adapter = CliModAdapter(...)
else:
    actual_adapter = _create_adapter(config)
```

- [ ] **Step 3.2: Add `--adapter` option to `autotest run` in the CLI group**

Read the existing `cli/main.py` to find the right insertion point, then add:

```python
@cli.command()
@click.option("--all", "run_all", is_flag=True, help="Run all test cases")
@click.option("--cases", multiple=True, help="Specific test case IDs")
@click.option("--failed", is_flag=True, help="Re-run failed cases only")
@click.option("--suite", help="Run a named test suite")
@click.option("--adapter", type=click.Choice(["cli", "agent"]), default=None,
              help="Adapter type (cli=STS2-Cli-Mod, agent=STS2-Agent)")
@click.option("--timeout", type=float, help="Per-case timeout in seconds")
# ... existing options
def run(
    run_all: bool, cases: tuple[str, ...], failed: bool, suite: str | None,
    adapter: str | None, timeout: float | None,
) -> None:
    """Run test cases."""
```

- [ ] **Step 3.3: Update `autotest doctor` to check agent endpoint**

Modify `src/sts2_autotest/cli/main.py` doctor command to optionally check Agent health:

```python
if config.adapter.agent.enabled or adapter_flag == "agent":
    click.echo("  STS2-Agent: ", nl=False)
    try:
        agent_adapter = AgentAdapter(endpoint=..., timeout=5)
        health = await agent_adapter.health_check()
        if health.healthy:
            click.echo("OK")
        else:
            click.echo(f"UNHEALTHY ({health.message})")
    except Exception as exc:
        click.echo(f"ERROR ({exc})")
```

- [ ] **Step 3.4: Run full suite to check no regressions**

```bash
lint-imports
mypy src/sts2_autotest --strict
python -m pytest tests/unit/ -v --tb=no --no-header -q
```

Expected: All pass.

- [ ] **Step 3.5: Commit**

```bash
git add src/sts2_autotest/cli/main.py
git commit -m "feat: wire AgentAdapter into CLI --adapter flag and doctor command"
```

---

### Task 4: Integration config loading — wire AgentAdapterConfig into loader

**Files:**
- Modify: `src/sts2_autotest/config/loader.py`
- Test: `tests/unit/test_config_schema.py` (already covers AgentAdapterConfig)

- [ ] **Step 4.1: Verify existing config loading works for AgentAdapterConfig**

Read `src/sts2_autotest/config/loader.py` and check that `STS2_AGENT_ENDPOINT`, `STS2_AGENT_TIMEOUT`, `STS2_AGENT_ENABLED` environment variables are mapped. If not, add them.

The loader likely maps environment variables `STS2_*` to config fields. Add:

```
STS2_AGENT_ENABLED → adapter.agent.enabled
STS2_AGENT_ENDPOINT → adapter.agent.endpoint
STS2_AGENT_TIMEOUT → adapter.agent.timeout
STS2_AGENT_TOOL_PROFILE → adapter.agent.tool_profile
STS2_AGENT_DEBUG_ACTIONS → adapter.agent.debug_actions
```

- [ ] **Step 4.2: Add environment variable mapping in loader**

Modify `src/sts2_autotest/config/loader.py` to add agent env var mappings in the `_ENV_MAP` (or equivalent):

```python
_ENV_MAP: dict[str, str] = {
    # ... existing mappings
    "STS2_AGENT_ENABLED": "adapter.agent.enabled",
    "STS2_AGENT_ENDPOINT": "adapter.agent.endpoint",
    "STS2_AGENT_TIMEOUT": "adapter.agent.timeout",
    "STS2_AGENT_TOOL_PROFILE": "adapter.agent.tool_profile",
    "STS2_AGENT_DEBUG_ACTIONS": "adapter.agent.debug_actions",
}
```

The loader must handle the type conversion for `enabled` (string "true"/"false" → bool) and `debug_actions` similarly.

- [ ] **Step 4.3: Run tests**

```bash
python -m pytest tests/unit/test_config_schema.py -v --tb=short
```

Expected: All config tests pass, including mutual exclusion test (cli + agent can't both be enabled).

- [ ] **Step 4.4: Commit**

```bash
git add src/sts2_autotest/config/loader.py
git commit -m "feat: add AgentAdapter env var mapping in config loader"
```

---

### Task 5: Integration smoke test — adapter switching through CLI

**Files:**
- Create: `tests/integration/test_agent_cli_integration.py`
- Modify: None

This task adds a mock-based integration test that exercises the full adapter switching path through the CLI.

- [ ] **Step 5.1: Write integration test**

Create `tests/integration/test_agent_cli_integration.py`:

```python
"""Integration test: CLI creates AgentAdapter when configured."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from sts2_autotest.cli.main import cli
from sts2_autotest.config.schema import (
    STS2Config,
    AdapterConfig,
    AgentAdapterConfig,
    CliAdapterConfig,
    FrameworkConfig,
    ExecutionConfig,
    StateMachineConfig,
)


def test_cli_run_with_agent_adapter_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given agent adapter enabled in env, CLI should not fail on config load."""
    monkeypatch.setenv("STS2_AGENT_ENABLED", "true")
    monkeypatch.setenv("STS2_CLI_ENABLED", "false")  # mutual exclusion
    monkeypatch.setenv("STS2_AGENT_ENDPOINT", "http://localhost:9999")
    monkeypatch.setenv("STS2_AGENT_TIMEOUT", "5")

    runner = CliRunner()
    # run with --help to check config load doesn't error
    result = runner.invoke(cli, ["run", "--help"])
    # Should not crash on config validation
    assert result.exit_code == 0


def test_mutual_exclusion_still_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both cli and agent enabled should error."""
    monkeypatch.setenv("STS2_AGENT_ENABLED", "true")
    monkeypatch.setenv("STS2_CLI_ENABLED", "true")

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    # Mutual exclusion should cause config load to fail
    assert result.exit_code != 0
    assert "Mutual exclusion" in result.output
```

- [ ] **Step 5.2: Run integration test**

```bash
python -m pytest tests/integration/test_agent_cli_integration.py -v --tb=short
```

Expected: Both tests PASS.

- [ ] **Step 5.3: Full regression check**

```bash
lint-imports
mypy src/sts2_autotest --strict
python -m pytest tests/unit/ tests/integration/ -v --tb=no --no-header -q
```

Expected: All pass.

- [ ] **Step 5.4: Commit**

```bash
git add tests/integration/test_agent_cli_integration.py
git commit -m "test: add AgentAdapter CLI integration tests"
```

---

### Task 6: Documentation — adapter switching guide

**Files:**
- Create: `docs/agent-adapter-guide.md`

- [ ] **Step 6.1: Write agent adapter usage guide**

Create `docs/agent-adapter-guide.md`:

```markdown
# STS2-Agent Adapter 使用指南

## 概述

AgentAdapter 通过 HTTP API 对接 STS2-Agent，提供异步原生游戏控制，
支持多人游戏、元数据查询和 MCP tool profile 切换。

## 前置条件

- STS2-Agent 已安装并运行（默认 `http://localhost:8080`）
- STS2-Agent 与游戏版本兼容

## 配置方式

### 方式一：YAML 配置文件

```yaml
adapter:
  cli:
    enabled: false
  agent:
    enabled: true
    endpoint: "http://localhost:8080"
    timeout: 30
    tool_profile: "guided"   # guided | layered | full
    debug_actions: false
```

### 方式二：环境变量

```bash
export STS2_AGENT_ENABLED=true
export STS2_CLI_ENABLED=false
export STS2_AGENT_ENDPOINT=http://localhost:8080
export STS2_AGENT_TIMEOUT=30
```

### 方式三：CLI 参数

```bash
autotest run --all --adapter agent
```

## 适配器切换

CLI 与 Agent 适配器互斥，不可同时启用。
通过 `--adapter` 参数可临时覆盖配置：

```bash
# 使用 Agent 适配器（即使配置为 CLI）
autotest run --all --adapter agent

# 使用 CLI 适配器（即使配置为 Agent）
autotest run --all --adapter cli
```

## Tool Profile

| Profile | 说明 | 适用场景 |
|---------|------|---------|
| guided  | 高层动作 + 最强护栏（默认） | 常规测试执行 |
| layered | 多层 agent 控制 | 复杂场景编排 |
| full    | 原始工具访问 | 调试/兼容性回归 |

## Capabilities

| 能力 | AgentAdapter | CliModAdapter |
|------|-------------|--------------|
| 单机控制 | ✅ | ✅ |
| 多人控制 | ✅ | ❌ |
| 元数据查询 | ✅ | ❌ |
| Debug Actions | 默认关闭 | ❌ |
| 异步原生 | ✅ (httpx) | ❌ (asyncio.to_thread) |
```

- [ ] **Step 6.2: Commit**

```bash
git add docs/agent-adapter-guide.md
git commit -m "docs: add AgentAdapter usage guide"
```

---

### Task 7: End-to-end verification — full pipeline

- [ ] **Step 7.1: Run complete verification suite**

```bash
lint-imports
mypy src/sts2_autotest --strict
python -m pytest tests/unit/ -v --tb=no --no-header -q
python -m pytest tests/integration/ -v --tb=no --no-header -q
```

Expected:
- lint-imports: 0 violations
- mypy: 0 errors
- Unit tests: all 935+ existing + new tests pass
- Integration tests: all pass

- [ ] **Step 7.2: Manual verification checklist**
  - [ ] `autotest doctor` shows agent status when `STS2_AGENT_ENABLED=true` is set
  - [ ] `autotest run --help` includes `--adapter` option
  - [ ] `AgentAdapterConfig` appears in `sts2-autotest.yaml` docs
  - [ ] Mutual exclusion prevents simultaneous cli+agent
  - [ ] AgentAdapter tests cover all 7 Protocol methods
  - [ ] Error mapping covers: connection refused, timeout, 5xx, JSON parse failure
  - [ ] Screen mapping covers all 15+ GameScreen enum values
  - [ ] Cleanup is idempotent (safe to call multiple times)
  - [ ] Version handshake rejects major version mismatch

- [ ] **Step 7.3: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix: address review findings for AgentAdapter"
```

---

## Summary of Files

| Action | File | Purpose |
|--------|------|---------|
| Modify | `pyproject.toml` | Add `httpx>=0.27.0` dependency |
| Modify | `src/sts2_autotest/config/schema.py` | Extend AgentAdapterConfig with path/profile fields |
| **Create** | `src/sts2_autotest/adapters/agent.py` | Full AgentAdapter implementation |
| **Create** | `tests/unit/test_agent_adapter.py` | Unit tests for all 7 Protocol methods |
| Modify | `src/sts2_autotest/cli/main.py` | Wire --adapter flag and _create_adapter helper |
| Modify | `src/sts2_autotest/config/loader.py` | Add agent env var mappings |
| **Create** | `tests/integration/test_agent_cli_integration.py` | CLI integration tests |
| **Create** | `docs/agent-adapter-guide.md` | Usage documentation |
