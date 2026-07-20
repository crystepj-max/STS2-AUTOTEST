#!/usr/bin/env python3
"""一次性现场探针：用生产代码路径完整执行一次取消恢复，逐步输出判定依据。

用途：复现 v11e/v11f 开局前清理/取消收尾中「abandon 不生效」的问题。
直接调用平台自己的 _recover_main_menu_via_restart / _ensure_clean_main_menu，
打印 settle 每帧的判定细节。探针会结束当前游戏进程并重新启动一次。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from sts2_autotest.adapters.agent import AgentAdapter
from sts2_autotest.cli import main as cli_main
from sts2_autotest.core.runtime_factory import build_lifecycle_manager
from sts2_autotest.core.steam import SteamController


def _wrap_get_state(adapter, loop):
    original = adapter.get_state

    async def traced():
        state = await original()
        view = cli_main._state_view(state)
        actions = cli_main._menu_actions(view)
        print(
            f"  [帧 {time.strftime('%H:%M:%S')}] screen={cli_main._screen_of(state)} "
            f"has_save={cli_main._menu_has_run_save_field(view)} "
            f"actions={actions[:6]}",
            flush=True,
        )
        return state

    return traced


def _wrap_act(adapter):
    original = adapter.act

    async def traced(action, args=None):
        print(f"  [act {time.strftime('%H:%M:%S')}] {action}", flush=True)
        result = await original(action, args)
        print(f"    → status={result.status} detail={result.detail}", flush=True)
        return result

    return traced


def main() -> int:
    adapter = AgentAdapter(
        endpoint="http://127.0.0.1:8080", timeout=30.0, debug_actions=False
    )
    adapter.get_state = _wrap_get_state(adapter, None)  # type: ignore[method-assign]
    adapter.act = _wrap_act(adapter)  # type: ignore[method-assign]

    steam = SteamController(startup_timeout=60.0)
    lifecycle = build_lifecycle_manager(adapter, steam, Path("tests/output"))
    loop = asyncio.new_event_loop()
    try:
        print("=== 现场探针：_recover_main_menu_via_restart 完整执行 ===", flush=True)
        result = cli_main._recover_main_menu_via_restart(lifecycle, adapter, loop)
        print("=== 结果 ===", flush=True)
        for key, value in result.items():
            print(f"  {key}: {value}", flush=True)
        return 0 if result.get("ok") else 1
    finally:
        loop.close()


if __name__ == "__main__":
    sys.exit(main())
