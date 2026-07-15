"""Unit tests for AgentAdapter -- all HTTP calls mocked with MockAsyncClient."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx
import pytest

from sts2_autotest.adapters.agent import AgentAdapter, FastMcpAgentClient
from sts2_autotest.adapters.base import ActionResult
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


class MockMcpClient:
    """用于验证 MCP 传输路径的轻量测试替身。"""

    def __init__(self) -> None:
        self.responses: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    def add_response(self, json_data: dict[str, Any]) -> None:
        self.responses.append(json_data)

    async def request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.requests.append({"method": method, "path": path, "json_data": json_data})
        return self.responses.pop(0) if self.responses else {}

    async def aclose(self) -> None:
        self.closed = True


class ValueErrorMcpClient:
    """用于验证业务 ValueError 不会被误报为 JSON 解析失败。"""

    async def request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise ValueError("business rule rejected")

    async def aclose(self) -> None:
        pass


class TestAgentAdapterTransport:
    """AgentAdapter 传输方式选择。"""

    def test_default_transport_uses_http_client(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"status": "ok"})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.health_check())

        assert result.healthy is True
        assert adapter.transport == "http"
        assert mock._requests[0]["url"] == "http://127.0.0.1:8080/health"

    def test_mcp_transport_uses_injected_client(self) -> None:
        mcp = MockMcpClient()
        mcp.add_response({"status": "ok"})
        adapter = AgentAdapter(transport="mcp", mcp_client=mcp)

        result = _run(adapter.health_check())

        assert result.healthy is True
        assert mcp.requests == [{"method": "GET", "path": "health", "json_data": None}]

    def test_fast_mcp_client_name_is_available(self) -> None:
        client = FastMcpAgentClient(endpoint="http://127.0.0.1:8765/mcp")

        assert client.endpoint == "http://127.0.0.1:8765/mcp"

    def test_fast_mcp_client_disables_env_proxy_lookup(self) -> None:
        client = FastMcpAgentClient()

        http_client = client._get_client()

        assert getattr(http_client, "_trust_env") is False

    def test_default_mcp_client_uses_adapter_endpoint(self) -> None:
        adapter = AgentAdapter(endpoint="http://example.test/custom", transport="mcp")

        client = cast(FastMcpAgentClient, adapter._get_mcp_client())

        assert client.endpoint == "http://example.test/custom"

    def test_default_http_client_disables_env_proxy_lookup(self) -> None:
        adapter = AgentAdapter()

        http_client = adapter._get_client()

        assert getattr(http_client, "_trust_env") is False

    def test_mcp_non_json_response_maps_to_parse_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not valid json")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        mcp = FastMcpAgentClient(endpoint="http://127.0.0.1:8765/mcp", client=client)
        adapter = AgentAdapter(transport="mcp", mcp_client=mcp)

        with pytest.raises(STS2Error) as exc:
            _run(adapter.get_state())

        assert exc.value.category == ErrorCategory.ADAPTER_ERROR
        assert exc.value.detail.get("subtype") == AdapterErrorSubType.JSON_PARSE_FAILURE
        assert exc.value.detail.get("path") == "state"
        assert exc.value.detail.get("method") == "GET"

    def test_mcp_business_value_error_is_not_json_parse_failure(self) -> None:
        adapter = AgentAdapter(transport="mcp", mcp_client=ValueErrorMcpClient())

        with pytest.raises(ValueError, match="business rule rejected"):
            _run(adapter.get_state())


class TestAgentAdapterHealthCheck:
    """health_check() maps to GET {endpoint}/health"""

    def test_healthy(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"status": "ok"})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.health_check())

        assert result.healthy is True
        assert mock._requests[0]["url"] == "http://127.0.0.1:8080/health"

    def test_real_agent_enveloped_ready_health_is_healthy(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True, "data": {"status": "ready"}})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.health_check())

        assert result.healthy is True

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
    """get_state() maps to POST {endpoint}/state"""

    def test_returns_game_state(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"screen": "MENU", "hp": 80})
        adapter = AgentAdapter(client=mock)

        state = _run(adapter.get_state())

        assert isinstance(state, GameState)
        assert state.screen == GameScreen.MAIN_MENU

    def test_real_agent_enveloped_state(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True, "data": {"screen": "COMBAT", "in_combat": True}})
        adapter = AgentAdapter(client=mock)

        state = _run(adapter.get_state())

        assert state.screen == GameScreen.COMBAT
        assert state.in_combat is True

    def test_card_selection_screen_maps_to_card_reward(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True, "data": {"screen": "CARD_SELECTION"}})
        adapter = AgentAdapter(client=mock)

        state = _run(adapter.get_state())

        assert state.screen == GameScreen.CARD_REWARD

    def test_reward_screen_maps_to_card_reward(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True, "data": {"screen": "REWARD"}})
        adapter = AgentAdapter(client=mock)

        state = _run(adapter.get_state())

        assert state.screen == GameScreen.CARD_REWARD

    def test_real_agent_main_menu_state_maps_to_main_menu(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True, "data": {"screen": "MAIN_MENU"}})
        adapter = AgentAdapter(client=mock)

        state = _run(adapter.get_state())

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
    """get_available_actions() maps to POST {endpoint}/actions/available"""

    def test_returns_list(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actions": ["play_card", "end_turn"]})
        adapter = AgentAdapter(client=mock)

        actions = _run(adapter.get_available_actions())

        assert actions == ["play_card", "end_turn"]

    def test_real_agent_enveloped_action_objects(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "actions": [
                        {"name": "end_turn", "requires_target": False},
                        {"name": "play_card", "requires_index": True},
                    ]
                },
            },
        )
        adapter = AgentAdapter(client=mock)

        actions = _run(adapter.get_available_actions())

        assert actions == ["end_turn", "play_card"]

    def test_main_menu_actions_add_start_new_run_alias(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "MAIN_MENU",
                    "actions": [
                        {"name": "open_character_select", "requires_target": False},
                        {"name": "open_timeline", "requires_target": False},
                    ],
                },
            },
        )
        adapter = AgentAdapter(client=mock)

        actions = _run(adapter.get_available_actions())

        assert actions == ["open_character_select", "open_timeline", "start_new_run"]

    def test_saved_run_actions_add_start_new_run_alias(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "MAIN_MENU",
                    "actions": [
                        {"name": "continue_run", "requires_target": False},
                        {"name": "abandon_run", "requires_target": False},
                        {"name": "open_timeline", "requires_target": False},
                    ],
                },
            },
        )
        adapter = AgentAdapter(client=mock)

        actions = _run(adapter.get_available_actions())

        assert actions == ["continue_run", "abandon_run", "open_timeline", "start_new_run"]

    def test_map_actions_add_choose_map_node_by_type_alias(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actions": ["choose_map_node"]})
        adapter = AgentAdapter(client=mock)

        actions = _run(adapter.get_available_actions())

        assert actions == ["choose_map_node", "choose_map_node_by_type"]

    def test_event_actions_add_choose_neow_blessing_alias(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actions": ["choose_event_option"]})
        adapter = AgentAdapter(client=mock)

        actions = _run(adapter.get_available_actions())

        assert actions == ["choose_event_option", "choose_event", "choose_neow_blessing"]

    def test_empty_list(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actions": []})
        adapter = AgentAdapter(client=mock)

        actions = _run(adapter.get_available_actions())

        assert actions == []

    def test_debug_actions_adds_custom_debug_actions(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"actions": ["play_card"]})
        adapter = AgentAdapter(client=mock, debug_actions=True)

        actions = _run(adapter.get_available_actions())

        assert actions == ["play_card", "give_card", "set_seed", "set_hp", "give_block", "win_combat", "enable_travel"]


class TestAgentAdapterAct:
    """act() maps to POST {endpoint}/action"""

    def test_success(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {"ok": True, "data": {"screen": "EVENT", "event": {"event_id": "OTHER_EVENT"}}},
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_event", {"index": 0}))

        assert isinstance(result, ActionResult)
        assert result.status == "success"

    def test_success_flattens_action_args(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {"ok": True, "data": {"screen": "EVENT", "event": {"event_id": "OTHER_EVENT"}}},
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_event", {"index": 0}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "choose_event_option",
            "option_index": 0,
        }

    def test_start_new_run_maps_to_open_character_select(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "actions": [
                        {"name": "open_character_select"},
                        {"name": "open_timeline"},
                    ]
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("start_new_run"))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "open_character_select",
        }

    def test_start_new_run_abandons_existing_save_then_opens_character_select(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "actions": [
                        {"name": "continue_run"},
                        {"name": "abandon_run"},
                        {"name": "open_timeline"},
                    ]
                },
            },
        )
        mock.add_response(200, {"ok": True})
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "actions": [
                        {"name": "confirm_modal"},
                        {"name": "dismiss_modal"},
                    ]
                },
            },
        )
        mock.add_response(200, {"ok": True})
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "actions": [
                        {"name": "open_character_select"},
                        {"name": "open_timeline"},
                    ]
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("start_new_run"))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {"action": "abandon_run"}
        assert mock._requests[3]["kwargs"]["json"] == {"action": "confirm_modal"}
        assert mock._requests[5]["kwargs"]["json"] == {"action": "open_character_select"}

    def test_choose_event_maps_to_choose_event_option(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {"ok": True, "data": {"screen": "EVENT", "event": {"event_id": "OTHER_EVENT"}}},
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_event", {"index": 0}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "choose_event_option",
            "option_index": 0,
        }

    def test_choose_neow_blessing_prefers_single_select_branch(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "EVENT",
                    "event": {
                        "event_id": "NEOW",
                        "options": [
                            {
                                "index": 0,
                                "text_key": "NEOW.pages.INITIAL.options.ARCANE_SCROLL",
                                "title": "奥术卷轴",
                                "description": "获得一张随机稀有牌。",
                                "is_locked": False,
                            },
                            {
                                "index": 1,
                                "text_key": "NEOW.pages.INITIAL.options.NEW_LEAF",
                                "title": "新叶",
                                "description": "变化1张牌。",
                                "is_locked": False,
                            },
                        ],
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        mock.add_response(200, {"ok": True, "data": {"screen": "MAP"}})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_neow_blessing"))

        assert result.status == "success"
        post_payloads = [
            request["kwargs"]["json"]
            for request in mock._requests
            if request["method"] == "POST" and "json" in request["kwargs"]
        ]
        assert post_payloads[0] == {
            "action": "choose_event_option",
            "option_index": 1,
        }

    def test_choose_event_auto_confirms_finished_event_with_single_proceed(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {"ok": True, "data": {"screen": "EVENT", "event": {"event_id": "OTHER_EVENT"}}},
        )
        mock.add_response(200, {"ok": True})
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "EVENT",
                    "event": {
                        "is_finished": True,
                        "options": [
                            {"index": 0, "is_proceed": True, "title": "Proceed"},
                        ],
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_event", {"index": 0}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "choose_event_option",
            "option_index": 0,
        }
        assert mock._requests[2]["method"] == "GET"
        assert mock._requests[2]["url"] == "http://127.0.0.1:8080/state"
        assert mock._requests[3]["kwargs"]["json"] == {
            "action": "choose_event_option",
            "option_index": 0,
        }

    def test_choose_event_neow_auto_advances_card_selection_then_proceed(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {"ok": True, "data": {"screen": "EVENT", "event": {"event_id": "NEOW"}}},
        )
        mock.add_response(200, {"ok": True})
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "CARD_SELECTION",
                    "selection": {
                        "cards": [
                            {"index": 0, "name": "Automation"},
                            {"index": 1, "name": "Volley"},
                        ]
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "EVENT",
                    "event": {
                        "is_finished": True,
                        "options": [
                            {"index": 0, "is_proceed": True, "title": "Proceed"},
                        ],
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        mock.add_response(200, {"ok": True, "data": {"screen": "MAP"}})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_event", {"index": 0}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "choose_event_option",
            "option_index": 0,
        }
        assert mock._requests[3]["kwargs"]["json"] == {
            "action": "select_deck_card",
            "option_index": 0,
        }
        assert mock._requests[5]["kwargs"]["json"] == {
            "action": "choose_event_option",
            "option_index": 0,
        }

    def test_choose_event_neow_auto_collects_reward_then_proceed(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {"ok": True, "data": {"screen": "EVENT", "event": {"event_id": "NEOW"}}},
        )
        mock.add_response(200, {"ok": True})
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "REWARD",
                    "reward": {"cards": [], "relics": []},
                },
            },
        )
        mock.add_response(200, {"ok": True})
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "EVENT",
                    "event": {
                        "is_finished": True,
                        "options": [
                            {"index": 0, "is_proceed": True, "title": "Proceed"},
                        ],
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        mock.add_response(200, {"ok": True, "data": {"screen": "MAP"}})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_event", {"index": 0}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "choose_event_option",
            "option_index": 0,
        }
        assert mock._requests[3]["kwargs"]["json"] == {
            "action": "collect_rewards_and_proceed",
        }
        assert mock._requests[5]["kwargs"]["json"] == {
            "action": "choose_event_option",
            "option_index": 0,
        }

    def test_choose_event_neow_waits_for_delayed_finished_event_then_proceed(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {"ok": True, "data": {"screen": "EVENT", "event": {"event_id": "NEOW"}}},
        )
        mock.add_response(200, {"ok": True})
        for _ in range(10):
            mock.add_response(
                200,
                {
                    "ok": True,
                    "data": {
                        "screen": "EVENT",
                        "event": {
                            "event_id": "NEOW",
                            "is_finished": False,
                            "options": [
                                {"index": 0, "is_proceed": False, "title": "Blessing"},
                            ],
                        },
                    },
                },
            )
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "EVENT",
                    "event": {
                        "event_id": "NEOW",
                        "is_finished": True,
                        "options": [
                            {"index": 0, "is_proceed": True, "title": "Proceed"},
                        ],
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        mock.add_response(200, {"ok": True, "data": {"screen": "MAP"}})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_event", {"index": 0}))

        assert result.status == "success"
        assert mock._requests[-2]["kwargs"]["json"] == {
            "action": "choose_event_option",
            "option_index": 0,
        }

    def test_select_deck_card_maps_index_to_option_index(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("select_deck_card", {"index": 2}))

        assert result.status == "success"
        assert mock._requests[0]["kwargs"]["json"] == {
            "action": "select_deck_card",
            "option_index": 2,
        }

    def test_select_deck_card_auto_confirms_finished_event_with_single_proceed(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True})
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "EVENT",
                    "event": {
                        "is_finished": True,
                        "options": [
                            {"index": 1, "is_proceed": True, "title": "Proceed"},
                        ],
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("select_deck_card", {"index": 0}))

        assert result.status == "success"
        assert mock._requests[0]["kwargs"]["json"] == {
            "action": "select_deck_card",
            "option_index": 0,
        }
        assert mock._requests[1]["method"] == "GET"
        assert mock._requests[2]["kwargs"]["json"] == {
            "action": "choose_event_option",
            "option_index": 1,
        }

    def test_collect_rewards_and_proceed_auto_confirms_finished_event_with_single_proceed(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True})
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "EVENT",
                    "event": {
                        "is_finished": True,
                        "options": [
                            {"index": 0, "is_proceed": True, "title": "Proceed"},
                        ],
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("collect_rewards_and_proceed"))

        assert result.status == "success"
        assert mock._requests[0]["kwargs"]["json"] == {
            "action": "collect_rewards_and_proceed",
        }
        assert mock._requests[2]["kwargs"]["json"] == {
            "action": "choose_event_option",
            "option_index": 0,
        }

    def test_choose_map_node_resolves_coordinates_to_option_index(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "MAP",
                    "map": {
                        "available_nodes": [
                            {"index": 0, "col": 0, "row": 0},
                            {"index": 1, "col": 1, "row": 0},
                        ]
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_map_node", {"col": 1, "row": 0}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "choose_map_node",
            "option_index": 1,
        }

    def test_choose_map_node_accepts_legacy_row_col_ordering(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "MAP",
                    "map": {
                        "available_nodes": [
                            {"index": 0, "col": 0, "row": 1},
                        ]
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_map_node", {"col": 1, "row": 0}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "choose_map_node",
            "option_index": 0,
        }

    def test_play_card_resolves_card_id_to_agent_card_index(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "COMBAT",
                    "combat": {
                        "hand": [
                            {"index": 0, "card_id": "STRIKE_IRONCLAD"},
                            {"index": 1, "card_id": "TWIN_STRIKE"},
                        ]
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("play_card", {"card_id": "TWIN_STRIKE", "target": 0}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "play_card",
            "card_index": 1,
            "target_index": 0,
        }

    def test_play_card_resolves_semantic_card_id_to_runtime_index(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "COMBAT",
                    "combat": {
                        "hand": [
                            {"index": 3, "card_id": "GAWAINMOD-EMERGENCY_RECRUIT"},
                        ]
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("play_card", {"card_id": "gawain:emergency_recruit"}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "play_card",
            "card_index": 3,
        }

    def test_play_card_drops_default_target_for_non_targeted_card(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "COMBAT",
                    "combat": {
                        "hand": [
                            {
                                "index": 4,
                                "card_id": "GAWAINMOD-PORTABLE_MAGIC_TERMINAL",
                                "requires_target": False,
                            },
                        ]
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("play_card", {"card_id": "gawain:portable_magic_terminal", "target": 0}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "play_card",
            "card_index": 4,
        }

    def test_give_card_uses_debug_console_command(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "COMBAT",
                    "run": {
                        "deck": [
                            {"card_id": "GAWAINMOD-EMERGENCY_RECRUIT"},
                        ]
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(
            endpoint="http://127.0.0.1:8080",
            client=mock,
            debug_actions=True,
        )

        result = _run(adapter.act("give_card", {"card_id": "gawain:emergency_recruit"}))

        assert result.status == "success"
        assert mock._requests[0]["url"] == "http://127.0.0.1:8080/state"
        assert mock._requests[1]["url"] == "http://127.0.0.1:8080/action"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "run_console_command",
            "command": "card GAWAINMOD-EMERGENCY_RECRUIT hand",
        }

    def test_give_card_requires_debug_actions(self) -> None:
        adapter = AgentAdapter()

        result = _run(adapter.act("give_card", {"card_id": "TWIN_STRIKE"}))

        assert result.status == "failure"
        assert result.detail == "give_card requires AgentAdapter(debug_actions=True)"

    def test_set_seed_uses_debug_console_command(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(
            endpoint="http://127.0.0.1:8080",
            client=mock,
            debug_actions=True,
        )

        result = _run(adapter.act("set_seed", {"seed": 35}))

        assert result.status == "success"
        assert mock._requests[0]["url"] == "http://127.0.0.1:8080/action"
        assert mock._requests[0]["kwargs"]["json"] == {
            "action": "run_console_command",
            "command": "gawain_emergency_recruit_seed 35",
        }

    def test_set_seed_requires_debug_actions(self) -> None:
        adapter = AgentAdapter()

        result = _run(adapter.act("set_seed", {"seed": 35}))

        assert result.status == "failure"
        assert result.detail == "set_seed requires AgentAdapter(debug_actions=True)"

    def test_set_hp_uses_damage_console_command(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "COMBAT",
                    "combat": {"player": {"current_hp": 80}},
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock, debug_actions=True)

        result = _run(adapter.act("set_hp", {"hp": 75}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "run_console_command",
            "command": "damage 5 0",
        }

    def test_set_hp_uses_heal_console_command_when_target_is_higher(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "COMBAT",
                    "combat": {"player": {"current_hp": 70}},
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock, debug_actions=True)

        result = _run(adapter.act("set_hp", {"hp": 75}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "run_console_command",
            "command": "heal 5",
        }

    def test_set_hp_requires_debug_actions(self) -> None:
        adapter = AgentAdapter()

        result = _run(adapter.act("set_hp", {"hp": 75}))

        assert result.status == "failure"
        assert result.detail == "set_hp requires AgentAdapter(debug_actions=True)"

    def test_give_block_uses_debug_console_command(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock, debug_actions=True)

        result = _run(adapter.act("give_block", {"amount": 9}))

        assert result.status == "success"
        assert mock._requests[0]["kwargs"]["json"] == {
            "action": "run_console_command",
            "command": "block 9 0",
        }

    def test_win_combat_uses_debug_console_command(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock, debug_actions=True)

        result = _run(adapter.act("win_combat"))

        assert result.status == "success"
        assert mock._requests[0]["kwargs"]["json"] == {
            "action": "run_console_command",
            "command": "win",
        }

    def test_enable_travel_uses_debug_console_command(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock, debug_actions=True)

        result = _run(adapter.act("enable_travel"))

        assert result.status == "success"
        assert mock._requests[0]["kwargs"]["json"] == {
            "action": "run_console_command",
            "command": "travel",
        }

    def test_choose_map_node_by_type_resolves_option_index(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "MAP",
                    "map": {
                        "available_nodes": [
                            {"index": 3, "node_type": "Monster"},
                            {"index": 16, "node_type": "RestSite"},
                        ]
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_map_node_by_type", {"node_type": "RestSite"}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "choose_map_node",
            "option_index": 16,
        }

    def test_choose_map_node_by_type_skips_current_traveled_node_when_travel_enabled(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "MAP",
                    "map": {
                        "current_node": {"row": 1, "col": 0},
                        "is_travel_enabled": True,
                        "nodes": [
                            {"index": 1, "row": 1, "col": 0, "node_type": "Monster", "state": "Traveled"},
                            {"index": 2, "row": 1, "col": 1, "node_type": "Monster", "state": "Travelable"},
                            {"index": 5, "row": 2, "col": 0, "node_type": "Unknown", "state": "Untravelable"},
                        ],
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_map_node_by_type", {"node_type": "Monster"}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "choose_map_node",
            "option_index": 2,
        }

    def test_choose_map_node_by_type_uses_full_map_when_travel_enabled(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "MAP",
                    "map": {
                        "current_node": {"row": 2, "col": 3},
                        "is_travel_enabled": True,
                        "available_nodes": [
                            {"index": 8, "row": 3, "col": 2, "node_type": "Monster", "state": "Travelable"},
                        ],
                        "nodes": [
                            {"index": 5, "row": 2, "col": 3, "node_type": "Monster", "state": "Traveled"},
                            {"index": 8, "row": 3, "col": 2, "node_type": "Monster", "state": "Travelable"},
                            {"index": 20, "row": 6, "col": 0, "node_type": "RestSite", "state": "Travelable"},
                        ],
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("choose_map_node_by_type", {"node_type": "RestSite"}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "choose_map_node",
            "option_index": 20,
        }

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

    def test_select_character_resolves_character_id_to_option_index(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "CHARACTER_SELECT",
                    "character_select": {
                        "characters": [
                            {"character_id": "IRONCLAD", "index": 0},
                            {"character_id": "SILENT", "index": 1},
                            {"character_id": "GAWAINMOD-GAWAIN", "index": 6},
                        ]
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("select_character", {"character_id": "GAWAINMOD-GAWAIN"}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "select_character",
            "option_index": 6,
        }

    def test_select_character_fuzzy_matches_lowercase_id(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "CHARACTER_SELECT",
                    "character_select": {
                        "characters": [
                            {"character_id": "IRONCLAD", "index": 0},
                            {"character_id": "GAWAINMOD-GAWAIN", "index": 6},
                        ]
                    },
                },
            },
        )
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("select_character", {"character_id": "gawain"}))

        assert result.status == "success"
        assert mock._requests[1]["kwargs"]["json"] == {
            "action": "select_character",
            "option_index": 6,
        }

    def test_select_character_passes_through_when_option_index_given(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("select_character", {"option_index": 3}))

        assert result.status == "success"
        assert mock._requests[0]["kwargs"]["json"] == {
            "action": "select_character",
            "option_index": 3,
        }

    def test_select_character_passes_through_when_character_not_found(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "CHARACTER_SELECT",
                    "character_select": {"characters": [{"character_id": "IRONCLAD", "index": 0}]},
                },
            },
        )
        mock.add_response(200, {"ok": False, "error": "CHARACTER_NOT_FOUND"})
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("select_character", {"character_id": "UNKNOWN_MOD-HERO"}))

        assert result.status == "failure"

    def test_enter_combat_is_noop_when_already_in_combat(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "screen": "COMBAT",
                },
            },
        )
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("enter_combat"))

        assert result.status == "success"
        assert result.state_changed is False
        assert len(mock._requests) == 1


class TestAgentAdapterWaitUntilActionable:
    """wait_until_actionable() polls real Agent health/actions endpoints."""

    def test_returns_true_when_ready(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True, "data": {"status": "ready"}})
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "actions": [
                        {"name": "play_card"},
                    ]
                },
            },
        )
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.wait_until_actionable(10.0))

        assert result is True
        assert [request["method"] for request in mock._requests] == ["GET", "GET"]
        assert mock._requests[0]["url"] == "http://127.0.0.1:8080/health"
        assert mock._requests[1]["url"] == "http://127.0.0.1:8080/actions/available"

    def test_ignores_debug_only_actions_until_real_action_is_available(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True, "data": {"status": "ready"}})
        mock.add_response(200, {"ok": True, "data": {"actions": []}})
        mock.add_response(200, {"ok": True, "data": {"status": "ready"}})
        mock.add_response(
            200,
            {
                "ok": True,
                "data": {
                    "actions": [
                        {"name": "open_character_select"},
                    ]
                },
            },
        )
        adapter = AgentAdapter(client=mock, debug_actions=True)

        result = _run(adapter.wait_until_actionable(1.0))

        assert result is True
        assert [request["url"] for request in mock._requests] == [
            "http://127.0.0.1:8080/health",
            "http://127.0.0.1:8080/actions/available",
            "http://127.0.0.1:8080/health",
            "http://127.0.0.1:8080/actions/available",
        ]

    def test_returns_false_on_timeout(self) -> None:
        mock = MockAsyncClient()
        mock.add_response(200, {"ok": True, "data": {"status": "ready"}})
        mock.add_response(200, {"ok": True, "data": {"actions": []}})
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

    def test_http_status_error_504_via_exception(self) -> None:
        """HTTPStatusError(504) exception injection triggers timeout result."""
        mock = MockAsyncClient()
        request = httpx.Request("GET", "http://127.0.0.1:8080/game_state")
        mock.add_exception(
            httpx.HTTPStatusError("Gateway Timeout", request=request, response=httpx.Response(504))
        )
        adapter = AgentAdapter(client=mock)

        result = _run(adapter.act("play_card"))
        assert result.status == "timeout"

    def test_http_status_error_500_via_exception(self) -> None:
        """HTTPStatusError exception injection triggers ADAPTER_ERROR."""
        mock = MockAsyncClient()
        request = httpx.Request("GET", "http://127.0.0.1:8080/game_state")
        mock.add_exception(
            httpx.HTTPStatusError("Server Error", request=request, response=httpx.Response(500))
        )
        adapter = AgentAdapter(client=mock)

        with pytest.raises(STS2Error) as exc:
            _run(adapter.get_state())
        assert exc.value.category == ErrorCategory.ADAPTER_ERROR

    def test_json_decode_error_via_mock(self) -> None:
        """JSON decode failure maps to ADAPTER_ERROR / JSON_PARSE_FAILURE."""
        mock = MockAsyncClient()
        # Override post to return non-JSON body that still creates httpx.Response
        mock.add_response(200, {"screen": "test"})  # Valid JSON, works fine
        adapter = AgentAdapter(client=mock)
        # Construct a response with invalid bytes
        raw_resp = httpx.Response(200, content=b"not valid json at all")
        # Clear queue and add the bad response
        mock.responses.clear()
        mock.responses.append(raw_resp)

        with pytest.raises(STS2Error) as exc:
            _run(adapter.get_state())
        assert exc.value.category == ErrorCategory.ADAPTER_ERROR
        assert exc.value.detail.get("subtype") == AdapterErrorSubType.JSON_PARSE_FAILURE


class TestStaticScreenChecker:
    """Static checks on screen map that require no HTTP calls."""

    def test_has_crashed_mapping(self) -> None:
        from sts2_autotest.adapters.agent import _SCREEN_MAP
        assert "CRASHED" in _SCREEN_MAP
        assert _SCREEN_MAP["CRASHED"] == GameScreen.CRASHED

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
            "REWARD": GameScreen.CARD_REWARD,
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
