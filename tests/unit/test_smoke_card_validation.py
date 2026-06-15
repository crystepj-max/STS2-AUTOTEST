"""Test card smoke validation logic with mocked AgentAdapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import os
import subprocess

import pytest

from sts2_autotest.adapters.base import ActionResult, HealthStatus
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.test_agent_runner import (
    CheckResult,
    TestAgentRunner,
    _find_build_output,
    _find_godot_path,
    _capture_macos_window_png,
    _launch_game_via_desktop_open,
    _start_steam_client_without_polling,
)


class TestSmokeCardValidation:

    def test_screenshot_saves_file(self, tmp_path):
        """_capture_screenshot should return a path when mss succeeds."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        runner._artifact_dir = runner._mod_project_path / "automation" / "autotest" / "output" / "test-task"
        runner._screenshot_dir = runner._artifact_dir / "screenshots"
        runner._screenshot_dir.mkdir(parents=True)

        with patch("sts2_autotest.core.test_agent_runner._IS_MACOS", False):
            with patch("mss.mss") as mock_mss:
                mock_sct = MagicMock()
                mock_mss.mss.return_value.__enter__.return_value = mock_sct
                mock_sct.monitors = [{}, {"width": 1920, "height": 1080}]
                result = runner._capture_screenshot("test-card.png")

        assert "screenshots/test-card.png" in result

    def test_screenshot_uses_macos_window_capture(self, tmp_path):
        """macOS should capture the game window instead of the whole desktop."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        runner._artifact_dir = runner._mod_project_path / "automation" / "autotest" / "output" / "test-task"
        runner._screenshot_dir = runner._artifact_dir / "screenshots"
        runner._screenshot_dir.mkdir(parents=True)

        with patch("sts2_autotest.core.test_agent_runner._IS_MACOS", True):
            with patch(
                "sts2_autotest.core.test_agent_runner._capture_macos_window_png",
                return_value=True,
            ) as mock_capture:
                result = runner._capture_screenshot("test-card.png")

        assert "screenshots/test-card.png" in result
        mock_capture.assert_called_once()

    def test_screenshot_skips_when_macos_window_missing(self, tmp_path):
        """macOS should omit screenshot evidence when the game window is unavailable."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        runner._artifact_dir = runner._mod_project_path / "automation" / "autotest" / "output" / "test-task"
        runner._screenshot_dir = runner._artifact_dir / "screenshots"
        runner._screenshot_dir.mkdir(parents=True)

        with patch("sts2_autotest.core.test_agent_runner._IS_MACOS", True):
            with patch(
                "sts2_autotest.core.test_agent_runner._capture_macos_window_png",
                return_value=False,
            ):
                result = runner._capture_screenshot("test-card.png")

        assert result == ""

    def test_macos_window_capture_fallback_uses_shared_selector(self, tmp_path):
        """Fallback script should not carry a separate window matching implementation."""
        captured: dict[str, str] = {}

        def fake_run(cmd, **kwargs):
            captured["script"] = cmd[2]
            return subprocess.CompletedProcess(cmd, 1, "", "")

        with patch(
            "sts2_autotest.core.test_agent_runner.subprocess.run",
            side_effect=fake_run,
        ):
            result = _capture_macos_window_png(tmp_path / "screen.png", "Slay the Spire 2")

        assert result is False
        assert "def _select_macos_window" in captured["script"]
        assert "def norm(" not in captured["script"]

    def test_verify_card_attack_ok(self, tmp_path):
        """Attack card should pass when HP delta matches expected damage."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        runner._artifact_dir = runner._mod_project_path / "automation" / "autotest" / "output" / "test-task"
        runner._screenshot_dir = runner._artifact_dir / "screenshots"
        runner._state_dir = runner._artifact_dir / "state"
        runner._screenshot_dir.mkdir(parents=True)
        runner._state_dir.mkdir(parents=True)

        mock_agent = AsyncMock()
        mock_agent.act.return_value = ActionResult(
            status="success", state_changed=True
        )

        before = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "enemies": [{"current_hp": 50, "max_hp": 50, "block": 0}],
                "player": {"block": 0, "current_hp": 80},
                "hand": [],
            },
        )
        after = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "enemies": [{"current_hp": 44, "max_hp": 50, "block": 0}],
                "player": {"block": 0, "current_hp": 80},
                "hand": [],
            },
        )
        mock_agent.get_state.side_effect = [before, after]

        card = {
            "card_id": "STRIKE",
            "name": "Strike",
            "index": 0,
            "energy_cost": 1,
            "playable": True,
            "dynamic_values": [
                {"name": "damage", "base_value": 6, "current_value": 6}
            ],
        }

        with patch.object(runner, "_capture_screenshot", return_value="screenshots/test.png"):
            with patch.object(runner, "_save_state_snapshot", return_value="state/test.json"):
                result = runner._verify_card_and_screenshot(
                    mock_agent, card, card_index=0, target_index=0
                )

        assert result["status"] == "OK"
        assert result["expected_damage"] == 6
        assert result["actual_damage"] == 6
        assert result["expected_block"] == 0
        assert result["actual_block"] == 0

    def test_verify_card_block_ok(self, tmp_path):
        """Block card should pass when block gained matches expected."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        runner._artifact_dir = runner._mod_project_path / "automation" / "autotest" / "output" / "test-task"
        runner._screenshot_dir = runner._artifact_dir / "screenshots"
        runner._state_dir = runner._artifact_dir / "state"
        runner._screenshot_dir.mkdir(parents=True)
        runner._state_dir.mkdir(parents=True)

        mock_agent = AsyncMock()
        mock_agent.act.return_value = ActionResult(
            status="success", state_changed=True
        )

        before = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "enemies": [{"current_hp": 50, "max_hp": 50, "block": 0}],
                "player": {"block": 0, "current_hp": 80},
                "hand": [],
            },
        )
        after = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "enemies": [{"current_hp": 50, "max_hp": 50, "block": 0}],
                "player": {"block": 5, "current_hp": 80},
                "hand": [],
            },
        )
        mock_agent.get_state.side_effect = [before, after]

        card = {
            "card_id": "DEFEND",
            "name": "Defend",
            "index": 1,
            "energy_cost": 1,
            "playable": True,
            "dynamic_values": [
                {"name": "block", "base_value": 5, "current_value": 5}
            ],
        }

        with patch.object(runner, "_capture_screenshot", return_value="screenshots/test.png"):
            with patch.object(runner, "_save_state_snapshot", return_value="state/test.json"):
                result = runner._verify_card_and_screenshot(
                    mock_agent, card, card_index=1, target_index=0
                )

        assert result["status"] == "OK"
        assert result["expected_block"] == 5
        assert result["actual_block"] == 5
        assert result["expected_damage"] == 0
        assert result["actual_damage"] == 0

    def test_verify_card_play_failed(self, tmp_path):
        """Card with play failure should report FAIL status."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        runner._artifact_dir = runner._mod_project_path / "automation" / "autotest" / "output" / "test-task"
        runner._screenshot_dir = runner._artifact_dir / "screenshots"
        runner._state_dir = runner._artifact_dir / "state"
        runner._screenshot_dir.mkdir(parents=True)
        runner._state_dir.mkdir(parents=True)

        mock_agent = AsyncMock()
        mock_agent.act.return_value = ActionResult(
            status="failure", state_changed=False,
            detail="Not enough energy"
        )

        before = GameState(
            screen=GameScreen.COMBAT,
            combat={
                "enemies": [{"current_hp": 50, "max_hp": 50, "block": 0}],
                "player": {"block": 0, "current_hp": 80},
                "hand": [],
            },
        )
        mock_agent.get_state.return_value = before

        card = {
            "card_id": "HEAVY_STRIKE",
            "name": "Heavy Strike",
            "index": 2,
            "energy_cost": 3,
            "playable": False,
            "dynamic_values": [
                {"name": "damage", "base_value": 14, "current_value": 14}
            ],
        }

        with patch.object(runner, "_capture_screenshot", return_value="screenshots/test.png"):
            with patch.object(runner, "_save_state_snapshot", return_value="state/test.json"):
                result = runner._verify_card_and_screenshot(
                    mock_agent, card, card_index=2, target_index=0
                )

        assert result["status"] == "FAIL"
        assert "Not enough energy" in result["error"]

    def test_build_card_detail_table_empty(self, tmp_path):
        """_build_card_detail_table returns empty string when no results."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        assert runner._build_card_detail_table() == ""

    def test_step_build_passes_game_mods_path_to_msbuild(self, tmp_path):
        """Build must pass ModsPath so the mod project can deploy runtime resources."""
        mod = tmp_path / "mod"
        infra = tmp_path / "infra"
        mods = tmp_path / "game-mods"
        mod.mkdir()
        infra.mkdir()
        mods.mkdir()
        target = mod / "Gawain.csproj"
        target.write_text("<Project />", encoding="utf-8")
        output = mod / "bin" / "Debug"
        output.mkdir(parents=True)

        runner = TestAgentRunner(
            mod_project=str(mod),
            task_id="test-task",
            infra_path=str(infra),
            game_mods_path=str(mods),
        )

        calls: list[list[str]] = []

        def fake_run_command(name, cmd, log_path, **kwargs):
            calls.append(cmd)
            return 0, ""

        with patch("sts2_autotest.core.test_agent_runner._find_sln_or_csproj", return_value=target):
            with patch("sts2_autotest.core.test_agent_runner._find_build_output", return_value=output):
                with patch("sts2_autotest.core.test_agent_runner._run_command", side_effect=fake_run_command):
                    runner._step_build()

        assert any(
            f"-p:ModsPath={mods}{os.sep}" in command
            for command in calls
        )

    def test_step_build_publishes_pck_when_manifest_requires_it(self, tmp_path):
        """A mod with has_pck must publish and leave the pck in the target mods directory."""
        mod = tmp_path / "mod"
        infra = tmp_path / "infra"
        mods = tmp_path / "game-mods"
        mod.mkdir()
        infra.mkdir()
        mods.mkdir()
        target = mod / "Gawain.csproj"
        target.write_text(
            "<Project><PropertyGroup><AssemblyName>Gawain</AssemblyName></PropertyGroup></Project>",
            encoding="utf-8",
        )
        (mod / "Gawain.json").write_text('{"id":"gawain","has_pck":true}', encoding="utf-8")
        output = mod / "bin" / "Debug"
        output.mkdir(parents=True)

        runner = TestAgentRunner(
            mod_project=str(mod),
            task_id="test-task",
            infra_path=str(infra),
            game_mods_path=str(mods),
        )

        calls: list[tuple[str, list[str]]] = []

        def fake_run_command(name, cmd, log_path, **kwargs):
            calls.append((name, cmd))
            if name == "dotnet publish":
                pck = mods / "Gawain" / "gawain.pck"
                pck.parent.mkdir(parents=True)
                pck.write_bytes(b"pck")
            return 0, ""

        with patch("sts2_autotest.core.test_agent_runner._find_sln_or_csproj", return_value=target):
            with patch("sts2_autotest.core.test_agent_runner._find_build_output", return_value=output):
                with patch("sts2_autotest.core.test_agent_runner._find_godot_path", return_value="/Applications/Godot"):
                    with patch("sts2_autotest.core.test_agent_runner._run_command", side_effect=fake_run_command):
                        runner._step_build()

        publish_calls = [cmd for name, cmd in calls if name == "dotnet publish"]
        assert publish_calls
        assert f"-p:ModsPath={mods}{os.sep}" in publish_calls[0]
        assert "-p:GodotPath=/Applications/Godot" in publish_calls[0]

    def test_step_build_skip_deploy_disables_msbuild_mods_copy(self, tmp_path):
        """--skip-deploy must not let the mod csproj copy into the real game mods dir."""
        mod = tmp_path / "mod"
        infra = tmp_path / "infra"
        mods = tmp_path / "game-mods"
        mod.mkdir()
        infra.mkdir()
        mods.mkdir()
        target = mod / "Gawain.csproj"
        target.write_text(
            "<Project><PropertyGroup><AssemblyName>Gawain</AssemblyName></PropertyGroup></Project>",
            encoding="utf-8",
        )
        (mod / "Gawain.json").write_text('{"id":"gawain","has_pck":true}', encoding="utf-8")
        output = mod / "bin" / "Debug"
        output.mkdir(parents=True)

        runner = TestAgentRunner(
            mod_project=str(mod),
            task_id="test-task",
            infra_path=str(infra),
            game_mods_path=str(mods),
            skip_deploy=True,
        )

        calls: list[tuple[str, list[str]]] = []

        def fake_run_command(name, cmd, log_path, **kwargs):
            calls.append((name, cmd))
            return 0, ""

        with patch("sts2_autotest.core.test_agent_runner._find_sln_or_csproj", return_value=target):
            with patch("sts2_autotest.core.test_agent_runner._find_build_output", return_value=output):
                with patch("sts2_autotest.core.test_agent_runner._run_command", side_effect=fake_run_command):
                    runner._step_build()

        build_calls = [cmd for name, cmd in calls if name == "dotnet build"]
        publish_calls = [cmd for name, cmd in calls if name == "dotnet publish"]
        assert build_calls
        assert "-p:ModsPath=" in build_calls[0]
        assert not publish_calls

    def test_find_build_output_allows_godot_bin_output(self, tmp_path):
        """Godot C# projects place build outputs under .godot/mono/temp/bin."""
        release = tmp_path / ".godot" / "mono" / "temp" / "bin" / "Release"
        release.mkdir(parents=True)

        assert _find_build_output(tmp_path) == release

    def test_find_build_output_ignores_godot_cache_bins(self, tmp_path):
        """Godot cache bins should not outrank the real mod build output."""
        release = tmp_path / "src" / "bin" / "Release"
        release.mkdir(parents=True)
        cache = tmp_path / ".godot" / "imported" / "bin" / "Release"
        cache.mkdir(parents=True)

        assert _find_build_output(tmp_path) == release

    def test_find_godot_path_prefers_user_godot_app_before_global_mono(self):
        """Default Godot discovery should prefer the STS2-compatible 4.5 user app."""
        user_godot = Path.home() / "Applications/Godot.app/Contents/MacOS/Godot"

        def fake_exists(path: Path) -> bool:
            return str(path) in {
                str(user_godot),
                "/Applications/Godot_mono.app/Contents/MacOS/Godot",
            }

        with patch.dict(os.environ, {"GODOT_PATH": ""}):
            with patch("sts2_autotest.core.test_agent_runner.Path.exists", fake_exists):
                assert _find_godot_path() == str(user_godot)

    def test_step_launch_game_uses_smoke_test_steam_controller(self, tmp_path):
        """agent-test launch must match smoke tests: Steam first, then steam://run."""
        mod = tmp_path / "mod"
        infra = tmp_path / "infra"
        mod.mkdir()
        infra.mkdir()
        runner = TestAgentRunner(
            mod_project=str(mod),
            task_id="test-task",
            infra_path=str(infra),
        )
        runner._ensure_dirs()

        steam = MagicMock()
        steam.start_steam.return_value = 111
        steam.start_game.return_value = 222

        with patch(
            "sts2_autotest.core.test_agent_runner.SteamController",
            return_value=steam,
        ) as controller:
            runner._step_launch_game()

        controller.assert_called_once_with(app_id="2868840", startup_timeout=60.0)
        steam.start_steam.assert_called_once_with()
        steam.start_game.assert_called_once_with(reuse_existing=True)
        launch_log = runner._artifact_dir / "launch.log"
        text = launch_log.read_text(encoding="utf-8")
        assert "steam://run/2868840" in text
        assert "rungameid" not in text

    def test_step_launch_game_falls_back_when_process_scan_is_sandboxed(self, tmp_path):
        """Sandboxed macOS runners should still launch through Steam without psutil scans."""
        mod = tmp_path / "mod"
        infra = tmp_path / "infra"
        mod.mkdir()
        infra.mkdir()
        runner = TestAgentRunner(
            mod_project=str(mod),
            task_id="test-task",
            infra_path=str(infra),
        )
        runner._ensure_dirs()

        steam = MagicMock()
        steam.start_steam.side_effect = PermissionError("Operation not permitted")

        with patch(
            "sts2_autotest.core.test_agent_runner.SteamController",
            return_value=steam,
        ):
            with patch(
                "sts2_autotest.core.test_agent_runner._launch_game_via_desktop_open",
            ) as fallback:
                runner._step_launch_game()

        fallback.assert_called_once_with("2868840", runner._artifact_dir / "launch.log")
        launch_log = runner._artifact_dir / "launch.log"
        text = launch_log.read_text(encoding="utf-8")
        assert "Falling back to desktop open commands" in text
        assert "steam://run/2868840" in text

    def test_steam_desktop_fallback_can_start_steam_executable(self, tmp_path):
        """If LaunchServices cannot open Steam.app, fallback to the Steam client binary."""
        launch_log = tmp_path / "launch.log"

        with patch(
            "sts2_autotest.core.test_agent_runner._run_launch_command",
            side_effect=RuntimeError("kLSNoExecutableErr"),
        ):
            with patch("sts2_autotest.core.test_agent_runner.subprocess.Popen") as popen:
                _start_steam_client_without_polling(launch_log)

        popen.assert_called_once()
        cmd = popen.call_args.args[0]
        assert cmd[0].endswith("steam_osx")
        text = launch_log.read_text(encoding="utf-8")
        assert "Steam.app launch failed" in text
        assert "Start Steam executable" in text

    def test_desktop_fallback_uses_steam_applaunch_when_url_handler_is_broken(self, tmp_path):
        """If steam:// cannot be opened by LaunchServices, use Steam's own applaunch."""
        launch_log = tmp_path / "launch.log"
        steam_exe = tmp_path / "steam_osx"
        steam_exe.write_text("", encoding="utf-8")

        with patch(
            "sts2_autotest.core.test_agent_runner._start_steam_client_without_polling",
            return_value=steam_exe,
        ):
            with patch(
                "sts2_autotest.core.test_agent_runner._run_launch_command",
                side_effect=RuntimeError("kLSExecutableIncorrectFormat"),
            ):
                with patch("sts2_autotest.core.test_agent_runner.time.sleep"):
                    with patch(
                        "sts2_autotest.core.test_agent_runner._popen_steam_executable"
                    ) as popen_steam:
                        _launch_game_via_desktop_open("2868840", launch_log)

        popen_steam.assert_called_once_with(
            steam_exe,
            ["-applaunch", "2868840"],
            launch_log,
            "Start game via Steam executable",
        )
        text = launch_log.read_text(encoding="utf-8")
        assert "Steam URL handler failed" in text

    def test_desktop_fallback_invalid_delay_uses_default(self, tmp_path):
        """Invalid STS2_STEAM_FALLBACK_DELAY should not crash the fallback path."""
        launch_log = tmp_path / "launch.log"
        steam_exe = tmp_path / "steam_osx"
        steam_exe.write_text("", encoding="utf-8")

        with patch.dict(os.environ, {"STS2_STEAM_FALLBACK_DELAY": "5s"}):
            with patch(
                "sts2_autotest.core.test_agent_runner._start_steam_client_without_polling",
                return_value=steam_exe,
            ):
                with patch(
                    "sts2_autotest.core.test_agent_runner._run_launch_command",
                    return_value="",
                ):
                    with patch("sts2_autotest.core.test_agent_runner.time.sleep") as sleep:
                        _launch_game_via_desktop_open("2868840", launch_log)

        sleep.assert_called_once_with(5.0)

    def test_run_launch_command_logs_timeout_exit_code(self, tmp_path):
        """Timeouts should leave an ExitCode marker in launch.log."""
        launch_log = tmp_path / "launch.log"

        with patch(
            "sts2_autotest.core.test_agent_runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["open"], timeout=15.0),
        ):
            with pytest.raises(RuntimeError):
                from sts2_autotest.core.test_agent_runner import _run_launch_command

                _run_launch_command("Start Steam", ["open", "steam://run/2868840"], launch_log)

        text = launch_log.read_text(encoding="utf-8")
        assert "Timeout after 15.0s" in text
        assert "ExitCode: -1" in text

    def test_step_game_smoke_waits_until_actionable_before_navigation(self, tmp_path):
        """Health can pass before the main menu is ready; wait for actions first."""
        mod = tmp_path / "mod"
        infra = tmp_path / "infra"
        mod.mkdir()
        infra.mkdir()
        runner = TestAgentRunner(
            mod_project=str(mod),
            task_id="test-task",
            infra_path=str(infra),
        )
        runner._ensure_dirs()

        agent = AsyncMock()
        agent.health_check.return_value = HealthStatus(healthy=True)
        agent.wait_until_actionable.return_value = True
        state = {
            "screen": "COMBAT",
            "available_actions": ["play_card"],
            "combat": {
                "hand": [
                    {
                        "card_id": "GAWAINMOD-STRIKE_GAWAIN",
                        "name": "打击",
                        "index": 0,
                        "playable": True,
                        "rules_text": "造成 6 点伤害。",
                        "dynamic_values": [],
                    }
                ]
            },
        }
        agent.get_state.return_value = state

        with patch("sts2_autotest.core.test_agent_runner.AgentAdapter", return_value=agent):
            with patch.object(runner, "_navigate_to_first_combat", return_value=state) as navigate:
                with patch.object(runner, "_verify_card_and_screenshot", return_value={"status": "OK"}):
                    with patch.object(runner, "_capture_screenshot", return_value="screenshots/final.png"):
                        runner._step_game_smoke()

        agent.wait_until_actionable.assert_awaited()
        navigate.assert_called_once_with(agent)

    def test_step_game_smoke_keeps_text_evidence_for_unplayable_remaining_cards(self, tmp_path):
        """Cards that become unplayable after energy is spent should not fail text smoke."""
        mod = tmp_path / "mod"
        infra = tmp_path / "infra"
        mod.mkdir()
        infra.mkdir()
        runner = TestAgentRunner(
            mod_project=str(mod),
            task_id="test-task",
            infra_path=str(infra),
        )
        runner._ensure_dirs()

        combat_start = {
            "screen": "COMBAT",
            "available_actions": ["play_card"],
            "combat": {
                "hand": [
                    {
                        "card_id": "GAWAINMOD-DEFEND_GAWAIN",
                        "name": "防御",
                        "index": 0,
                        "playable": True,
                        "rules_text": "获得 5 点格挡。",
                        "dynamic_values": [],
                    },
                    {
                        "card_id": "GAWAINMOD-EMERGENCY_RECRUIT",
                        "name": "紧急征召",
                        "index": 1,
                        "playable": True,
                        "rules_text": "随机召唤 1 名基础仆从。",
                        "dynamic_values": [],
                    },
                ]
            },
            "run": {"character_id": "GAWAINMOD-GAWAIN", "character_name": "高文"},
        }
        text_only_state = {
            "screen": "COMBAT",
            "available_actions": ["end_turn"],
            "combat": {
                "hand": [
                    {
                        "card_id": "GAWAINMOD-EMERGENCY_RECRUIT",
                        "name": "紧急征召",
                        "index": 1,
                        "playable": False,
                        "rules_text": "随机召唤 1 名基础仆从。",
                        "resolved_rules_text": "随机召唤 1 名基础仆从。",
                        "dynamic_values": [],
                    }
                ]
            },
            "run": {"character_id": "GAWAINMOD-GAWAIN", "character_name": "高文"},
        }

        agent = AsyncMock()
        agent.health_check.return_value = HealthStatus(healthy=True)
        agent.wait_until_actionable.return_value = True
        agent.get_state.side_effect = [combat_start, text_only_state, text_only_state]
        agent.act.return_value = ActionResult(status="success", state_changed=True)

        with patch("sts2_autotest.core.test_agent_runner.AgentAdapter", return_value=agent):
            with patch.object(runner, "_navigate_to_first_combat", return_value=combat_start):
                with patch.object(runner, "_verify_card_and_screenshot", return_value={"status": "OK"}) as verify:
                    with patch.object(runner, "_build_text_only_card_result", return_value={"status": "TEXT_ONLY"}) as text_only:
                        runner._step_game_smoke()

        verify.assert_called_once()
        text_only.assert_called_once()
        assert any(result.name == "Card Smoke Test" and result.status == "PASSED" for result in runner.results)

    def test_build_html_report_config_includes_card_results_and_skip_status(self, tmp_path):
        """HTML report config should preserve skipped cases and card screenshot evidence."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        runner._artifact_dir = tmp_path / "artifacts"
        (runner._artifact_dir / "screenshots").mkdir(parents=True)
        (runner._artifact_dir / "screenshots" / "card-GAWAINMOD-STRIKE_GAWAIN-before.png").write_bytes(b"png")
        runner.results = [
            CheckResult("Deploy Mod", "SKIPPED", "deploy.log", "Skipped by flag"),
            CheckResult("Card Smoke Test", "PASSED", "screenshots/", "Card smoke finished"),
        ]
        runner._card_results = [
            {
                "card_id": "GAWAINMOD-STRIKE_GAWAIN",
                "name": "打击",
                "status": "TEXT_ONLY",
                "expected_damage": 6,
                "expected_block": 0,
                "screenshot_before": "automation/autotest/output/test-task/screenshots/card-GAWAINMOD-STRIKE_GAWAIN-before.png",
                "screenshot_after": "",
            }
        ]

        config = runner._build_html_report_config()

        assert config["test_cases"][0]["result"] == "跳过"
        assert config["test_cases"][1]["card_results"][0]["screenshot_before"] == "screenshots/card-GAWAINMOD-STRIKE_GAWAIN-before.png"
        assert config["card_results"][0]["result"] == "跳过"

    def test_open_character_select_retries_main_menu_preload_409(self, tmp_path):
        """The main menu can expose actions before open_character_select succeeds."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        mock_agent = AsyncMock()
        mock_agent.get_state.side_effect = [
            {"screen": "MAIN_MENU"},
            {"screen": "MAIN_MENU"},
            {"screen": "CHARACTER_SELECT"},
        ]
        mock_agent.get_available_actions.return_value = ["open_character_select"]
        mock_agent.act.side_effect = [
            ActionResult(status="failure", state_changed=False, detail="HTTP error: 409"),
            ActionResult(status="success", state_changed=True),
        ]
        mock_agent.wait_until_actionable.return_value = True

        with patch("sts2_autotest.core.test_agent_runner.time.sleep"):
            runner._open_character_select(mock_agent)

        assert mock_agent.act.await_count == 2

    def test_navigate_to_first_combat_prefers_gawain_character_id(self, tmp_path):
        """Gawain smoke should select by stable character id before UI position."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )

        mock_agent = AsyncMock()
        mock_agent.act.return_value = ActionResult(status="success", state_changed=True)
        mock_agent.wait_until_actionable.return_value = True
        mock_agent.get_state.side_effect = [
            {"screen": "CHARACTER_SELECT"},
            {"screen": "CHARACTER_SELECT"},
            {
                "screen": "COMBAT",
                "combat": {"players": [{"character_id": "GAWAINMOD-GAWAIN"}]},
                "run": {
                    "character_id": "GAWAINMOD-GAWAIN",
                    "character_name": "高文",
                    "relics": [
                        {
                            "relic_id": "GAWAINMOD-MAGIC_TERMINAL",
                            "name": "魔能终端",
                            "description": "战斗开始时，获得 1 点储能。",
                        }
                    ],
                    "deck": [
                        {"card_id": "GAWAINMOD-STRIKE_GAWAIN", "name": "打击", "rules_text": "造成 6 点伤害。"},
                        {"card_id": "GAWAINMOD-DEFEND_GAWAIN", "name": "防御", "rules_text": "获得 5 点格挡。"},
                        {"card_id": "GAWAINMOD-EMERGENCY_RECRUIT", "name": "紧急征召", "rules_text": "随机召唤 1 名基础仆从。"},
                        {"card_id": "GAWAINMOD-MAGIC_DRAW", "name": "魔力汲取", "rules_text": "获得 2 点储能。"},
                        {"card_id": "GAWAINMOD-PORTABLE_MAGIC_TERMINAL", "name": "便携魔导终端", "rules_text": "选择 1 名没有装备增益的仆从。"},
                    ],
                },
            },
        ]

        runner._navigate_to_first_combat(mock_agent)

        calls = [(call.args[0], call.args[1]) for call in mock_agent.act.call_args_list]
        assert ("select_character", {"character_id": "GAWAINMOD-GAWAIN"}) in calls
        assert not any(args == {"option_index": 0} for _, args in calls)

    def test_navigate_to_first_combat_resolves_gawain_option_index(self, tmp_path):
        """Gawain smoke must resolve the Gawain button index instead of hardcoding 0."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )

        class State(dict):
            pass

        mock_agent = AsyncMock()
        mock_agent.act.side_effect = [
            ActionResult(status="failure", state_changed=False, detail="runtime character_id unsupported"),
            ActionResult(status="failure", state_changed=False, detail="character_id unsupported"),
            ActionResult(status="failure", state_changed=False, detail="runtime character unsupported"),
            ActionResult(status="failure", state_changed=False, detail="character unsupported"),
            ActionResult(status="success", state_changed=True),
            ActionResult(status="success", state_changed=True),
        ]
        mock_agent.wait_until_actionable.return_value = True
        character_select_state = State(
            screen="CHARACTER_SELECT",
            character_select={
                "characters": [
                    {"index": 0, "character_id": "IRONCLAD"},
                    {"index": 1, "character_id": "THE_SILENT"},
                    {"index": 7, "character_id": "GAWAINMOD-GAWAIN", "name": "高文"},
                ]
            },
        )
        mock_agent.get_state.side_effect = [
            character_select_state,
            character_select_state,
            character_select_state,
            State(
                screen="COMBAT",
                combat={"players": [{"character_id": "GAWAINMOD-GAWAIN"}]},
                run={
                    "character_id": "GAWAINMOD-GAWAIN",
                    "character_name": "高文",
                    "relics": [
                        {
                            "relic_id": "GAWAINMOD-MAGIC_TERMINAL",
                            "name": "魔能终端",
                            "description": "战斗开始时，获得 1 点储能。",
                        }
                    ],
                    "deck": [
                        {
                            "card_id": "GAWAINMOD-STRIKE_GAWAIN",
                            "name": "打击",
                            "rules_text": "造成 6 点伤害。",
                        },
                        {
                            "card_id": "GAWAINMOD-DEFEND_GAWAIN",
                            "name": "防御",
                            "rules_text": "获得 5 点格挡。",
                        },
                        {
                            "card_id": "GAWAINMOD-EMERGENCY_RECRUIT",
                            "name": "紧急征召",
                            "rules_text": "随机召唤 1 名基础仆从。",
                        },
                        {
                            "card_id": "GAWAINMOD-MAGIC_DRAW",
                            "name": "魔力汲取",
                            "rules_text": "获得 2 点储能。",
                        },
                        {
                            "card_id": "GAWAINMOD-PORTABLE_MAGIC_TERMINAL",
                            "name": "便携魔导终端",
                            "rules_text": "选择 1 名没有装备增益的仆从。",
                        },
                    ],
                },
            ),
        ]

        runner._navigate_to_first_combat(mock_agent)

        calls = [(call.args[0], call.args[1]) for call in mock_agent.act.call_args_list]
        assert ("select_character", {"option_index": 7}) in calls
        assert ("select_character", {"option_index": 0}) not in calls

    def test_navigate_to_first_combat_advances_through_event_after_map(self, tmp_path):
        """Gawain smoke should continue through Neow/event screens until combat starts."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )

        combat_state = {
            "screen": "COMBAT",
            "combat": {"players": [{"character_id": "GAWAINMOD-GAWAIN"}]},
            "run": {
                "character_id": "GAWAINMOD-GAWAIN",
                "character_name": "高文",
                "relics": [
                    {
                        "relic_id": "GAWAINMOD-MAGIC_TERMINAL",
                        "name": "魔能终端",
                        "description": "战斗开始时，获得 1 点储能。",
                    }
                ],
                "deck": [
                    {"card_id": "GAWAINMOD-STRIKE_GAWAIN", "name": "打击", "rules_text": "造成 6 点伤害。"},
                    {"card_id": "GAWAINMOD-DEFEND_GAWAIN", "name": "防御", "rules_text": "获得 5 点格挡。"},
                    {"card_id": "GAWAINMOD-EMERGENCY_RECRUIT", "name": "紧急征召", "rules_text": "随机召唤 1 名基础仆从。"},
                    {"card_id": "GAWAINMOD-MAGIC_DRAW", "name": "魔力汲取", "rules_text": "获得 2 点储能。"},
                    {"card_id": "GAWAINMOD-PORTABLE_MAGIC_TERMINAL", "name": "便携魔导终端", "rules_text": "选择 1 名没有装备增益的仆从。"},
                ],
            },
        }
        mock_agent = AsyncMock()
        mock_agent.act.return_value = ActionResult(status="success", state_changed=True)
        mock_agent.wait_until_actionable.return_value = True
        mock_agent.get_state.side_effect = [
            {"screen": "CHARACTER_SELECT"},
            {"screen": "CHARACTER_SELECT"},
            {"screen": "MAP"},
            {"screen": "EVENT", "run": combat_state["run"]},
            combat_state,
        ]

        runner._navigate_to_first_combat(mock_agent)

        calls = [(call.args[0], call.args[1]) for call in mock_agent.act.call_args_list]
        assert ("choose_map_node", {"option_index": 0}) in calls
        assert ("choose_event_option", {"option_index": 0}) in calls

    def test_unresolved_gawain_key_scan_catches_model_loc_keys(self, tmp_path):
        """Raw GAWAINMOD localization keys must fail even when they use hyphen syntax."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        state = {
            "combat": {
                "players": [{"character_id": "gawain", "character_name": "GAWAINMOD-GAWAIN.title"}],
                "hand": [
                    {
                        "card_id": "GAWAINMOD-STRIKE_GAWAIN",
                        "name": "GAWAINMOD-STRIKE_GAWAIN.title",
                        "rules_text": "GAWAINMOD-STRIKE_GAWAIN.description",
                    }
                ],
            }
        }

        assert runner._find_unresolved_localization_keys(state)

    def test_gawain_runtime_text_state_requires_character_relic_and_deck(self, tmp_path):
        """Runtime acceptance must cover Gawain character, starter relic, and starter deck text."""
        runner = TestAgentRunner(
            mod_project=str(tmp_path / "mod"),
            task_id="test-task",
            infra_path=str(tmp_path / "infra"),
        )
        state = {
            "run": {
                "character_id": "GAWAINMOD-GAWAIN",
                "character_name": "高文",
                "relics": [
                    {
                        "relic_id": "GAWAINMOD-MAGIC_TERMINAL",
                        "name": "魔能终端",
                        "description": "战斗开始时，获得 1 点储能。",
                    }
                ],
                "deck": [
                    {
                        "card_id": "GAWAINMOD-STRIKE_GAWAIN",
                        "name": "打击",
                        "rules_text": "造成 6 点伤害。",
                    },
                    {
                        "card_id": "GAWAINMOD-DEFEND_GAWAIN",
                        "name": "防御",
                        "rules_text": "获得 5 点格挡。",
                    },
                    {
                        "card_id": "GAWAINMOD-EMERGENCY_RECRUIT",
                        "name": "紧急征召",
                        "rules_text": "随机召唤 1 名基础仆从。",
                    },
                    {
                        "card_id": "GAWAINMOD-MAGIC_DRAW",
                        "name": "魔力汲取",
                        "rules_text": "获得 2 点储能。",
                    },
                    {
                        "card_id": "GAWAINMOD-PORTABLE_MAGIC_TERMINAL",
                        "name": "便携魔导终端",
                        "rules_text": "选择 1 名没有装备增益的仆从。",
                    },
                ],
            }
        }

        runner._assert_gawain_runtime_text_state(state)
