"""真实游戏链路集成测试。

这些测试要求 Slay the Spire 2 正在运行，且 STS2-Cli-Mod 已加载并能通过
`sts2` CLI 通信。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.evidence.capture import ScreenCapture

from .conftest import _run

_GAME_WINDOW_TITLE = "Slay the Spire 2"
_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"

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

    def test_game_state_evidence_persisted_to_output(
        self, game_adapter: CliModAdapter
    ) -> None:
        """真实游戏状态以 JSON 落盘 tests/output，作为 nightly 游戏证据。

        冒烟断言本身不产生文件；nightly 分类器（issue #83）要求
        PASSED 必须有截图/状态 JSON/日志证据，这里显式持久化
        真实状态快照（issue #66/#15）。
        """
        snapshot = _run(game_adapter.capture_bug_snapshot())
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        evidence = {
            "source": "integration-game-smoke",
            "captured_at": snapshot["timestamp"].isoformat(),
            "game_state": snapshot["game_state"].model_dump(),
            "available_actions": snapshot["available_actions"],
        }
        target = _OUTPUT_DIR / "game-state-evidence.json"
        target.write_text(
            json.dumps(evidence, ensure_ascii=False, default=str, indent=2) + "\n",
            encoding="utf-8",
        )
        persisted = json.loads(target.read_text(encoding="utf-8"))
        assert persisted["game_state"]["screen"] in {s.value for s in GameScreen}

    def test_game_screenshot_persisted_best_effort(
        self, game_adapter: CliModAdapter
    ) -> None:
        """尽力截取游戏窗口；窗口不在屏或采集异常不作为冒烟失败。"""
        screenshot_dir = _OUTPUT_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        capture = ScreenCapture(screenshot_dir)
        try:
            result = capture.capture_with_validation(
                _GAME_WINDOW_TITLE, "nightly-game-smoke"
            )
        except (OSError, RuntimeError) as exc:
            pytest.skip(f"截图子系统不可用：{exc}")
            return
        if result.status == "skipped":
            pytest.skip(result.message or "游戏窗口不在屏")
