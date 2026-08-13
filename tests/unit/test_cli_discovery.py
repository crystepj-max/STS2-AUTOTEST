"""Tests for adapters/discovery.py — STS2-Cli-Mod CLI discovery."""

import os
from unittest.mock import patch

from sts2_autotest.adapters.discovery import discover_sts2_cli


class TestDiscoverSts2Cli:
    """CLI discovery mechanism tests."""

    @patch.dict(os.environ, {"STS2_CLI_PATH": "/custom/path/sts2.exe"})
    @patch("pathlib.Path.is_file", return_value=True)
    def test_env_variable_takes_priority(self, mock_is_file: object) -> None:
        result = discover_sts2_cli()
        assert result == "/custom/path/sts2.exe"

    @patch.dict(os.environ, {"STS2_CLI_PATH": "/nonexistent/sts2.exe"})
    @patch("pathlib.Path.is_file", return_value=False)
    @patch("shutil.which", return_value=None)
    def test_env_variable_file_must_exist(self, mock_which: object, mock_is_file: object) -> None:
        result = discover_sts2_cli()
        assert result is None

    @patch.dict(os.environ, {}, clear=True)
    @patch("shutil.which", return_value="/usr/bin/sts2")
    def test_system_path_found(self, mock_which: object) -> None:
        result = discover_sts2_cli()
        assert result == "/usr/bin/sts2"

    @patch.dict(os.environ, {}, clear=True)
    @patch("shutil.which", return_value=None)
    @patch("pathlib.Path.is_file", return_value=False)
    def test_returns_none_when_not_found(self, mock_is_file: object, mock_which: object) -> None:
        result = discover_sts2_cli()
        assert result is None

    @patch.dict(os.environ, {"STS2_CLI_PATH": "/env/sts2.exe"}, clear=False)
    @patch("pathlib.Path.is_file", return_value=True)
    def test_env_path_found_when_file_exists(self, mock_is_file: object) -> None:
        result = discover_sts2_cli()
        assert result == "/env/sts2.exe"
