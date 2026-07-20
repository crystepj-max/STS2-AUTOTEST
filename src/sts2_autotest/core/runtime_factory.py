"""统一构造运行期公共能力。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def build_lifecycle_manager(adapter: Any, steam_controller: Any, evidence_root: Path) -> Any:
    """构建可自动重启的生命周期管理器。

    优先使用显式环境变量（STS2_GAME_EXE / STS2_GAME_DIR）；二者皆缺时回退到
    discovery 自动定位 Steam 游戏目录。只有确实无法定位游戏时才返回 None，
    使预检能够真正尝试「游戏未启动 → 自行拉起」的恢复，而非静默跳过（修复四：
    系统重启后 8080 connection refused 的原始问题）。无法拉起时由预检返回显式
    环境阻塞，而不是把任务放行到旅程里才失败。
    """
    game_exe = os.environ.get("STS2_GAME_EXE")
    game_dir = os.environ.get("STS2_GAME_DIR")
    if not game_exe and not game_dir:
        from sts2_autotest.adapters.discovery import find_game_dir

        discovered = find_game_dir()
        if discovered is None:
            return None
        game_dir = str(discovered)

    from sts2_autotest.core.lifecycle import GameLifecycleManager

    game_log = evidence_root / "logs" / "game-process.log"
    game_log.parent.mkdir(parents=True, exist_ok=True)
    return GameLifecycleManager(
        adapter,
        game_exe=game_exe,
        game_dir=game_dir,
        steam_controller=steam_controller,
        game_log=str(game_log),
    )
