"""Real STS2-Agent smoke tests.

These tests require Slay the Spire 2 to be running with the STS2-Agent mod
loaded. The MCP smoke additionally requires the STS2-Agent network MCP server.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine
from typing import Any, TypeVar

import httpx
import pytest

from sts2_autotest.adapters.agent import AgentAdapter, FastMcpAgentClient
from sts2_autotest.common.state import GameState

T = TypeVar("T")


def _run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _agent_endpoint() -> str:
    return os.environ.get("STS2_AGENT_ENDPOINT", "http://127.0.0.1:8080")


def _mcp_endpoint() -> str:
    return os.environ.get("STS2_AGENT_MCP_ENDPOINT", "http://127.0.0.1:8765/mcp")


def _mcp_healthz_url() -> str:
    endpoint = _mcp_endpoint().rstrip("/")
    if endpoint.endswith("/mcp"):
        return f"{endpoint[:-4]}/healthz"
    return f"{endpoint}/healthz"


async def _require_agent(adapter: AgentAdapter) -> AgentAdapter:
    health = await adapter.health_check()
    if not health.healthy:
        pytest.skip(
            "STS2-Agent HTTP service is not ready; start the game with the "
            "STS2-Agent mod loaded"
        )
    return adapter


def _require_mcp_server() -> None:
    try:
        response = httpx.get(_mcp_healthz_url(), timeout=3.0)
        if response.status_code >= 400:
            pytest.skip(
                "STS2-Agent network MCP server healthz is not ready"
            )
    except httpx.HTTPError:
        pytest.skip(
            "STS2-Agent network MCP server is not running; start it before "
            "running MCP smoke tests"
        )


pytestmark = [pytest.mark.integration, pytest.mark.requires_game]


class TestAgentHttpSmoke:
    """Smoke the real STS2-Agent HTTP mod API through AgentAdapter."""

    def test_health_state_and_actions(self) -> None:
        async def scenario() -> tuple[bool, GameState, list[str]]:
            adapter = AgentAdapter(endpoint=_agent_endpoint(), timeout=5.0)
            await _require_agent(adapter)
            try:
                health = await adapter.health_check()
                state = await adapter.get_state()
                actions = await adapter.get_available_actions()
                return health.healthy, state, actions
            finally:
                await adapter.cleanup()

        healthy, state, actions = _run(scenario())

        assert healthy is True
        assert isinstance(state, GameState)
        assert isinstance(actions, list)
        assert all(isinstance(action, str) for action in actions)

    def test_bug_snapshot_uses_real_agent(self) -> None:
        async def scenario() -> dict[str, object]:
            adapter = AgentAdapter(endpoint=_agent_endpoint(), timeout=5.0)
            await _require_agent(adapter)
            try:
                return await adapter.capture_bug_snapshot()
            finally:
                await adapter.cleanup()

        snapshot = _run(scenario())

        assert isinstance(snapshot["game_state"], GameState)
        assert isinstance(snapshot["available_actions"], list)
        assert "timestamp" in snapshot


class TestAgentMcpSmoke:
    """Smoke the real STS2-Agent network MCP server through AgentAdapter."""

    def test_mcp_health_state_and_actions(self) -> None:
        _require_mcp_server()

        async def scenario() -> tuple[bool, GameState, list[str]]:
            adapter = AgentAdapter(
                transport="mcp",
                mcp_client=FastMcpAgentClient(_mcp_endpoint()),
                timeout=10.0,
            )
            await _require_agent(adapter)
            try:
                health = await adapter.health_check()
                state = await adapter.get_state()
                actions = await adapter.get_available_actions()
                return health.healthy, state, actions
            finally:
                await adapter.cleanup()

        healthy, state, actions = _run(scenario())

        assert healthy is True
        assert isinstance(state, GameState)
        assert isinstance(actions, list)
        assert all(isinstance(action, str) for action in actions)
