"""Integration tests: AgentAdapter CLI config loading and adapter switching.

The CLI uses argparse (not Click), so we test it via direct function calls
with capsys/monkeypatch rather than Click's CliRunner.
"""

from __future__ import annotations

import pytest

from sts2_autotest.cli.main import cli


class TestAgentCliConfig:
    """Verify CLI config loading works with agent adapter settings."""

    def test_agent_env_config_does_not_crash(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Given agent adapter enabled in env, CLI should parse successfully."""
        monkeypatch.setenv("STS2_ADAPTER__AGENT__ENABLED", "true")
        monkeypatch.setenv("STS2_ADAPTER__CLI__ENABLED", "false")
        monkeypatch.setenv(
            "STS2_ADAPTER__AGENT__ENDPOINT", "http://localhost:9999",
        )

        # --help is handled by argparse before any adapter code runs
        with pytest.raises(SystemExit) as excinfo:
            cli(["run", "--help"])
        assert excinfo.value.code == 0

    def test_mutual_exclusion_still_enforced(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        """Both cli and agent enabled should error on config validation."""
        monkeypatch.setenv("STS2_ADAPTER__AGENT__ENABLED", "true")
        monkeypatch.setenv("STS2_ADAPTER__CLI__ENABLED", "true")

        with pytest.raises(SystemExit) as excinfo:
            cli(["doctor"])
        assert excinfo.value.code != 0

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert (
            "Mutual" in output
            or "mutual" in output.lower()
            or "exclusion" in output
        )

    def test_help_shows_adapter_option(self, capsys: pytest.CaptureFixture) -> None:
        """autotest run --help should include --adapter option."""
        with pytest.raises(SystemExit) as excinfo:
            cli(["run", "--help"])
        assert excinfo.value.code == 0

        captured = capsys.readouterr()
        assert "--adapter" in captured.out
        assert "cli" in captured.out
        assert "agent" in captured.out

    def test_invalid_adapter_value_rejected(
        self, capsys: pytest.CaptureFixture,
    ) -> None:
        """--adapter with invalid value should error."""
        with pytest.raises(SystemExit) as excinfo:
            cli(["run", "--adapter", "invalid"])
        assert excinfo.value.code != 0
