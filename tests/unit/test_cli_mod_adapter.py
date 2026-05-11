"""Tests for adapters/cli_mod.py — CliModAdapter (MVP stub)."""

import asyncio
from typing import Any

import pytest

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.common.errors import STS2Error
from sts2_autotest.common.state import GameScreen, GameState


def _run(coro: Any) -> Any:
    """Bridge async → sync for testing."""
    return asyncio.run(coro)


@pytest.fixture
def adapter() -> CliModAdapter:
    return CliModAdapter(cli_path="sts2", timeout=30.0)


class TestCliModAdapterInit:
    """Constructor and defaults."""

    def test_defaults(self) -> None:
        a = CliModAdapter()
        assert a.cli_path == "sts2"
        assert a.timeout == 30.0

    def test_custom_params(self) -> None:
        a = CliModAdapter(cli_path="/usr/local/bin/sts2", timeout=10.0)
        assert a.cli_path == "/usr/local/bin/sts2"
        assert a.timeout == 10.0


class TestHealthCheck:
    """health_check() tests."""

    def test_returns_healthy(self, adapter: CliModAdapter) -> None:
        result = _run(adapter.health_check())
        assert isinstance(result, HealthStatus)
        assert result.healthy is True



class TestGetState:
    """get_state() tests."""

    def test_returns_game_state(self, adapter: CliModAdapter) -> None:
        result = _run(adapter.get_state())
        assert isinstance(result, GameState)
        assert result.screen == GameScreen.MAIN_MENU

    def test_cached_on_second_call(self, adapter: CliModAdapter) -> None:
        _run(adapter.get_state())  # populates cache
        _run(adapter.get_state())  # should use cache
        assert adapter._cache_stale is False



class TestGetAvailableActions:
    """get_available_actions() tests."""

    def test_returns_list(self, adapter: CliModAdapter) -> None:
        result = _run(adapter.get_available_actions())
        assert isinstance(result, list)



class TestAct:
    """act() tests."""

    def test_returns_action_result(self, adapter: CliModAdapter) -> None:
        result = _run(adapter.act("play_card", {"card_id": "VoidSlash"}))
        assert isinstance(result, ActionResult)
        assert result.status == "success"
        assert result.state_changed is True

    def test_invalidates_cache(self, adapter: CliModAdapter) -> None:
        _run(adapter.get_state())  # populates cache
        assert adapter._cache_stale is False
        _run(adapter.act("end_turn"))
        assert adapter._cache_stale is True



class TestWaitUntilActionable:
    """wait_until_actionable() tests."""

    def test_returns_true(self, adapter: CliModAdapter) -> None:
        result = _run(adapter.wait_until_actionable(timeout=5.0))
        assert result is True



class TestCaptureBugSnapshot:
    """capture_bug_snapshot() tests."""

    def test_returns_dict_with_keys(self, adapter: CliModAdapter) -> None:
        result = _run(adapter.capture_bug_snapshot())
        assert isinstance(result, dict)
        assert "game_state" in result
        assert "available_actions" in result
        assert "timestamp" in result



class TestVersionHandshake:
    """Version parsing and validation tests (FR50)."""

    def test_valid_version(self, adapter: CliModAdapter) -> None:
        adapter._check_version("1.2.3")
        assert adapter._version_checked is True

    def test_valid_version_with_extra_output(self, adapter: CliModAdapter) -> None:
        adapter._check_version("1.0.0\n")
        assert adapter._version_checked is True

    def test_invalid_format(self, adapter: CliModAdapter) -> None:
        with pytest.raises(STS2Error, match="Cannot parse version"):
            adapter._check_version("not-a-version")

    def test_incompatible_major(self, adapter: CliModAdapter) -> None:
        with pytest.raises(STS2Error, match="incompatible"):
            adapter._check_version("2.0.0")

    def test_empty_string(self, adapter: CliModAdapter) -> None:
        with pytest.raises(STS2Error):
            adapter._check_version("")

    def test_version_handshake_on_init(self) -> None:
        a = CliModAdapter(version_output="1.2.3")
        assert a._version_checked is True

    def test_version_handshake_skipped_when_none(self) -> None:
        a = CliModAdapter()
        assert a._version_checked is False

    def test_init_rejects_incompatible_version(self) -> None:
        with pytest.raises(STS2Error, match="incompatible"):
            CliModAdapter(version_output="2.0.0")


class TestProtocolCompliance:
    """CliModAdapter satisfies GameAdapterProtocol."""

    def test_isinstance_check(self, adapter: CliModAdapter) -> None:
        assert isinstance(adapter, GameAdapterProtocol)

    def test_has_all_seven_methods(self) -> None:
        expected = {
            "health_check", "get_state", "get_available_actions",
            "act", "wait_until_actionable", "capture_bug_snapshot",
            "cleanup",
        }
        actual = {
            name for name in dir(CliModAdapter)
            if not name.startswith("_") and callable(getattr(CliModAdapter, name, None))
        }
        assert expected <= actual


class TestErrorClassification:
    """Error wrapping tests (FR26)."""

    def test_sts2error_structure(self, adapter: CliModAdapter) -> None:
        """Verify that errors follow the structured format."""
        try:
            adapter._check_version("invalid")
        except STS2Error as exc:
            d = exc.to_dict()
            assert d["type"] is not None
            assert "message" in d
            assert "detail" in d
            assert "timestamp" in d
            assert "subtype" in d["detail"]
            assert "command" in d["detail"]


class TestMockReplaceability:
    """Orchestrator can depend on Protocol, not CliModAdapter (FR25)."""

    class MockAdapter:
        async def health_check(self) -> Any:
            return HealthStatus(healthy=True)

        async def get_state(self) -> Any:
            return GameState(screen=GameScreen.MAIN_MENU)

        async def get_available_actions(self) -> Any:
            return ["play_card", "end_turn"]

        async def act(self, action: str, args: Any = None) -> Any:
            return ActionResult(status="success", state_changed=True)

        async def wait_until_actionable(self, timeout: float) -> Any:
            return True

        async def capture_bug_snapshot(self) -> Any:
            return {}

        async def cleanup(self) -> None:
            pass

    def test_mock_passes_protocol_check(self) -> None:
        mock = self.MockAdapter()
        assert isinstance(mock, GameAdapterProtocol)

    def test_mock_can_replace_real_adapter(self) -> None:
        """Any code typed against GameAdapterProtocol accepts this mock."""
        mock = self.MockAdapter()
        adapter_ref: GameAdapterProtocol = mock  # type: ignore[assignment]
        assert adapter_ref is not None
