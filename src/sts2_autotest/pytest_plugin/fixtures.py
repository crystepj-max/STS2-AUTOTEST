"""pytest fixtures — async bridge and orchestrator lifecycle (FR16, FR51).

Session-scoped loop bridges async adapter calls to synchronous test
functions. Function-scoped game_state provides a fresh state snapshot
for each test.
"""

import asyncio
from typing import Any, Generator

import pytest

from sts2_autotest.adapters.discovery import find_game_dir, steam_roots
from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.state import GameState
from sts2_autotest.core.orchestrator import TestOrchestrator
from sts2_autotest.core.steam import SteamController

logger = get_logger("pytest_plugin.fixtures")

SESSION_TEARDOWN_TIMEOUT = 10.0


class UserError(Exception):
    """Raised when the user misconfigures their test (e.g., async def)."""


class SessionInitError(Exception):
    """Raised when the test session fails to initialize."""


def _session_init_error_message() -> str:
    """Build a user-facing session initialization failure message."""
    return (
        "Failed to start test session. The framework tried Steam startup and "
        "then waited for an externally launched game, but the adapter did not "
        "become ready. Start the game in the desktop session with "
        "`steam.exe -applaunch 2868840`, then verify "
        "`sts2.exe ping` returns connected=true before rerunning the tests."
    )


def _find_game_dir_for_bootstrap() -> str | None:
    """Locate the installed game directory for runtime bootstrap."""
    game_dir = find_game_dir(steam_roots())
    return str(game_dir) if game_dir is not None else None


def _find_steam_exe_for_bootstrap() -> str:
    """Locate the Steam executable used for runtime bootstrap."""
    for root in steam_roots():
        candidate = root / "steam.exe"
        if candidate.is_file():
            return str(candidate)
    return "steam.exe"


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
        steam.start_game()
    except Exception as exc:
        logger.warning("Runtime bootstrap failed: %s", exc)
        return False
    return True


def _wait_for_adapter_ready(
    loop: asyncio.AbstractEventLoop,
    adapter: CliModAdapter,
    timeout: float = 30.0,
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
    adapter: CliModAdapter,
) -> bool:
    """Start the orchestrator session, bootstrapping the runtime on first failure."""
    ok = loop.run_until_complete(orch.start_session())
    if ok:
        return True

    logger.info("Initial session start failed; attempting runtime bootstrap")
    try:
        loop.run_until_complete(adapter.cleanup())
    except Exception as exc:
        logger.warning("Adapter cleanup before bootstrap retry failed: %s", exc)

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
    """Session-scoped orchestrator with CliModAdapter.

    Initialized once per test session. Teardown enforces a 10-second
    timeout — if stop_session hangs, pending tasks are cancelled and
    the loop is forcibly closed.
    """
    adapter = CliModAdapter()
    orch = TestOrchestrator(adapter=adapter)
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
