"""真实游戏链路集成测试。

这些测试要求 Slay the Spire 2 正在运行，且 STS2-Cli-Mod 已加载并能通过
`sts2` CLI 通信。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.common.state import GameScreen, GameState

from .conftest import _run


pytestmark = [pytest.mark.integration, pytest.mark.requires_game]


@pytest.fixture(scope="module", autouse=True)
def require_real_game(real_cli_path: str) -> None:
    """模块级真实游戏 gate，避免无游戏环境下每个测试都等待超时。"""
    adapter = CliModAdapter(cli_path=real_cli_path, timeout=5.0)
    try:
        health = _run(adapter.health_check())
        if not health.healthy:
            pytest.skip(f"游戏或 STS2-Cli-Mod 未就绪：{health.message}")
    finally:
        _run(adapter.cleanup())


class TestRealGameState:
    """真实游戏状态读取。"""

    def test_health_check_is_healthy(self, game_adapter: CliModAdapter) -> None:
        health = _run(game_adapter.health_check())
        assert health.healthy is True

    def test_state_returns_game_state(self, game_adapter: CliModAdapter) -> None:
        state = _run(game_adapter.get_state())
        assert isinstance(state, GameState)
        assert isinstance(state.screen, GameScreen)

    def test_real_screen_maps_to_known_or_loading_state(
        self, game_adapter: CliModAdapter
    ) -> None:
        state = _run(game_adapter.get_state())
        assert state.screen in set(GameScreen)
        assert state.screen != GameScreen.CRASHED

    def test_state_model_is_frozen(self, game_adapter: CliModAdapter) -> None:
        state = _run(game_adapter.get_state())
        assert isinstance(state, GameState)
        with pytest.raises(ValidationError):
            state.screen = GameScreen.COMBAT  # type: ignore[misc]

    def test_available_actions_follow_current_screen(
        self, game_adapter: CliModAdapter
    ) -> None:
        state = _run(game_adapter.get_state())
        actions = _run(game_adapter.get_available_actions())
        assert isinstance(actions, list)
        if state.screen not in {
            GameScreen.UNKNOWN,
            GameScreen.CRASHED,
        }:
            assert actions


class TestRealGameSnapshot:
    """真实游戏 bug snapshot 契约。"""

    def test_bug_snapshot_has_state_actions_and_timestamp(
        self, game_adapter: CliModAdapter
    ) -> None:
        snapshot = _run(game_adapter.capture_bug_snapshot())
        assert {"game_state", "available_actions", "timestamp"} <= snapshot.keys()
        assert isinstance(snapshot["game_state"], GameState)
        assert isinstance(snapshot["available_actions"], list)
        assert snapshot["timestamp"].utcoffset() is not None
