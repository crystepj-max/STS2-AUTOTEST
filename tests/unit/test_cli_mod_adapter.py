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

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
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
        assert "return_to_menu" in result
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

        for action in ("return_to_menu", "start_new_run", "select_character", "embark"):
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

    def test_has_all_seven_methods(self) -> None:
        expected = {
            "health_check", "get_state", "get_available_actions",
            "act", "wait_until_actionable", "capture_bug_snapshot",
            "cleanup",
        }
        actual = {
            name for name in dir(CliModAdapter)
            if not name.startswith("_") and callable(getattr(CliModAdapter, name, None))
        }
        assert expected <= actual


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

    def test_mock_passes_protocol_check(self) -> None:
        mock = self.MockAdapter()
        assert isinstance(mock, GameAdapterProtocol)

    def test_mock_can_replace_real_adapter(self) -> None:
        mock = self.MockAdapter()
        adapter_ref: GameAdapterProtocol = mock  # type: ignore[assignment]
        assert adapter_ref is not None
