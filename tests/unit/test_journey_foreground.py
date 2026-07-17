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
    in_combat: bool = False
    combat: dict | None = None

    def model_dump(self) -> dict:
        return {
            "screen": self.screen,
            "available_actions": list(self.available_actions),
            "event": self.event,
            "map": self.map,
            "in_combat": self.in_combat,
            "combat": self.combat,
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
            self.state = _State(
                "COMBAT",
                ["end_turn"],
                in_combat=True,
                combat={"enemies": [{"index": 0, "is_alive": True}]},
            )
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


# ── 截图前的状态稳定等待 ──


class _FlappingState:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self) -> dict:
        return dict(self._payload)


class _FlappingAdapter:
    """前两次读取屏幕仍停在旧页，第三次才稳定到新页。"""

    def __init__(self, sequence: list[dict]) -> None:
        self._sequence = list(sequence)
        self.reads = 0

    async def get_state(self) -> _FlappingState:
        index = min(self.reads, len(self._sequence) - 1)
        self.reads += 1
        return _FlappingState(self._sequence[index])

    async def get_available_actions(self) -> list[str]:
        return []


def test_wait_for_stable_api_state_returns_state_after_stabilizes() -> None:
    from sts2_autotest.cli.main import _wait_for_stable_api_state

    adapter = _FlappingAdapter(
        [
            {"screen": "MAP", "map": {"is_traveling": True}},
            {"screen": "MAP", "map": {"is_traveling": False}},
            {"screen": "MAP", "map": {"is_traveling": False}},
        ]
    )
    initial = {"screen": "CARD_REWARD", "reward": {"cards": [1]}}

    result = asyncio.run(
        _wait_for_stable_api_state(adapter, initial, timeout=2.0, interval=0.01, settle=0.01)
    )

    # 初始状态是旧页；第一次读取仍在旅行，第二次读取与第三次一致后才返回。
    assert result["screen"] == "MAP"
    assert result["map"]["is_traveling"] is False
    assert adapter.reads >= 3


def test_wait_for_stable_api_state_keeps_last_read_on_timeout() -> None:
    from sts2_autotest.cli.main import _wait_for_stable_api_state

    # 状态永远变化（计数器递增），等待必须在超时后用最后一次读取返回。
    adapter = _FlappingAdapter(
        [{"screen": "COMBAT", "turn": turn} for turn in range(50)]
    )
    initial = {"screen": "COMBAT", "turn": -1}

    result = asyncio.run(
        _wait_for_stable_api_state(adapter, initial, timeout=0.05, interval=0.01, settle=0.01)
    )

    assert result["screen"] == "COMBAT"
    assert result["turn"] > 0


def test_success_path_captures_final_state_screenshot(
    tmp_path: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功路径必须额外落一张 FINAL_ 前缀的终态截图。

    API 状态可能先于画面翻页（第二章 MAP 截图曾拍到事件页）；旅程结束后的
    延长 settle 截图是与最终状态一致的视觉凭证。
    """
    import sts2_autotest.core.evidence_hooks as hooks_module
    from sts2_autotest.cli.main import _run_journey_foreground

    captured: list[tuple[str, dict]] = []

    class _FakeEvidence:
        def on_case_start(self, case_id: str) -> None:
            pass

        def on_case_end(self, result) -> None:
            pass

        def on_crash(self, case_id: str, error: Exception) -> None:
            pass

        def on_session_end(self, summary: dict) -> None:
            pass

        def capture_state(self, case_id: str, state: dict) -> None:
            captured.append((case_id, state))

    monkeypatch.setattr(hooks_module, "build_evidence_hooks", lambda *a, **k: _FakeEvidence())

    import os

    os.environ["STS2_AUTOTEST_EVIDENCE_DIR"] = str(tmp_path / "evidence")

    rc = _run_journey_foreground(
        _SuccessAdapter(),
        journey="first_battle",
        character_id="IRONCLAD",
        timeout=10.0,
        run_id="test-final-shot",
    )

    assert rc == 0
    final_shots = [name for name, _state in captured if "_FINAL_" in name]
    assert len(final_shots) == 1
    assert final_shots[0].startswith("journey_first_battle_FINAL_COMBAT_")
    # 终态凭证携带的必须是最终状态本身
    final_state = next(state for name, state in captured if "_FINAL_" in name)
    assert final_state["screen"] == "COMBAT"
