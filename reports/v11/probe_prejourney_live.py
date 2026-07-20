#!/usr/bin/env python3
"""一次性现场探针 #2：逐步追踪开局前清理 + 环境预检的判定依据。

复现 v11g worker 在干净菜单上卡住/重启的问题。
不做任何修改性操作（_ensure_clean_main_menu 在干净菜单上只读；
_run_environment_precheck 可能就绪返回，也可能触发拉起——探针如实记录）。
"""

from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, "src")

from sts2_autotest.adapters.agent import AgentAdapter
from sts2_autotest.cli import main as cli_main
from sts2_autotest.core.runtime_factory import build_lifecycle_manager
from sts2_autotest.core.steam import SteamController


def _wrap_get_state(adapter):
    original = adapter.get_state

    async def traced():
        state = await original()
        view = cli_main._state_view(state)
        print(
            f"  [帧 {time.strftime('%H:%M:%S')}] screen={cli_main._screen_of(state)} "
            f"has_save={cli_main._menu_has_run_save_field(view)} "
            f"actions={cli_main._menu_actions(view)[:6]}",
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


async def _traced_ready(lifecycle):
    t0 = time.monotonic()
    readiness = await lifecycle.ensure_environment_ready()
    print(
        f"  [precheck] ensure_environment_ready → ready={getattr(readiness, 'ready', None)} "
        f"reason={getattr(readiness, 'reason', None)} 耗时 {time.monotonic()-t0:.1f}s",
        flush=True,
    )
    return readiness


def main() -> int:
    adapter = AgentAdapter(
        endpoint="http://127.0.0.1:8080", timeout=30.0, debug_actions=False
    )
    adapter.get_state = _wrap_get_state(adapter)  # type: ignore[method-assign]
    adapter.act = _wrap_act(adapter)  # type: ignore[method-assign]

    steam = SteamController(startup_timeout=60.0)
    lifecycle = build_lifecycle_manager(adapter, steam, __import__("pathlib").Path("tests/output"))

    loop = asyncio.new_event_loop()
    try:
        print("=== 步骤1: _ensure_clean_main_menu（当前应为干净菜单） ===", flush=True)
        t0 = time.monotonic()
        ok = cli_main._ensure_clean_main_menu(adapter, lifecycle, loop)
        print(f"=== _ensure_clean_main_menu → {ok}，耗时 {time.monotonic()-t0:.1f}s ===", flush=True)

        print("=== 步骤2: _run_environment_precheck ===", flush=True)
        t0 = time.monotonic()
        reason = cli_main._run_environment_precheck(adapter)
        print(
            f"=== _run_environment_precheck → reason={reason}，耗时 {time.monotonic()-t0:.1f}s ===",
            flush=True,
        )
        return 0 if (ok and reason is None) else 1
    finally:
        loop.close()


if __name__ == "__main__":
    sys.exit(main())
