"""可复用的游戏旅程。

旅程只描述游戏层面的目标，不包含任何项目角色、卡牌或数值断言。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sts2_autotest.adapters.base import GameAdapterProtocol
from sts2_autotest.core.navigation import NavigationBlocked, progress_until


class JourneyFailure(RuntimeError):
    """旅程无法到达目标，调用方应记录凭证并交给恢复策略。"""

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


StatePredicate = Callable[[dict[str, Any]], bool]
ProgressCallback = Callable[[dict[str, Any]], None]
ObservationCallback = Callable[[dict[str, Any]], None]

TARGET_SCENES = frozenset({
    "MAIN_MENU", "CHARACTER_SELECT", "MAP", "EVENT", "COMBAT", "REST",
    "SHOP", "CHEST", "CARD_REWARD", "NEXT_ACT",
})


def _extract_chapter(state: dict[str, Any]) -> int | None:
    """从通用运行状态中读取章节编号，不依赖角色或 Mod 字段。"""
    candidates: list[tuple[str, Any]] = []
    for block in (state, state.get("run") or {}, state.get("map") or {}):
        if isinstance(block, dict):
            for key in (
                "chapter", "act", "act_number", "act_index", "act_id", "current_act"
            ):
                candidates.append((key, block.get(key)))
    for key, value in candidates:
        if isinstance(value, int):
            return value + 1 if key in {"act_index", "act_id"} else (value + 1 if value == 0 else value)
        if isinstance(value, str):
            digits = "".join(char for char in value if char.isdigit())
            if digits:
                number = int(digits)
                return number + 1 if key in {"act_index", "act_id"} else number
    return None


def _extract_floor(state: dict[str, Any]) -> int | None:
    map_block = state.get("map") or {}
    for block in (map_block, map_block.get("current_node") or {}, state.get("run") or {}):
        if not isinstance(block, dict):
            continue
        for key in ("floor", "row", "y", "current_floor"):
            value = block.get(key)
            if isinstance(value, int):
                return value
    return None


def _fingerprint(state: dict[str, Any]) -> str:
    volatile = {"state_version", "request_id", "timestamp", "updated_at"}
    return json.dumps(
        {key: value for key, value in state.items() if key not in volatile},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


class GenericJourneys:
    """面向目标的通用游戏流程。"""

    def __init__(
        self,
        adapter: GameAdapterProtocol,
        *,
        timeout: float = 60.0,
        target_scene: str = "MAP",
        route_policy: str = "leftmost",
        combat_mode: str = "traversal",
        progress_callback: ProgressCallback | None = None,
        observation_callback: ObservationCallback | None = None,
    ) -> None:
        self.adapter = adapter
        self.timeout = timeout
        self.target_scene = target_scene.upper()
        self.route_policy = route_policy
        self.combat_mode = combat_mode
        self._progress_callback = progress_callback
        self._observation_callback = observation_callback
        self._trajectory: list[str] = []
        self._scene_trace: list[dict[str, Any]] = []
        self._operations: list[dict[str, Any]] = []
        self._map_route: list[dict[str, Any]] = []
        self._rooms: list[str] = []
        self._last_snapshot: dict[str, Any] | None = None
        self._last_action: str | None = None
        self._last_observed_change: str | None = None
        self._steps = 0
        self._started = time.monotonic()

    async def snapshot(self) -> dict[str, Any]:
        state = await self.adapter.get_state()
        payload = state.model_dump()
        payload["available_actions"] = await self.adapter.get_available_actions()
        screen = str(payload.get("screen") or "")
        if not self._trajectory or self._trajectory[-1] != screen:
            self._trajectory.append(screen)
            if screen and screen not in {"MAIN_MENU", "CHARACTER_SELECT", "MAP"}:
                if screen not in self._rooms:
                    self._rooms.append(screen)
        if self._last_snapshot is None or _fingerprint(self._last_snapshot) != _fingerprint(payload):
            self._last_observed_change = screen or "UNKNOWN"
            self._scene_trace.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "screen": screen,
                "state": payload,
            })
            if self._observation_callback is not None:
                self._observation_callback(payload)
        self._last_snapshot = payload
        self._publish_progress()
        return payload

    @property
    def trajectory(self) -> list[str]:
        """本次旅程经历过的页面轨迹（相邻重复已合并）。"""
        return list(self._trajectory)

    @property
    def evidence(self) -> dict[str, Any]:
        return {
            "scene_trajectory": list(self._trajectory),
            "scene_trace": list(self._scene_trace),
            "operations": list(self._operations),
            "map_route": list(self._map_route),
            "rooms": list(self._rooms),
            "duration_ms": int((time.monotonic() - self._started) * 1000),
        }

    def _publish_progress(self) -> None:
        if self._progress_callback is None:
            return
        state = self._last_snapshot or {}
        payload = {
            "current_chapter": _extract_chapter(state),
            "current_floor": _extract_floor(state),
            "current_screen": str(state.get("screen") or "UNKNOWN"),
            "target_scene": self.target_scene,
            "route_policy": self.route_policy,
            "combat_mode": self.combat_mode,
            "rooms_processed": len(self._rooms),
            "room_types": list(self._rooms),
            "last_action": self._last_action,
            "last_observed_change": self._last_observed_change,
            "steps": self._steps,
            "recovering": False,
            "elapsed_ms": int((time.monotonic() - self._started) * 1000),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._progress_callback(payload)

    async def _execute_action(self, action: str, params: dict[str, Any] | None = None) -> Any:
        before = self._last_snapshot or await self.snapshot()
        started = time.monotonic()
        result = await self.adapter.act(action, params or {})
        self._steps += 1
        self._last_action = action
        after = await self.snapshot()
        self._operations.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "params": params or {},
            "status": getattr(result, "status", "unknown"),
            "detail": getattr(result, "detail", None),
            "before_screen": before.get("screen"),
            "after_screen": after.get("screen"),
            "before_state": before,
            "after_state": after,
            "duration_ms": int((time.monotonic() - started) * 1000),
        })
        if action == "choose_map_node":
            map_before = before.get("map") or {}
            map_after = after.get("map") or {}
            self._map_route.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "before_coordinate": map_before.get("current_node") or map_before.get("current_coordinate"),
                "available_nodes": map_before.get("available_nodes") or map_before.get("travelable_coords") or [],
                "selected": params or {},
                "after_coordinate": map_after.get("current_node") or map_after.get("current_coordinate"),
                "entered_screen": after.get("screen"),
                "traveling": map_after.get("is_traveling"),
            })
        self._publish_progress()
        return result

    async def _act_confirmed(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        before = await self.snapshot()
        available = list(before.get("available_actions") or [])
        if action not in available:
            raise JourneyFailure(
                f"Action {action!r} is not available on {before.get('screen')}: {available}"
            )
        result = await self._execute_action(action, params)
        if getattr(result, "status", "failure") != "success":
            raise JourneyFailure(f"Action {action!r} failed: {getattr(result, 'detail', '')}")
        after = self._last_snapshot or await self.snapshot()
        if json.dumps(before, sort_keys=True, ensure_ascii=False) == json.dumps(
            after, sort_keys=True, ensure_ascii=False
        ):
            raise JourneyFailure(
                f"Action {action!r} reported success but produced no observable state change"
            )
        return after

    @staticmethod
    def _map_is_stable(state: dict[str, Any]) -> bool:
        map_block = state.get("map") or {}
        return not bool(map_block.get("is_traveling"))

    async def reset_to_main_menu(self) -> dict[str, Any]:
        """清理可处理的残留页面并回到主菜单。"""
        for _ in range(8):
            state = await self.snapshot()
            screen = str(state.get("screen") or "").upper()
            if screen == "MAIN_MENU":
                return state
            actions = list(state.get("available_actions") or [])
            return_action = next(
                (
                    name
                    for name in ("return_to_menu", "return_to_main_menu")
                    if name in actions
                ),
                None,
            )
            if return_action is not None:
                await self._act_confirmed(return_action)
                continue
            if "abandon_run" in actions:
                await self._act_confirmed("abandon_run")
                continue
            if "confirm_modal" in actions:
                await self._act_confirmed("confirm_modal")
                continue
            if "close_main_menu_submenu" in actions:
                await self._act_confirmed("close_main_menu_submenu")
                continue
            if screen == "CARD_REWARD":
                reward_action = next(
                    (
                        name
                        for name in (
                            "collect_rewards_and_proceed",
                            "resolve_rewards",
                            "proceed",
                        )
                        if name in actions
                    ),
                    None,
                )
                if reward_action is not None:
                    await self._act_confirmed(reward_action)
                    continue
            if screen == "COMBAT":
                # 战斗页通常只公开出牌/结束回合；复位是独立的安全恢复动作，
                # 直接请求放弃旧局并在下一轮确认回到主菜单。
                result = await self._execute_action("abandon_run")
                if getattr(result, "status", "failure") != "success":
                    raise JourneyFailure(
                        "Cannot abandon COMBAT run: "
                        f"{getattr(result, 'detail', '')}"
                    )
                continue
            capabilities = getattr(self.adapter, "capabilities", None)
            if bool(getattr(capabilities, "supports_debug_actions", False)):
                result = await self._execute_action("abandon_run")
                if getattr(result, "status", "failure") == "success":
                    continue
            raise JourneyFailure(f"Cannot reset {screen} to MAIN_MENU: {actions}")
        raise JourneyFailure("Reset to MAIN_MENU exceeded the safe step limit")

    async def resolve_until(
        self,
        target_screen: str,
        *,
        timeout: float | None = None,
        arrival_predicate: StatePredicate | None = None,
    ) -> dict[str, Any]:
        """自动处理事件、卡包、选牌、奖励和地图推进直到目标状态。"""
        try:
            return await progress_until(
                get_state=self.snapshot,
                act=self._execute_action,
                target_screen=target_screen,
                timeout=timeout or self.timeout,
                arrival_predicate=arrival_predicate,
                route_policy=self.route_policy,
                combat_mode=self.combat_mode,
                recover=self._recover_connection,
            )
        except NavigationBlocked as exc:
            screen = str((exc.last_state or {}).get("screen") or "UNKNOWN")
            raise JourneyFailure(
                f"无法到达目标页面 {target_screen}：卡在 {screen}，"
                f"最近一次操作={exc.last_action or '无'}，原因={exc}",
                last_state=exc.last_state,
                last_action=exc.last_action,
                reason_code=exc.reason_code,
            ) from exc

    async def _recover_connection(self) -> bool:
        try:
            await self.adapter.cleanup()
            return (await self.adapter.health_check()).healthy
        except Exception:
            return False

    async def start_new_run(
        self,
        character_id: str,
        *,
        target_screen: str = "MAP",
    ) -> dict[str, Any]:
        """从主菜单创建新局，并按目标停在开局场景或稳定地图。"""
        target_screen = target_screen.upper()
        state = await self.reset_to_main_menu()
        if target_screen == "MAIN_MENU":
            return state
        actions = list(state.get("available_actions") or [])
        start_action = next(
            (name for name in ("start_new_run", "new_run", "open_character_select") if name in actions),
            None,
        )
        if start_action is None:
            raise JourneyFailure(f"New run is unavailable on MAIN_MENU: {actions}")
        await self._act_confirmed(start_action)

        state = await self.snapshot()
        if str(state.get("screen") or "").upper() != "CHARACTER_SELECT":
            raise JourneyFailure(
                f"创建新局后未进入角色选择：当前页面={state.get('screen')}",
                last_state=state,
                last_action=start_action,
            )
        if target_screen == "CHARACTER_SELECT":
            return state

        state = await self.snapshot()
        character_select = state.get("character_select") or {}
        selected_character = (
            character_select.get("selected_character_id")
            or character_select.get("selected_character")
            or character_select.get("selected")
        ) if isinstance(character_select, dict) else None
        if str(selected_character or "").upper() != character_id.upper():
            await self._act_confirmed("select_character", {"character_id": character_id})
            state = await self.snapshot()
        if "embark" in list(state.get("available_actions") or []):
            await self._act_confirmed("embark")

        if target_screen == "EVENT":
            return await self.resolve_until("EVENT", timeout=self.timeout)

        return await self.resolve_until(
            target_screen,
            arrival_predicate=self._map_is_stable,
        )

    async def execute_target(
        self,
        *,
        character_id: str,
        target_scene: str,
        route_policy: str = "leftmost",
        combat_mode: str = "traversal",
    ) -> dict[str, Any]:
        """统一目标场景执行器；整章遍历只是 NEXT_ACT 的一种目标。"""
        target = target_scene.upper()
        if target not in TARGET_SCENES:
            raise JourneyFailure(f"不支持的目标场景：{target_scene}")
        self.target_scene = target
        self.route_policy = route_policy
        self.combat_mode = combat_mode

        if target in {"MAIN_MENU", "CHARACTER_SELECT", "EVENT", "MAP"}:
            return await self.start_new_run(
                character_id,
                target_screen=target,
            )
        if target == "NEXT_ACT":
            initial = await self.start_new_run(character_id, target_screen="MAP")
            initial_chapter = _extract_chapter(initial)
            if initial_chapter is None:
                raise JourneyFailure(
                    "无法读取第一章章节编号，不能证明已进入下一章",
                    last_state=initial,
                    last_action=None,
                )
            return await self.resolve_until(
                "MAP",
                arrival_predicate=lambda state: (
                    self._map_is_stable(state)
                    and (_extract_chapter(state) or 0) > initial_chapter
                ),
                timeout=self.timeout,
            )

        return await self.start_new_run(character_id, target_screen=target)

    async def resume_run(self) -> dict[str, Any]:
        """从主菜单恢复已有局，并确认进入可继续操作的状态。"""
        current = await self.snapshot()
        current_screen = str(current.get("screen") or "").upper()
        if current_screen in {"MAP", "COMBAT"}:
            return current
        if current_screen not in {"MAIN_MENU", "UNKNOWN", ""}:
            try:
                return await self.resolve_until("MAP", arrival_predicate=self._map_is_stable)
            except JourneyFailure:
                pass
        state = await self.reset_to_main_menu()
        actions = list(state.get("available_actions") or [])
        if "continue_run" not in actions:
            raise JourneyFailure(f"No resumable run is available: {actions}")
        await self._act_confirmed("continue_run")
        return await self.resolve_until("MAP", arrival_predicate=self._map_is_stable)

    async def enter_first_battle(self, *, character_id: str) -> dict[str, Any]:
        """创建新局并到达第一场真实战斗。"""
        self._trajectory = []
        await self.start_new_run(character_id)
        return await self.resolve_until("COMBAT")

    async def finish_interstitials(self) -> dict[str, Any]:
        """处理当前事件、卡包或战后奖励，直到稳定回到地图。"""
        return await self.resolve_until("MAP", arrival_predicate=self._map_is_stable)
