"""旅程任务运行时：一次 journey run 从预检到终态归类的完整生命周期执行器。

调用方只需构造 :class:`JourneyRequest` 并调用 :class:`JourneyExecutor`（前台）
或 :func:`run_journey_worker`（worker 子进程全流程）。终态归类、run-result.json
双路径落盘、RunStore 回写、阶段位、进度发布、取消轮询、证据封存、干净主菜单
恢复与受控重启全部隐藏在本接口之后。

领域词汇见仓库根 CONTEXT.md（旅程任务 / 干净主菜单 / 受控重启 / 终态归类 /
证据封存 / 阶段位）。
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sts2_autotest.adapters.base import GameAdapterProtocol

DEFAULT_EVIDENCE_DIR = "tests/output"

TerminalStatus = Literal[
    "PASSED", "CANCELLED", "FAILED_PLATFORM", "BLOCKED_ENVIRONMENT", "FAILED_PRODUCT",
]


# ---------------------------------------------------------------------------
# Interface：请求 / 环境 / 观测点 / 结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JourneyRequest:
    """一次旅程任务的输入。字段与既有 CLI 旗标一一对应，语义不变。

    - ``journey`` 取值：new_run | resume_run | first_battle | card_test |
      goal_scene | act_traversal | finish_interstitials。
    - ``card_test`` 必须携带 ``card_id``，否则旅程以 FAILED_PLATFORM 收场。
    - ``target_scene`` 解析在实现内完成：act_traversal 强制 NEXT_ACT；留空时
      按 journey 映射默认目标。调用方不感知 resolved_target。
    - ``precheck=True`` 仅真实 CLI / worker 入口使用：启动前先确保干净主菜单
      再做环境预检，失败以 exit_code=2 结束；False 跳过门禁（测试后门）。
    """

    journey: str
    character_id: str
    timeout: float
    target_scene: str | None = None
    route_policy: str = "leftmost"
    combat_mode: str = "traversal"
    card_id: str | None = None
    precheck: bool = False


@dataclass(frozen=True)
class ExecutorEnv:
    """任务的两个文件系统锚点。None 字段回退环境变量（worker 零配置）。

    - ``evidence_root``：None → STS2_AUTOTEST_EVIDENCE_DIR → tests/output
    - ``run_root``：None → STS2_AUTOTEST_RUN_ROOT → tests/output/.runs
    """

    evidence_root: Path | None = None
    run_root: Path | None = None

    def resolve_evidence_root(self) -> Path:
        if self.evidence_root is not None:
            return self.evidence_root
        return Path(os.environ.get("STS2_AUTOTEST_EVIDENCE_DIR", DEFAULT_EVIDENCE_DIR))

    def resolve_run_root(self) -> Path:
        if self.run_root is not None:
            return self.run_root
        return Path(os.environ.get("STS2_AUTOTEST_RUN_ROOT", "tests/output/.runs"))


@dataclass
class RunObservers:
    """执行过程观测点。默认 None 的位由实现按 run_id 接线（行为与历史闭包一致）。

    - ``on_progress``：旅程进度载荷回调；默认 = 写 RunStore(run_root) 的 progress。
    - ``on_observation``：关键屏观测回调（async）；默认 = 关键屏稳定后截图。
    - ``cancel_check``：取消轮询；默认 = 读 RunStore 记录的 cancel_requested。
    """

    on_progress: Callable[[dict[str, Any]], None] | None = None
    on_observation: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    cancel_check: Callable[[], bool] | None = None


@dataclass(frozen=True)
class JourneyOutcome:
    """一次执行的终态。exit_code 与历史六出口逐值一致。

    - 0：PASSED，或收尾干净的 CANCELLED。
    - 1：FAILED_PLATFORM / BLOCKED_ENVIRONMENT / FAILED_PRODUCT，或收尾失败的取消。
    - 2：仅预检环境阻塞（BLOCKED_ENVIRONMENT，phase=precheck）。
    """

    exit_code: int
    status: str
    result: dict[str, Any]
    evidence_dir: Path | None
    run_id: str | None


# ---------------------------------------------------------------------------
# 默认工厂（调用期解析，保持既有测试对 build_evidence_hooks 的 monkeypatch 位）
# ---------------------------------------------------------------------------


def _default_evidence_factory(evidence_root: Path, pack_id: str | None) -> Any:
    from sts2_autotest.core.evidence_hooks import build_evidence_hooks

    return build_evidence_hooks(evidence_root, pack_id=pack_id)


def _default_lifecycle_factory(adapter: Any, evidence_root: Path) -> Any:
    """构造 lifecycle 管理器（仅封装，不启进程）；无法定位游戏时降级 None。"""
    from sts2_autotest.core.runtime_factory import build_lifecycle_manager
    from sts2_autotest.core.steam import SteamController

    steam = SteamController(startup_timeout=60.0)
    try:
        return build_lifecycle_manager(adapter, steam, evidence_root)
    except (OSError, ValueError) as exc:
        print(f"[autotest] lifecycle manager unavailable: {exc}")
        return None


# ---------------------------------------------------------------------------
# 搬迁实现：干净主菜单恢复（自 cli/main.py 424-955 原样迁入）
# ---------------------------------------------------------------------------

# 游戏主菜单的开新局能力可能使用其中任一动作名（与 journeys.start_new_run 一致）。
_NEW_RUN_ACTIONS = ("start_new_run", "new_run", "open_character_select")


def _screen_of(state: Any) -> str | None:
    """Extract the normalized screen name from a state dict or GameState."""
    if state is None:
        return None
    if isinstance(state, dict):
        scr = state.get("screen")
    else:
        scr = getattr(state, "screen", None)
    return str(scr).upper() if scr is not None else None


def _wait_for_main_menu(
    adapter: Any,
    loop: Any,
    *,
    timeout: float = 120.0,
    force_fresh: Any = None,
    sleep: Callable[[float], None] | None = None,
    require_actions: bool = False,
) -> Any | None:
    """Poll until a MAIN_MENU state is readable; return latest state or None.

    require_actions=True 时仅当菜单已发布可执行操作（控制模组加载完成）才
    算到达——V11 实测：游戏重启后画面先显示主菜单，模组仍在加载（界面右下
    角「正在加载模组运行」，可达 1-2 分钟），期间动作列表为空，此时读取
    的状态不可用于干净判定。
    """
    delay = sleep if sleep is not None else time.sleep
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            if callable(force_fresh):
                force_fresh()
            st = loop.run_until_complete(adapter.get_state())
            last = st
            if _screen_of(st) == "MAIN_MENU":
                if not require_actions:
                    return st
                _, actions, _src = _frame_signals(adapter, loop, st)
                if actions:
                    return st
        except Exception:
            last = None
        delay(1.0)
    return last


def _state_view(state: Any) -> dict[str, Any]:
    """把 GameState / dict / 普通对象归一成普通 dict 视图（含 pydantic extras）。

    真实游戏控制接口的主菜单状态里 ``has_run_save`` 嵌套在 ``menu`` 下，
    ``available_actions`` 为字符串列表；判定逻辑统一基于该视图，避免按
    对象形态各写一套取值导致漏判。
    """
    if state is None:
        return {}
    if isinstance(state, dict):
        return state
    model_dump = getattr(state, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:  # noqa: BLE001
            pass
    view: dict[str, Any] = {}
    for key in ("screen", "timestamp", "has_run_save", "menu", "available_actions"):
        value = getattr(state, key, None)
        if value is not None:
            view[key] = value
    return view


def _menu_has_run_save_field(view: dict[str, Any]) -> bool | None:
    """存档内省字段（三态）：True=有旧局；False=明确无旧局；None=字段未发布。

    V11 真实验收证据：游戏控制服务直接内省存档系统，该字段比界面动作列表
    可信——菜单重建期动作列表会短暂摆出陈旧项（放弃成功后仍短暂出现
    continue_run/abandon_run，但 start_new_run 可直接开局且无确认框，
    证明存档已删除、动作是伪影）。
    """
    if "has_run_save" in view:
        value = view.get("has_run_save")
        return value if isinstance(value, bool) else None
    menu = view.get("menu")
    if isinstance(menu, dict) and "has_run_save" in menu:
        value = menu.get("has_run_save")
        return value if isinstance(value, bool) else None
    return None


def _menu_actions(view: dict[str, Any]) -> list[str]:
    return [str(action) for action in (view.get("available_actions") or [])]


def _adapter_actions(adapter: Any, loop: Any) -> list[str]:
    """经适配器协议方法获取可执行动作；失败返回空列表。

    关键差异（V11 实测）：Agent 适配器把真实动作内嵌在状态里；CliMod 适配器
    状态不内嵌动作（菜单动作由屏幕类型静态派生），必须通过协议方法单独获取。
    """
    try:
        actions = loop.run_until_complete(adapter.get_available_actions())
    except Exception:  # noqa: BLE001
        return []
    return [str(action) for action in (actions or [])]


def _frame_signals(
    adapter: Any, loop: Any, state: Any
) -> tuple[dict[str, Any], list[str], str]:
    """组合一帧的判定信号：状态视图 + 有效动作列表 + 动作来源。

    有效动作优先取状态内嵌值（state_reported，即状态接口报告值——重建期
    可能为陈旧报告，不保证此刻可点击）；缺失时退回适配器协议方法
    （adapter_derived——CliMod 路径为静态派生名称）；两者皆无则为 none。
    """
    view = _state_view(state)
    actions = _menu_actions(view)
    if actions:
        return view, actions, "state_reported"
    derived = _adapter_actions(adapter, loop)
    if derived:
        return view, derived, "adapter_derived"
    return view, [], "none"


def _frame_dirty(view: dict[str, Any], actions: list[str]) -> bool:
    """该帧是否存在旧局：内省字段优先，字段缺失时退回动作列表。"""
    has_save = _menu_has_run_save_field(view)
    if has_save is not None:
        return has_save
    return "continue_run" in actions


def _frame_clean(view: dict[str, Any], actions: list[str]) -> bool:
    """该帧是否满足干净主菜单：无旧局 + 存在开新局能力。

    has_run_save 显式 False 时忽略动作列表中的陈旧/静态项（V11 实测：
    Agent 菜单重建期会摆出陈旧 continue/abandon；CliMod 动作列表为静态
    派生，同样不代表真实旧局）。
    """
    if _screen_of(view) != "MAIN_MENU":
        return False
    has_save = _menu_has_run_save_field(view)
    if has_save is True:
        return False
    if not any(name in actions for name in _NEW_RUN_ACTIONS):
        return False
    if has_save is False:
        return True
    return "continue_run" not in actions and "abandon_run" not in actions


def _final_state_snapshot(adapter: Any, loop: Any, state: Any) -> dict[str, Any]:
    """恢复后完整状态快照——写入报告供审计独立核对，而非只信平台布尔值。

    has_run_save 为三态权威旧局信号（True/False/None=字段未发布）。
    动作列表标注来源，只描述"谁报告的"，不声称"此刻一定可点击"：
    state_reported 为状态接口报告的动作（菜单重建期可能是陈旧报告）；
    adapter_derived 为适配器协议派生/静态名称——两种来源下的
    continue/abandon 都不单独作为旧局证据，旧局以 has_run_save 为准。
    """
    view, actions, source = _frame_signals(adapter, loop, state)
    snapshot: dict[str, Any] = {
        "screen": _screen_of(state),
        "has_run_save": _menu_has_run_save_field(view),
        "available_actions": actions,
        "actions_source": source,
        "has_continue_run": (
            "continue_run" in actions if source == "state_reported" else None
        ),
        "has_abandon_run": (
            "abandon_run" in actions if source == "state_reported" else None
        ),
        "has_new_run_action": any(name in actions for name in _NEW_RUN_ACTIONS),
        "state_timestamp": view.get("timestamp"),
        "captured_at": datetime.now(UTC).isoformat(),
    }
    if source == "state_reported":
        snapshot["actions_note"] = (
            "动作列表为状态接口报告值（菜单重建期可能为陈旧报告）；"
            "旧局以 has_run_save 为准"
        )
    else:
        snapshot["actions_note"] = (
            "动作列表为适配器派生/静态名称，continue/abandon 不代表存在旧局；"
            "旧局以 has_run_save 为准"
        )
    return snapshot


def _settle_main_menu_state(
    adapter: Any,
    loop: Any,
    force_fresh: Any = None,
    *,
    tries: int = 6,
    gap: float = 1.0,
    sleep: Callable[[float], None] | None = None,
    stable_clean_frames: int = 3,
    stable_dirty_frames: int = 3,
) -> tuple[Any | None, str]:
    """连续刷新读取主菜单状态，返回（最近可判定帧, 稳定结论）。

    稳定结论三态：
    - ``"dirty"``：任一帧确认存在旧局（has_run_save 显式 True 或字段缺失时
      动作列表出现 continue_run）。旧局信号一旦出现即锁存——信号可能闪退，
      仍须放弃；连续 stable_dirty_frames 帧确认后提前结束等待。
    - ``"clean"``：连续 stable_clean_frames 个可判定帧均满足干净定义。
      要求连续稳定是为双向防错：既防 V10 的假干净（旧局信号晚到），也防
      V11 实测的反向瞬态（放弃成功后菜单重建摆出陈旧动作/空动作）。
    - ``"undecidable"``：窗口内无法确认（空动作重建帧、信号摇摆、读取中断）。
      调用方必须按失败处理，不得当成干净。

    空操作列表 = 菜单仍在初始化/重建，该帧不可用于判定（不计入稳定计数）。
    V11 实测菜单发布动作可超过 15 秒，调用方应给足 tries 窗口。
    已离开主菜单或读取失败则提前停止（此时返回帧为 None 或非主菜单态）。
    """
    delay = sleep if sleep is not None else time.sleep
    last: Any = None
    decidable: Any = None
    saw_saved_run = False
    clean_streak = 0
    dirty_streak = 0
    for _ in range(tries):
        if callable(force_fresh):
            force_fresh()
        try:
            last = loop.run_until_complete(adapter.get_state())
        except Exception:  # noqa: BLE001
            last = None
        if _screen_of(last) != "MAIN_MENU":
            break
        view, actions, _src = _frame_signals(adapter, loop, last)
        if _frame_dirty(view, actions):
            saw_saved_run = True
            clean_streak = 0
            dirty_streak += 1
            decidable = last
        elif _frame_clean(view, actions):
            clean_streak += 1
            dirty_streak = 0
            decidable = last
        else:
            # 可判定但既非脏也非净（如有动作但无开新局能力）→ 重置稳定计数；
            # 空动作重建帧不算可判定帧。
            clean_streak = 0
            dirty_streak = 0
            if actions:
                decidable = last
        if saw_saved_run and dirty_streak >= stable_dirty_frames:
            break
        if not saw_saved_run and clean_streak >= stable_clean_frames:
            break
        delay(gap)
    if saw_saved_run:
        verdict = "dirty"
    elif clean_streak >= stable_clean_frames:
        verdict = "clean"
    else:
        verdict = "undecidable"
    return (decidable if decidable is not None else last), verdict


def _abandon_saved_run(
    adapter: Any, loop: Any, *, force_fresh: Any = None
) -> bool:
    """Abandon the saved run from the main menu and confirm deletion.

    Executes the existing ``abandon_run`` action, handles a possible confirm
    modal, then verifies the saved run is gone. Returns success.
    """
    try:
        loop.run_until_complete(adapter.act("abandon_run"))
    except Exception as exc:  # noqa: BLE001
        print(f"[autotest] abandon_run failed: {exc}")
        return False
    if callable(force_fresh):
        force_fresh()
    try:
        st = loop.run_until_complete(adapter.get_state())
        _view, actions, _src = _frame_signals(adapter, loop, st)
        if "confirm_modal" in actions:
            loop.run_until_complete(adapter.act("confirm_modal"))
    except Exception as exc:  # noqa: BLE001
        print(f"[autotest] abandon confirm handling failed: {exc}")
    if callable(force_fresh):
        force_fresh()
    try:
        st2 = loop.run_until_complete(adapter.get_state())
        view2, actions2, _src2 = _frame_signals(adapter, loop, st2)
        return not _frame_dirty(view2, actions2)
    except Exception:  # noqa: BLE001
        return False


def _wait_game_gone(lifecycle: Any, *, timeout: float = 30.0) -> None:
    """轮询直到游戏进程消失（best-effort；超时即返回，交由后续启动兜底）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not lifecycle.game_process_present():
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)


def _force_adapter_state_refresh(adapter: Any) -> None:
    """重启/恢复后强制适配器丢弃状态缓存，以免读到旧画面。

    已知债（CONTEXT.md）：cli_mod 适配器的 ``_cache_stale`` 为私有属性，
    正解是协议级公开 ``invalidate_cache()``；在收敛前保持 hasattr 保护的
    原行为。
    """
    if hasattr(adapter, "_cache_stale"):
        try:
            adapter._cache_stale = True
        except Exception:  # noqa: BLE001
            pass


def recover_main_menu_via_restart(
    lifecycle: Any,
    adapter: Any,
    loop: Any,
    *,
    sleep: Callable[[float], None] | None = None,
    settle_tries: int = 10,
    post_abandon_tries: int = 45,
    menu_timeout: float = 120.0,
    operational_timeout: float = 360.0,
) -> dict[str, Any]:
    """审查结论（P1 正式通过方式）：取消后通过「受控重启」恢复到干净主菜单。

    这是*结果目标*（target=MAIN_MENU），不要求走 ESC→放弃 的普通界面退出。
    流程（复用生命周期有界恢复，最多重启一次，禁止无限重启）：
      1. 停止继续操作（取消已生效，JourneyCancelled 已中断旅程）。
      2. 仅结束已确认的 STS2 游戏进程（lifecycle 按进程名/game_exe 兜底）。
      3. 等待游戏进程完全消失、8080 端口释放。
      4. 只启动一次游戏（ensure_environment_ready 内部单次 relaunch）。
      5. 验证健康 + 游戏状态 + 可执行操作全部可读。
      6. 确认到达主菜单。
      7. 若主菜单仍显示旧局：执行现有 abandon_run；处理确认框；确认旧局删除。
      8. 最终确认（MAIN_MENU / 不再提供 continue_run / 可开新局）。

    V10 复核修复：
    - P0-1/P0-2：干净判定只用「放弃后连续稳定读取的最后一帧完整状态」；
      ``ok`` 要求满足干净主菜单全部条件，不再只看 screen=MAIN_MENU。
    - P0-3：全函数只允许一次启动——首启动后未达主菜单即判环境阻塞返回，
      禁止第二次启动；``restart_count`` 记录真实启动次数。
    - P1-1：``final_state`` 保存恢复后完整状态快照供审计独立核对。
    返回结构化 dict；blocked=True → BLOCKED_ENVIRONMENT；
    ok=False 且 blocked=False → FAILED_PLATFORM；ok=True → CANCELLED。
    """
    result: dict[str, Any] = {
        "target": "MAIN_MENU",
        "recovery_method": "controlled_restart",
        "normal_menu_abandon": False,
        "restart_count": 0,
        "final_screen": None,
        "clean_main_menu": False,
        "old_run_abandoned": False,
        "ok": False,
        "blocked": False,
        "reason": None,
        "final_state": None,
    }

    def _force_fresh() -> None:
        _force_adapter_state_refresh(adapter)

    try:
        if hasattr(adapter, "reset_http_client"):
            adapter.reset_http_client()

        # 步骤2-3：无条件结束当前游戏（局内 EVENT/MAP/COMBAT 或已就绪都终止）。
        # 关键：ensure_environment_ready 在「游戏仍可控制」时会*跳过*重启、沿用
        # 原会话；而取消恢复的目标是干净主菜单，必须先把仍在跑的局内实例杀掉，
        # 否则游戏永远停在原屏到不了 MAIN_MENU。因此先显式 terminate + 等进程消失。
        lifecycle.terminate()
        _wait_game_gone(lifecycle, timeout=30.0)
        # 步骤4-5：唯一一次启动（terminate 已在步骤2-3 完成；此处 relaunch→probe）。
        # V10 复核 P0-3：首启动后只允许等待状态稳定，绝不再调用启动恢复——
        # 第二次启动会重新引入 Steam 多弹窗、旧实例冲突和 WindowServer 事故风险。
        # 注意：ensure_environment_ready 的「就绪」判定只看「端口通 + 首帧 screen
        # 非 UNKNOWN」。重启后游戏常需更久才真正可操控（首帧常为 UNKNOWN），因此
        # 这里不因它「未就绪」就立刻判阻塞——先 relaunch，再用 _wait_for_main_menu
        # 轮询（容忍 UNKNOWN 瞬态、最长 menu_timeout）确认真达主菜单。
        readiness = loop.run_until_complete(lifecycle.ensure_environment_ready())
        # restart_count 记录真实启动次数：本函数仅在此处启动一次。
        result["restart_count"] = 1
        _force_fresh()
        if hasattr(adapter, "reset_http_client"):
            adapter.reset_http_client()

        # 步骤6：轮询直到主菜单（启动后需转场时间，给游戏充分初始化窗口）。
        st = _wait_for_main_menu(
            adapter, loop, timeout=menu_timeout, force_fresh=_force_fresh, sleep=sleep
        )
        if st is None or _screen_of(st) != "MAIN_MENU":
            # 一次启动后仍不可操作 → 环境阻塞，禁止再次启动。
            result["blocked"] = True
            reason = getattr(readiness, "reason", None) or "unknown"
            result["reason"] = (
                f"main menu not reached after single controlled restart: {reason}"
            )
            return result

        # 步骤6b：等待控制模组加载完成——V11 实测游戏重启后画面先显示主菜单，
        # 模组仍在加载（「正在加载模组运行」，可达 1-2 分钟），期间动作列表为空，
        # 任何干净/旧局判定都不可信。到达帧已带动作则无需再等；否则等菜单真正
        # 可操作（有效动作非空：状态内嵌或适配器协议方法派生）。
        _, st_actions, _src = _frame_signals(adapter, loop, st)
        if not st_actions:
            st = _wait_for_main_menu(
                adapter, loop, timeout=operational_timeout, force_fresh=_force_fresh,
                sleep=sleep, require_actions=True,
            )
            _, st_actions, _src = _frame_signals(adapter, loop, st)
            if st is None or not st_actions:
                result["blocked"] = True
                result["reason"] = (
                    "main menu not operational after single controlled restart "
                    "(control mod did not finish loading)"
                )
                return result

        # 步骤7：多次稳定读取；确认旧局即执行 abandon_run（含确认框），放弃后
        # 再连续稳定读取——放弃可能触发菜单重建，单帧瞬态干净/瞬态陈旧动作都不
        # 可信（V10 假阳性 + V11 实测空动作重建窗）。读取失败（None）= 控制入口
        # 失联 → 环境阻塞。
        last, verdict = _settle_main_menu_state(
            adapter, loop, _force_fresh, tries=settle_tries, sleep=sleep
        )
        if last is None:
            result["blocked"] = True
            result["reason"] = "game control lost while settling main menu state"
            return result
        if verdict == "dirty":
            _abandon_saved_run(adapter, loop, force_fresh=_force_fresh)
            last, verdict = _settle_main_menu_state(
                adapter, loop, _force_fresh, tries=post_abandon_tries, sleep=sleep
            )
            if last is None:
                result["blocked"] = True
                result["reason"] = "game control lost after abandon_run"
                return result
            result["old_run_abandoned"] = verdict == "clean"

        # 步骤8：以稳定结论做干净判定并留存最近可判定帧快照（P1-1）。
        result["final_screen"] = _screen_of(last)
        result["final_state"] = _final_state_snapshot(adapter, loop, last)
        result["clean_main_menu"] = verdict == "clean"
        if not result["clean_main_menu"]:
            if verdict == "dirty":
                result["reason"] = "saved run still present after abandon"
            else:
                result["reason"] = "main menu reached but not verifiably clean"
            return result
        result["ok"] = True
        return result
    except Exception as exc:  # noqa: BLE001
        result["blocked"] = True
        result["reason"] = f"controlled restart recovery exception: {exc}"
        print(f"[autotest] controlled restart recovery failed: {exc}")
        return result


def ensure_clean_main_menu(
    adapter: Any, lifecycle: Any, loop: Any
) -> bool:
    """开工前确保游戏处于干净主菜单（审查结论：连续任务不继承残局）。

    跨 Agent / 连续任务场景下，上一任务可能在游戏内留下 COMBAT/MAP/EVENT 等残局
    （例如 resume 任务 PASSED 后游戏留在战斗中）。first_battle / new_run 旅程开局
    必须经主菜单 ``start_new_run`` 动作，而 ``reset_to_main_menu`` 只能处理菜单态、
    无法把战斗态退回——若当前屏非 MAIN_MENU 会抛 JourneyFailure，导致新局起不来。

    此处复用受控重启能力把游戏拉回干净主菜单，保证每个新任务都从主菜单重新开局。
    lifecycle 不可用时（环境非本框架管理）返回 True 交由后续旅程自行判定，不退化行为。
    """
    if lifecycle is None:
        return True
    try:
        st = loop.run_until_complete(adapter.get_state())
    except Exception:  # noqa: BLE001
        st = None
    scr = ""
    if st is not None:
        if hasattr(st, "screen"):
            scr = str(st.screen).upper()
        elif isinstance(st, dict):
            scr = str(st.get("screen", "")).upper()
    if scr == "MAIN_MENU":
        # 即便已在主菜单，若残留旧局也要放弃——否则后续 new_run 点 start_new_run
        # 会触发「放弃旧局」确认框，扰乱整个旅程状态（松哥 8 步流程第 7 步）。
        # 放弃后以最后一帧稳定状态复核；仍未干净则经受控重启兜底（V11：
        # 复用已修复的干净判定，避免把脏菜单当成开局起点）。
        def _ff() -> None:
            _force_adapter_state_refresh(adapter)

        # 等待控制模组加载完成（菜单可操作：有效动作非空）。模组加载时长波动大
        # （V11 实测 60s～>180s），给足窗口；超时则走受控重启兜底。
        op = _wait_for_main_menu(
            adapter, loop, timeout=300.0, force_fresh=_ff,
            require_actions=True,
        )
        _, op_actions, _src = _frame_signals(adapter, loop, op)
        if op is None or not op_actions:
            recovered = recover_main_menu_via_restart(lifecycle, adapter, loop)
            return bool(recovered.get("ok"))
        _last, verdict = _settle_main_menu_state(adapter, loop, _ff, tries=30)
        if verdict == "dirty":
            _abandon_saved_run(adapter, loop, force_fresh=_ff)
            _last, verdict = _settle_main_menu_state(adapter, loop, _ff, tries=30)
        if verdict != "clean":
            # 旧局清不掉、或菜单迟迟不发布可执行操作（无法确认干净）→
            # 经受控重启兜底，保证后续任务从可验证的干净起点开局（V11）。
            recovered = recover_main_menu_via_restart(lifecycle, adapter, loop)
            return bool(recovered.get("ok"))
        return True
    print(
        f"[autotest] pre-journey screen={scr or 'UNKNOWN'}; "
        f"performing controlled restart to recover clean MAIN_MENU"
    )
    recovered = recover_main_menu_via_restart(lifecycle, adapter, loop)
    return bool(recovered.get("ok"))


# ---------------------------------------------------------------------------
# 搬迁实现：证据封存 / 稳定等待 / 预检 / 取消收尾报告
# （自 cli/main.py 1741-2192 原样迁入；_classify_cancel_cleanup_error 为
#   生产零调用的死代码，未迁入。）
# ---------------------------------------------------------------------------


def _write_journey_failure(
    pack_dir: Path,
    *,
    journey: str,
    failure: dict[str, Any],
    duration_ms: int,
) -> None:
    """把失败留证单独落盘：卡在哪个页面、最后执行了什么、状态轨迹与原因。"""
    try:
        reports = pack_dir / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        payload = {
            "journey": journey,
            "duration_ms": duration_ms,
            "stuck_screen": failure.get("stuck_screen"),
            "last_action": failure.get("last_action"),
            "reason_code": failure.get("reason_code"),
            "reason": failure.get("reason"),
            "status_trajectory": failure.get("status_trajectory"),
            "last_state": failure.get("last_state"),
            "evidence_hint": "崩溃截图见 ../screenshots，游戏日志见 ../logs",
        }
        (reports / "journey-failure.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def compact_persistent_result(payload: dict[str, Any], evidence_dir: Path) -> dict[str, Any]:
    """任务记录只保存摘要；完整状态轨迹留在证据文件中。"""
    compact = {
        key: value
        for key, value in payload.items()
        if key not in {"journey_evidence", "last_state"}
    }
    if "journey_evidence" in payload:
        compact["journey_trace_path"] = str(
            (evidence_dir / "reports" / "journey-trace.json").resolve()
        )
    failure = compact.get("failure")
    if isinstance(failure, dict) and isinstance(failure.get("last_state"), dict):
        failure = dict(failure)
        failure.pop("last_state", None)
        failure["last_state_path"] = str(
            (evidence_dir / "reports" / "journey-failure.json").resolve()
        )
        compact["failure"] = failure
    return compact


def _write_journey_evidence(
    evidence_root: Path,
    run_id: str | None,
    *,
    journey: str,
    target_scene: str,
    evidence: dict[str, Any],
    duration_ms: int | None = None,
) -> None:
    """把通用旅程的场景、操作、地图和证据清单写成机器可读文件。"""
    if not run_id:
        return
    report_dir = evidence_root / run_id / "reports"
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        trace_evidence = dict(evidence)
        if duration_ms is not None:
            trace_evidence["duration_ms"] = duration_ms
        trace = {
            "journey": journey,
            "target_scene": target_scene,
            **trace_evidence,
        }
        (report_dir / "journey-trace.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        root = evidence_root / run_id
        screenshot_paths = sorted(
            path for path in (root / "screenshots").glob("*")
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
        ) if (root / "screenshots").is_dir() else []
        log_paths = sorted(
            path for path in (root / "logs").glob("*")
            if path.is_file() and path.suffix.lower() == ".log"
        ) if (root / "logs").is_dir() else []
        screenshot_count = len(screenshot_paths)
        log_count = len(log_paths)
        files = [
            str(path.relative_to(root))
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]
        declared_files = [
            "summary.json",
            "summary.md",
            "reports/run-result.json",
            "reports/journey-trace.json",
            "reports/evidence-manifest.json",
            "reports/junit.xml",
        ]
        missing_files = [name for name in declared_files if not (root / name).is_file()]

        def image_dimensions(path: Path) -> tuple[int, int] | None:
            try:
                with path.open("rb") as handle:
                    data = handle.read(1024 * 1024)
            except OSError:
                return None
            if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
                return (
                    int.from_bytes(data[16:20], "big"),
                    int.from_bytes(data[20:24], "big"),
                )
            if not data.startswith(b"\xff\xd8"):
                return None
            offset = 2
            while offset + 9 < len(data):
                if data[offset] != 0xFF:
                    offset += 1
                    continue
                while offset < len(data) and data[offset] == 0xFF:
                    offset += 1
                if offset >= len(data):
                    break
                marker = data[offset]
                offset += 1
                if marker in {0xD8, 0xD9}:
                    continue
                if offset + 2 > len(data):
                    break
                segment_length = int.from_bytes(data[offset:offset + 2], "big")
                if segment_length < 2 or offset + segment_length > len(data):
                    break
                if marker in {
                    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
                } and segment_length >= 7:
                    height = int.from_bytes(data[offset + 3:offset + 5], "big")
                    width = int.from_bytes(data[offset + 5:offset + 7], "big")
                    return width, height
                offset += segment_length
            return None

        screenshot_details = []
        for path in screenshot_paths:
            dimensions = image_dimensions(path)
            screenshot_details.append({
                "path": str(path.relative_to(root)),
                "format": path.suffix.lower().lstrip("."),
                "width": dimensions[0] if dimensions else None,
                "height": dimensions[1] if dimensions else None,
                "bytes": path.stat().st_size,
            })
        log_details = [
            {
                "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
            }
            for path in log_paths
        ]

        from sts2_autotest.core.run_service import resolve_artifact_path

        artifact_path = resolve_artifact_path(evidence_root, run_id)
        archive_counts: dict[str, Any] = {"status": "unavailable"}
        if artifact_path is not None:
            try:
                with zipfile.ZipFile(artifact_path) as archive:
                    archive_screenshots = sum(
                        name.startswith("screenshots/")
                        and Path(name).suffix.lower() in {".png", ".jpg", ".jpeg"}
                        for name in archive.namelist()
                    )
                    archive_logs = sum(
                        name.startswith("logs/") and Path(name).suffix.lower() == ".log"
                        for name in archive.namelist()
                    )
                archive_counts = {
                    "status": "verified",
                    "path": str(artifact_path),
                    "screenshot_count": archive_screenshots,
                    "log_count": archive_logs,
                    "counts_match": (
                        archive_screenshots == screenshot_count
                        and archive_logs == log_count
                    ),
                }
                if not archive_counts["counts_match"]:
                    archive_counts["status"] = "mismatch"
            except (OSError, zipfile.BadZipFile) as exc:
                archive_counts = {
                    "status": "unreadable",
                    "path": str(artifact_path),
                    "reason": str(exc),
                }

        if platform.system() == "Darwin":
            log_source_candidates = [
                Path.home() / "Library/Application Support/SlayTheSpire2/logs",
                Path.home() / "Library/Application Support/Godot/app_userdata/Slay the Spire 2/logs",
            ]
        elif platform.system() == "Windows":
            log_source_candidates = [
                Path(os.environ.get("APPDATA", "")) / "Godot/app_userdata/Slay the Spire 2/logs",
            ]
        else:
            log_source_candidates = [
                Path.home() / ".local/share/godot/app_userdata/Slay the Spire 2/logs",
            ]
        log_source = os.environ.get("STS2_GODOT_LOG_DIR") or next(
            (str(path) for path in log_source_candidates if path.is_dir()),
            str(log_source_candidates[0]),
        )
        (report_dir / "evidence-manifest.json").write_text(
            json.dumps(
                {
                    "evidence_root": str(root.resolve()),
                    "declared": declared_files,
                    "existing_files": files,
                    "missing_files": missing_files,
                    "evidence_level": os.environ.get("STS2_AUTOTEST_EVIDENCE", "full"),
                    "screenshot_count": screenshot_count,
                    "log_count": log_count,
                    "screenshots": screenshot_details,
                    "logs": log_details,
                    "log_source": log_source,
                    "artifact_path": str(artifact_path) if artifact_path else None,
                    "archive": archive_counts,
                    "capture_status": {
                        "screenshots": (
                            {"status": "available", "count": screenshot_count}
                            if screenshot_count
                            else {
                                "status": "unavailable",
                                "count": 0,
                                "reason": "运行结束时截图目录为空；未将空目录当作已截图。",
                            }
                        ),
                        "logs": (
                            {"status": "available", "count": log_count}
                            if log_count
                            else {
                                "status": "unavailable",
                                "count": 0,
                                "reason": "本次运行没有可复制的游戏日志文件。",
                            }
                        ),
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


async def _wait_for_stable_api_state(
    adapter: GameAdapterProtocol,
    initial: dict[str, Any],
    *,
    timeout: float = 4.0,
    interval: float = 0.5,
    settle: float = 0.5,
) -> dict[str, Any]:
    """截图前等待 API 状态连续一致，并给窗口渲染留出 settle 时间。

    游戏画面切换滞后于状态接口（曾出现第二章 MAP 截图仍显示卡牌选择
    界面）；连续两次读取指纹一致后再等一个 settle 间隔，可显著降低
    截图拍到上一页的概率。超时仍未稳定时用最后一次读取，不阻塞任务。
    """
    from sts2_autotest.core.journeys import state_fingerprint

    latest = initial
    previous_fp = state_fingerprint(initial)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        try:
            payload = (await adapter.get_state()).model_dump()
            payload["available_actions"] = await adapter.get_available_actions()
        except Exception:  # noqa: BLE001
            break
        latest = payload
        current_fp = state_fingerprint(payload)
        if current_fp == previous_fp:
            break
        previous_fp = current_fp
    await asyncio.sleep(settle)
    return latest


def _resolve_combat_mode_with_debug_check(
    adapter: GameAdapterProtocol,
    combat_mode: str,
    loop: asyncio.AbstractEventLoop,
) -> tuple[str, str | None]:
    """启动真实任务前再验证调试能力，据此决定有效战斗模式（修复二运行期双真）。

    traversal（快速结束战斗）需"配置要求启用 AND 实际探测确认可用"双真才可启用；
    未验证时降级为 basic，避免对不可用的 win_combat 反复空试。death 模式是刻意
    只结束回合的死亡测试，绝不降级。探测非破坏性、绝不抛错。

    Returns:
        (有效战斗模式, 降级原因)。未降级时原因为 None。
    """
    if combat_mode != "traversal":
        return combat_mode, None
    verify = getattr(adapter, "verify_debug_actions", None)
    if verify is None:
        return "basic", "DEBUG_VERIFY_UNSUPPORTED"
    try:
        verification = loop.run_until_complete(verify())
    except Exception:  # noqa: BLE001
        return "basic", "DEBUG_VERIFY_ERROR"
    if getattr(verification, "verified", False):
        return "traversal", None
    return "basic", getattr(verification, "reason", None) or "DEBUG_CONSOLE_UNAVAILABLE"


def _run_environment_precheck(adapter: GameAdapterProtocol) -> str | None:
    """启动旅程前验证游戏可控；不可控则做有界自恢复（修复一串接）。

    仅当项目提供了游戏可执行文件/目录（本框架可自行拉起）时才启用预检——否则
    跳过，因为不去恢复不归我们管理的环境。就绪或跳过返回 None；未就绪返回阻塞
    原因字符串（调用方据此判 BLOCKED_ENVIRONMENT）。预检绝不抛错冒泡。

    Returns:
        None 表示环境就绪或预检不适用；非空字符串为阻塞原因。
    """
    from sts2_autotest.core.runtime_factory import build_lifecycle_manager
    from sts2_autotest.core.steam import SteamController

    evidence_root = Path(
        os.environ.get("STS2_AUTOTEST_EVIDENCE_DIR", DEFAULT_EVIDENCE_DIR)
    )
    try:
        lifecycle = build_lifecycle_manager(
            adapter, SteamController(), evidence_root
        )
    except (OSError, ValueError):
        lifecycle = None
    if lifecycle is None:
        return None  # 环境非本框架管理，跳过预检

    # 恢复拉起改用 ``open <bundle>``（见 lifecycle.GameLifecycleManager.launch），
    # macOS 上可稳定拉起、约 18s 到达可控主菜单；因此放宽等待窗口，给刚拉起的
    # 游戏留出初始化时间（首帧常为 UNKNOWN，wait_for_controllable 会等到真正屏幕）。
    try:
        lifecycle.api_timeout = min(float(getattr(lifecycle, "api_timeout", 150.0)), 180.0)
    except (TypeError, ValueError):
        lifecycle.api_timeout = 180.0

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        readiness = loop.run_until_complete(lifecycle.ensure_environment_ready())
    except Exception as exc:  # noqa: BLE001 - 预检失败不得中断，归类为环境阻塞
        return f"PRECHECK_ERROR:{exc!r}"
    finally:
        loop.close()
        asyncio.set_event_loop(None)
        # 预检在临时循环上经共享适配器发起了 HTTP 请求，连接已绑定到刚关闭的循环。
        # 丢弃适配器缓存的 httpx 客户端，避免后续旅程复用中毒连接、在响应关闭阶段
        # 抛出 "Event loop is closed"。用 getattr 兼容非 Agent 适配器。
        reset = getattr(adapter, "reset_http_client", None)
        if callable(reset):
            reset()

    if getattr(readiness, "ready", False):
        return None
    reason = getattr(readiness, "reason", None)
    reason_str = getattr(reason, "value", None) or (str(reason) if reason else None)
    return reason_str or "ENVIRONMENT_NOT_READY"


def _build_cancel_result(
    *,
    duration_ms: int,
    trajectory: list[Any],
    pre_cancel_state: Any,
    recovered_state: Any,
    last_action: Any,
    journey_evidence: dict[str, Any],
    evidence_dir: str | None,
    recovery: dict[str, Any],
    lifecycle: Any,
) -> dict[str, Any]:
    """构造取消收尾报告数据（run-result.json 的 extra 部分）。

    阶段 C 复审 B1（issue #37）：restart_count 提升到报告顶层，与 relaunch_count
    同口径——仅当受控重启恢复路径真实执行过（lifecycle 可用）才暴露 recovery 中
    的真实计数；lifecycle 不可用（无恢复数据源）不写该 key，禁止编造 0。
    recovery 子 dict 原样保留，供审计核对恢复结果细节。
    """
    result: dict[str, Any] = {
        "duration_ms": duration_ms,
        "status_trajectory": trajectory,
        "pre_cancel_state": pre_cancel_state,
        "pre_cancel_screen": (pre_cancel_state or {}).get("screen")
        if isinstance(pre_cancel_state, dict) else None,
        "recovered_screen": (recovered_state or {}).get("screen")
        if isinstance(recovered_state, dict) else None,
        "last_action": last_action,
        "journey_evidence": journey_evidence,
        "evidence_dir": evidence_dir,
        # 受控重启恢复结果标记（如实写，不得伪装 normal_game_menu）。
        "recovery": {
            "target": recovery.get("target"),
            "recovery_method": recovery.get("recovery_method"),
            "normal_menu_abandon": recovery.get("normal_menu_abandon"),
            "restart_count": recovery.get("restart_count"),
            "final_screen": recovery.get("final_screen"),
            "clean_main_menu": recovery.get("clean_main_menu"),
            "old_run_abandoned": recovery.get("old_run_abandoned"),
            "reason": recovery.get("reason"),
            "final_state": recovery.get("final_state"),
        },
    }
    if lifecycle is not None:
        count = recovery.get("restart_count")
        if isinstance(count, int):
            result["restart_count"] = count
    return result


# ---------------------------------------------------------------------------
# JourneyExecutor
# ---------------------------------------------------------------------------


class JourneyExecutor:
    """绑定一次旅程任务的执行器：构造后调用 :meth:`execute` 至多一次。

    adapter 被消费：execute 的 finally 保证恰好调用一次 ``adapter.cleanup()``。
    事件循环由实现在 execute 内创建并销毁；调用方线程不得持有运行中的循环。
    """

    def __init__(
        self,
        adapter: GameAdapterProtocol,
        request: JourneyRequest,
        *,
        run_id: str | None = None,
        env: ExecutorEnv | None = None,
        evidence_factory: Callable[[Path, str | None], Any] | None = None,
        lifecycle_factory: Callable[[Any, Path], Any] | None = None,
        observers: RunObservers | None = None,
    ) -> None:
        self._adapter = adapter
        self._request = request
        self._run_id = run_id
        self._env = env or ExecutorEnv()
        self._evidence_factory = evidence_factory or _default_evidence_factory
        self._lifecycle_factory = lifecycle_factory or _default_lifecycle_factory
        self._observers = observers

    def execute(self) -> JourneyOutcome:
        """阻塞执行旅程直到终态归类。旅程域失败绝不外抛（全部折算为 outcome）；
        唯一外抛窗口是 precheck=True 且启动前恢复/预检自身抛异常（防睡眠守护
        已先释放）。每次调用至多写一份终态 run-result.json；从不写
        RunRecord.status（记录收口是 worker 路径 complete_record 的职责，
        取消路径经 finish_cancel 提前置终态）。
        """
        adapter = self._adapter
        request = self._request
        run_id = self._run_id
        journey = request.journey
        character_id = request.character_id
        timeout = request.timeout
        target_scene = request.target_scene
        route_policy = request.route_policy
        combat_mode = request.combat_mode
        card_id = request.card_id
        evidence_root = self._env.resolve_evidence_root()
        observers = self._observers or RunObservers()

        from sts2_autotest.common.errors import STS2Error
        from sts2_autotest.core.action_model import TestResult
        from sts2_autotest.core.anti_sleep import AntiSleepGuard
        from sts2_autotest.core.journeys import (
            GenericJourneys,
            JourneyCancelled,
            JourneyFailure,
            extract_chapter,
        )

        case_id = f"journey:{journey}"
        evidence = self._evidence_factory(evidence_root, run_id)
        evidence.on_case_start(case_id)

        # 取消收尾（审查结论 #5）在局内取消且 reset 失败时需要 lifecycle 的受控
        # 重启能力回到主菜单。此处构建管理器（仅封装，不启进程）；若环境无法定位
        # 游戏目录则置 None，取消收尾会据此跳过受控重启并归类为清理失败。
        lifecycle: Any = self._lifecycle_factory(adapter, evidence_root)

        # P0-3：防睡眠守护覆盖整个真实任务生命周期（预检 → 运行 → 取消清理 → 证据封存）。
        # macOS 上经 caffeinate 防止显示器/系统睡眠中断渲染与游戏控制 API；非 macOS 为
        # no-op。无论通过/失败/取消/阻塞，均在函数 finally 中关闭，避免孤儿进程。
        anti_sleep = AntiSleepGuard()
        anti_sleep_started = anti_sleep.start()

        async def capture_key_state(state: dict[str, Any]) -> None:
            screen = str(state.get("screen") or "").upper()
            chapter = extract_chapter(state)
            if screen in {"EVENT", "COMBAT", "CARD_REWARD", "GAME_OVER"} or (
                screen == "MAP" and chapter == 2
            ):
                capture_state = getattr(evidence, "capture_state", None)
                if callable(capture_state):
                    stable_state = await _wait_for_stable_api_state(adapter, state)
                    safe_case_id = case_id.replace(":", "_")
                    capture_state(
                        f"{safe_case_id}_{screen}_{int(time.monotonic() * 1000)}",
                        stable_state,
                    )

        observation_callback = observers.on_observation or capture_key_state

        def publish_progress(progress: dict[str, Any]) -> None:
            if observers.on_progress is not None:
                observers.on_progress(progress)
                return
            if not run_id:
                return
            try:
                from sts2_autotest.core.run_service import RunStore

                RunStore(self._env.resolve_run_root()).update(
                    run_id,
                    progress=progress,
                )
            except (OSError, RuntimeError, ValueError):
                pass

        def cancel_requested() -> bool:
            """读取本任务是否被请求取消。旅程据此在发起下一步操作前停止。"""
            if observers.cancel_check is not None:
                return bool(observers.cancel_check())
            if not run_id:
                return False
            try:
                from sts2_autotest.core.run_service import RunStore

                record = RunStore(self._env.resolve_run_root()).load(run_id)
                return bool(record and record.cancel_requested)
            except (OSError, RuntimeError, ValueError):
                return False

        def write_result(
            status: str,
            message: str | None = None,
            *,
            extra: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "run_id": run_id,
                "task_id": run_id,
                "status": status,
            }
            if message:
                payload["message"] = message
            if extra:
                payload.update(extra)
            # P0-3：记录防睡眠守护是否在任务开始时成功拉起，便于无人值守审计。
            payload["anti_sleep_started"] = bool(anti_sleep_started)
            if not run_id:
                return payload
            result_dir = evidence_root / run_id / "reports"
            try:
                result_dir.mkdir(parents=True, exist_ok=True)
                (result_dir / "run-result.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                # 兼容 RunStore 布局：detach 任务状态存于 .runs/{run_id}/run.json，
                # 但报告层（report 命令 / run-result.json）默认只认顶层
                # tests/output/{run_id}/reports/。两处不协同会导致取消类任务的
                # run-result.json 落盘位置与 run 记录脱节（审查结论：四处一致缺口）。
                # 此处把 run-result.json 同步镜像进 .runs/{run_id}/reports/，
                # 让证据与任务记录同源同址，report 命令也能据此发现并合成报告。
                store_dir = evidence_root / ".runs" / run_id / "reports"
                try:
                    store_dir.mkdir(parents=True, exist_ok=True)
                    (store_dir / "run-result.json").write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
            except OSError:
                pass
            return payload

        def persist_artifact_path() -> None:
            """把已生成的实际压缩包路径写回任务结果，拒绝伪造默认路径。

            P1-4：同时更新顶层 run-result.json 与 .runs 镜像，避免顶层目录被保留策略
            清理后内部报告丢失证据包地址。两处必须由同一个动作原子更新。
            """
            if not run_id:
                return
            from sts2_autotest.core.run_service import resolve_artifact_path

            artifact = resolve_artifact_path(evidence_root, run_id)
            if artifact is None:
                return
            artifact_path = str(artifact)
            targets = [
                evidence_root / run_id / "reports" / "run-result.json",
                evidence_root / ".runs" / run_id / "reports" / "run-result.json",
            ]
            for result_path in targets:
                try:
                    if not result_path.is_file():
                        continue
                    loaded = json.loads(result_path.read_text(encoding="utf-8"))
                    if not isinstance(loaded, dict):
                        continue
                    loaded["artifact_path"] = artifact_path
                    loaded["evidence_pack_url"] = artifact_path
                    result_path.write_text(
                        json.dumps(loaded, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except (OSError, ValueError, TypeError):
                    pass

        def make_outcome(status: str, exit_code: int, payload: dict[str, Any]) -> JourneyOutcome:
            return JourneyOutcome(
                exit_code=exit_code,
                status=status,
                result=payload,
                evidence_dir=(evidence_root / run_id) if run_id else None,
                run_id=run_id,
            )

        # 修复一串接：真正启动旅程前做环境预检（游戏可控性 + 有界自恢复）。
        # 环境不就绪时直接判 BLOCKED_ENVIRONMENT，绝不带病进入旅程执行。
        # 仅真实 CLI 入口（precheck=True）执行；内部单测直接调用时跳过。
        if request.precheck:
            # v9 修复①：连续任务场景下上一任务可能把游戏留在战斗/地图/结算残局
            # （#6「留在战斗中」），预检会把残留进程判成 GAME_PROCESS_STALE 直接 BLOCK，
            # 导致旅程派发层的受控重启（ensure_clean_main_menu）根本没机会执行。
            # 故在预检之前先确保干净主菜单：非主菜单态即经受控重启（走可靠的 Steam
            # 重拉路径）回到 MAIN_MENU，再进预检，避免预检在发起恢复前就拦截。
            # 首任务游戏本就在 MAIN_MENU 时此处仅一次 get_state 空转，不会误重启。
            try:
                if lifecycle is not None:
                    _pc_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(_pc_loop)
                    try:
                        ensure_clean_main_menu(adapter, lifecycle, _pc_loop)
                    finally:
                        _pc_loop.close()
                        asyncio.set_event_loop(None)
                        reset = getattr(adapter, "reset_http_client", None)
                        if callable(reset):
                            reset()
                precheck_reason = _run_environment_precheck(adapter)
            except Exception:
                # 启动前恢复或环境预检自身抛异常（而非返回 reason）：
                # 先停防睡眠守护再上抛，避免遗留孤儿 caffeinate（松哥 P1 复查要点）。
                try:
                    anti_sleep.stop()
                except Exception:  # noqa: BLE001
                    pass
                raise
            if precheck_reason is not None:
                payload = write_result(
                    "BLOCKED_ENVIRONMENT",
                    message=f"environment precheck failed: {precheck_reason}",
                    extra={"reason": precheck_reason, "phase": "precheck"},
                )
                evidence.on_session_end({
                    "total": 1, "passed": 0, "failed": 0, "crashed": 0, "skipped": 1,
                    "duration_ms": 0, "status": "BLOCKED_ENVIRONMENT",
                })
                print(
                    json.dumps(
                        {
                            "journey": journey,
                            "status": "BLOCKED_ENVIRONMENT",
                            "reason": precheck_reason,
                            "phase": "precheck",
                        },
                        ensure_ascii=False,
                    )
                )
                # v9 修复②：预检失败提前 return 会跳过函数末尾 finally（含
                # anti_sleep.stop），导致遗留孤儿 caffeinate。此处显式释放防睡眠守护。
                try:
                    anti_sleep.stop()
                except Exception:  # noqa: BLE001
                    pass
                return make_outcome("BLOCKED_ENVIRONMENT", 2, payload)

        started = time.monotonic()
        runner: GenericJourneys | None = None

        resolved_target = (target_scene or "").upper()
        if journey == "act_traversal":
            resolved_target = "NEXT_ACT"
        elif not resolved_target:
            resolved_target = {
                "new_run": "MAP",
                "resume_run": "MAP",
                "first_battle": "COMBAT",
                "finish_interstitials": "MAP",
            }.get(journey, "MAP")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # 修复二运行期双真：启动旅程前再次验证调试能力。traversal（快速结束战斗）
            # 仅在"配置启用 AND 实际探测确认"双真时才真正启用，否则降级为 basic，
            # 避免对不可用的 win_combat 反复空试拖垮任务。
            effective_combat_mode, debug_downgrade = _resolve_combat_mode_with_debug_check(
                adapter, combat_mode, loop
            )
            if debug_downgrade:
                print(
                    json.dumps(
                        {
                            "debug_actions_downgrade": debug_downgrade,
                            "requested_combat_mode": combat_mode,
                            "combat_mode": effective_combat_mode,
                        },
                        ensure_ascii=False,
                    )
                )
            runner = GenericJourneys(
                adapter,
                timeout=timeout,
                target_scene=resolved_target,
                route_policy=route_policy,
                combat_mode=effective_combat_mode,
                progress_callback=publish_progress,
                observation_callback=observation_callback,
                cancel_check=cancel_requested,
            )
            if journey == "new_run":
                # 连续任务场景下上一任务可能把游戏留在战斗/地图残局；开局前先确保
                # 干净主菜单，避免 start_new_run 在 COMBAT 态因无法回菜单而失败。
                ensure_clean_main_menu(adapter, lifecycle, loop)
                result = loop.run_until_complete(runner.start_new_run(character_id))
            elif journey == "resume_run":
                result = loop.run_until_complete(runner.resume_run())
            elif journey == "first_battle":
                # 同上：第二任务（不继承残局）必须从干净主菜单重新开局。
                ensure_clean_main_menu(adapter, lifecycle, loop)
                result = loop.run_until_complete(runner.enter_first_battle(character_id=character_id))
            elif journey == "card_test":
                if not card_id:
                    raise JourneyFailure("card_test 旅程需要 --card-id 参数")
                result = loop.run_until_complete(
                    runner.card_test(character_id, card_id)
                )
            elif journey in {"goal_scene", "act_traversal"} or target_scene:
                result = loop.run_until_complete(
                    runner.execute_target(
                        character_id=character_id,
                        target_scene=resolved_target,
                        route_policy=route_policy,
                        combat_mode=effective_combat_mode,
                    )
                )
            else:
                result = loop.run_until_complete(runner.finish_interstitials())

            duration_ms = int((time.monotonic() - started) * 1000)
            # 终态视觉凭证：API 状态可能先于画面翻页（曾出现第二章 MAP 命名截图
            # 仍显示事件页）。旅程结束后不再有任何操作，延长 settle 让渲染追上，
            # 保证至少一张截图与最终状态一致。凭证失败不影响任务结果。
            capture_final = getattr(evidence, "capture_state", None)
            if callable(capture_final):
                try:
                    stable_final = loop.run_until_complete(
                        _wait_for_stable_api_state(
                            adapter, result, timeout=6.0, interval=0.5, settle=2.5
                        )
                    )
                    safe_case_id = case_id.replace(":", "_")
                    final_screen = str(stable_final.get("screen") or "UNKNOWN").upper()
                    capture_final(
                        f"{safe_case_id}_FINAL_{final_screen}_{int(time.monotonic() * 1000)}",
                        stable_final,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[autotest] WARNING: final-state screenshot failed (non-blocking): {exc}")
            trajectory = list(runner.trajectory)
            journey_evidence = runner.evidence
            journey_evidence["duration_ms"] = duration_ms
            evidence.on_case_end(TestResult(case_id, "pass", json.dumps(result, ensure_ascii=False)))
            _write_journey_evidence(
                evidence_root,
                run_id,
                journey=journey,
                target_scene=resolved_target,
                evidence=journey_evidence,
                duration_ms=duration_ms,
            )
            payload = write_result(
                "PASSED",
                extra={
                    "duration_ms": duration_ms,
                    "status_trajectory": trajectory,
                    "final_state": result.get("screen"),
                    "target_scene": resolved_target,
                    "journey_evidence": journey_evidence,
                    "evidence_dir": str(evidence_root / run_id) if run_id else None,
                    # 阶段 C（issue #37）：重拉计数进报告——真实生产者 lifecycle.relaunch_count。
                    "relaunch_count": (
                        lifecycle.relaunch_count if lifecycle is not None else 0
                    ),
                },
            )
            evidence.on_session_end({
                "total": 1, "passed": 1, "failed": 0, "crashed": 0, "skipped": 0,
                "duration_ms": duration_ms,
            })
            persist_artifact_path()
            _write_journey_evidence(
                evidence_root,
                run_id,
                journey=journey,
                target_scene=resolved_target,
                evidence=journey_evidence,
                duration_ms=duration_ms,
            )
            refresh_artifact = getattr(evidence, "refresh_artifact", None)
            if callable(refresh_artifact):
                refresh_artifact()
            # 松哥 P1 复查：PASSED 收尾后须把证据封存状态回写 RunStore，
            # 否则恢复任务已有完整压缩包但记录仍显示 evidence_sealed=false。
            if run_id:
                try:
                    from sts2_autotest.core.run_service import RunStore

                    RunStore(self._env.resolve_run_root()).update(
                        run_id,
                        evidence_sealed=True,
                        evidence_dir=str(evidence_root / run_id),
                    )
                except (OSError, RuntimeError, ValueError):
                    pass
            print(json.dumps({"journey": journey, "status": "PASSED", "state": result}, ensure_ascii=False))
            return make_outcome("PASSED", 0, payload)
        except JourneyCancelled as exc:
            # 修复三：取消收尾是完整生命周期，不是简单终止。
            # 顺序：停止新操作(已在旅程内停) → 存取消前状态 → 恢复干净主菜单 →
            # 校验 → 写报告 → 封存证据 → 才把任务标为终态 → 释放名额。
            from sts2_autotest.common.errors import CancelFailureReason

            duration_ms = int((time.monotonic() - started) * 1000)
            pre_cancel_state = getattr(exc, "last_state", None)
            last_action = getattr(exc, "last_action", None)
            cleanup_reason: str | None = None
            recovered_state: dict[str, Any] | None = None
            # JourneyCancelled 只可能在 runner 创建后由其执行过程抛出，此处 runner 必非 None。
            assert runner is not None

            # 1) 受控重启恢复到干净主菜单（P1 正式通过方式：结果目标=MAIN_MENU，
            #    不要求 ESC 正常退出）。仅重启一次；失败按 BLOCKED_ENVIRONMENT 归类，
            #    禁止无限重启引发 Steam 弹窗或系统事故。
            recovery: dict[str, Any] = (
                recover_main_menu_via_restart(lifecycle, adapter, loop)
                if lifecycle is not None
                else {
                    "ok": False,
                    "blocked": False,
                    "final_screen": None,
                    "recovery_method": None,
                    "normal_menu_abandon": False,
                    "restart_count": 0,
                    "clean_main_menu": False,
                    "old_run_abandoned": False,
                    "target": "MAIN_MENU",
                    "reason": "lifecycle unavailable",
                    "final_state": None,
                }
            )
            # P1-1：报告保留恢复后完整状态快照（含 has_run_save/可执行操作/时间戳），
            # 审计可独立核对，而非只能相信平台自己计算的 clean_main_menu 布尔值。
            recovered_state = recovery.get("final_state") or {
                "screen": recovery.get("final_screen")
            }
            if recovery.get("blocked"):
                cleanup_reason = CancelFailureReason.GAME_CONTROL_UNAVAILABLE.value
            elif recovery.get("ok"):
                cleanup_reason = None
            else:
                cleanup_reason = CancelFailureReason.CANCEL_CLEANUP_FAILED.value

            # 终态判定：受控重启失败→BLOCKED_ENVIRONMENT；清理失败→FAILED_PLATFORM；
            # 干净恢复→CANCELLED。report 必须如实写 recovery_method=controlled_restart，
            # 不得写成 normal_game_menu（正常 ESC 放弃已降级为后续增强，不阻塞 P1）。
            if cleanup_reason is None:
                terminal_status = "CANCELLED"
            elif str(cleanup_reason) == CancelFailureReason.GAME_CONTROL_UNAVAILABLE.value:
                terminal_status = "BLOCKED_ENVIRONMENT"
            else:
                terminal_status = "FAILED_PLATFORM"

            trajectory = list(runner.trajectory) if runner is not None else []
            journey_evidence = runner.evidence if runner is not None else {}
            journey_evidence["duration_ms"] = duration_ms

            # 2) 写取消报告 + 落盘证据 + 封存。证据封存失败单独归类。
            sealed = False
            cancel_result = _build_cancel_result(
                duration_ms=duration_ms,
                trajectory=trajectory,
                pre_cancel_state=pre_cancel_state,
                recovered_state=recovered_state,
                last_action=last_action,
                journey_evidence=journey_evidence,
                evidence_dir=str(evidence_root / run_id) if run_id else None,
                recovery=recovery,
                lifecycle=lifecycle,
            )
            # V11 复核要点：最终截图必须在恢复收尾（干净确认）之后采集，
            # 与报告中的 final_state 互为独立证据，随证据包一并封存。
            try:
                capture_state = getattr(evidence, "capture_state", None)
                if callable(capture_state) and recovery.get("final_state") is not None:
                    capture_state(
                        f"{case_id}_recovery_final".replace(":", "_"),
                        recovery["final_state"],
                    )
            except Exception as shot_exc:  # noqa: BLE001
                print(f"[autotest] recovery final screenshot failed: {shot_exc}")
            payload = {}
            try:
                evidence.on_case_end(TestResult(case_id, "skip", "cancelled"))
                # P0-1：先落盘 run-result.json，再生成证据清单，保证清单统计时文件已存在。
                payload = write_result(
                    terminal_status,
                    message="Run cancelled by request",
                    extra=cancel_result,
                )
                # 第一次清单：run-result.json 已存在，summary.md/summary.json 随后由
                # on_session_end 落盘；此处清单的 missing_files 可能仍含这两者，但会在
                # 下方「最终清单」重算时消除。
                _write_journey_evidence(
                    evidence_root,
                    run_id,
                    journey=journey,
                    target_scene=resolved_target,
                    evidence=journey_evidence,
                    duration_ms=duration_ms,
                )
                evidence.on_session_end({
                    "total": 1, "passed": 0, "failed": 0, "crashed": 0, "skipped": 1,
                    "duration_ms": duration_ms,
                    "status": terminal_status,
                })
                persist_artifact_path()
                # 最终清单：此时 run-result.json 已含 artifact_path、压缩包已生成，
                # archive_counts 可校验、missing_files 应为空。
                _write_journey_evidence(
                    evidence_root,
                    run_id,
                    journey=journey,
                    target_scene=resolved_target,
                    evidence=journey_evidence,
                    duration_ms=duration_ms,
                )
                refresh_artifact = getattr(evidence, "refresh_artifact", None)
                if callable(refresh_artifact):
                    # 最终封存：必须排在「最终清单生成」之后，确保压缩包内的证据清单
                    # 与落盘版本完全一致（修复 P0：压缩包内清单声称文件缺失的矛盾）。
                    refresh_artifact()
                sealed = True
            except Exception as evi_exc:  # noqa: BLE001
                if cleanup_reason is None:
                    cleanup_reason = CancelFailureReason.CANCEL_EVIDENCE_FAILED.value
                print(f"[autotest] cancel cleanup (evidence sealing) failed: {evi_exc}")

            # 3) 只有收尾全部走完，才把任务标为终态并释放名额。
            if run_id:
                try:
                    from sts2_autotest.core.run_service import RunStore

                    RunStore(self._env.resolve_run_root()).finish_cancel(
                        run_id,
                        reason=cleanup_reason,
                        evidence_dir=str(evidence_root / run_id),
                        sealed=sealed,
                        result=cancel_result,
                    )
                except (OSError, RuntimeError, ValueError) as store_exc:
                    print(f"[autotest] finish_cancel persist failed: {store_exc}")
            print(json.dumps(
                {"journey": journey, "status": terminal_status, "reason": cleanup_reason},
                ensure_ascii=False,
            ))
            return make_outcome(terminal_status, 0 if cleanup_reason is None else 1, payload)
        except (STS2Error, JourneyFailure) as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            last_state = getattr(exc, "last_state", None)
            last_action = getattr(exc, "last_action", None)
            trajectory = list(runner.trajectory) if runner is not None else []
            stuck_screen = (
                str((last_state or {}).get("screen"))
                if isinstance(last_state, dict) else None
            )
            failure = {
                "stuck_screen": stuck_screen,
                "last_action": last_action,
                "last_state": last_state,
                "reason_code": getattr(exc, "reason_code", None),
                "reason": str(exc),
                "status_trajectory": trajectory,
            }
            # 通用旅程的导航、动作、证据和目标达成失败均属于平台执行失败；
            # 只有项目断言才允许归类为 FAILED_PRODUCT。
            environment_signals = (
                "connection refused",
                "connection error",
                "game not running",
                "mod not loaded",
                "cannot connect",
            )
            is_environment_blocked = isinstance(exc, STS2Error) and any(
                signal in str(exc).lower() for signal in environment_signals
            )
            status = "BLOCKED_ENVIRONMENT" if is_environment_blocked else "FAILED_PLATFORM"
            journey_evidence = runner.evidence if runner is not None else {}
            journey_evidence["duration_ms"] = duration_ms
            evidence.on_crash(case_id, exc)
            evidence.on_case_end(TestResult(case_id, "crash", str(exc)))
            _write_journey_evidence(
                evidence_root,
                run_id,
                journey=journey,
                target_scene=resolved_target,
                evidence=journey_evidence,
                duration_ms=duration_ms,
            )
            payload = write_result(
                status,
                message=str(exc),
                extra={
                    "duration_ms": duration_ms,
                    "status_trajectory": trajectory,
                    "failure": failure,
                    "target_scene": resolved_target,
                    "journey_evidence": journey_evidence,
                    "evidence_dir": str(evidence_root / run_id) if run_id else None,
                },
            )
            if run_id:
                _write_journey_failure(evidence_root / run_id, journey=journey, failure=failure, duration_ms=duration_ms)
            evidence.on_session_end({
                "total": 1, "passed": 0, "failed": 0, "crashed": 1, "skipped": 0,
                "duration_ms": duration_ms,
                "status": status,
            })
            persist_artifact_path()
            _write_journey_evidence(
                evidence_root,
                run_id,
                journey=journey,
                target_scene=resolved_target,
                evidence=journey_evidence,
                duration_ms=duration_ms,
            )
            refresh_artifact = getattr(evidence, "refresh_artifact", None)
            if callable(refresh_artifact):
                refresh_artifact()
            print(json.dumps({"journey": journey, "status": status, "message": str(exc)}, ensure_ascii=False))
            return make_outcome(status, 1, payload)
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.monotonic() - started) * 1000)
            evidence.on_crash(case_id, exc)
            evidence.on_case_end(TestResult(case_id, "fail", str(exc)))
            payload = write_result(
                "FAILED_PRODUCT",
                message=str(exc),
                extra={
                    "duration_ms": duration_ms,
                    "evidence_dir": str(evidence_root / run_id) if run_id else None,
                },
            )
            evidence.on_session_end({
                "total": 1, "passed": 0, "failed": 1, "crashed": 0, "skipped": 0,
                "duration_ms": duration_ms,
            })
            print(json.dumps({"journey": journey, "status": "FAILED_PRODUCT", "message": str(exc)}, ensure_ascii=False))
            return make_outcome("FAILED_PRODUCT", 1, payload)
        finally:
            try:
                loop.run_until_complete(adapter.cleanup())
            except Exception:  # noqa: BLE001
                pass
            loop.close()
            # P0-3：无论任务以何种方式结束，都释放防睡眠守护，避免留下孤儿 caffeinate。
            try:
                anti_sleep.stop()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# worker：子进程全流程 + 进程入口
# ---------------------------------------------------------------------------


def run_journey_worker(
    adapter_factory: Callable[[], GameAdapterProtocol],
    request: JourneyRequest,
    run_id: str,
    *,
    env: ExecutorEnv | None = None,
) -> int:
    """在 worker 子进程里执行一个已入队旅程任务直到记录收口。

    封装原 run_cmd 的 worker 事务：wait_for_turn 排队 → PRECHECK→PREPARING→
    STARTING→RUNNING 阶段位 → 执行 → 终态保护（取消终态不被覆盖）→
    COLLECTING → 读 run-result.json → complete_record。

    顺序约束：run_id 必须已由提交方 store.create 后存在；同一 run_id 同时
    只能有一个执行者（由队列互斥保证）。
    """
    from sts2_autotest.core.run_service import (
        RunCancelled,
        RunStore,
        complete_record,
        wait_for_turn,
    )

    resolved_env = env or ExecutorEnv()
    store = RunStore(resolved_env.resolve_run_root())
    try:
        wait_for_turn(store, run_id, timeout=request.timeout * 10)
        for phase in ("PRECHECK", "PREPARING", "STARTING", "RUNNING"):
            store.update(run_id, phase=phase, status=phase)
        adapter = adapter_factory()
        outcome = JourneyExecutor(
            adapter, request, run_id=run_id, env=resolved_env
        ).execute()
        rc = outcome.exit_code
        # 若旅程内部已处理取消并把任务标为终态（finish_cancel），不要用
        # complete_record 覆盖它——取消收尾的终态与原因码必须保留。
        finalized = store.load(run_id)
        if finalized is not None and finalized.is_terminal:
            return rc
        store.update(run_id, phase="COLLECTING", status="COLLECTING")
        evidence_root = resolved_env.resolve_evidence_root()
        run_evidence_dir = evidence_root / run_id
        result_payload: dict[str, Any] = {}
        result_path = run_evidence_dir / "reports" / "run-result.json"
        if result_path.is_file():
            try:
                loaded = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    result_payload = loaded
            except (OSError, ValueError):
                pass
        complete_record(
            store,
            run_id,
            exit_code=rc,
            result=compact_persistent_result(result_payload, run_evidence_dir),
            evidence_dir=str(run_evidence_dir if run_evidence_dir.exists() else evidence_root),
        )
        return rc
    except RunCancelled:
        return 1
    except Exception as exc:  # noqa: BLE001
        complete_record(
            store,
            run_id,
            exit_code=1,
            message=str(exc),
        )
        print(f"[autotest] persistent run failed: {exc}")
        return 1


def add_journey_arguments(parser: Any) -> None:
    """注册旅程旗标——CLI ``run`` 子命令与 worker 入口共用的唯一定义。"""
    parser.add_argument(
        "--journey",
        choices=[
            "new_run", "resume_run", "first_battle", "finish_interstitials",
            "goal_scene", "act_traversal", "card_test",
        ],
        default=None,
        help="Run one reusable game journey instead of a project case suite",
    )
    parser.add_argument(
        "--character-id",
        default="IRONCLAD",
        help="Character for new_run/first_battle",
    )
    parser.add_argument(
        "--card-id",
        default=None,
        help="card_test 旅程要验证的卡牌 ID（运行时控制台格式）",
    )
    parser.add_argument(
        "--target-scene",
        choices=[
            "MAIN_MENU", "CHARACTER_SELECT", "MAP", "EVENT", "COMBAT",
            "REST", "SHOP", "CHEST", "CARD_REWARD", "NEXT_ACT",
        ],
        default=None,
        help="目标场景；act_traversal 默认使用 NEXT_ACT",
    )
    parser.add_argument(
        "--route-policy",
        choices=["leftmost", "target"],
        default="leftmost",
        help="地图路线规则",
    )
    parser.add_argument(
        "--combat-mode",
        choices=["traversal", "basic", "death"],
        default="traversal",
        help="战斗处理规则",
    )


def worker_main(argv: list[str]) -> int:
    """worker 子进程入口：解析既有旅程旗标协议并执行到收口。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m sts2_autotest.core.run_executor",
        description="Journey run worker (spawned by the persistent run service)",
    )
    parser.add_argument("--internal-run-id", required=True, help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=int, default=30, help="Case timeout (seconds)")
    parser.add_argument("--adapter", choices=["cli", "agent"], default=None, help=argparse.SUPPRESS)
    parser.add_argument("--project", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--evidence", choices=["none", "minimal", "full"], default="full",
        help=argparse.SUPPRESS,
    )
    add_journey_arguments(parser)
    args = parser.parse_args(argv)
    if not (args.journey or args.target_scene):
        print("[autotest] run_executor worker requires --journey or --target-scene")
        return 2
    if args.evidence:
        os.environ["STS2_AUTOTEST_EVIDENCE"] = str(args.evidence)
    from sts2_autotest.core.runtime_factory import create_adapter_from_env, is_agent_default

    adapter_type: str = args.adapter or ("agent" if is_agent_default() else "cli")
    project: str | None = args.project
    request = JourneyRequest(
        journey=args.journey,
        character_id=args.character_id,
        timeout=float(args.timeout),
        target_scene=args.target_scene,
        route_policy=args.route_policy,
        combat_mode=args.combat_mode,
        card_id=args.card_id,
        precheck=True,
    )
    return run_journey_worker(
        lambda: create_adapter_from_env(adapter_type, project=project),
        request,
        args.internal_run_id,
    )


if __name__ == "__main__":
    sys.exit(worker_main(sys.argv[1:]))
