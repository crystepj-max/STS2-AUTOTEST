"""自适应屏幕导航。"""

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
        if isinstance(index, int):
            return index
        return fallback
    return 0


def _first_card_id(state: dict[str, Any]) -> str | None:
    """从游戏状态中提取首张卡牌 ID。"""
    selection = state.get("selection") or {}
    cards = selection.get("cards") or []
    for card in cards:
        if not isinstance(card, dict):
            continue
        card_id = card.get("card_id")
        if isinstance(card_id, str) and card_id:
            return card_id
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
        return fallback
    return None


def _preferred_deck_card_index(state: dict[str, Any]) -> int | None:
    """返回通用的默认选牌位置。

    具体项目对“保留哪些牌”的业务偏好必须由项目用例显式提供，
    通用平台只负责选择一个当前可用的位置，避免把某个 Mod 的牌组规则
    固化到所有项目的公共导航中。
    """
    return _first_card_index(state)


def _targeted_map_action(
    actions: list[str],
    target_screen: str | None,
    map_block: dict[str, Any],
) -> ActionSpec | None:
    """当调用方已明确目标场景时，优先按节点类型导航。

    支持 COMBAT / REST / CHEST 三类目标。仅当目标类型节点当前一步可旅行时才返回
    对应动作；否则返回 None，由上层（驱动）通过战斗推进后再重试。
    """
    if target_screen not in ("COMBAT", "REST", "CHEST"):
        return None

    if target_screen == "COMBAT":
        type_for_target: tuple[str, ...] = ("MONSTER",)
    elif target_screen == "REST":
        type_for_target = ("REST", "REST_SITE")
    else:  # CHEST
        type_for_target = ("CHEST", "TREASURE")

    def _matches(node: dict[str, Any]) -> bool:
        nt = str(node.get("node_type", "")).upper()
        return any(t in nt for t in type_for_target)

    candidates = [
        node
        for node in list(map_block.get("available_nodes") or [])
        if isinstance(node, dict) and _matches(node)
    ]
    candidates.extend(
        node
        for node in list(map_block.get("nodes") or [])
        if isinstance(node, dict)
        and str(node.get("state", "")).upper() == "TRAVELABLE"
        and _matches(node)
    )
    if candidates and "choose_map_node_by_type" in actions:
        return "choose_map_node_by_type", {"node_type": candidates[0].get("node_type")}
    return None


def _detect_card_reward_no_progress(
    prev_screen: str, action_name: str, action_status: str, new_screen: str
) -> bool:
    """战后卡牌选择专项规则检查：动作返回成功但页面未离开。

    当 `CARD_REWARD` 页面上的动作返回 success、但重新读取后屏幕仍是 `CARD_REWARD`
    时，说明出现了“假成功”卡死信号，必须上报为失败，而不能静默视为通过。
    这是 Handoff 2A“禁止降级”规则要求的失败判定之一。
    """
    return (
        prev_screen == "CARD_REWARD"
        and new_screen == "CARD_REWARD"
        and action_status == "success"
    )


def choose_progress_action(
    state: dict[str, Any],
    target_screen: str | None = None,
    *,
    deck_card_cursor: int = 0,
) -> ActionSpec | None:
    """读取游戏状态，返回下一步应执行的导航动作。

    本函数保持纯函数（无模块级可变状态），因此可独立、乱序、整组调用而不互相影响。
    牌堆选牌场景下，本游戏版本的 ``selection.selected_count`` 快照恒为 0（不可靠），
    无法据此判断已选满几张；逐张选不同索引的“游标”由有状态驱动 ``progress_until`` 维护，
    并通过 ``deck_card_cursor`` 显式传入，保证每次返回不同索引而不重复同一张。
    """
    actions = list(state.get("available_actions") or [])
    screen = str(state.get("screen") or "").upper()
    wanted = target_screen.upper() if isinstance(target_screen, str) else None

    if screen == "MAIN_MENU":
        if "open_character_select" in actions:
            return "open_character_select", {}
        if "abandon_run" in actions:
            return "abandon_run", {}

    if screen == "MAP" and "discard_potion" in actions:
        return "discard_potion", {"option_index": 0}

    if screen == "CARD_REWARD":
        # 战后卡牌/奖励选择页。STS2-Agent 真实屏幕名为 CARD_SELECTION（deck_card_select
        # 选牌子界面）或 REWARD（奖励主界面），均已映射到 CARD_REWARD。
        # 回归默认不验证奖励：优先“跳过”；若当前页面不提供跳过动作，则按页面要求做最小
        # 选择后继续（见 Handoff 2A “禁止降级”规则）。
        # 1) 牌库选牌子界面：仅暴露 select_deck_card，无独立跳过动作 → 逐张选不同卡直到离开。
        #    注意：本游戏版本的 selection.selected_count 快照不可靠（恒为 0），不能据此选同一张，
        #    否则会反复「选中/取消」同一张而卡死。改用显式传入的 deck_card_cursor 参数
    #    （无模块级可变状态）保证每次选不同索引，避免测试/调用间共享选牌进度。
        if "select_deck_card" in actions:
            sel = state.get("selection") or {}
            cards = sel.get("cards") or []
            if not cards:
                return "select_deck_card", {"option_index": 0}
            n = len(cards)
            # 已尝试全部不同卡仍卡在 CARD_REWARD：尝试确认/继续，否则交回 None 触发失败判定。
            # deck_card_cursor 由调用方（progress_until）维护，保证逐张选不同索引而不重复同一张。
            if deck_card_cursor >= n:
                if "confirm_modal" in actions:
                    return "confirm_modal", {}
                if "proceed" in actions:
                    return "proceed", {}
                return None
            ci = deck_card_cursor % n
            idx = cards[ci].get("index", ci)
            return "select_deck_card", {"option_index": idx}
        # 2) 卡牌奖励子界面：优先跳过
        if "skip_reward_cards" in actions:
            return "skip_reward_cards", {}
        if "reward_skip_card" in actions:
            return "reward_skip_card", {"type": "card"}
        # 3) 奖励主界面：无人值守推进（收取并离开到地图）
        if "collect_rewards_and_proceed" in actions:
            return "collect_rewards_and_proceed", {}
        # 4) 奖励主界面选择卡选项
        if "choose_reward_card" in actions:
            opts = (state.get("reward") or {}).get("card_options") or []
            idx = opts[0].get("index", 0) if opts else 0
            return "choose_reward_card", {"option_index": idx}
        if "reward_choose_card" in actions:
            return "reward_choose_card", {"type": "card"}
        # 5) 完成必要领取后离开
        if "claim_reward" in actions and "proceed" in actions:
            return "claim_reward", {"option_index": 0}
        if "proceed" in actions:
            return "proceed", {}
        return None

    if "tri_select_skip" in actions:
        return "tri_select_skip", {}
    if "grid_select_skip" in actions:
        return "grid_select_skip", {}
    if "hand_confirm_selection" in actions:
        return "hand_confirm_selection", {}

    card_id = _first_card_id(state)
    card_index = _preferred_deck_card_index(state)
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

    # 卡包选择（Neow 开场多阶段）：选包 → 确认
    if "choose_bundle" in actions:
        return "choose_bundle", {"option_index": 0}
    if "confirm_bundle" in actions:
        return "confirm_bundle", {}

    if screen == "CHEST":
        for ca in ("open_chest", "choose_treasure_relic", "pick_relic", "proceed"):
            if ca in actions:
                return ca, {}
        return None

    if screen == "REST":
        # 营火：先执行一个休息选项（升级/休整/锻造），卡牌选择由上层自动处理
        for ra in ("choose_rest_option", "rest", "smith", "tome", "recuperate"):
            if ra in actions:
                return ra, ({"option_index": 0} if ra == "choose_rest_option" else {})
        if "proceed" in actions:
            return "proceed", {}
        return None

    if screen in {"SHOP", "RELIC_REWARD", "BOSS_REWARD"}:
        if "proceed" in actions:
            return "proceed", {}

    if screen == "MAP":
        map_block = state.get("map") or {}
        if map_block.get("is_traveling"):
            return None
        if map_block.get("local_vote") and not actions:
            return None

        targeted = _targeted_map_action(actions, wanted, map_block)
        if targeted is not None:
            return targeted

    if screen == "MAP" and "choose_map_node" in actions:
        return "choose_map_node", {"option_index": 0}

    if "confirm_modal" in actions:
        return "confirm_modal", {}

    return None


async def progress_until(
    get_state: StateGetter,
    act: ActionCallback,
    target_screen: str,
    *,
    timeout: float = 40.0,
    delay: float = 0.5,
    arrival_predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """自适应推进到目标屏幕，并可额外验证目标状态已经稳定。"""
    deadline = time.time() + timeout
    last_state: dict[str, Any] | None = None
    wanted = target_screen.upper()
    stuck_count = 0
    # 牌堆选牌游标：本游戏版本 selection.selected_count 快照恒为 0（不可靠），
    # 不能据此判断是否已选满。游标由本有状态驱动维护，每次 select_deck_card 后 +1，
    # 保证逐张选不同索引；离开 CARD_REWARD 即归零，避免影响其它屏幕或下一轮。
    deck_cursor = 0
    # 多选取进展跟踪：用于区分“逐张选不同卡（正常进展）”与“同一张反复选（假成功卡死）”。
    last_select_index: int | None = None
    selects_done = 0
    last_action_success = False

    while time.time() < deadline:
        state = await get_state()
        last_state = state
        if (
            str(state.get("screen") or "").upper() == wanted
            and (arrival_predicate is None or arrival_predicate(state))
        ):
            return state

        # 离开卡牌选择页时重置选牌游标与进度跟踪（避免影响其它屏幕或下一轮选择）。
        if str(state.get("screen") or "").upper() != "CARD_REWARD":
            deck_cursor = 0
            last_select_index = None
            selects_done = 0

        spec = choose_progress_action(state, target_screen=wanted, deck_card_cursor=deck_cursor)
        if spec is None:
            # 没有可执行动作：若仍卡在 CARD_REWARD、且已经成功选过牌却没离开 → 卡死。
            if (
                str(state.get("screen") or "").upper() == "CARD_REWARD"
                and selects_done > 0
                and last_action_success
            ):
                raise NavigationBlocked(
                    f"战后卡牌选择未推进：已选满不同卡但仍在 {wanted}（疑似卡死）"
                )
            await asyncio.sleep(delay)
            continue

        action_name, params = spec
        prev_screen = str(state.get("screen") or "").upper()
        result = await act(action_name, params)
        action_status = "success"
        if result is not None:
            action_status = getattr(result, "status", "success") or "success"
        success = action_status == "success"
        last_action_success = success

        # 仅当本次确实选了一张牌才推进游标（保证下一轮选不同索引）。
        if action_name == "select_deck_card":
            deck_cursor += 1
            selects_done += 1

        await asyncio.sleep(delay)

        new_state = await get_state()
        new_screen = str(new_state.get("screen") or "").upper()

        # 专项规则检查：动作返回成功但页面未离开 CARD_REWARD → 卡死信号。
        # - 选牌动作：只有“同一张反复选（toggle）”才算卡死；选不同张是正常多选取进展。
        # - 非选牌动作（如 proceed/confirm）：成功却未离页即异常。
        if success and new_screen == "CARD_REWARD" and prev_screen == "CARD_REWARD":
            if action_name == "select_deck_card":
                idx = params.get("option_index")
                if idx == last_select_index:
                    stuck_count += 1
                else:
                    stuck_count = 0
                last_select_index = idx
            elif _detect_card_reward_no_progress(prev_screen, action_name, action_status, new_screen):
                stuck_count += 1
            else:
                stuck_count = 0
        else:
            stuck_count = 0

        if stuck_count >= 2:
            raise NavigationBlocked(
                f"战后卡牌选择未推进：动作 {action_name!r} 返回成功但仍在 {prev_screen}"
                f"（疑似卡死，已连续 {stuck_count} 次）"
            )

        last_state = new_state
        if (
            new_screen == wanted
            and (arrival_predicate is None or arrival_predicate(new_state))
        ):
            return new_state

    screen = str((last_state or {}).get("screen") or "")
    raise NavigationBlocked(f"Waiting for {target_screen} timed out, last screen: {screen}")
