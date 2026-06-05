"""Test card smoke validation logic with mocked AgentAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sts2_autotest.adapters.base import ActionResult
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.test_agent_runner import TestAgentRunner


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

        with patch("mss.mss") as mock_mss:
            mock_sct = MagicMock()
            mock_mss.mss.return_value.__enter__.return_value = mock_sct
            mock_sct.monitors = [{}, {"width": 1920, "height": 1080}]
            result = runner._capture_screenshot("test-card.png")

        assert "screenshots/test-card.png" in result

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
