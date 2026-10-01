"""适配器语义层：两个传输 adapter（CliMod / Agent）共享的策略与判定。

这里收敛的是「与传输无关、只与游戏语义有关」的实现：屏幕名归一、状态载荷
过滤、动作错误归类、版本握手与排障快照组合。两个 adapter 只保留各自的
传输细节（subprocess / HTTP、信封解析、缓存与重试）。

接缝说明：本模块位于 adapters 包内（不进 common）——当前仅两个 adapter
消费；若未来出现第三个消费者再按 common 准入规则上移。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol
from sts2_autotest.common.errors import AdapterErrorSubType, ErrorCategory, STS2Error
from sts2_autotest.common.state import GameScreen, GameState

# ---------------------------------------------------------------------------
# 屏幕名归一（历史两份映射的超集；键值在两份副本中从未冲突，漂移只发生在
# 一侧新增键而另一侧缺失——合并后不可能再漂移）
# ---------------------------------------------------------------------------

SCREEN_NAME_TO_GAME_SCREEN: dict[str, GameScreen] = {
    "MENU": GameScreen.MAIN_MENU,
    "MAIN_MENU": GameScreen.MAIN_MENU,
    # STS2-Agent 以 MODAL 上报模态弹窗（视为菜单态处理）。
    "MODAL": GameScreen.MAIN_MENU,
    "SINGLEPLAYER_SUBMENU": GameScreen.MAIN_MENU,
    "CHARACTER_SELECT": GameScreen.CHARACTER_SELECT,
    "MAP": GameScreen.MAP,
    "COMBAT": GameScreen.COMBAT,
    "SHOP": GameScreen.SHOP,
    "REST": GameScreen.REST,
    "REST_SITE": GameScreen.REST,
    "EVENT": GameScreen.EVENT,
    # GRID_CARD_SELECT：事件附带的 grid 卡牌选择（新叶祝福等），CLI 直接上报。
    "GRID_CARD_SELECT": GameScreen.EVENT,
    "TREASURE": GameScreen.CHEST,
    "CHEST": GameScreen.CHEST,
    "BUNDLE_SELECTION": GameScreen.BUNDLE_SELECTION,
    "BUNDLE_SELECT": GameScreen.BUNDLE_SELECTION,
    "CONFIRM_BUNDLE": GameScreen.BUNDLE_SELECTION,
    "BOSS_REWARD": GameScreen.BOSS_REWARD,
    # 战后奖励主界面（STS2-Agent api.md 协议 2026-03-11）、卡牌奖励选择子界面
    # （deck_card_select，真实屏幕名 CARD_SELECTION，v0.7.2+）与 CLI 的 REWARD
    # 上报统一归入 CARD_REWARD，否则会被映射成 UNKNOWN 导致导航卡死。
    "REWARD": GameScreen.CARD_REWARD,
    "CARD_REWARD": GameScreen.CARD_REWARD,
    # 防御性：新版本可能以 CARD_SELECTION 上报战后选牌子界面。
    "CARD_SELECTION": GameScreen.CARD_REWARD,
    "RELIC_REWARD": GameScreen.RELIC_REWARD,
    # 卡包选择页（Scroll Boxes 遗物触发）。
    # 三选一卡牌事件屏（tri_select_card / tri_select_skip）；不映射会让导航器
    # 看到 UNKNOWN + 空动作而空转超时。
    "TRI_SELECT": GameScreen.TRI_SELECT,
    "GAME_OVER": GameScreen.GAME_OVER,
    "VICTORY": GameScreen.VICTORY,
    "CRASHED": GameScreen.CRASHED,
}


def map_screen_name(screen_raw: str) -> GameScreen:
    """Map a transport-reported screen name to GameScreen, falling back to UNKNOWN."""
    return SCREEN_NAME_TO_GAME_SCREEN.get(screen_raw, GameScreen.UNKNOWN)


# ---------------------------------------------------------------------------
# 状态载荷过滤
# ---------------------------------------------------------------------------


def filter_state_extra(data: dict[str, Any]) -> dict[str, Any]:
    """Extract extra fields from a state response for the GameState model.

    GameState(screen=..., extra="allow") accepts arbitrary fields,
    but we skip the 'screen' key (already consumed) and 'error' key
    (not a state field).
    """
    skip_keys = {"screen", "error"}
    return {k: v for k, v in data.items() if k not in skip_keys}


# ---------------------------------------------------------------------------
# 动作错误归类
# ---------------------------------------------------------------------------


def classify_action_error(
    exc: STS2Error,
    *,
    state_changed: bool = False,
    treat_as_success: Callable[[STS2Error], bool] | None = None,
) -> ActionResult:
    """把动作执行中的 STS2Error 归类为 ActionResult（传输无关的统一策略）。

    - 命中 ``treat_as_success`` 谓词（如游戏移除 get_IsPlayPhase 的降级）时
      返回成功（动作已下发、游戏仍在推进，交由上层重读状态确认真实转移）；
    - 超时类错误（category=TIMEOUT_ERROR 或 subtype=TIMEOUT）→ ``timeout``；
    - 其余 → ``failure``。
    ``state_changed`` 原样透传，由调用方决定动作是否已产生状态转移。
    """
    if treat_as_success is not None and treat_as_success(exc):
        return ActionResult(status="success", state_changed=True, detail=exc.message)
    if (
        exc.category == ErrorCategory.TIMEOUT_ERROR
        or exc.detail.get("subtype") == AdapterErrorSubType.TIMEOUT
    ):
        return ActionResult(status="timeout", state_changed=state_changed, detail=exc.message)
    return ActionResult(status="failure", state_changed=state_changed, detail=exc.message)


# ---------------------------------------------------------------------------
# 版本握手（两传输同构：同 regex、同 major 校验，仅命令提示与升级对象不同）
# ---------------------------------------------------------------------------


def check_version_compatibility(
    version_str: str,
    *,
    supported_major: int,
    command_hint: str,
    upgrade_target: str,
) -> None:
    """Parse 'MAJOR.MINOR.PATCH' and verify the major version (FR50).

    Raises STS2Error(ADAPTER_ERROR) on parse failure or major mismatch.
    """
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version_str.strip())
    if not match:
        raise STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message=f"Cannot parse version from: {version_str!r}",
            detail={
                "subtype": AdapterErrorSubType.JSON_PARSE_FAILURE,
                "command": command_hint,
                "raw_output": version_str,
            },
        )
    major = int(match.group(1))
    if major != supported_major:
        raise STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message=(
                f"Adapter major version {major} is incompatible "
                f"(supported: {supported_major}). "
                f"Please upgrade {upgrade_target}."
            ),
            detail={
                "subtype": AdapterErrorSubType.VERSION_MISMATCH,
                "command": command_hint,
                "raw_output": version_str,
            },
        )


# ---------------------------------------------------------------------------
# 排障快照组合（get_state + get_available_actions 的统一兜底组合）
# ---------------------------------------------------------------------------


async def compose_bug_snapshot(
    adapter: GameAdapterProtocol,
    *,
    fallback_state: GameState | None = None,
) -> dict[str, Any]:
    """Compose get_state() + get_available_actions() into a snapshot dict.

    Returns a dict with keys: game_state, available_actions, timestamp.
    Falls back to UNKNOWN (or ``fallback_state``) / empty list if the adapter
    raises.
    """
    try:
        state = await adapter.get_state()
        actions = await adapter.get_available_actions()
    except STS2Error:
        state = fallback_state or GameState(screen=GameScreen.UNKNOWN)
        actions = []

    return {
        "game_state": state,
        "available_actions": actions,
        "timestamp": datetime.now(UTC),
    }


# ---------------------------------------------------------------------------
# 推进收敛判定词表：「何时需要推进、何时算收敛」的共享判定条件。
# 历史上四份推进实现（cli_mod 收敛序列 / agent _finish_interstitials /
# navigation 决策表 / journeys 回退链）各自持有屏幕集合字面量，漂移无防线。
# 收敛序列本身保留传输方言（LOC-004 决策 B），此处只单源化判定条件；
# 各实现取共享集合的子集时用集合运算表达，漂移在 review 即可见。
# ---------------------------------------------------------------------------

# 推进已收敛：到达 MAP（目标屏）或 COMBAT（首战类目标的实际终点）即无需推进。
CONVERGED_SCREENS: frozenset[GameScreen] = frozenset(
    {GameScreen.MAP, GameScreen.COMBAT}
)

# 需要推进收敛的中间屏并集：事件 / 卡牌奖励 / 三选一 / 卡包选择。
# 各传输方言按需取子集（如 agent 额外处理 RELIC_REWARD、CLI 的事件收敛
# 含 grid 卡牌屏——GRID_CARD_SELECT 归一为 EVENT）。
INTERSTITIAL_SCREENS: frozenset[GameScreen] = frozenset(
    {
        GameScreen.EVENT,
        GameScreen.CARD_REWARD,
        GameScreen.TRI_SELECT,
        GameScreen.BUNDLE_SELECTION,
    }
)


# ---------------------------------------------------------------------------
# 动作词表：同一语义动作在两个传输方言中的候选名（按优先级排列）。
#
# 导航层「按 available_actions 实际暴露的名字选动作」的逻辑统一引用这些常量；
# 新增传输方言时在此登记，而不是在导航代码里继续堆 if-chain。
# ---------------------------------------------------------------------------

# 事件选项：CLI 用 choose_event(index)，Agent 用 choose_event_option(option_index)。
EVENT_CHOICE_ACTIONS: tuple[str, ...] = ("choose_event", "choose_event_option")

# 回主菜单：不同版本/传输上报的名字不同。
RETURN_TO_MENU_ACTIONS: tuple[str, ...] = ("return_to_menu", "return_to_main_menu")

# 卡牌奖励跳过：CLI 两个名字按界面形态二选一。
CARD_REWARD_SKIP_ACTIONS: tuple[str, ...] = ("skip_reward_cards", "reward_skip_card")

# 奖励主界面无人值守推进（收取并离开到地图）。
REWARD_PROCEED_ACTIONS: tuple[str, ...] = (
    "collect_rewards_and_proceed",
    "resolve_rewards",
    "proceed",
)

# 事件推进（含 Neow 祝福兜底）：无明确选项时的候选顺序。
EVENT_ADVANCE_ACTIONS: tuple[str, ...] = (
    "choose_event",
    "choose_event_option",
    "choose_neow_blessing",
)
