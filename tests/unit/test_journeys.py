"""通用新局、开局事件和地图稳定性测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sts2_autotest.adapters.base import ActionResult
from sts2_autotest.core.journeys import GenericJourneys


@dataclass
class _State:
    screen: str
    available_actions: list[str]
    event: dict | None = None
    map: dict | None = None

    def model_dump(self) -> dict:
        return {
            "screen": self.screen,
            "available_actions": list(self.available_actions),
            "event": self.event,
            "map": self.map,
        }


class _Adapter:
    def __init__(self) -> None:
        self.state = _State("MAIN_MENU", ["start_new_run"])

    async def get_state(self) -> _State:
        return self.state

    async def get_available_actions(self) -> list[str]:
        return list(self.state.available_actions)

    async def act(self, action: str, params: dict | None = None) -> ActionResult:
        if action == "start_new_run":
            self.state = _State("CHARACTER_SELECT", ["select_character"])
        elif action == "select_character":
            self.state = _State("CHARACTER_SELECT", ["embark"])
        elif action == "embark":
            self.state = _State(
                "EVENT",
                ["choose_event_option"],
                event={"options": [{"index": 0, "is_locked": False}]},
            )
        elif action == "choose_event_option":
            self.state = _State(
                "MAP",
                [],
                map={"is_traveling": False, "available_nodes": [{"index": 0}]},
            )
        return ActionResult(status="success", state_changed=True)


def test_start_new_run_handles_character_and_opening_event() -> None:
    adapter = _Adapter()
    result = asyncio.run(GenericJourneys(adapter).start_new_run("IRONCLAD"))
    assert result["screen"] == "MAP"
    assert result["map"]["is_traveling"] is False


def test_resume_run_keeps_an_already_playable_run() -> None:
    adapter = _Adapter()
    adapter.state = _State("COMBAT", ["end_turn"])

    result = asyncio.run(GenericJourneys(adapter).resume_run())

    assert result["screen"] == "COMBAT"
