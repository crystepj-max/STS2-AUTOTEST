"""自适应屏幕导航。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

ActionSpec = tuple[str, dict[str, Any]]
ActionCallback = Callable[[str, dict[str, Any]], Coroutine[Any, Any, Any]]
StateGetter = Callable[[], Coroutine[Any, Any, dict[str, Any]]]


class NavigationBlocked(Exception):
    """导航超时或卡死。

    携带最后观察到的游戏状态与最近一次尝试的动作，便于上层把它转换为
    带“卡在哪个页面 / 最后执行了什么”的失败凭证。
    """

    def __init__(
        self,
        message: str,
        *,
        last_state: dict[str, Any] | None = None,
        last_action: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.last_state = last_state
        self.last_action = last_action
        self.reason_code = reason_code


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
    candidates.sort(key=_map_node_sort_key)
    if candidates and "choose_map_node_by_type" in actions:
        return "choose_map_node_by_type", {"node_type": _map_node_type(candidates[0])}
    return None


def _map_node_type(node: dict[str, Any]) -> str:
    return str(node.get("node_type") or node.get("type") or "UNKNOWN")


def _map_node_sort_key(node: dict[str, Any]) -> tuple[int, int, int]:
    """以实际横向坐标为第一排序依据，列表顺序只作为最后的稳定兜底。"""
    col = node.get("col", node.get("x", node.get("column", 10**9)))
    row = node.get("row", node.get("y", node.get("floor", 10**9)))
    index = node.get("index", 10**9)
    return (
        col if isinstance(col, int) else 10**9,
        row if isinstance(row, int) else 10**9,
        index if isinstance(index, int) else 10**9,
    )


def _available_map_nodes(map_block: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [
        node for node in list(map_block.get("available_nodes") or [])
        if isinstance(node, dict)
    ]
    if nodes:
        return nodes

    all_nodes = [
        node for node in list(map_block.get("nodes") or [])
        if isinstance(node, dict)
        and str(node.get("state") or "").upper() in {"TRAVELABLE", "AVAILABLE", "CURRENT"}
    ]
    if all_nodes:
        return all_nodes

    travelable = map_block.get("travelable_coords") or []
    by_coord = {
        (node.get("col"), node.get("row")): node
        for node in list(map_block.get("nodes") or [])
        if isinstance(node, dict)
    }
    result: list[dict[str, Any]] = []
    for coord in travelable:
        if not isinstance(coord, dict):
            continue
        node = by_coord.get((coord.get("col"), coord.get("row")))
        if node is not None:
            result.append(node)
        else:
            result.append(dict(coord))
    return result


def _leftmost_map_action(actions: list[str], map_block: dict[str, Any]) -> ActionSpec | None:
    nodes = sorted(_available_map_nodes(map_block), key=_map_node_sort_key)
    if not nodes or "choose_map_node" not in actions:
        return None
    node = nodes[0]
    if isinstance(node.get("index"), int):
        return "choose_map_node", {"option_index": node["index"]}
    if len(nodes) == 1:
        return "choose_map_node", {"option_index": 0}
    col = node.get("col", node.get("x"))
    row = node.get("row", node.get("y"))
    if isinstance(col, int) and isinstance(row, int):
        return "choose_map_node", {"col": col, "row": row}
    return "choose_map_node", {"option_index": 0}


def _first_live_enemy(combat: dict[str, Any]) -> int | None:
    for enemy in list(combat.get("enemies") or []):
        if not isinstance(enemy, dict) or enemy.get("is_alive") is False:
            continue
        for key in ("combat_id", "index", "id"):
            value = enemy.get(key)
            if isinstance(value, int):
                return value
    return None


def _basic_combat_action(state: dict[str, Any], actions: list[str]) -> ActionSpec | None:
    combat = state.get("combat") or {}
    hand = combat.get("hand") or []
    if "play_card" in actions and isinstance(hand, list):
        for card in hand:
            if (
                not isinstance(card, dict)
                or card.get("can_play") is False
                or card.get("playable") is False
                or card.get("is_playable") is False
            ):
                continue
            card_id = card.get("id") or card.get("card_id")
            if not isinstance(card_id, str) or not card_id:
                continue
            params: dict[str, Any] = {"card_id": card_id}
            requires_target = card.get("requires_target")
            target_type = str(card.get("target_type") or "").upper()
            if requires_target is True or target_type in {"ANYENEMY", "ENEMY"}:
                target = _first_live_enemy(combat)
                if target is not None:
                    params["target"] = target
                else:
                    continue
            return "play_card", params
    if "end_turn" in actions:
        return "end_turn", {}
    return None


def _reward_card_action(state: dict[str, Any], actions: list[str]) -> ActionSpec | None:
    reward = state.get("reward") or state.get("rewards") or {}
    if not isinstance(reward, dict):
        reward = {}
    options = reward.get("card_options") or reward.get("cards") or []
    if "reward_choose_card" in actions and isinstance(options, list) and options:
        ranks = {"COMMON": 0, "UNCOMMON": 1, "RARE": 2, "SPECIAL": 3}
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for position, option in enumerate(options):
            if not isinstance(option, dict):
                continue
            rarity = str(option.get("rarity") or option.get("tier") or "").upper()
            ranked.append((ranks.get(rarity, -1), position, option))
        if ranked:
            rarity_rank, position, option = max(ranked, key=lambda item: (item[0], item[1]))
            del rarity_rank
            return "reward_choose_card", {
                "option_index": option.get("index", position),
            }
    raw_rewards = reward.get("rewards") or reward.get("items") or []
    if isinstance(raw_rewards, dict):
        raw_rewards = raw_rewards.get("rewards") or []
    if "reward_choose_card" in actions and isinstance(raw_rewards, list):
        for reward_position, item in enumerate(raw_rewards):
            if not isinstance(item, dict):
                continue
            reward_type = str(item.get("type") or item.get("reward_type") or "").upper()
            if reward_type != "CARD":
                continue
            choices = item.get("card_choices") or item.get("cards") or []
            if not isinstance(choices, list) or not choices:
                continue
            ranks = {"COMMON": 0, "UNCOMMON": 1, "RARE": 2, "SPECIAL": 3}
            nested_ranked: list[tuple[int, int, dict[str, Any]]] = []
            for position, option in enumerate(choices):
                if not isinstance(option, dict):
                    continue
                rarity = str(option.get("rarity") or option.get("tier") or "").upper()
                nested_ranked.append((ranks.get(rarity, -1), position, option))
            if not nested_ranked:
                continue
            _, position, option = max(nested_ranked, key=lambda entry: (entry[0], entry[1]))
            params: dict[str, Any] = {
                "type": "card",
                "nth": item.get("index", reward_position),
            }
            card_id = option.get("id") or option.get("card_id")
            if card_id:
                params["card_id"] = card_id
            else:
                params["option_index"] = option.get("index", position)
            return "reward_choose_card", params
    return None


def _first_reward_type(state: dict[str, Any]) -> str | None:
    reward = state.get("reward") or state.get("rewards") or {}
    if not isinstance(reward, dict):
        reward = {}
    raw = reward.get("rewards") or reward.get("items") or []
    if isinstance(raw, dict):
        raw = raw.get("rewards") or []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                value = item.get("type") or item.get("reward_type")
                if isinstance(value, str) and value:
                    return value.lower()
    for key in ("gold", "relic", "potion", "special_card"):
        if reward.get(key) not in (None, False, [], {}):
            return key
    return None


def _rest_action(state: dict[str, Any], actions: list[str]) -> ActionSpec | None:
    if "choose_rest_option" not in actions:
        for name in ("rest", "smith", "recuperate", "proceed"):
            if name in actions:
                return name, {}
        return None
    rest = state.get("rest") or {}
    options = rest.get("options") or rest.get("available_options") or []
    player = (
        (state.get("combat") or {}).get("player")
        or state.get("player")
        or state.get("run")
        or {}
    )
    current_hp = player.get("current_hp")
    max_hp = player.get("max_hp")
    low_hp = isinstance(current_hp, int) and isinstance(max_hp, int) and current_hp * 2 < max_hp
    candidates = [item for item in options if isinstance(item, dict)]
    if candidates:
        candidates = [
            item
            for item in candidates
            if item.get("is_enabled") is not False
            and item.get("enabled") is not False
            and item.get("disabled") is not True
        ]
        if not candidates:
            return None
        if low_hp:
            selected = next(
                (
                    item
                    for item in candidates
                    if str(
                        item.get("option_id") or item.get("id") or item.get("type") or ""
                    ).upper()
                    in {"REST", "HEAL", "RECOVER"}
                ),
                candidates[0],
            )
        else:
            selected = next(
                (
                    item
                    for item in candidates
                    if str(
                        item.get("option_id") or item.get("id") or item.get("type") or ""
                    ).upper()
                    in {"SMITH", "UPGRADE"}
                ),
                candidates[0],
            )
        value = selected.get("index", selected.get("option_index", 0))
        return "choose_rest_option", {"option_index": value}
    return "choose_rest_option", {"option_index": 0}


def _state_fingerprint(state: dict[str, Any]) -> str:
    """去掉读取编号后比较业务状态，避免把重复读取误判为进展。"""
    volatile = {"state_version", "request_id", "timestamp", "updated_at"}
    cleaned = {key: value for key, value in state.items() if key not in volatile}
    return json.dumps(cleaned, sort_keys=True, ensure_ascii=False, default=str)


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


def _combat_action_surface_incomplete(
    state: dict[str, Any], actions: list[str]
) -> bool:
    """检测战斗牌面已出现但控制入口未公开出牌动作。"""
    if str(state.get("screen") or "").upper() != "COMBAT":
        return False
    if "win_combat" in actions:
        return False
    if "play_card" in actions:
        return False
    combat = state.get("combat") or {}
    player = combat.get("player") or {}
    if not isinstance(player, dict) or not isinstance(player.get("energy"), int):
        return False
    if player["energy"] <= 0 or combat.get("hand"):
        return False
    agent_combat = ((state.get("agent_view") or {}).get("combat") or {})
    return isinstance(agent_combat.get("draw"), list) and bool(agent_combat["draw"])


def _is_real_combat_state(state: dict[str, Any]) -> bool:
    """只有存在真实战斗数据和战斗控制入口时，才算已进入战斗。"""
    if str(state.get("screen") or "").upper() != "COMBAT":
        return False
    if state.get("in_combat") is not True:
        return False
    combat = state.get("combat")
    if not isinstance(combat, dict):
        return False
    enemies = combat.get("enemies")
    if not isinstance(enemies, list) or not any(
        isinstance(enemy, dict) and enemy.get("is_alive") is not False
        for enemy in enemies
    ):
        return False
    actions = list(state.get("available_actions") or [])
    return any(action in actions for action in ("play_card", "end_turn", "win_combat"))


def choose_progress_action(
    state: dict[str, Any],
    target_screen: str | None = None,
    *,
    deck_card_cursor: int = 0,
    route_policy: str = "leftmost",
    combat_mode: str = "traversal",
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
        # 有可恢复存档时，主菜单的「推进」语义是继续存档（进入 MAP/局内），
        # 而非丢弃。否则 resume_run 在回到主菜单时会被导航器误选 abandon_run
        # 而 FAIL（"No saved run exists"）。abandon_run 仅作为无存档时的兜底。
        if "continue_run" in actions:
            return "continue_run", {}
        if "abandon_run" in actions:
            return "abandon_run", {}

    if screen == "MAP" and "discard_potion" in actions:
        potions = (state.get("run") or {}).get("potions") or state.get("potions") or []
        for potion in potions:
            if not isinstance(potion, dict):
                continue
            if potion.get("can_discard") is not True and potion.get("occupied") is not True:
                continue
            index = potion.get("index", potion.get("i"))
            if isinstance(index, int):
                return "discard_potion", {"option_index": index}

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
        selected_card = _reward_card_action(state, actions)
        if selected_card is not None:
            return selected_card
        # 2) 卡牌奖励子界面：无法识别候选时才跳过
        if "skip_reward_cards" in actions:
            return "skip_reward_cards", {}
        if "reward_skip_card" in actions:
            return "reward_skip_card", {"type": "card", "nth": 0}
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

    if screen == "COMBAT":
        if combat_mode == "death":
            # 死亡测试：只结束玩家回合，绝不出牌或使用快速结束，
            # 直到角色被怪物击杀（由上层把 GAME_OVER 作为成功终态）。
            if "end_turn" in actions:
                return "end_turn", {}
            return None
        if combat_mode not in {"traversal", "basic"}:
            return None
        if combat_mode == "traversal" and "win_combat" in actions:
            return "win_combat", {}
        return _basic_combat_action(state, actions)

    if "tri_select_skip" in actions and screen != "TRI_SELECT":
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

    # EVENT 屏幕：优先处理需要选择的事件选项（开局 Neow 祝福等）。
    # 兼容 cli 适配器的 choose_event(index) 与 agent 适配器的
    # choose_event_option(option_index) 两种动作名；没有可选项时（纯对话）才推进对话。
    # 整段只在 EVENT 屏幕生效——避免 MAP 等其它屏幕恰好暴露 advance_dialogue /
    # choose_bundle 时被误选，导致导航迷失（例如开局后卡在地图进不了首战）。
    if screen == "EVENT":
        event = state.get("event") or {}
        options = event.get("options") or []
        if options:
            if "choose_event" in actions:
                return "choose_event", {"index": _first_unlocked_option(options)}
            if "choose_event_option" in actions:
                return "choose_event_option", {"option_index": _first_unlocked_option(options)}
        if "choose_neow_blessing" in actions:
            return "choose_neow_blessing", {}

        if "advance_dialogue" in actions:
            return "advance_dialogue", {}


    if screen == "BUNDLE_SELECTION":
        # 卡包选择页（Scroll Boxes 遗物）：真实 CLI 动作是 bundle_select <index>
        # （预览）+ bundle_confirm（确认），二者被适配器合并为单个 bundle_select
        # 复合动作，一步预览并确认第一个卡包。旧代码用的 choose_bundle /
        # confirm_bundle 是不存在的命令，会导致该页永远卡在 UNKNOWN。
        if "bundle_select" in actions:
            return "bundle_select", {"index": 0}
        if "bundle_confirm" in actions:
            return "bundle_confirm", {}

    if screen == "TRI_SELECT":
        # 三选一卡牌事件屏：真实 CLI 动作是 tri_select_card <card_ids>
        # （选一张）/ tri_select_skip（跳过，若允许）。导航器默认选第一张卡
        # 向 MAP 推进；若无可选项则尝试跳过。旧逻辑因屏幕被映射成 UNKNOWN
        # 而卡死（available_actions 为空），现已由适配器正确映射 TRI_SELECT。
        tri = state.get("tri_select") or {}
        cards = tri.get("cards") or []
        if cards:
            first = cards[0]
            cid = first.get("card_id")
            if cid is None:
                cid = first.get("index")
            if cid is not None:
                return "tri_select_card", {"card_id": str(cid)}
        if "tri_select_skip" in actions:
            return "tri_select_skip", {}

    if screen == "CHEST":
        for ca in ("open_chest", "choose_treasure_relic", "pick_relic", "proceed"):
            if ca in actions:
                chest_params: dict[str, Any] = (
                    {"option_index": 0}
                    if ca in {"choose_treasure_relic", "pick_relic"}
                    else {}
                )
                return ca, chest_params
        return None

    if screen == "REST":
        return _rest_action(state, actions)

    if screen in {"SHOP", "RELIC_REWARD", "BOSS_REWARD"}:
        if screen in {"RELIC_REWARD", "BOSS_REWARD"}:
            for name in ("relic_select", "pick_relic", "reward_claim", "relic_skip"):
                if name in actions:
                    reward_params: dict[str, Any] = {}
                    if name in {"relic_select", "pick_relic"}:
                        reward_params["option_index"] = 0
                    elif name == "reward_claim":
                        reward_params["type"] = _first_reward_type(state) or "relic"
                    return name, reward_params
        if "proceed" in actions:
            return "proceed", {}

    if screen == "SHOP":
        for name in ("leave_shop", "shop_exit", "proceed"):
            if name in actions:
                return name, {}

    if screen == "MAP":
        map_block = state.get("map") or {}
        if map_block.get("is_traveling"):
            return None
        if map_block.get("local_vote") and not actions:
            return None

        if route_policy == "target" or wanted in {"COMBAT", "REST", "CHEST"}:
            targeted = _targeted_map_action(actions, wanted, map_block)
            if targeted is not None:
                return targeted
        leftmost = _leftmost_map_action(actions, map_block)
        if leftmost is not None:
            return leftmost

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
    route_policy: str = "leftmost",
    combat_mode: str = "traversal",
    recover: Callable[[], Awaitable[bool]] | None = None,
    no_progress_timeout: float = 10.0,
) -> dict[str, Any]:
    """自适应推进到目标屏幕，并可额外验证目标状态已经稳定。"""
    deadline = time.monotonic() + timeout
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
    last_action: str | None = None
    pseudo_combat_seen = False
    # UNKNOWN 兜底：记录首次“屏幕为 UNKNOWN 且无任何可执行动作”的时刻，
    # 一旦持续超过 no_progress_timeout 即快速失败并回传原始 screen 名，
    # 避免未映射屏幕（如新增的卡包页）让主循环空转到 deadline（曾静默卡死 10 分钟）。
    unknown_stuck_since: float | None = None

    while time.monotonic() < deadline:
        state = await get_state()
        last_state = state
        screen = str(state.get("screen") or "").upper()
        if screen == "COMBAT" and not _is_real_combat_state(state):
            pseudo_combat_seen = True
        if screen in {"GAME_OVER", "VICTORY", "CRASHED"} and screen != wanted:
            reason_code = {
                "GAME_OVER": "COMBAT_FAILED",
                "VICTORY": "RUN_COMPLETED",
                "CRASHED": "GAME_CRASHED",
            }[screen]
            raise NavigationBlocked(
                f"到达终止页面 {screen}，无法继续到达 {wanted}",
                last_state=last_state,
                last_action=last_action,
                reason_code=reason_code,
            )
        if (
            screen == wanted
            and (wanted != "COMBAT" or _is_real_combat_state(state))
            and (arrival_predicate is None or arrival_predicate(state))
        ):
            return state

        actions = list(state.get("available_actions") or [])
        if _combat_action_surface_incomplete(state, actions):
            raise NavigationBlocked(
                "战斗已出现可处理牌面，但控制入口未公开 play_card，无法执行通用出牌策略",
                last_state=last_state,
                last_action=last_action,
                reason_code="ACTION_SURFACE_INCOMPLETE",
            )

        # 离开卡牌选择页时重置选牌游标与进度跟踪（避免影响其它屏幕或下一轮选择）。
        if str(state.get("screen") or "").upper() != "CARD_REWARD":
            deck_cursor = 0
            last_select_index = None
            selects_done = 0

        spec = choose_progress_action(
            state,
            target_screen=wanted,
            deck_card_cursor=deck_cursor,
            route_policy=route_policy,
            combat_mode=combat_mode,
        )
        if spec is None:
            map_block = state.get("map") or {}
            if (
                str(state.get("screen") or "").upper() == "MAP"
                and wanted != "MAP"
                and not bool(map_block.get("is_traveling"))
                and "choose_map_node" not in list(state.get("available_actions") or [])
            ):
                raise NavigationBlocked(
                    f"目标页面 {wanted} 在当前稳定地图不可达",
                    last_state=last_state,
                    last_action=last_action,
                    reason_code="TARGET_UNREACHABLE",
                )
            # 没有可执行动作：若仍卡在 CARD_REWARD、且已经成功选过牌却没离开 → 卡死。
            if (
                str(state.get("screen") or "").upper() == "CARD_REWARD"
                and selects_done > 0
                and last_action_success
            ):
                raise NavigationBlocked(
                    f"战后卡牌选择未推进：已选满不同卡但仍在 {wanted}（疑似卡死）",
                    last_state=last_state,
                    last_action=last_action,
                    reason_code="NO_PROGRESS",
                )
            # UNKNOWN 兜底：屏幕无法识别且没有任何可执行动作时，若尝试 recover 无效，
            # 持续超过 no_progress_timeout 即快速失败，回传原始 screen 名便于补映射，
            # 绝不空转到 deadline（历史上曾因未映射的卡包页静默卡死约 10 分钟）。
            if screen == "UNKNOWN":
                now = time.monotonic()
                if unknown_stuck_since is None:
                    unknown_stuck_since = now
                    if recover is not None:
                        recovered = await recover()
                        if recovered:
                            unknown_stuck_since = None
                            await asyncio.sleep(delay)
                            continue
                elif now - unknown_stuck_since >= no_progress_timeout:
                    raw_screen = state.get("screen")
                    raise NavigationBlocked(
                        f"屏幕无法识别为已知页面（screen={raw_screen!r}）且无任何可执行动作，"
                        f"持续 {no_progress_timeout:.0f} 秒无进展；可能是未映射的新屏幕，"
                        f"需在 _SCREEN_MAP / choose_progress_action 中补充映射",
                        last_state=last_state,
                        last_action=last_action,
                        reason_code="UNKNOWN_SCREEN_STUCK",
                    )
            await asyncio.sleep(delay)
            continue
        unknown_stuck_since = None

        action_name, params = spec
        last_action = action_name
        prev_screen = str(state.get("screen") or "").upper()
        result = await act(action_name, params)
        action_status = "success"
        if result is not None:
            action_status = getattr(result, "status", "success") or "success"
        success = action_status == "success"
        if not success and action_name in ("grid_select_skip", "tri_select_skip"):
            # 不可跳过的选牌页面（变化/移除类事件）：跳过被游戏拒绝后改选第一张
            # 可用牌推进，避免整局因"无法跳过"卡死（2026-07-20 三次真实任务暴露）。
            fallback_name = (
                "grid_select_card" if action_name == "grid_select_skip" else "tri_select_card"
            )
            fallback_card = _first_card_id(state)
            if fallback_card:
                fb_result = await act(fallback_name, {"card_id": fallback_card})
                fb_status = getattr(fb_result, "status", "") if fb_result else ""
                if fb_status == "success":
                    action_name = fallback_name
                    last_action = f"{last_action}->{fallback_name}"
                    success = True
        last_action_success = success

        if not success:
            raise NavigationBlocked(
                f"动作 {action_name!r} 未执行成功：{getattr(result, 'detail', '') or action_status}",
                last_state=state,
                last_action=last_action,
                reason_code="ACTION_FAILED",
            )

        # 仅当本次确实选了一张牌才推进游标（保证下一轮选不同索引）。
        if action_name == "select_deck_card":
            deck_cursor += 1
            selects_done += 1

        await asyncio.sleep(delay)

        new_state = await get_state()
        before_fingerprint = _state_fingerprint(state)
        after_fingerprint = _state_fingerprint(new_state)
        card_selection_progress = (
            action_name == "select_deck_card"
            and params.get("option_index") != last_select_index
        )
        if before_fingerprint == after_fingerprint and not card_selection_progress:
            observed_until = time.monotonic() + min(
                no_progress_timeout,
                max(0.0, deadline - time.monotonic()),
            )
            recovered = False
            while time.monotonic() < observed_until:
                await asyncio.sleep(min(delay or 0.5, max(0.01, observed_until - time.monotonic())))
                candidate = await get_state()
                if _state_fingerprint(candidate) != before_fingerprint:
                    new_state = candidate
                    break
            else:
                if recover is not None and not recovered:
                    recovered = await recover()
                    if recovered:
                        candidate = await get_state()
                        if _state_fingerprint(candidate) != before_fingerprint:
                            new_state = candidate
                if _state_fingerprint(new_state) == before_fingerprint:
                    raise NavigationBlocked(
                        f"动作 {action_name!r} 返回成功但在 {no_progress_timeout:.0f} 秒内没有可观察变化",
                        last_state=new_state,
                        last_action=action_name,
                        reason_code="NO_PROGRESS",
                    )
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
                f"（疑似卡死，已连续 {stuck_count} 次）",
                last_state=new_state,
                last_action=action_name,
                reason_code="NO_PROGRESS",
            )

        last_state = new_state
        if (
            new_screen == wanted
            and (wanted != "COMBAT" or _is_real_combat_state(new_state))
            and (arrival_predicate is None or arrival_predicate(new_state))
        ):
            return new_state

    screen = str((last_state or {}).get("screen") or "")
    if wanted == "COMBAT" and pseudo_combat_seen:
        raise NavigationBlocked(
            "观察到 COMBAT 页面但未形成真实战斗：缺少 in_combat、敌人或战斗动作",
            last_state=last_state,
            last_action=last_action,
            reason_code="TRANSITION",
        )
    raise NavigationBlocked(
        f"Waiting for {target_screen} timed out, last screen: {screen}",
        last_state=last_state,
        last_action=last_action,
        reason_code="TIMEOUT",
    )
