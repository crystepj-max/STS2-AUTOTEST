"""pytest fixtures — async bridge and orchestrator lifecycle (FR16, FR51).

Session-scoped loop bridges async adapter calls to synchronous test
functions. Function-scoped game_state provides a fresh state snapshot
for each test.
"""

import asyncio
import os
import platform
from pathlib import Path
from typing import Any, Callable, Generator

import pytest

from sts2_autotest.adapters.base import GameAdapterProtocol
from sts2_autotest.adapters.discovery import find_game_dir, steam_roots
from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.state import GameState
from sts2_autotest.core.evidence_hooks import build_evidence_hooks
from sts2_autotest.core.orchestrator import TestOrchestrator
from sts2_autotest.core.recovery import DefaultRecoveryStrategy
from sts2_autotest.core.runtime_factory import build_lifecycle_manager
from sts2_autotest.core.steam import SteamController

logger = get_logger("pytest_plugin.fixtures")

_IS_MACOS = platform.system() == "Darwin"

SESSION_TEARDOWN_TIMEOUT = 10.0


class UserError(Exception):
    """Raised when the user misconfigures their test (e.g., async def)."""


class SessionInitError(Exception):
    """Raised when the test session fails to initialize."""


def _session_init_error_message() -> str:
    """Build a user-facing session initialization failure message."""
    if _is_agent_enabled():
        endpoint = os.environ.get("STS2_ADAPTER__AGENT__ENDPOINT", "http://127.0.0.1:8080")
        return (
            "Failed to start test session with AgentAdapter. "
            "Ensure STS2-Agent is running and reachable at "
            f"{endpoint}, then rerun the tests. "
            "Set STS2_ADAPTER__AGENT__ENABLED=false to use CliModAdapter instead."
        )
    if _IS_MACOS:
        launch_cmd = "open steam://run/2868840"
        ping_cmd = "sts2 ping"
    else:
        launch_cmd = "steam.exe -applaunch 2868840"
        ping_cmd = "sts2.exe ping"
    return (
        "Failed to start test session. The framework tried Steam startup and "
        "then waited for an externally launched game, but the adapter did not "
        "become ready. Start the game in the desktop session with "
        f"`{launch_cmd}`, then verify "
        f"`{ping_cmd}` returns connected=true before rerunning the tests."
    )


def _is_agent_enabled() -> bool:
    """Check if agent adapter is enabled via STS2_ADAPTER__AGENT__ENABLED env var."""
    raw = os.environ.get("STS2_ADAPTER__AGENT__ENABLED", "false")
    return raw.lower() in ("true", "1", "yes")


def _create_adapter_from_env() -> GameAdapterProtocol:
    """Create the appropriate adapter based on environment variables.

    When STS2_ADAPTER__AGENT__ENABLED is true, creates an AgentAdapter;
    otherwise creates a CliModAdapter (the default).
    """
    if _is_agent_enabled():
        from sts2_autotest.adapters.agent import AgentAdapter, FastMcpAgentClient
        from sts2_autotest.adapters.project_extension import (
            load_card_id_prefixes,
            load_seed_command_template,
            resolve_base_dir,
        )

        transport_raw = os.environ.get("STS2_ADAPTER__AGENT__TRANSPORT", "http")
        if transport_raw not in ("http", "mcp"):
            raise ValueError("STS2_ADAPTER__AGENT__TRANSPORT must be 'http' or 'mcp'")
        transport: str = transport_raw
        endpoint = os.environ.get("STS2_ADAPTER__AGENT__ENDPOINT", "http://127.0.0.1:8080")
        mcp_client = (
            FastMcpAgentClient(
                endpoint=os.environ.get("STS2_ADAPTER__AGENT__MCP_ENDPOINT", endpoint)
            )
            if transport == "mcp"
            else None
        )
        extension_base_dir = resolve_base_dir()
        return AgentAdapter(
            endpoint=endpoint,
            timeout=float(os.environ.get("STS2_ADAPTER__AGENT__TIMEOUT", "30")),
            tool_profile=os.environ.get("STS2_ADAPTER__AGENT__TOOL_PROFILE", "guided"),
            debug_actions=os.environ.get("STS2_ADAPTER__AGENT__DEBUG_ACTIONS", "false").lower()
            in ("true", "1", "yes"),
            mcp_client=mcp_client,
            transport=transport,  # type: ignore[arg-type]
            card_id_prefixes=load_card_id_prefixes(extension_base_dir),
            seed_command_template=load_seed_command_template(extension_base_dir),
        )
    return CliModAdapter()


def _create_adapter_factory() -> Callable[[], GameAdapterProtocol]:
    """Return a callable that creates fresh adapter instances (for recovery)."""
    if _is_agent_enabled():
        return _create_adapter_from_env
    return lambda: CliModAdapter()


def _find_game_dir_for_bootstrap() -> str | None:
    """Locate the installed game directory for runtime bootstrap."""
    game_dir = find_game_dir(steam_roots())
    return str(game_dir) if game_dir is not None else None


def _find_steam_exe_for_bootstrap() -> str:
    """Locate the Steam executable used for runtime bootstrap."""
    steam_names = ["steam.exe", "steam_osx", "steam"]
    for root in steam_roots():
        for name in steam_names:
            candidate = root / name
            if candidate.is_file():
                return str(candidate)
        # macOS: Steam.app bundle
        if _IS_MACOS:
            app_candidate = root / "Steam.AppBundle" / "Steam" / "Contents" / "MacOS" / "steam_osx"
            if app_candidate.is_file():
                return str(app_candidate)
    return "steam.exe" if not _IS_MACOS else "steam_osx"


def _bootstrap_runtime() -> bool:
    """Start Steam and the game so the adapter can connect on retry."""
    game_dir = _find_game_dir_for_bootstrap()
    steam_exe = _find_steam_exe_for_bootstrap()
    try:
        steam = SteamController(
            startup_timeout=60.0,
            game_dir=game_dir,
            steam_exe=steam_exe,
        )
        steam.start_steam()
        steam.start_game(reuse_existing=True)
    except Exception as exc:
        logger.warning("Runtime bootstrap failed: %s", exc)
        return False
    return True


def _wait_for_adapter_ready(
    loop: asyncio.AbstractEventLoop,
    adapter: GameAdapterProtocol,
    timeout: float = 120.0,
) -> bool:
    """Wait until the adapter reports a readable and actionable game state."""
    try:
        ready = loop.run_until_complete(adapter.wait_until_actionable(timeout))
    except Exception as exc:
        logger.warning("Adapter readiness wait failed during bootstrap: %s", exc)
        return False
    if not ready:
        logger.warning("Adapter did not become ready within %.1fs after bootstrap", timeout)
    return ready


def _start_orchestrator_session(
    loop: asyncio.AbstractEventLoop,
    orch: TestOrchestrator,
    adapter: GameAdapterProtocol,
) -> bool:
    """Start the orchestrator session, bootstrapping the runtime on first failure.

    For CliModAdapter: attempts Steam + game launch bootstrap.
    For AgentAdapter: skips Steam bootstrap (agent is an HTTP service);
        just waits for the adapter to become ready.
    """
    ok = loop.run_until_complete(orch.start_session())
    if ok:
        return True

    logger.info("Initial session start failed; attempting runtime bootstrap")
    try:
        loop.run_until_complete(adapter.cleanup())
    except Exception as exc:
        logger.warning("Adapter cleanup before bootstrap retry failed: %s", exc)

    if _is_agent_enabled():
        logger.info("Agent adapter mode: bootstrapping Steam/game before waiting for agent service")
        if not _bootstrap_runtime():
            logger.info(
                "Runtime bootstrap did not start the game; waiting for an external launch"
            )
        if not _wait_for_adapter_ready(loop, adapter):
            return False
    else:
        if not _bootstrap_runtime():
            logger.info(
                "Runtime bootstrap did not start the game; waiting for an external launch"
            )
        if not _wait_for_adapter_ready(loop, adapter):
            return False

    return loop.run_until_complete(orch.start_session())


@pytest.fixture(scope="session")
def _session_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Session-scoped event loop for async adapter calls."""
    loop = asyncio.new_event_loop()
    yield loop
    # Cleanup pending async generators
    try:
        loop.run_until_complete(loop.shutdown_asyncgens())
    except Exception:
        pass
    loop.close()


@pytest.fixture(scope="session")
def _orchestrator(_session_loop: asyncio.AbstractEventLoop) -> Generator[TestOrchestrator, None, None]:
    """Session-scoped orchestrator.

    Adapter type is selected via STS2_ADAPTER__AGENT__ENABLED env var:
      - false (default): CliModAdapter with Steam bootstrap
      - true: AgentAdapter (HTTP/MCP), no Steam bootstrap

    Initialized once per test session. Teardown enforces a 10-second
    timeout — if stop_session hangs, pending tasks are cancelled and
    the loop is forcibly closed.
    """
    adapter = _create_adapter_from_env()
    steam = SteamController(startup_timeout=60.0)
    recovery = DefaultRecoveryStrategy(
        adapter_factory=_create_adapter_factory(),
        game_startup_timeout=60.0,
        steam_controller=steam,
    )
    evidence_root = Path(
        os.environ.get("STS2_AUTOTEST_EVIDENCE_DIR", "tests/output")
    )
    try:
        lifecycle = build_lifecycle_manager(adapter, steam, evidence_root)
    except (OSError, ValueError) as exc:
        logger.warning("Lifecycle manager unavailable: %s", exc)
        lifecycle = None
    if lifecycle is not None:
        recovery = DefaultRecoveryStrategy(
            adapter_factory=_create_adapter_factory(),
            game_startup_timeout=60.0,
            steam_controller=steam,
            lifecycle_manager=lifecycle,
        )
    orch = TestOrchestrator(
        adapter=adapter,
        recovery=recovery,
        evidence=build_evidence_hooks(evidence_root),
        lifecycle=lifecycle,
        lock_path=str(evidence_root / ".sts2-autotest.lock"),
    )
    ok = _start_orchestrator_session(_session_loop, orch, adapter)
    if not ok:
        # Clean up partially initialized resources before failing
        try:
            _session_loop.run_until_complete(adapter.cleanup())
        except Exception:
            pass
        raise SessionInitError(_session_init_error_message())
    yield orch
    try:
        _session_loop.run_until_complete(
            asyncio.wait_for(orch.stop_session(), timeout=SESSION_TEARDOWN_TIMEOUT)
        )
    except asyncio.TimeoutError:
        logger.warning("Session teardown timed out after %ss", SESSION_TEARDOWN_TIMEOUT)
    except Exception as exc:
        logger.warning("Session teardown failed: %s", exc)


@pytest.fixture
def autotest(
    request: pytest.FixtureRequest,
    _session_loop: asyncio.AbstractEventLoop,
    _orchestrator: TestOrchestrator,
) -> Generator[TestOrchestrator, None, None]:
    """Per-function fixture providing the orchestrator.

    Raises UserError if the test function is async (async def).
    """
    if asyncio.iscoroutinefunction(request.function):
        raise UserError(
            "STS2-AUTOTEST does not support async test functions. "
            "Please change 'async def' to 'def' in your test. "
            "The framework handles async adapter calls internally."
        )
    yield _orchestrator


@pytest.fixture
def game_state(
    autotest: TestOrchestrator,
    _session_loop: asyncio.AbstractEventLoop,
) -> GameState:
    """Per-function fixture returning a current GameState snapshot."""
    return _session_loop.run_until_complete(autotest.adapter.get_state())
