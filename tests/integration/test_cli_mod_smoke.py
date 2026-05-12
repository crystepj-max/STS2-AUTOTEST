"""Integration smoke tests for CliModAdapter against real STS2-Cli-Mod.

These tests require a real STS2-Cli-Mod installation and the game running.
They are skipped automatically if the CLI is not discoverable.

Run with: python -m pytest tests/integration/ -v
"""

import asyncio
import json
import shutil
from typing import Any

import pytest

from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.adapters.base import HealthStatus
from sts2_autotest.adapters.discovery import discover_sts2_cli
from sts2_autotest.common.errors import STS2Error
from sts2_autotest.common.state import GameScreen, GameState


def _run(coro: Any) -> Any:
    """Bridge async → sync for testing."""
    return asyncio.run(coro)


# Skip entire module if CLI not available
cli_path = discover_sts2_cli()
pytestmark = pytest.mark.skipif(
    cli_path is None,
    reason="STS2-Cli-Mod CLI not found — set STS2_CLI_PATH or install to a common location",
)


@pytest.fixture
def adapter() -> CliModAdapter:
    """Create adapter with discovered CLI path."""
    return CliModAdapter(cli_path=cli_path, timeout=10.0)


class TestPingHealthCheck:
    """Verify CLI connectivity via ping command."""

    def test_ping_returns_result(self, adapter: CliModAdapter) -> None:
        """Ping should return a HealthStatus (healthy or unhealthy depending on game state)."""
        result = _run(adapter.health_check())
        assert isinstance(result, HealthStatus)
        # If game is running and mod loaded → healthy; otherwise → unhealthy with message
        if not result.healthy:
            assert result.message is not None, "Unhealthy result should have a message"

    def test_ping_is_fast(self, adapter: CliModAdapter) -> None:
        """Ping should respond within 10 seconds (even if game is not running)."""
        import time
        start = time.monotonic()
        _run(adapter.health_check())
        elapsed = time.monotonic() - start
        assert elapsed < 10.0, f"Ping took {elapsed:.2f}s — expected <10s"


class TestGetState:
    """Verify game state retrieval via sts2 state command."""

    def test_state_returns_game_state_or_error(self, adapter: CliModAdapter) -> None:
        """State should return GameState if game is running, or raise STS2Error if not."""
        try:
            result = _run(adapter.get_state())
            assert isinstance(result, GameState)
        except STS2Error as exc:
            # Game not running — expected error
            assert "Game not running" in str(exc.message) or "CONNECTION" in str(exc.detail.get("error_code", ""))

    def test_state_is_immutable_when_available(self, adapter: CliModAdapter) -> None:
        """If state is available, verify it's frozen."""
        try:
            result = _run(adapter.get_state())
            with pytest.raises(Exception):
                result.screen = GameScreen.COMBAT  # type: ignore[misc]
        except STS2Error:
            pytest.skip("Game not running — cannot test immutability")


class TestVersionHandshake:
    """Verify version detection via sts2 --version."""

    def test_version_command_works(self, adapter: CliModAdapter) -> None:
        """Run sts2 --version and verify it returns a parseable version."""
        import subprocess
        proc = subprocess.Popen(
            [adapter.cli_path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
        )
        stdout_bytes, _ = proc.communicate(timeout=10.0)
        assert proc.returncode == 0, f"sts2 --version failed with exit code {proc.returncode}"
        output = stdout_bytes.decode("utf-8", errors="replace").strip()
        # Version should match MAJOR.MINOR.PATCH pattern (with optional git hash)
        import re
        assert re.match(r"^\d+\.\d+\.\d+", output), (
            f"Unexpected version format: {output!r}"
        )


class TestCliModAdapterEndToEnd:
    """End-to-end flow: discover → connect → read state → cleanup."""

    def test_full_lifecycle(self) -> None:
        """Test the complete adapter lifecycle from discovery to cleanup."""
        a = CliModAdapter(timeout=10.0)
        # Discovery happened in __init__
        assert a.cli_path is not None

        # Health check (may be unhealthy if game not running)
        health = _run(a.health_check())
        assert isinstance(health, HealthStatus)

        # Get state (may fail if game not running — that's OK)
        try:
            state = _run(a.get_state())
            assert isinstance(state, GameState)
        except STS2Error:
            pass  # Game not running — expected

        # Cleanup
        _run(a.cleanup())
        assert a._cache_stale is True
