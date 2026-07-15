from __future__ import annotations

import asyncio
from typing import Any

import httpx

from sts2_autotest.adapters.agent import AgentAdapter


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class MockAsyncClient:
    def __init__(self) -> None:
        self.responses: list[httpx.Response] = []
        self._requests: list[dict[str, Any]] = []

    def add_response(self, status: int = 200, json_data: dict[str, Any] | None = None) -> None:
        self.responses.append(httpx.Response(status, json=json_data or {}))

    async def get(self, url: str, **kwargs: object) -> httpx.Response:
        self._requests.append({"method": "GET", "url": url, "kwargs": kwargs})
        return self.responses.pop(0)

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        self._requests.append({"method": "POST", "url": url, "kwargs": kwargs})
        return self.responses.pop(0)

    async def aclose(self) -> None:
        return None


def test_http_409_includes_server_message_for_set_seed() -> None:
    mock = MockAsyncClient()
    mock.add_response(
        409,
        {"ok": False, "error": {"message": "The command 'seed' does not exist."}},
    )
    adapter = AgentAdapter(
        endpoint="http://127.0.0.1:8080",
        client=mock,
        debug_actions=True,
    )

    result = _run(adapter.act("set_seed", {"seed": 3}))

    assert result.status == "failure"
    assert result.detail == "The command 'seed' does not exist."
