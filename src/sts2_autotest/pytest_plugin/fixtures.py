"""pytest fixtures — async bridge and orchestrator lifecycle (FR16, FR51).

Session-scoped loop bridges async adapter calls to synchronous test
functions. Function-scoped game_state provides a fresh state snapshot
for each test.
"""

import asyncio
from typing import Any, Generator

import pytest

from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.state import GameState
from sts2_autotest.core.orchestrator import TestOrchestrator

logger = get_logger("pytest_plugin.fixtures")

SESSION_TEARDOWN_TIMEOUT = 10.0


class UserError(Exception):
    """Raised when the user misconfigures their test (e.g., async def)."""


class SessionInitError(Exception):
    """Raised when the test session fails to initialize."""


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
    ok = _session_loop.run_until_complete(orch.start_session())
    if not ok:
        # Clean up partially initialized resources before failing
        try:
            _session_loop.run_until_complete(adapter.cleanup())
        except Exception:
            pass
        raise SessionInitError(
            "Failed to start test session — adapter health check or "
            "state validation failed. Check game and adapter availability."
        )
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
