"""战后卡牌选择页（CARD_SELECTION / REWARD）专项规则检查。

对应 Handoff 2A 任务 A 第 5 条要求：新增专项规则检查，至少覆盖
“可跳过 / 必须选择 / 选择后继续 / 动作返回成功但页面未离开的失败判定”。

设计约束（评审修订）：
- ``choose_progress_action`` 必须是纯函数，不得持有模块级可变状态，
  否则不同测试/多次调用会互相污染选择进度。
- 牌堆选牌的“逐张选不同索引”游标由有状态驱动 ``progress_until`` 维护，
  并通过 ``deck_card_cursor`` 参数显式传入纯函数，保证顺序无关、可重复。

所有动作名与参数格式均依据 STS2-Agent api.md（协议 2026-03-11）与实时游戏
（v0.107.1 / agent v0.7.2，真实屏幕名 CARD_SELECTION）确认，未做任何臆测。
"""

from __future__ import annotations

import asyncio

import pytest

from sts2_autotest.core.navigation import (
    NavigationBlocked,
    _detect_card_reward_no_progress,
    choose_progress_action,
    progress_until,
)


class _FakeResult:
    """模拟 Adapter ActionResult，仅暴露 status 字段。"""

    def __init__(self, status: str = "success") -> None:
        self.status = status


# ── 1) 可跳过：奖励子界面优先返回跳过动作 ─────────────────────


def test_skippable_prefers_skip_reward_cards() -> None:
    """CARD_REWARD 卡牌奖励子界面暴露 skip_reward_cards 时，应优先跳过。"""
    state = {
        "screen": "CARD_REWARD",
        "available_actions": ["choose_reward_card", "skip_reward_cards"],
        "reward": {"pending_card_choice": True, "card_options": [{"index": 0}]},
    }
    action, params = choose_progress_action(state)
    assert action == "skip_reward_cards"
    assert params == {}


def test_skippable_cli_reward_skip_card() -> None:
    """CliMod 路径下卡牌奖励子界面暴露 reward_skip_card 时，应返回该跳过动作。"""
    state = {
        "screen": "CARD_REWARD",
        "available_actions": ["reward_choose_card", "reward_skip_card"],
    }
    action, params = choose_progress_action(state)
    assert action == "reward_skip_card"
    assert params.get("type") == "card"


# ── 2) 必须选择：纯函数 + 显式游标，顺序无关 ────────────────


def test_must_select_returns_select_deck_card() -> None:
    """CARD_SELECTION（deck_card_select）子界面仅暴露 select_deck_card，必须选择。

    纯函数调用（deck_card_cursor=0）：固定返回首张卡的索引，结果与执行顺序无关。
    """
    state = {
        "screen": "CARD_REWARD",
        "available_actions": ["select_deck_card"],
        "selection": {
            "kind": "deck_card_select",
            "cards": [{"index": 3, "card_id": "GAWAINMOD-X"}],
            "selected_count": 0,
        },
    }
    action, params = choose_progress_action(state, deck_card_cursor=0)
    assert action == "select_deck_card"
    assert params == {"option_index": 3}


def test_select_deck_card_uses_cursor_for_distinct_index() -> None:
    """逐张选不同卡：游标=1 时返回第二张卡索引，由显式参数决定，不依赖模块状态。

    取代原先依赖“上一条测试副作用把全局游标推进到 1”的写法——那是顺序耦合根因。
    """
    state = {
        "screen": "CARD_REWARD",
        "available_actions": ["select_deck_card"],
        "selection": {
            "kind": "deck_card_select",
            "cards": [
                {"index": 0, "card_id": "GAWAINMOD-A"},
                {"index": 1, "card_id": "GAWAINMOD-B"},
            ],
            "selected_count": 1,
        },
    }
    action, params = choose_progress_action(state, deck_card_cursor=1)
    assert action == "select_deck_card"
    assert params == {"option_index": 1}


def test_select_deck_card_cursor_exhausts_then_proceed_or_none() -> None:
    """游标耗尽（>= 卡数）时应尝试 confirm/proceed；都没有则返回 None 触发失败判定。"""
    # 有 proceed 可用 → 返回 proceed
    state_proceed = {
        "screen": "CARD_REWARD",
        "available_actions": ["select_deck_card", "proceed"],
        "selection": {
            "kind": "deck_card_select",
            "cards": [{"index": 0}, {"index": 1}],
            "selected_count": 2,
        },
    }
    assert choose_progress_action(state_proceed, deck_card_cursor=2) == ("proceed", {})

    # 无 confirm/proceed → 返回 None（交由 progress_until 走卡死/超时判定）
    state_none = {
        "screen": "CARD_REWARD",
        "available_actions": ["select_deck_card"],
        "selection": {
            "kind": "deck_card_select",
            "cards": [{"index": 0}, {"index": 1}],
            "selected_count": 2,
        },
    }
    assert choose_progress_action(state_none, deck_card_cursor=2) is None


# ── 3) 选择后继续：progress_until 选不同索引后离开 CARD_REWARD ──


def _make_deck_get(issued: list[int], n_cards: int = 3) -> "callable":
    """构造一个 get_state：前 n_cards 次 select 仍停留 CARD_REWARD，之后到达 MAP。"""

    class _Get:
        def __init__(self) -> None:
            self.n = 0

        async def __call__(self) -> dict:
            self.n += 1
            if len(issued) < n_cards:
                return {
                    "screen": "CARD_REWARD",
                    "available_actions": ["select_deck_card"],
                    "selection": {
                        "kind": "deck_card_select",
                        "cards": [{"index": i} for i in range(n_cards)],
                        "selected_count": 0,
                    },
                }
            return {"screen": "MAP", "available_actions": []}

    return _Get()


async def _collect_act(issued: list[int], action: str, params: dict) -> _FakeResult:
    if action == "select_deck_card":
        issued.append(params["option_index"])
    return _FakeResult("success")


def test_progress_until_selects_distinct_indices_then_leaves() -> None:
    """progress_until 应逐张选不同索引（0,1,2），选满后离开 CARD_REWARD 到达 MAP。"""
    issued: list[int] = []
    result = asyncio.run(
        progress_until(_make_deck_get(issued), lambda a, p: _collect_act(issued, a, p),
                       "MAP", timeout=5.0, delay=0.0)
    )
    assert result["screen"] == "MAP"
    # 关键断言：三张不同索引各被选一次，证明没有反复选同一张导致卡死。
    assert sorted(issued) == [0, 1, 2]


def test_continue_after_select_reaches_map() -> None:
    """选择动作后屏幕离开 CARD_REWARD 到达 MAP，progress_until 应成功返回（单卡场景）。"""

    class _Get:
        def __init__(self) -> None:
            self.n = 0

        async def __call__(self) -> dict:
            self.n += 1
            if self.n <= 1:
                return {
                    "screen": "CARD_REWARD",
                    "available_actions": ["select_deck_card"],
                    "selection": {
                        "kind": "deck_card_select",
                        "cards": [{"index": 0}],
                        "selected_count": 0,
                    },
                }
            return {"screen": "MAP", "available_actions": []}

    async def _act(action: str, params: dict) -> _FakeResult:
        return _FakeResult("success")

    result = asyncio.run(progress_until(_Get(), _act, "MAP", timeout=5.0, delay=0.0))
    assert result["screen"] == "MAP"


def test_progress_until_repeatable_across_calls() -> None:
    """同一逻辑连续两次 progress_until 都应成功到达 MAP。

    证明游标为每次调用局部状态，不存在跨调用的模块级污染（评审要求的“可重复运行”）。
    """
    for _ in range(2):
        issued: list[int] = []

        async def _act(action: str, params: dict) -> _FakeResult:
            if action == "select_deck_card":
                issued.append(params["option_index"])
            return _FakeResult("success")

        result = asyncio.run(
            progress_until(_make_deck_get(issued), _act, "MAP", timeout=5.0, delay=0.0)
        )
        assert result["screen"] == "MAP"
        assert sorted(issued) == [0, 1, 2]


# ── 4) 动作返回成功但页面未离开 → 失败判定 ───────────────────


def test_action_success_but_page_stuck_raises() -> None:
    """动作返回 success 但 CARD_REWARD 页面未离开，应判定为卡死并抛出 NavigationBlocked。

    用 3 张卡避免游标立刻耗尽，使连续两次 select_deck_card 成功却未离开能被卡死判定捕获。
    """

    async def _get() -> dict:
        return {
            "screen": "CARD_REWARD",
            "available_actions": ["select_deck_card"],
            "selection": {
                "kind": "deck_card_select",
                "cards": [{"index": 0}, {"index": 1}, {"index": 2}],
                "selected_count": 0,
            },
        }

    async def _act(action: str, params: dict) -> _FakeResult:
        # 模拟“假成功”：返回 success，但屏幕没有变化
        return _FakeResult("success")

    with pytest.raises(NavigationBlocked):
        asyncio.run(progress_until(_get, _act, "MAP", timeout=5.0, delay=0.0))


def test_action_failure_does_not_trigger_stuck_guard() -> None:
    """动作返回 failure 时不应被误判为“成功但未离开”，而是走正常超时路径。"""

    async def _get() -> dict:
        return {
            "screen": "CARD_REWARD",
            "available_actions": ["select_deck_card"],
            "selection": {
                "kind": "deck_card_select",
                "cards": [{"index": 0}],
                "selected_count": 0,
            },
        }

    async def _act(action: str, params: dict) -> _FakeResult:
        return _FakeResult("failure")

    # 返回 failure 不会触发“连续两次成功但未离开”的卡死判定，最终因超时抛错
    with pytest.raises(NavigationBlocked):
        asyncio.run(progress_until(_get, _act, "MAP", timeout=1.5, delay=0.0))


# ── 辅助：_detect_card_reward_no_progress 纯函数判定 ──────────


def test_detect_no_progress_helper() -> None:
    """_detect_card_reward_no_progress 仅在 CARD_REWARD→CARD_REWARD 且 success 时为 True。"""
    assert _detect_card_reward_no_progress("CARD_REWARD", "select_deck_card", "success", "CARD_REWARD") is True
    assert _detect_card_reward_no_progress("CARD_REWARD", "select_deck_card", "success", "MAP") is False
    assert _detect_card_reward_no_progress("CARD_REWARD", "select_deck_card", "failure", "CARD_REWARD") is False
    assert _detect_card_reward_no_progress("MAP", "proceed", "success", "MAP") is False
