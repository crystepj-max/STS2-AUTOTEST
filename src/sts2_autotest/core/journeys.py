"""可复用的游戏旅程。

旅程只描述游戏层面的目标，不包含任何项目角色、卡牌或数值断言。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sts2_autotest.adapters.base import GameAdapterProtocol
from sts2_autotest.core.navigation import NavigationBlocked, progress_until


class JourneyFailure(RuntimeError):
    """旅程无法到达目标，调用方应记录凭证并交给恢复策略。"""


StatePredicate = Callable[[dict[str, Any]], bool]


class GenericJourneys:
    """面向目标的通用游戏流程。"""

    def __init__(self, adapter: GameAdapterProtocol, *, timeout: float = 60.0) -> None:
        self.adapter = adapter
        self.timeout = timeout

    async def snapshot(self) -> dict[str, Any]:
        state = await self.adapter.get_state()
        payload = state.model_dump()
        payload["available_actions"] = await self.adapter.get_available_actions()
        return payload

    async def _act_confirmed(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        before = await self.snapshot()
        available = list(before.get("available_actions") or [])
        if action not in available:
            raise JourneyFailure(
                f"Action {action!r} is not available on {before.get('screen')}: {available}"
            )
        result = await self.adapter.act(action, params or {})
        if getattr(result, "status", "failure") != "success":
            raise JourneyFailure(f"Action {action!r} failed: {getattr(result, 'detail', '')}")
        after = await self.snapshot()
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
            if "return_to_menu" in actions:
                await self._act_confirmed("return_to_menu")
                continue
            if "abandon_run" in actions:
                await self._act_confirmed("abandon_run")
                continue
            if "confirm_modal" in actions:
                await self._act_confirmed("confirm_modal")
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
                act=lambda action, params: self.adapter.act(action, params),
                target_screen=target_screen,
                timeout=timeout or self.timeout,
                arrival_predicate=arrival_predicate,
            )
        except NavigationBlocked as exc:
            raise JourneyFailure(str(exc)) from exc

    async def start_new_run(self, character_id: str) -> dict[str, Any]:
        """从主菜单创建新局并推进到稳定地图。"""
        state = await self.reset_to_main_menu()
        actions = list(state.get("available_actions") or [])
        if "start_new_run" in actions:
            await self._act_confirmed("start_new_run")
        elif "open_character_select" in actions:
            await self._act_confirmed("open_character_select")
        else:
            raise JourneyFailure(f"New run is unavailable on MAIN_MENU: {actions}")

        state = await self.snapshot()
        if str(state.get("screen") or "").upper() == "CHARACTER_SELECT":
            await self._act_confirmed("select_character", {"character_id": character_id})
            state = await self.snapshot()
            if "embark" in list(state.get("available_actions") or []):
                await self._act_confirmed("embark")

        return await self.resolve_until(
            "MAP",
            arrival_predicate=self._map_is_stable,
        )

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
        await self.start_new_run(character_id)
        return await self.resolve_until("COMBAT")

    async def finish_interstitials(self) -> dict[str, Any]:
        """处理当前事件、卡包或战后奖励，直到稳定回到地图。"""
        return await self.resolve_until("MAP", arrival_predicate=self._map_is_stable)
