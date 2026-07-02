"""自适应屏幕导航 — 根据游戏状态自动选择推进动作。

从 Gawain 项目级 navigation.py 下沉到框架层，
保留所有已验证的边缘场景处理：

- 锁定选项跳过（`_first_unlocked_option`）
- 确认弹窗自动忽略（`confirm_modal`）
- 卡牌奖励 / 三选一 / 格栅选自动跳过
- 剧情对话自动推进（`advance_dialogue`）
- 地图节点自动选择
- 旧局残留自动清理（`abandon_run`）
- 奖励收取（`collect_rewards_and_proceed`）
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any

ActionSpec = tuple[str, dict[str, Any]]
ActionCallback = Callable[[str, dict[str, Any]], Coroutine[Any, Any, Any]]
StateGetter = Callable[[], Coroutine[Any, Any, dict[str, Any]]]


class NavigationBlocked(Exception):
    """导航超时或卡死。"""


def _first_unlocked_option(options: list[dict[str, Any]]) -> int:
    """跳过锁定选项，返回第一个可用的选项索引。"""
    for fallback, option in enumerate(options):
        if not isinstance(option, dict):
            continue
        if option.get("is_locked"):
            continue
        index = option.get("index", fallback)
        return index if isinstance(index, int) else fallback
    return 0


def _first_card_id(state: dict[str, Any]) -> str | None:
    """从游戏状态中提取首张卡牌 ID。"""
    selection = state.get("selection") or {}
    cards = selection.get("cards") or []
    for card in cards:
        if isinstance(card, dict) and card.get("card_id"):
            return str(card["card_id"])
    return None


def _first_card_index(state: dict[str, Any]) -> int | None:
    """从游戏状态中提取首张卡牌索引。"""
    selection = state.get("selection") or {}
    cards = selection.get("cards") or []
    for fallback, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        index = card.get("index", fallback)
        if isinstance(index, int):
            return index
    return None


def choose_progress_action(state: dict[str, Any]) -> ActionSpec | None:
    """读取游戏状态，返回应该执行的（动作名, 参数）。

    覆盖场景（按优先级）：
      主菜单 → 选择角色 / 放弃旧局
      任意弹窗 → confirm_modal
      奖励界面 → collect_rewards_and_proceed
      三选一 / 格栅选 → skip
      手牌确认 → confirm
      选卡 → tri_select / grid_select / deck_select / hand_select
      事件选项 → choose_event_option（跳过锁定项）
      对话 → advance_dialogue
      地图 → choose_map_node
    """
    actions = state.get("available_actions") or []
    screen = str(state.get("screen") or "").upper()

    if screen == "MAIN_MENU":
        if "open_character_select" in actions:
            return "open_character_select", {}
        if "abandon_run" in actions:
            return "abandon_run", {}

    if "confirm_modal" in actions:
        return "confirm_modal", {}

    if screen == "MAP" and "discard_potion" in actions:
        return "discard_potion", {"option_index": 0}

    if "collect_rewards_and_proceed" in actions or state.get("reward") is not None or screen == "CARD_REWARD":
        return "collect_rewards_and_proceed", {}

    if "tri_select_skip" in actions:
        return "tri_select_skip", {}

    if "grid_select_skip" in actions:
        return "grid_select_skip", {}

    if "hand_confirm_selection" in actions:
        return "hand_confirm_selection", {}

    card_id = _first_card_id(state)
    card_index = _first_card_index(state)
    if "tri_select_card" in actions and card_id:
        return "tri_select_card", {"card_id": card_id}
    if "grid_select_card" in actions and card_id:
        return "grid_select_card", {"card_id": card_id}
    if "select_deck_card" in actions and card_index is not None:
        return "select_deck_card", {"option_index": card_index}
    if "hand_select_card" in actions and card_id:
        return "hand_select_card", {"card_id": card_id}

    event = state.get("event") or {}
    options = event.get("options") or []
    if "choose_event_option" in actions and isinstance(options, list):
        return "choose_event_option", {"option_index": _first_unlocked_option(options)}

    if "advance_dialogue" in actions:
        return "advance_dialogue", {}

    if screen == "MAP":
        map_block = state.get("map") or {}
        if map_block.get("local_vote") and not actions:
            return None

    if screen == "MAP" and "choose_map_node" in actions:
        return "choose_map_node", {"option_index": 0}

    return None


async def progress_until(
    get_state: StateGetter,
    act: ActionCallback,
    target_screen: str,
    *,
    timeout: float = 40.0,
    delay: float = 0.5,
) -> dict[str, Any]:
    """自适应推进到目标屏幕。

    轮询游戏状态 → 自动选择合适动作 → 执行 → 直到抵达 target_screen。
    覆盖弹窗、奖励、选卡、事件选项、对话等所有中间状态。

    返回抵达目标屏幕时的游戏状态。
    超时抛出 NavigationBlocked。
    """
    deadline = time.time() + timeout
    last_state: dict[str, Any] | None = None
    wanted = target_screen.upper()

    while time.time() < deadline:
        state = await get_state()
        last_state = state
        if str(state.get("screen") or "").upper() == wanted:
            return state

        spec = choose_progress_action(state)
        if spec is None:
            await asyncio.sleep(delay)
            continue

        action_name, params = spec
        await act(action_name, params)
        await asyncio.sleep(delay)

    screen = (last_state or {}).get("screen")
    raise NavigationBlocked(f"Waiting for {target_screen} timed out, last screen: {screen}")
