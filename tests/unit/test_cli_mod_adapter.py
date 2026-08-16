"""CliModAdapter 单元测试。

这些测试 mock `subprocess.Popen`，不依赖真实 STS2-Cli-Mod 安装。
真实 CLI 和真实游戏链路由 `tests/integration/` 覆盖。
"""

import asyncio
import json
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sts2_autotest.adapters.base import (
    ActionResult,
    DebugVerification,
    GameAdapterProtocol,
    HealthStatus,
)
from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.common.errors import STS2Error
from sts2_autotest.common.state import GameScreen, GameState


def _run(coro: Any) -> Any:
    """Bridge async to sync for testing."""
    return asyncio.run(coro)


def _mock_popen_ok(data: dict[str, Any]) -> MagicMock:
    """Create a mock Popen process with successful CLI response."""
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (
        json.dumps({"ok": True, "data": data}).encode("utf-8"),
        b"",
    )
    mock_proc.returncode = 0
    return mock_proc


def _mock_popen_error(
    returncode: int = 1, stderr: str = "", stdout: str = ""
) -> MagicMock:
    """Create a mock Popen process with failed CLI response."""
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (
        stdout.encode("utf-8"),
        stderr.encode("utf-8"),
    )
    mock_proc.returncode = returncode
    return mock_proc


@pytest.fixture
def adapter() -> CliModAdapter:
    return CliModAdapter(cli_path="sts2", timeout=30.0)


class TestCliModAdapterInit:
    """Constructor and defaults."""

    def test_defaults(self) -> None:
        a = CliModAdapter(cli_path="sts2", timeout=30.0)
        assert a.cli_path == "sts2"
        assert a.timeout == 30.0

    def test_custom_params(self) -> None:
        a = CliModAdapter(cli_path="/usr/local/bin/sts2", timeout=10.0)
        assert a.cli_path == "/usr/local/bin/sts2"
        assert a.timeout == 10.0


class TestHealthCheck:
    """health_check() tests."""

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_healthy_when_ping_ok(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({})
        result = _run(adapter.health_check())
        assert isinstance(result, HealthStatus)
        assert result.healthy is True
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "ping" in cmd

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_unhealthy_when_cli_fails(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_error(returncode=1)
        result = _run(adapter.health_check())
        assert isinstance(result, HealthStatus)
        assert result.healthy is False

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_unhealthy_when_file_not_found(self, mock_popen: MagicMock) -> None:
        a = CliModAdapter(cli_path="/nonexistent/sts2", timeout=5.0)
        mock_popen.side_effect = FileNotFoundError()
        result = _run(a.health_check())
        assert result.healthy is False


class TestGetState:
    """get_state() tests."""

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_returns_game_state_from_cli(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "MENU"})
        result = _run(adapter.get_state())
        assert isinstance(result, GameState)
        assert result.screen == GameScreen.MAIN_MENU

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_combat_screen_mapping(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "COMBAT"})
        result = _run(adapter.get_state())
        assert result.screen == GameScreen.COMBAT

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_singleplayer_submenu_maps_to_main_menu(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "SINGLEPLAYER_SUBMENU"})
        result = _run(adapter.get_state())
        assert result.screen == GameScreen.MAIN_MENU

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_unknown_screen_fallback(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "NEW_SCREEN"})
        result = _run(adapter.get_state())
        assert result.screen == GameScreen.UNKNOWN

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_grid_card_select_maps_to_event(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "GRID_CARD_SELECT"})
        result = _run(adapter.get_state())
        assert result.screen == GameScreen.EVENT

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_reward_maps_to_card_reward(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "REWARD"})
        result = _run(adapter.get_state())
        assert result.screen == GameScreen.CARD_REWARD

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_cached_on_second_call(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "MENU"})
        _run(adapter.get_state())
        _run(adapter.get_state())
        # Popen should only be called once (cache hit on second call)
        assert mock_popen.call_count == 1

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_extra_fields_preserved(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({
            "screen": "COMBAT",
            "combat": {"player_hp": 50},
            "timestamp": 1234567890,
        })
        result = _run(adapter.get_state())
        assert result.screen == GameScreen.COMBAT


class TestGetAvailableActions:
    """get_available_actions() tests."""

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_actions_from_main_menu(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "MENU"})
        result = _run(adapter.get_available_actions())
        assert isinstance(result, list)
        assert "new_run" in result

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_actions_from_combat(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "COMBAT"})
        result = _run(adapter.get_available_actions())
        assert "play_card" in result
        assert "end_turn" in result

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_no_actions_for_unknown(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "UNKNOWN_SCREEN"})
        result = _run(adapter.get_available_actions())
        assert result == []

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_actions_from_grid_card_select(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        mock_popen.return_value = _mock_popen_ok({
            "screen": "GRID_CARD_SELECT",
            "grid_card_select": {"cards": [{"card_id": "STRIKE_IRONCLAD"}]},
        })
        result = _run(adapter.get_available_actions())
        # GRID_CARD_SELECT 属局内模态，return_to_menu 不可用（CLI 仅支持 GAME_OVER/
        # VICTORY），已从动作表移除。
        assert "return_to_menu" not in result
        assert "start_new_run" in result
        assert "select_character" in result
        assert "embark" in result
        assert "grid_select_card" in result
        assert "advance_dialogue" in result


class TestAct:
    """act() tests."""

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_success(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({})
        result = _run(adapter.act("play_card", {"card_id": "VoidSlash"}))
        assert isinstance(result, ActionResult)
        assert result.status == "success"
        assert result.state_changed is True

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_invalidates_cache(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "MENU"})
        _run(adapter.get_state())
        assert adapter._cache_stale is False
        mock_popen.return_value = _mock_popen_ok({})
        _run(adapter.act("end_turn"))
        assert adapter._cache_stale is True

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_failure_on_cli_error(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_error(returncode=2)
        result = _run(adapter.act("invalid_action"))
        assert result.status == "failure"
        assert result.state_changed is False

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_timeout_result(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_error(returncode=4)
        result = _run(adapter.act("slow_action"))
        assert result.status == "timeout"

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_start_new_run_selects_standard_from_submenu(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        mock_popen.side_effect = [
            _mock_popen_ok({"screen": "MENU"}),
            _mock_popen_ok({}),
            _mock_popen_ok({
                "screen": "SINGLEPLAYER_SUBMENU",
                "singleplayer_submenu": {"standard_available": True},
            }),
            _mock_popen_ok({}),
        ]

        result = _run(adapter.act("start_new_run"))

        assert result.status == "success"
        commands = [call.args[0] for call in mock_popen.call_args_list]
        assert commands == [
            ["sts2", "state"],
            ["sts2", "new_run"],
            ["sts2", "state"],
            ["sts2", "choose_game_mode", "standard"],
        ]

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_start_new_run_continues_from_existing_submenu(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(
            screen=GameScreen.MAIN_MENU,
            singleplayer_submenu={"standard_available": True},
        )
        adapter._cache_stale = False
        mock_popen.return_value = _mock_popen_ok({})

        result = _run(adapter.act("start_new_run"))

        assert result.status == "success"
        commands = [call.args[0] for call in mock_popen.call_args_list]
        assert commands == [["sts2", "choose_game_mode", "standard"]]

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_embark_waits_until_character_select_is_left(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        mock_popen.side_effect = [
            _mock_popen_ok({}),
            _mock_popen_ok({"screen": "CHARACTER_SELECT"}),
            _mock_popen_ok({"screen": "EVENT"}),
        ]

        result = _run(adapter.act("embark"))

        assert result.status == "success"
        commands = [call.args[0] for call in mock_popen.call_args_list]
        assert commands == [
            ["sts2", "embark"],
            ["sts2", "state"],
            ["sts2", "state"],
        ]

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_setup_actions_are_noops_after_new_run_started(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(screen=GameScreen.EVENT)
        adapter._cache_stale = False

        # 仅 start_new_run / select_character / embark 在局内（EVENT）属"已满足"
        # 的空操作成功。return_to_menu 不再被当作无操作：CLI 仅支持 GAME_OVER/
        # VICTORY，局内调用必须真实下发（修复「reported success but produced no
        # observable state change」），故不纳入此处 no-op 断言。
        for action in ("start_new_run", "select_character", "embark"):
            result = _run(adapter.act(action))
            assert result.status == "success"

        mock_popen.assert_not_called()

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_advance_dialogue_is_noop_when_event_choice_is_pending(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(
            screen=GameScreen.EVENT,
            event={"is_in_dialogue": False, "options": [{"index": 0}]},
        )
        adapter._cache_stale = False

        result = _run(adapter.act("advance_dialogue"))

        assert result.status == "success"
        mock_popen.assert_not_called()

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_advance_dialogue_selects_first_grid_card(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(
            screen=GameScreen.EVENT,
            grid_card_select={"cards": [{"card_id": "STRIKE_IRONCLAD"}]},
        )
        adapter._cache_stale = False
        mock_popen.return_value = _mock_popen_ok({})

        result = _run(adapter.act("advance_dialogue"))

        assert result.status == "success"
        commands = [call.args[0] for call in mock_popen.call_args_list]
        assert commands == [["sts2", "grid_select_card", "STRIKE_IRONCLAD"]]

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_advance_dialogue_is_noop_on_map(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(screen=GameScreen.MAP)
        adapter._cache_stale = False

        result = _run(adapter.act("advance_dialogue"))

        assert result.status == "success"
        mock_popen.assert_not_called()

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_advance_dialogue_is_noop_in_combat(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(screen=GameScreen.COMBAT)
        adapter._cache_stale = False

        result = _run(adapter.act("advance_dialogue"))

        assert result.status == "success"
        mock_popen.assert_not_called()

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_choose_event_is_noop_on_map(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(screen=GameScreen.MAP)
        adapter._cache_stale = False

        result = _run(adapter.act("choose_event", {"index": 0}))

        assert result.status == "success"
        mock_popen.assert_not_called()

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_choose_event_is_noop_in_combat(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(screen=GameScreen.COMBAT)
        adapter._cache_stale = False

        result = _run(adapter.act("choose_event", {"index": 0}))

        assert result.status == "success"
        mock_popen.assert_not_called()

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_choose_map_node_falls_back_to_travelable_monster(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(
            screen=GameScreen.MAP,
            map={
                "travelable_coords": [{"col": 3, "row": 0}],
                "nodes": [
                    {"col": 3, "row": 0, "type": "MONSTER", "state": "TRAVELABLE"}
                ],
            },
        )
        adapter._cache_stale = False
        mock_popen.return_value = _mock_popen_ok({})

        result = _run(adapter.act("choose_map_node", {"col": 2, "row": 1}))

        assert result.status == "success"
        commands = [call.args[0] for call in mock_popen.call_args_list]
        assert commands == [["sts2", "choose_map_node", "3", "0"]]

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_choose_map_node_advances_pending_event_before_map_choice(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(
            screen=GameScreen.EVENT,
            event={"is_in_dialogue": False, "options": [{"index": 0}]},
        )
        adapter._cache_stale = False
        mock_popen.side_effect = [
            _mock_popen_ok({}),
            _mock_popen_ok(
                {
                    "screen": "MAP",
                    "map": {
                        "travelable_coords": [{"col": 3, "row": 0}],
                        "nodes": [
                            {
                                "col": 3,
                                "row": 0,
                                "type": "MONSTER",
                                "state": "TRAVELABLE",
                            }
                        ],
                    },
                }
            ),
            _mock_popen_ok({}),
        ]

        result = _run(adapter.act("choose_map_node", {"col": 2, "row": 1}))

        assert result.status == "success"
        commands = [call.args[0] for call in mock_popen.call_args_list]
        assert commands == [
            ["sts2", "choose_event", "0"],
            ["sts2", "state"],
            ["sts2", "choose_map_node", "3", "0"],
        ]

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_skip_card_reward_maps_to_reward_skip_card(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(screen=GameScreen.CARD_REWARD)
        adapter._cache_stale = False
        mock_popen.return_value = _mock_popen_ok({})

        result = _run(adapter.act("skip_card_reward"))

        assert result.status == "success"
        commands = [call.args[0] for call in mock_popen.call_args_list]
        assert commands == [["sts2", "reward_skip_card"]]

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_choose_map_node_is_noop_in_combat(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(screen=GameScreen.COMBAT)
        adapter._cache_stale = False

        result = _run(adapter.act("choose_map_node", {"col": 2, "row": 1}))

        assert result.status == "success"
        mock_popen.assert_not_called()

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_enter_combat_is_noop_in_combat(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(screen=GameScreen.COMBAT)
        adapter._cache_stale = False

        result = _run(adapter.act("enter_combat"))

        assert result.status == "success"
        mock_popen.assert_not_called()


class TestWaitUntilActionable:
    """wait_until_actionable() tests."""

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_returns_true_when_actionable(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "MENU"})
        result = _run(adapter.wait_until_actionable(timeout=5.0))
        assert result is True

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_wait_until_actionable_refreshes_cached_actions(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        adapter._cached_state = GameState(screen=GameScreen.MAIN_MENU)
        adapter._cache_stale = False
        adapter._available_actions_cache = ["new_run"]
        mock_popen.side_effect = [
            _mock_popen_ok({}),
            _mock_popen_ok({"screen": "CHARACTER_SELECT"}),
        ]

        assert _run(adapter.wait_until_actionable(timeout=5.0)) is True

        actions = _run(adapter.get_available_actions())
        assert "select_character" in actions
        assert "new_run" not in actions

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_returns_false_on_timeout(self, mock_popen: MagicMock) -> None:
        a = CliModAdapter(cli_path="sts2", timeout=0.1)
        mock_popen.return_value = _mock_popen_error(returncode=1)
        result = _run(a.wait_until_actionable(timeout=0.2))
        assert result is False


class TestCaptureBugSnapshot:
    """capture_bug_snapshot() tests."""

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_returns_dict_with_keys(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_popen.return_value = _mock_popen_ok({"screen": "MENU"})
        result = _run(adapter.capture_bug_snapshot())
        assert isinstance(result, dict)
        assert "game_state" in result
        assert "available_actions" in result
        assert "timestamp" in result


class TestVersionHandshake:
    """Version parsing and validation tests (FR50)."""

    def test_valid_version(self, adapter: CliModAdapter) -> None:
        adapter._check_version("0.102.1")
        assert adapter._version_checked is True

    def test_valid_version_with_git_hash(self, adapter: CliModAdapter) -> None:
        adapter._check_version("0.102.1+9b0771f18f6cb0f782e755285759886183c26f5e")
        assert adapter._version_checked is True

    def test_valid_version_with_extra_output(self, adapter: CliModAdapter) -> None:
        adapter._check_version("0.1.0\n")
        assert adapter._version_checked is True

    def test_invalid_format(self, adapter: CliModAdapter) -> None:
        with pytest.raises(STS2Error, match="Cannot parse version"):
            adapter._check_version("not-a-version")

    def test_incompatible_major(self, adapter: CliModAdapter) -> None:
        with pytest.raises(STS2Error, match="incompatible"):
            adapter._check_version("2.0.0")

    def test_empty_string(self, adapter: CliModAdapter) -> None:
        with pytest.raises(STS2Error):
            adapter._check_version("")

    def test_version_handshake_on_init(self) -> None:
        a = CliModAdapter(cli_path="sts2", version_output="0.1.0")
        assert a._version_checked is True

    def test_version_handshake_skipped_when_none(self) -> None:
        a = CliModAdapter(cli_path="sts2")
        assert a._version_checked is False

    def test_init_rejects_incompatible_version(self) -> None:
        with pytest.raises(STS2Error, match="incompatible"):
            CliModAdapter(cli_path="sts2", version_output="2.0.0")


class TestProtocolCompliance:
    """CliModAdapter satisfies GameAdapterProtocol."""

    def test_isinstance_check(self, adapter: CliModAdapter) -> None:
        assert isinstance(adapter, GameAdapterProtocol)

    def test_has_all_eight_methods(self) -> None:
        expected = {
            "health_check", "get_state", "get_available_actions",
            "act", "wait_until_actionable", "capture_bug_snapshot",
            "cleanup", "verify_debug_actions",
        }
        actual = {
            name for name in dir(CliModAdapter)
            if not name.startswith("_") and callable(getattr(CliModAdapter, name, None))
        }
        assert expected <= actual

    def test_verify_debug_actions_reports_not_supported(
        self, adapter: CliModAdapter
    ) -> None:
        """CliMod 无调试控制台，调试能力应诚实报告未支持。"""
        result = _run(adapter.verify_debug_actions())
        assert result.configured is False
        assert result.verified is False
        assert result.reason == "NOT_SUPPORTED"
        assert result.checked_at is not None


class TestErrorClassification:
    """Error wrapping tests (FR26)."""

    def test_sts2error_structure(self, adapter: CliModAdapter) -> None:
        try:
            adapter._check_version("invalid")
        except STS2Error as exc:
            d = exc.to_dict()
            assert d["type"] is not None
            assert "message" in d
            assert "detail" in d
            assert "timestamp" in d
            assert "subtype" in d["detail"]
            assert "command" in d["detail"]

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_timeout_error(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="sts2", timeout=30.0),
            (b"", b""),
        ]
        mock_popen.return_value = mock_proc
        with pytest.raises(STS2Error) as exc_info:
            adapter._run_cli("state")
        assert exc_info.value.detail.get("subtype") == "timeout"
        mock_proc.kill.assert_called_once()
        assert mock_proc.communicate.call_count == 2

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_file_not_found_error(self, mock_popen: MagicMock) -> None:
        a = CliModAdapter(cli_path="/nonexistent/sts2", timeout=5.0)
        mock_popen.side_effect = FileNotFoundError()
        with pytest.raises(STS2Error) as exc_info:
            a._run_cli("ping")
        assert exc_info.value.detail.get("subtype") == "process_exit"

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_json_parse_failure(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"not json", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        with pytest.raises(STS2Error) as exc_info:
            adapter._run_cli("state")
        assert exc_info.value.detail.get("subtype") == "json_parse_failure"

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_error_code_preserved_in_detail(self, mock_popen: MagicMock, adapter: CliModAdapter) -> None:
        """Verify error_code from CLI JSON error is included in STS2Error detail."""
        error_json = json.dumps({"ok": False, "error": "CONNECTION", "message": "Game not running"})
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", error_json.encode("utf-8"))
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc
        with pytest.raises(STS2Error) as exc_info:
            adapter._run_cli("state")
        assert exc_info.value.detail.get("error_code") == "CONNECTION"


class TestMockReplaceability:
    """Orchestrator can depend on Protocol, not CliModAdapter (FR25)."""

    class MockAdapter:
        async def health_check(self) -> Any:
            return HealthStatus(healthy=True)

        async def get_state(self) -> Any:
            return GameState(screen=GameScreen.MAIN_MENU)

        async def get_available_actions(self) -> Any:
            return ["play_card", "end_turn"]

        async def act(self, action: str, args: Any = None) -> Any:
            return ActionResult(status="success", state_changed=True)

        async def wait_until_actionable(self, timeout: float) -> Any:
            return True

        async def capture_bug_snapshot(self) -> Any:
            return {}

        async def cleanup(self) -> None:
            pass

        async def verify_debug_actions(self) -> Any:
            return DebugVerification(configured=False, verified=False)

    def test_mock_passes_protocol_check(self) -> None:
        mock = self.MockAdapter()
        assert isinstance(mock, GameAdapterProtocol)

    def test_mock_can_replace_real_adapter(self) -> None:
        mock = self.MockAdapter()
        adapter_ref: GameAdapterProtocol = mock  # type: ignore[assignment]
        assert adapter_ref is not None


# ── 阶段 A：埋点计数 + 轮询间隔配置 + 战斗等待退避（issue #37）──

class _FakeTime:
    """替身 time 模块：monotonic 递增 + 记录 sleep 调用，避免真实等待。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _combat_payload(is_player_turn: bool, hand: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """构造 CLI state 响应的战斗载荷。"""
    return {
        "screen": "COMBAT",
        "combat": {
            "is_player_turn": is_player_turn,
            "is_player_actions_disabled": False,
            "hand": hand or [],
            "enemies": [],
        },
    }


class TestCliLaunchCount:
    """_run_cli 入口埋点计数：每次工具启动（含失败）都计入，供性能对比。"""

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_launch_count_starts_at_zero(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        assert adapter.cli_launch_count == 0

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_launch_count_increments_per_cli_call(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        mock_popen.return_value = _mock_popen_ok({})
        _run(adapter.health_check())   # ping → 1 次启动
        _run(adapter.get_state())      # state → 1 次启动
        assert adapter.cli_launch_count == 2

    @patch("sts2_autotest.adapters.cli_mod.subprocess.Popen")
    def test_launch_count_counts_failures_too(
        self, mock_popen: MagicMock, adapter: CliModAdapter
    ) -> None:
        """失败调用同样计数——统计口径为「实际启动次数」，不得漏计。

        用 get_state 而非 health_check：health_check 既有设计吞掉错误返回
        healthy=False，不传播异常；get_state 会把 CLI 失败作为 STS2Error 抛出。
        """
        mock_popen.return_value = _mock_popen_error(returncode=1, stderr="boom")
        with pytest.raises(STS2Error):
            _run(adapter.get_state())
        assert adapter.cli_launch_count == 1

    def test_launch_count_is_per_instance(self) -> None:
        a = CliModAdapter(cli_path="sts2", timeout=30.0)
        b = CliModAdapter(cli_path="sts2", timeout=30.0)
        assert a.cli_launch_count == 0
        assert b.cli_launch_count == 0


class TestPollIntervalConfig:
    """轮询间隔配置化：默认 0.5s，可经构造参数调整。"""

    def test_default_poll_interval(self) -> None:
        a = CliModAdapter(cli_path="sts2", timeout=30.0)
        assert a.poll_interval == 0.5

    def test_custom_poll_interval(self) -> None:
        a = CliModAdapter(cli_path="sts2", timeout=30.0, poll_interval=1.0)
        assert a.poll_interval == 1.0


class TestCombatWaitBackoff:
    """阶段 A：战斗等待轮询自适应降频。

    非玩家回合（状态未变）→ 轮询间隔翻倍（0.5 → 1.0 封顶）；
    玩家回合出现（状态变化）→ 复位基础间隔。等待期间不再逐轮满频查询。
    """

    def _run_combat_policy(
        self, poll_interval: float, responses: list[dict[str, Any]]
    ) -> tuple[Any, _FakeTime, CliModAdapter]:
        fake_time = _FakeTime()
        adapter = CliModAdapter(cli_path="sts2", timeout=5.0, poll_interval=poll_interval)
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = [
            (json.dumps({"ok": True, "data": resp}).encode("utf-8"), b"")
            for resp in responses
        ]
        mock_proc.returncode = 0
        with patch("sts2_autotest.adapters.cli_mod.subprocess.Popen",
                   return_value=mock_proc), patch(
            "sts2_autotest.adapters.cli_mod.time", fake_time
        ):
            # _combat_basic_policy_sync 是同步函数，直接调用（不走 asyncio 桥接）。
            result = adapter._combat_basic_policy_sync()
        return result, fake_time, adapter

    def test_wait_backoff_doubles_then_caps(self) -> None:
        """连续等待：0.5 → 1.0 → 1.0（封顶 = 2×poll_interval），不再逐轮满频。"""
        result, fake_time, adapter = self._run_combat_policy(
            0.5,
            [
                _combat_payload(False),  # 等待 → 0.5
                _combat_payload(False),  # 等待 → 1.0
                _combat_payload(False),  # 等待 → 1.0（封顶）
                {"screen": "VICTORY"},   # 战斗结束
            ],
        )
        assert result.status == "success"
        assert fake_time.sleeps == [0.5, 1.0, 1.0]
        # 4 次状态读取（3 次等待 + 1 次战斗结束判定）= 4 次工具启动，
        # 无等待期间之外的冗余启动。
        assert adapter.cli_launch_count == 4

    def test_wait_backoff_resets_on_player_turn(self) -> None:
        """玩家回合出现 → 退避复位；再次等待从基础间隔重新翻倍。"""
        result, fake_time, adapter = self._run_combat_policy(
            0.5,
            [
                _combat_payload(False),                                  # 等待 → 0.5
                _combat_payload(False),                                  # 等待 → 1.0
                _combat_payload(True, hand=[{"id": "1", "type": "Attack", "can_play": True, "target_type": "None"}]),  # 玩家回合 → play_card
                _combat_payload(False),                                  # play_card 响应（返回后状态仍为等待）
                _combat_payload(False),                                  # 敌人回合（已复位）→ 0.5
                {"screen": "VICTORY"},
            ],
        )
        assert result.status == "success"
        assert fake_time.sleeps == [0.5, 1.0, 0.5]
        # 5 次状态读取（4 次等待 + 1 次战斗结束判定）+ 1 次 play_card = 6 次启动
        assert adapter.cli_launch_count == 6

    def test_wait_backoff_uses_configured_poll_interval(self) -> None:
        """配置 poll_interval=1.0 → 等待基础间隔 1.0、封顶 2.0。"""
        result, fake_time, adapter = self._run_combat_policy(
            1.0,
            [
                _combat_payload(False),  # 等待 → 1.0
                _combat_payload(False),  # 等待 → 2.0（封顶）
                {"screen": "VICTORY"},
            ],
        )
        assert result.status == "success"
        assert fake_time.sleeps == [1.0, 2.0]
