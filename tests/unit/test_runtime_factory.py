"""统一运行期能力工厂测试（P0#4：自动恢复不再被跳过）。

验证 build_lifecycle_manager 在缺省环境变量时回退到 discovery 自动定位游戏
目录并构建可自动重启的生命周期管理器；只有当确实无法定位游戏时才返回 None，
使预检能够真正尝试「游戏未启动 → 自行拉起」的恢复，而不是静默跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sts2_autotest.core.lifecycle import GameLifecycleManager
from sts2_autotest.core.runtime_factory import build_lifecycle_manager


class TestBuildLifecycleManagerDiscovery:
    """缺省环境变量时回退 discovery 构建管理器。"""

    def test_returns_manager_when_discovered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无 STS2_GAME_EXE / STS2_GAME_DIR，但 discovery 能定位游戏目录 → 构建管理器。"""
        monkeypatch.delenv("STS2_GAME_EXE", raising=False)
        monkeypatch.delenv("STS2_GAME_DIR", raising=False)
        monkeypatch.setattr(
            "sts2_autotest.adapters.discovery.find_game_dir",
            lambda roots=None: tmp_path / "game",
        )
        mgr = build_lifecycle_manager(object(), object(), tmp_path / "evidence")
        assert isinstance(mgr, GameLifecycleManager)

    def test_returns_none_when_nothing_discoverable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """既无环境变量也无法发现游戏 → 返回 None（环境非本框架管理，预检跳过）。"""
        monkeypatch.delenv("STS2_GAME_EXE", raising=False)
        monkeypatch.delenv("STS2_GAME_DIR", raising=False)
        monkeypatch.setattr(
            "sts2_autotest.adapters.discovery.find_game_dir",
            lambda roots=None: None,
        )
        assert build_lifecycle_manager(object(), object(), tmp_path / "evidence") is None

    def test_explicit_env_dir_takes_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """显式 STS2_GAME_DIR 优先，不依赖 discovery。"""
        monkeypatch.setenv("STS2_GAME_DIR", str(tmp_path / "explicit"))
        monkeypatch.setattr(
            "sts2_autotest.adapters.discovery.find_game_dir",
            lambda roots=None: tmp_path / "should-not-be-used",
        )
        mgr = build_lifecycle_manager(object(), object(), tmp_path / "evidence")
        assert isinstance(mgr, GameLifecycleManager)
        # 显式目录应被采用（管理器内部解析出的 exe 基于 explicit 目录）
        assert "explicit" in str(mgr.game_exe)
