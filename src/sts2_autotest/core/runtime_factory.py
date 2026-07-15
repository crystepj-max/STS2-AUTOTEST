"""统一构造运行期公共能力。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def build_lifecycle_manager(adapter: Any, steam_controller: Any, evidence_root: Path) -> Any:
    """仅在项目提供游戏可执行文件时启用可自动重启的生命周期管理。"""
    if not (os.environ.get("STS2_GAME_EXE") or os.environ.get("STS2_GAME_DIR")):
        return None

    from sts2_autotest.core.lifecycle import GameLifecycleManager

    game_log = evidence_root / "logs" / "game-process.log"
    game_log.parent.mkdir(parents=True, exist_ok=True)
    return GameLifecycleManager(
        adapter,
        steam_controller=steam_controller,
        game_log=str(game_log),
    )
