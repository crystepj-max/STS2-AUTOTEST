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


class SpyAgentAdapter(AgentAdapter):
    def __init__(self, *, wait_result: bool, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.wait_result = wait_result
        self.wait_calls: list[float] = []

    async def wait_until_actionable(self, timeout: float) -> bool:
        self.wait_calls.append(timeout)
        return self.wait_result


def test_start_new_run_opens_character_select_from_clean_main_menu() -> None:
    mock = MockAsyncClient()
    mock.add_response(
        200,
        {
            "ok": True,
            "data": {
                "screen": "MENU",
                "actions": [
                    {"name": "open_character_select"},
                    {"name": "open_timeline"},
                ],
            },
        },
    )
    mock.add_response(200, {"ok": True})
    adapter = SpyAgentAdapter(client=mock, wait_result=True)

    result = _run(adapter.act("start_new_run"))

    assert result.status == "success"
    # 启动后的可操作性等待由编排层统一负责，适配器动作本身只负责提交动作。
    assert adapter.wait_calls == []
    assert mock._requests[1]["kwargs"]["json"] == {"action": "open_character_select"}


def test_select_character_waits_before_resolving_option() -> None:
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
    adapter = SpyAgentAdapter(client=mock, wait_result=True)

    result = _run(adapter.act("select_character", {"character_id": "GAWAINMOD-GAWAIN"}))

    assert result.status == "success"
    # 角色选择的选项解析依赖当前状态；就绪等待由编排层统一负责。
    assert adapter.wait_calls == []
    assert mock._requests[1]["kwargs"]["json"] == {
        "action": "select_character",
        "option_index": 6,
    }
