"""干净主菜单判定核心（纯函数）。

历史上这段判定只存在于 run_executor（取消收尾/开工前的受控重启路径），
journeys.reset_to_main_menu 与 orchestrator._auto_reset_to_main_menu 各自
只用动作列表判断——V10/V11 真实验收证明动作列表在菜单重建期会摆出陈旧项，
``has_run_save`` 三态内省才是权威旧局信号（LOC-005 决策 B：恢复方式保留
场景差异，判定口径统一到这一份）。

判定语义（与 CONTEXT.md「干净主菜单」词条一致）：
- ``menu_has_run_save_field``：三态 True=有旧局 / False=明确无 / None=未发布；
- ``frame_dirty``：内省字段优先，缺失时退回 continue_run 动作；
- ``frame_clean``：主菜单 + 无旧局 + 有开新局能力。
"""

from __future__ import annotations

from typing import Any

# 游戏主菜单的开新局能力可能使用其中任一动作名（与 journeys.start_new_run 一致）。
NEW_RUN_ACTIONS = ("start_new_run", "new_run", "open_character_select")


def screen_of(state: Any) -> str | None:
    """Extract the normalized screen name from a state dict or GameState."""
    if state is None:
        return None
    if isinstance(state, dict):
        scr = state.get("screen")
    else:
        scr = getattr(state, "screen", None)
    return str(scr).upper() if scr is not None else None


def state_view(state: Any) -> dict[str, Any]:
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


def menu_has_run_save_field(view: dict[str, Any]) -> bool | None:
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


def menu_actions(view: dict[str, Any]) -> list[str]:
    return [str(action) for action in (view.get("available_actions") or [])]


def frame_dirty(view: dict[str, Any], actions: list[str]) -> bool:
    """该帧是否存在旧局：内省字段优先，字段缺失时退回动作列表。"""
    has_save = menu_has_run_save_field(view)
    if has_save is not None:
        return has_save
    return "continue_run" in actions


def frame_clean(view: dict[str, Any], actions: list[str]) -> bool:
    """该帧是否满足干净主菜单：无旧局 + 存在开新局能力。

    has_run_save 显式 False 时忽略动作列表中的陈旧/静态项（V11 实测：
    Agent 菜单重建期会摆出陈旧 continue/abandon；CliMod 动作列表为静态
    派生，同样不代表真实旧局）。
    """
    if screen_of(view) != "MAIN_MENU":
        return False
    has_save = menu_has_run_save_field(view)
    if has_save is True:
        return False
    if not any(name in actions for name in NEW_RUN_ACTIONS):
        return False
    if has_save is False:
        return True
    return "continue_run" not in actions and "abandon_run" not in actions
