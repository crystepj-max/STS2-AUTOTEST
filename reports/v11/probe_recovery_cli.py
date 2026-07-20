#!/usr/bin/env python3
"""现场探针 #3（CliMod 生产路径）：建局 → 受控重启恢复 → 验证干净主菜单。

步骤：
1. 用 CliModAdapter 创建真实对局（start_new_run → embark → 等到局内页面）。
2. 直接调用平台 _recover_main_menu_via_restart（生产代码），逐帧打印判定。
3. 校验：restart_count=1、放弃真实执行、最终干净。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.cli import main as cli_main
from sts2_autotest.core.runtime_factory import build_lifecycle_manager
from sts2_autotest.core.steam import SteamController


def _wrap(adapter):
    original_state = adapter.get_state
    original_act = adapter.act
    original_actions = adapter.get_available_actions

    async def traced_state():
        state = await original_state()
        view = cli_main._state_view(state)
        print(
            f"  [帧 {time.strftime('%H:%M:%S')}] screen={cli_main._screen_of(state)} "
            f"has_save={cli_main._menu_has_run_save_field(view)} "
            f"embedded_actions={cli_main._menu_actions(view)[:4]}",
            flush=True,
        )
        return state

    async def traced_actions():
        actions = await original_actions()
        print(f"  [协议动作 {time.strftime('%H:%M:%S')}] {actions[:6]}", flush=True)
        return actions

    async def traced_act(action, args=None):
        print(f"  [act {time.strftime('%H:%M:%S')}] {action}", flush=True)
        result = await original_act(action, args)
        print(f"    → status={result.status}", flush=True)
        return result

    adapter.get_state = traced_state  # type: ignore[method-assign]
    adapter.act = traced_act  # type: ignore[method-assign]
    adapter.get_available_actions = traced_actions  # type: ignore[method-assign]


async def _create_run(adapter: CliModAdapter) -> None:
    print("=== 步骤1: 创建真实对局 ===", flush=True)
    result = await adapter.act("start_new_run")
    print(f"start_new_run → {result.status}", flush=True)
    for _ in range(30):
        state = await adapter.get_state()
        screen = str(state.screen).upper()
        if screen in {"EVENT", "MAP", "COMBAT"}:
            print(f"已进入局内: {screen}", flush=True)
            return
        if screen == "CHARACTER_SELECT":
            await adapter.act("embark")
        await asyncio.sleep(1.0)
    raise RuntimeError("未能在 30s 内进入局内")


def main() -> int:
    adapter = CliModAdapter()
    _wrap(adapter)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_create_run(adapter))

        print("=== 步骤2: _recover_main_menu_via_restart（CliMod 生产路径） ===", flush=True)
        steam = SteamController(startup_timeout=60.0)
        lifecycle = build_lifecycle_manager(adapter, steam, Path("tests/output"))
        result = cli_main._recover_main_menu_via_restart(lifecycle, adapter, loop)
        print("=== 结果 ===", flush=True)
        for key, value in result.items():
            print(f"  {key}: {value}", flush=True)
        return 0 if result.get("ok") else 1
    finally:
        loop.close()


if __name__ == "__main__":
    sys.exit(main())
