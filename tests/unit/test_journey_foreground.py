"""Tests for cli/main.py `_run_journey_foreground` — 真实耗时与失败留证。

验证“新局进入首战”任务入口：
1. 成功时 run-result.json 含真实 duration_ms / status_trajectory / final_state / task_id；
2. 失败时 journey-failure.json 含卡屏页面 / 最后操作 / 原因 / 轨迹 / 最后状态，且耗时 > 0。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest

from sts2_autotest.adapters.base import ActionResult


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


class _SuccessAdapter:
    """完整走完 主菜单→角色选择→开局事件→地图→首战。"""

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
            self.state = _State(
                "EVENT",
                ["choose_event_option"],
                event={"options": [{"index": 0, "is_locked": False}]},
            )
        elif action == "embark":
            self.state = _State(
                "MAP",
                ["choose_map_node"],
                map={"is_traveling": False, "available_nodes": [{"index": 0, "node_type": "Monster"}]},
            )
        elif action == "choose_event_option":
            self.state = _State(
                "MAP",
                ["choose_map_node"],
                map={"is_traveling": False, "available_nodes": [{"index": 0, "node_type": "Monster"}]},
            )
        elif action == "choose_map_node":
            self.state = _State("COMBAT", ["end_turn"])
        return ActionResult(status="success", state_changed=True)

    async def cleanup(self) -> None:
        return None


class _StuckAdapter:
    """从主菜单走到开局事件页后始终不前进，触发导航超时。"""

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
            self.state = _State(
                "EVENT",
                ["choose_event_option"],
                event={"options": [{"index": 0, "is_locked": False}]},
            )
        # choose_event_option 返回成功但不前进 → 开局事件页卡死
        return ActionResult(status="success", state_changed=False)

    async def cleanup(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_screenshots(monkeypatch: pytest.MonkeyPatch) -> None:
    # 用 StubEvidenceHooks 避免真实截图采集依赖游戏窗口
    monkeypatch.setenv("STS2_AUTOTEST_EVIDENCE", "none")


def test_first_battle_success_writes_real_duration_and_trajectory(
    tmp_path: pytest.FixtureRequest,
) -> None:
    from sts2_autotest.cli.main import _run_journey_foreground

    run_id = "test-success-1"
    evidence_dir = tmp_path / "evidence"
    import os

    os.environ["STS2_AUTOTEST_EVIDENCE_DIR"] = str(evidence_dir)

    rc = _run_journey_foreground(
        _SuccessAdapter(),
        journey="first_battle",
        character_id="IRONCLAD",
        timeout=10.0,
        run_id=run_id,
    )

    assert rc == 0
    result_path = evidence_dir / run_id / "reports" / "run-result.json"
    assert result_path.is_file()
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    # 真实耗时应大于 0（任务确实跑了一遍多步流程）
    assert payload["duration_ms"] > 0
    assert payload["status"] == "PASSED"
    assert payload["task_id"] == run_id
    assert payload["final_state"] == "COMBAT"
    assert payload["status_trajectory"] == [
        "MAIN_MENU",
        "CHARACTER_SELECT",
        "EVENT",
        "MAP",
        "COMBAT",
    ]
    assert payload["evidence_dir"] == str(evidence_dir / run_id)


def test_first_battle_failure_writes_journey_failure_with_evidence(
    tmp_path: pytest.FixtureRequest,
) -> None:
    from sts2_autotest.cli.main import _run_journey_foreground

    run_id = "test-failure-1"
    evidence_dir = tmp_path / "evidence"
    import os

    os.environ["STS2_AUTOTEST_EVIDENCE_DIR"] = str(evidence_dir)

    rc = _run_journey_foreground(
        _StuckAdapter(),
        journey="first_battle",
        character_id="IRONCLAD",
        timeout=0.3,
        run_id=run_id,
    )

    assert rc == 1
    # run-result.json：失败状态 + 真实耗时 + 失败留证
    result_path = evidence_dir / run_id / "reports" / "run-result.json"
    assert result_path.is_file()
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] in ("FAILED_PRODUCT", "FAILED_PLATFORM")
    assert payload["duration_ms"] > 0
    assert payload["failure"]["stuck_screen"] == "EVENT"
    assert payload["failure"]["last_action"] == "choose_event_option"
    assert payload["failure"]["status_trajectory"] == [
        "MAIN_MENU",
        "CHARACTER_SELECT",
        "EVENT",
    ]
    assert payload["failure"]["last_state"]["screen"] == "EVENT"

    # journey-failure.json：独立落盘的失败留证
    failure_path = evidence_dir / run_id / "reports" / "journey-failure.json"
    assert failure_path.is_file()
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["journey"] == "first_battle"
    assert failure["stuck_screen"] == "EVENT"
    assert failure["last_action"] == "choose_event_option"
    assert "reason" in failure
    assert failure["status_trajectory"] == [
        "MAIN_MENU",
        "CHARACTER_SELECT",
        "EVENT",
    ]
