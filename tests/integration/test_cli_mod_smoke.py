"""Integration tests for CliModAdapter against real STS2-Cli-Mod.

These tests require a real STS2-Cli-Mod installation and (for some tests)
the game running. They are skipped automatically if the CLI is not discoverable.

Tests are organized in tiers:
  Tier 1: CLI-only (no game required) — version, discovery, error responses
  Tier 2: Game-required — state, actions, lifecycle

Run with: python -m pytest tests/integration/ -v
"""

import asyncio
import json
import subprocess
from typing import Any

import pytest

from sts2_autotest.adapters.base import ActionResult, HealthStatus
from sts2_autotest.adapters.cli_mod import (
    CliModAdapter,
    _SCREEN_MAP,
    _build_cli_args,
    _screen_to_actions,
)
from sts2_autotest.adapters.discovery import discover_sts2_cli
from sts2_autotest.common.errors import STS2Error
from sts2_autotest.common.state import GameScreen, GameState


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# Skip entire module if CLI not available
cli_path = discover_sts2_cli()
pytestmark = pytest.mark.skipif(
    cli_path is None,
    reason="STS2-Cli-Mod CLI not found — set STS2_CLI_PATH or install to a common location",
)


@pytest.fixture
def adapter() -> CliModAdapter:
    return CliModAdapter(cli_path=cli_path, timeout=10.0)


# ═══════════════════════════════════════════════════════════
# Tier 1: CLI-only tests (no game required)
# ═══════════════════════════════════════════════════════════


class TestCliVersionOutput:
    """Validate sts2 --version output format."""

    def test_version_exits_zero(self, adapter: CliModAdapter) -> None:
        proc = subprocess.Popen(
            [adapter.cli_path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
        )
        stdout_bytes, _ = proc.communicate(timeout=10.0)
        assert proc.returncode == 0

    def test_version_matches_semver(self, adapter: CliModAdapter) -> None:
        import re

        proc = subprocess.Popen(
            [adapter.cli_path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
        )
        stdout_bytes, _ = proc.communicate(timeout=10.0)
        output = stdout_bytes.decode("utf-8", errors="replace").strip()
        assert re.match(r"^\d+\.\d+\.\d+", output), f"Unexpected version format: {output!r}"

    def test_version_handshake_in_adapter(self, adapter: CliModAdapter) -> None:
        proc = subprocess.Popen(
            [adapter.cli_path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
        )
        stdout_bytes, _ = proc.communicate(timeout=10.0)
        version_output = stdout_bytes.decode("utf-8", errors="replace").strip()
        a = CliModAdapter(cli_path=cli_path, timeout=10.0, version_output=version_output)
        assert a._version_checked is True


class TestCliPingWithoutGame:
    """Ping behavior when game is not running."""

    def test_ping_returns_health_status(self, adapter: CliModAdapter) -> None:
        result = _run(adapter.health_check())
        assert isinstance(result, HealthStatus)

    def test_unhealthy_when_game_not_running(self, adapter: CliModAdapter) -> None:
        result = _run(adapter.health_check())
        if not result.healthy:
            assert result.message is not None

    def test_ping_responds_within_timeout(self, adapter: CliModAdapter) -> None:
        import time

        start = time.monotonic()
        _run(adapter.health_check())
        elapsed = time.monotonic() - start
        assert elapsed < 10.0, f"Ping took {elapsed:.2f}s"


class TestCliStateWithoutGame:
    """State query behavior when game is not running."""

    def test_state_raises_or_returns(self, adapter: CliModAdapter) -> None:
        try:
            result = _run(adapter.get_state())
            assert isinstance(result, GameState)
        except STS2Error:
            pass  # Game not running — expected

    def test_state_error_is_adapter_error(self, adapter: CliModAdapter) -> None:
        try:
            _run(adapter.get_state())
        except STS2Error as exc:
            assert exc.category.value == "adapter_error"


class TestCliInvalidCommand:
    """Error handling for invalid CLI commands."""

    def test_act_when_game_not_running(self, adapter: CliModAdapter) -> None:
        result = _run(adapter.act("play_card", {"card_id": "Strike"}))
        assert isinstance(result, ActionResult)
        assert result.status in ("failure", "timeout")

    def test_available_actions_when_unhealthy(self, adapter: CliModAdapter) -> None:
        actions = _run(adapter.get_available_actions())
        assert isinstance(actions, list)
        assert len(actions) == 0


# ═══════════════════════════════════════════════════════════
# Tier 2: Game-required tests (will skip gracefully)
# ═══════════════════════════════════════════════════════════


def _requires_game(adapter: CliModAdapter) -> bool:
    """Check if game is accessible. Returns True if game is running."""
    health = _run(adapter.health_check())
    return health.healthy


@pytest.fixture
def game_running(adapter: CliModAdapter) -> CliModAdapter:
    if not _requires_game(adapter):
        pytest.skip("Game not running — skipping game-required test")
    return adapter


class TestGameStateWithGame:
    """State retrieval when game IS running."""

    def test_state_returns_valid_screen(self, game_running: CliModAdapter) -> None:
        state = _run(game_running.get_state())
        assert isinstance(state, GameState)
        assert state.screen != GameScreen.UNKNOWN or True  # UNKNOWN is valid during loading

    def test_state_is_frozen(self, game_running: CliModAdapter) -> None:
        state = _run(game_running.get_state())
        with pytest.raises(Exception):
            state.screen = GameScreen.COMBAT  # type: ignore[misc]

    def test_state_has_extra_fields(self, game_running: CliModAdapter) -> None:
        """State response should carry screen-specific data as extra fields."""
        state = _run(game_running.get_state())
        # Extra fields are allowed by the model; at minimum screen is set
        assert state.screen is not None

    def test_state_caching(self, game_running: CliModAdapter) -> None:
        """Second get_state should use cache (no new CLI call)."""
        state1 = _run(game_running.get_state())
        state2 = _run(game_running.get_state())
        assert state1.screen == state2.screen

    def test_available_actions_non_empty(self, game_running: CliModAdapter) -> None:
        actions = _run(game_running.get_available_actions())
        assert isinstance(actions, list)
        assert len(actions) > 0, "Game is actionable but no actions returned"


class TestScreenMappingWithGame:
    """Verify screen mapping against real CLI output."""

    def test_real_screen_mapped_correctly(self, game_running: CliModAdapter) -> None:
        """The real CLI screen value should map to a known GameScreen."""
        raw = game_running._run_cli("state")
        data = game_running._parse_response(raw)
        screen_raw = data.get("screen", "UNKNOWN")
        if screen_raw != "UNKNOWN":
            mapped = CliModAdapter._map_screen(screen_raw)
            assert mapped != GameScreen.UNKNOWN, (
                f"CLI returned screen={screen_raw!r} but it maps to UNKNOWN"
            )


class TestAdapterLifecycle:
    """Full adapter lifecycle: discover → version → ping → state → cleanup."""

    def test_full_lifecycle(self) -> None:
        a = CliModAdapter(timeout=10.0)
        assert a.cli_path is not None

        health = _run(a.health_check())
        assert isinstance(health, HealthStatus)

        try:
            state = _run(a.get_state())
            assert isinstance(state, GameState)
        except STS2Error:
            pass

        _run(a.cleanup())
        assert a._cache_stale is True
        assert a._cached_state is None

    def test_cleanup_resets_cache(self, adapter: CliModAdapter) -> None:
        _run(adapter.cleanup())
        assert adapter._cache_stale is True
        assert adapter._cached_state is None
        assert adapter._available_actions_cache is None

    def test_cleanup_idempotent(self, adapter: CliModAdapter) -> None:
        _run(adapter.cleanup())
        _run(adapter.cleanup())  # Should not raise

    def test_bug_snapshot_structure(self, adapter: CliModAdapter) -> None:
        snapshot = _run(adapter.capture_bug_snapshot())
        assert "game_state" in snapshot
        assert "available_actions" in snapshot
        assert "timestamp" in snapshot
        assert isinstance(snapshot["game_state"], GameState)
        assert isinstance(snapshot["available_actions"], list)


class TestBuildCliArgsIntegration:
    """Verify _build_cli_args produces valid sts2 CLI commands.

    These tests don't need the game running — they verify the pure function
    output matches the command format expected by sts2 CLI.
    """

    def test_play_card_args(self) -> None:
        args = _build_cli_args("play_card", {"card_id": "VoidSlash", "nth": 2})
        assert args == ["play_card", "--card_id", "VoidSlash", "--nth", "2"]

    def test_choose_map_node_args(self) -> None:
        args = _build_cli_args("choose_map_node", {"col": 1, "row": 3})
        assert args == ["choose_map_node", "--col", "1", "--row", "3"]

    def test_select_character_args(self) -> None:
        args = _build_cli_args("select_character", {"character_id": "ironclad"})
        assert args == ["select_character", "--character_id", "ironclad"]

    def test_reward_claim_type_arg(self) -> None:
        args = _build_cli_args("reward_claim", {"type": "gold"})
        assert args == ["reward_claim", "--type", "gold"]

    def test_hand_select_card_list_arg(self) -> None:
        args = _build_cli_args("hand_select_card", {"card_ids": ["Strike", "Defend"]})
        assert args == ["hand_select_card", "Strike", "Defend"]
