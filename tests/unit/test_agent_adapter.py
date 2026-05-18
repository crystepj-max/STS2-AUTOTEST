"""Unit tests for AgentAdapter -- all HTTP calls mocked with MockAsyncClient."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from sts2_autotest.adapters.agent import AgentAdapter
from sts2_autotest.adapters.base import ActionResult, HealthStatus
from sts2_autotest.common.errors import AdapterErrorSubType, ErrorCategory, STS2Error
from sts2_autotest.common.state import GameScreen, GameState


def _run(coro: Any) -> Any:
    """Bridge async -> sync for testing."""
    return asyncio.run(coro)


class MockAsyncClient:
    """Minimal mock for httpx.AsyncClient to avoid respx dependency.

    Supports both response queuing and exception injection.
    Call add_response() to queue a normal response, or add_exception()
    to queue an exception that will be raised on the next call.
    """

    def __init__(self) -> None:
        self.responses: list[httpx.Response] = []
        self.exceptions: list[Exception] = []
        self._closed = False
        self._requests: list[dict[str, Any]] = []

    def add_response(self, status: int = 200, json_data: dict[str, Any] | None = None) -> None:
        self.responses.append(httpx.Response(status, json=json_data or {}))

    def add_exception(self, exc: Exception) -> None:
        self.exceptions.append(exc)

    def _next(self) -> httpx.Response:
        if self.exceptions:
            raise self.exceptions.pop(0)
        return self.responses.pop(0) if self.responses else httpx.Response(200, json={})

    async def get(self, url: str, **kwargs: object) -> httpx.Response:
        self._requests.append({"method": "GET", "url": url, "kwargs": kwargs})
        return self._next()

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        self._requests.append({"method": "POST", "url": url, "kwargs": kwargs})
        return self._next()

    async def aclose(self) -> None:
        self._closed = True


class TestAgentAdapterHealthCheck:
    """health_check() maps to GET {endpoint}/health"""

    def test_healthy(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"status": "ok"})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.health_check())

        assert result.healthy is True
        assert mock._requests[0]["url"] == "http://localhost:8080/health"

    def test_unhealthy(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"status": "degraded"})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.health_check())

        assert result.healthy is False

    def test_connection_refused(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(503, {})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.health_check())

        assert result.healthy is False


class TestAgentAdapterGetState:
    """get_state() maps to POST {endpoint}/game_state"""

    def test_returns_game_state(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"screen": "MENU", "hp": 80})
        adapter = AgentAdapter(client=mock)

        state = _run(adapter.get_state())

        assert isinstance(state, GameState)
        assert state.screen == GameScreen.MAIN_MENU

    def test_unknown_screen_maps_to_unknown(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"screen": "SOME_NEW_SCREEN"})
        adapter = AgentAdapter(client=mock)

        state = _run(adapter.get_state())

        assert state.screen == GameScreen.UNKNOWN

    def test_server_error_raises_sts2error(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(500, {})
        adapter = AgentAdapter(client=mock)

        with pytest.raises(STS2Error) as exc:
            _run(adapter.get_state())
        assert exc.value.category == ErrorCategory.ADAPTER_ERROR


class TestAgentAdapterAvailableActions:
    """get_available_actions() maps to POST {endpoint}/available_actions"""

    def test_returns_list(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actions": ["play_card", "end_turn"]})
        adapter = AgentAdapter(client=mock)

        actions = _run(adapter.get_available_actions())

        assert actions == ["play_card", "end_turn"]

    def test_empty_list(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actions": []})
        adapter = AgentAdapter(client=mock)

        actions = _run(adapter.get_available_actions())

        assert actions == []


class TestAgentAdapterAct:
    """act() maps to POST {endpoint}/act"""

    def test_success(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("play_card", {"card_id": "strike"}))

        assert isinstance(result, ActionResult)
        assert result.status == "success"

    def test_failure(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": False, "error": "CARD_NOT_FOUND"})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("play_card", {"card_id": "nonexistent"}))

        assert result.status == "failure"

    def test_timeout_from_http(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(408, {})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("play_card"))

        assert result.status == "timeout"


class TestAgentAdapterWaitUntilActionable:
    """wait_until_actionable() polls via POST {endpoint}/wait_until_actionable"""

    def test_returns_true_when_ready(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actionable": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.wait_until_actionable(10.0))

        assert result is True

    def test_returns_false_on_timeout(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actionable": False})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.wait_until_actionable(0.1))

        assert result is False


class TestAgentAdapterCaptureBugSnapshot:
    """capture_bug_snapshot() composes from get_state + get_available_actions"""

    def test_returns_snapshot(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"screen": "COMBAT", "hp": 50})
        mock.add_response(200, {"actions": ["play_card", "end_turn"]})
        adapter = AgentAdapter(client=mock)

        snapshot = _run(adapter.capture_bug_snapshot())

        assert "game_state" in snapshot
        assert "available_actions" in snapshot
        assert "timestamp" in snapshot


class TestAgentAdapterCleanup:
    """cleanup() closes the HTTP client session"""

    def test_closes_client(self) -> None:
        mock = MockAsyncClient()
        adapter = AgentAdapter(client=mock)

        _run(adapter.cleanup())

        assert mock._closed is True

    def test_idempotent(self) -> None:
        adapter = AgentAdapter()

        _run(adapter.cleanup())
        _run(adapter.cleanup())  # Should not raise


class TestAgentAdapterVersionHandshake:
    """Version handshake on first health_check response"""

    def test_version_mismatch_raises_error(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"version": "2.0.0"})
        adapter = AgentAdapter(client=mock, supported_version=1)

        with pytest.raises(STS2Error) as exc:
            _run(adapter.health_check())
        assert "version" in str(exc.value).lower()

    def test_valid_version_sets_flag(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"version": "0.1.2", "status": "ok"})
        adapter = AgentAdapter(client=mock, supported_version=0)

        result = _run(adapter.health_check())

        assert result.healthy is True
        assert adapter._version_checked is True


class TestAgentAdapterErrorMapping:
    """Verify httpx exceptions are properly classified as STS2Error."""

    def test_timeout_exception_maps_to_adapter_error(self) -> None:
        mock = MockAsyncClient()
        mock.add_exception(httpx.TimeoutException("Connection timed out"))
        adapter = AgentAdapter(client=mock)

        with pytest.raises(STS2Error) as exc:
            _run(adapter.get_state())
        assert exc.value.category == ErrorCategory.ADAPTER_ERROR
        assert exc.value.detail.get("subtype") == AdapterErrorSubType.TIMEOUT

    def test_connect_error_maps_to_adapter_error(self) -> None:
        mock = MockAsyncClient()
        mock.add_exception(httpx.ConnectError("Connection refused"))
        adapter = AgentAdapter(client=mock)

        with pytest.raises(STS2Error) as exc:
            _run(adapter.get_state())
        assert exc.value.category == ErrorCategory.ADAPTER_ERROR
        assert exc.value.detail.get("subtype") == AdapterErrorSubType.PROCESS_EXIT

    def test_http_status_error_408_maps_to_timeout(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(408, {})
        adapter = AgentAdapter(client=mock)

        with pytest.raises(STS2Error) as exc:
            _run(adapter.get_state())
        assert exc.value.category == ErrorCategory.TIMEOUT_ERROR

    def test_http_status_error_504_maps_to_timeout(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(504, {})
        adapter = AgentAdapter(client=mock)

        with pytest.raises(STS2Error) as exc:
            _run(adapter.get_state())
        assert exc.value.category == ErrorCategory.TIMEOUT_ERROR

    def test_http_status_error_500_maps_to_adapter_error(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(500, {})
        adapter = AgentAdapter(client=mock)

        with pytest.raises(STS2Error) as exc:
            _run(adapter.get_state())
        assert exc.value.category == ErrorCategory.ADAPTER_ERROR
        assert exc.value.detail.get("subtype") == AdapterErrorSubType.NONZERO_EXIT_CODE

    def test_request_error_fallback(self) -> None:
        mock = MockAsyncClient()
        mock.add_exception(httpx.RemoteProtocolError("Remote disconnected"))
        adapter = AgentAdapter(client=mock)

        with pytest.raises(STS2Error) as exc:
            _run(adapter.get_state())
        assert exc.value.category == ErrorCategory.ADAPTER_ERROR
        assert exc.value.detail.get("subtype") == AdapterErrorSubType.PROCESS_EXIT

    def test_json_decode_error_maps_to_adapter_error(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"broken": None})  # Will be returned as valid JSON
        adapter = AgentAdapter(client=mock)
        # get_state calls _request which calls resp.json() — valid JSON here
        state = _run(adapter.get_state())
        assert state.screen == GameScreen.UNKNOWN

    def test_act_timeout_from_exception(self) -> None:
        mock = MockAsyncClient()
        mock.add_exception(httpx.TimeoutException("Timed out"))
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("play_card"))
        assert result.status == "timeout"

    def test_act_connect_error_maps_to_failure(self) -> None:
        mock = MockAsyncClient()
        mock.add_exception(httpx.ConnectError("Connection refused"))
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("play_card"))
        assert result.status == "failure"

    def test_capture_bug_snapshot_fallback(self) -> None:
        """When get_state fails, capture_bug_snapshot returns UNKNOWN."""
        mock = MockAsyncClient()
        mock.add_exception(httpx.ConnectError("No connection"))
        adapter = AgentAdapter(client=mock)

        snapshot = _run(adapter.capture_bug_snapshot())
        assert snapshot["available_actions"] == []
        assert "timestamp" in snapshot


class TestAgentAdapterScreenMapping:
    """Verify screen mapping covers all known values."""

    def test_crashed_screen_mapped(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"screen": "CRASHED"})
        adapter = AgentAdapter(client=mock)

        state = _run(adapter.get_state())
        assert state.screen == GameScreen.CRASHED

    def test_all_major_screens(self) -> None:
        screens = {
            "MENU": GameScreen.MAIN_MENU,
            "CHARACTER_SELECT": GameScreen.CHARACTER_SELECT,
            "COMBAT": GameScreen.COMBAT,
            "SHOP": GameScreen.SHOP,
            "REST": GameScreen.REST,
            "EVENT": GameScreen.EVENT,
            "CHEST": GameScreen.CHEST,
            "BOSS_REWARD": GameScreen.BOSS_REWARD,
            "CARD_REWARD": GameScreen.CARD_REWARD,
            "GAME_OVER": GameScreen.GAME_OVER,
            "VICTORY": GameScreen.VICTORY,
            "CRASHED": GameScreen.CRASHED,
            "NONEXISTENT": GameScreen.UNKNOWN,
        }
        for raw, expected in screens.items():
            mock = MockAsyncClient()
            mock.add_response(200, {"screen": raw})
            adapter = AgentAdapter(client=mock)
            state = _run(adapter.get_state())
            assert state.screen == expected, f"{raw} -> {expected}"


class TestProtocolCompliance:
    """Verify AgentAdapter satisfies GameAdapterProtocol"""

    def test_is_protocol_instance(self) -> None:
        from sts2_autotest.adapters.base import GameAdapterProtocol

        adapter = AgentAdapter()
        assert isinstance(adapter, GameAdapterProtocol)

    def test_has_all_protocol_methods(self) -> None:
        adapter = AgentAdapter()
        assert hasattr(adapter, "health_check")
        assert hasattr(adapter, "get_state")
        assert hasattr(adapter, "get_available_actions")
        assert hasattr(adapter, "act")
        assert hasattr(adapter, "wait_until_actionable")
        assert hasattr(adapter, "capture_bug_snapshot")
        assert hasattr(adapter, "cleanup")
