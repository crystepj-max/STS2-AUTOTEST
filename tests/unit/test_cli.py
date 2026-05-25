"""Tests for cli/main.py — CLI entry points."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sts2_autotest.cli.main import (
    DEFAULT_EVIDENCE_DIR,
    _check_env,
    _create_adapter,
    _create_parser,
    doctor_cmd,
    report_cmd,
    run_cmd,
)


class TestCLIParser:
    """CLI argument parsing."""

    def test_parser_has_commands(self) -> None:
        p = _create_parser()
        assert p is not None

    def test_run_all(self) -> None:
        args = _create_parser().parse_args(["run", "--all"])
        assert args.command == "run"
        assert args.all is True

    def test_run_cases(self) -> None:
        args = _create_parser().parse_args(["run", "--cases", "TC-001", "TC-002"])
        assert args.cases == ["TC-001", "TC-002"]

    def test_run_timeout_default(self) -> None:
        args = _create_parser().parse_args(["run", "--all"])
        assert args.timeout == 30

    def test_doctor_json(self) -> None:
        args = _create_parser().parse_args(["doctor", "--json"])
        assert args.json is True

    def test_report_with_id(self) -> None:
        args = _create_parser().parse_args(["report", "run-001"])
        assert args.run_id == "run-001"

    def test_report_evidence_dir(self) -> None:
        args = _create_parser().parse_args(["report", "--evidence-dir", "/tmp/evidence"])
        assert args.evidence_dir == "/tmp/evidence"


class TestCLICommands:
    """CLI command dispatch."""

    def test_run_all_returns_zero_or_one(self) -> None:
        """run_cmd executes orchestrator and returns exit code."""
        args = _create_parser().parse_args(["run", "--all"])
        result = run_cmd(args)
        assert result in (0, 1)

    def test_run_no_option_returns_one(self) -> None:
        args = _create_parser().parse_args(["run"])
        result = run_cmd(args)
        assert result == 1

    def test_doctor_returns_zero_or_one(self) -> None:
        args = _create_parser().parse_args(["doctor"])
        result = doctor_cmd(args)
        assert result in (0, 1)

    def test_doctor_json_output(self) -> None:
        args = _create_parser().parse_args(["doctor", "--json"])
        # doctor_cmd prints JSON to stdout; just ensure it doesn't crash
        result = doctor_cmd(args)
        assert result in (0, 1)

    def test_report_missing_evidence_dir(self) -> None:
        args = _create_parser().parse_args(["report", "run-001", "--evidence-dir", "/nonexistent"])
        result = report_cmd(args)
        assert result == 1

    def test_invalid_command_prints_help(self) -> None:
        try:
            _create_parser().parse_args([])
        except SystemExit:
            pass  # expected when no command given


class TestCreateAdapter:
    """_create_adapter reads agent transport env vars."""

    def test_agent_mcp_endpoint_inherits_agent_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from sts2_autotest.adapters.agent import AgentAdapter, FastMcpAgentClient

        monkeypatch.setenv("STS2_ADAPTER__AGENT__TRANSPORT", "mcp")
        monkeypatch.setenv("STS2_ADAPTER__AGENT__ENDPOINT", "http://example.test/custom")
        monkeypatch.delenv("STS2_ADAPTER__AGENT__MCP_ENDPOINT", raising=False)

        adapter = _create_adapter("agent")

        assert isinstance(adapter, AgentAdapter)
        assert adapter.endpoint == "http://example.test/custom"
        assert isinstance(adapter._mcp_client, FastMcpAgentClient)
        assert adapter._mcp_client.endpoint == "http://example.test/custom"

    def test_agent_mcp_endpoint_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from sts2_autotest.adapters.agent import AgentAdapter, FastMcpAgentClient

        monkeypatch.setenv("STS2_ADAPTER__AGENT__TRANSPORT", "mcp")
        monkeypatch.setenv("STS2_ADAPTER__AGENT__ENDPOINT", "http://example.test/custom")
        monkeypatch.setenv("STS2_ADAPTER__AGENT__MCP_ENDPOINT", "http://mcp.example.test/override")

        adapter = _create_adapter("agent")

        assert isinstance(adapter, AgentAdapter)
        assert adapter.endpoint == "http://example.test/custom"
        assert isinstance(adapter._mcp_client, FastMcpAgentClient)
        assert adapter._mcp_client.endpoint == "http://mcp.example.test/override"


class TestDoctorEnvCheck:
    """_check_env performs real environment checks."""

    def test_returns_dict(self) -> None:
        checks = _check_env()
        assert isinstance(checks, dict)

    def test_has_expected_keys(self) -> None:
        checks = _check_env()
        expected_keys = (
            "python", "steam_installed", "game_installed",
            "sts2_cli_mod", "disk_space", "screenshot_dir_writable", "session_locked",
        )
        for key in expected_keys:
            assert key in checks, f"Missing key: {key}"

    def test_python_check_ok(self) -> None:
        """Python >= 3.11 should always pass in this project."""
        checks = _check_env()
        assert checks["python"]["status"] == "OK"


class TestDoctorCI:
    """doctor --ci compact JSON output."""

    @patch("sts2_autotest.cli.main._check_env")
    def test_ci_healthy(self, mock_check: patch) -> None:
        """All OK → healthy: true, exit 0."""
        mock_check.return_value = {
            "python": {"status": "OK", "message": "3.11"},
            "disk_space": {"status": "OK", "message": "ok"},
        }
        args = _create_parser().parse_args(["doctor", "--ci"])
        result = doctor_cmd(args)
        assert result == 0

    @patch("sts2_autotest.cli.main._check_env")
    def test_ci_not_found_is_unhealthy(self, mock_check: patch) -> None:
        """NOT_FOUND status must count as unhealthy (AC1/AC3 regression)."""
        mock_check.return_value = {
            "python": {"status": "OK", "message": "3.11"},
            "steam_installed": {"status": "NOT_FOUND", "message": "Steam not found"},
        }
        args = _create_parser().parse_args(["doctor", "--ci"])
        result = doctor_cmd(args)
        assert result == 1  # NOT_FOUND → unhealthy

    @patch("sts2_autotest.cli.main._check_env")
    def test_ci_multiple_failures(self, mock_check: patch) -> None:
        """Multiple non-OK checks produce exit code 1."""
        mock_check.return_value = {
            "python": {"status": "OK", "message": "3.11"},
            "steam_installed": {"status": "NOT_FOUND", "message": "missing"},
            "disk_space": {"status": "FAIL", "message": "low"},
        }
        args = _create_parser().parse_args(["doctor", "--ci"])
        result = doctor_cmd(args)
        assert result == 1


class TestReportFromEvidence:
    """report_cmd reads summary.json from evidence directory."""

    def test_report_reads_summary_json(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-001"
        run_dir.mkdir()
        summary = {"schema_version": "1.0.0", "pack_id": "run-001"}
        (run_dir / "summary.json").write_text(json.dumps(summary))

        args = _create_parser().parse_args(["report", "run-001", "--evidence-dir", str(tmp_path)])
        result = report_cmd(args)
        assert result == 0

    def test_report_invalid_json(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-002"
        run_dir.mkdir()
        (run_dir / "summary.json").write_text("not valid json")

        args = _create_parser().parse_args(["report", "run-002", "--evidence-dir", str(tmp_path)])
        result = report_cmd(args)
        assert result == 1

    def test_report_lists_available_runs(self, tmp_path: Path) -> None:
        (tmp_path / "run-a").mkdir()
        (tmp_path / "run-b").mkdir()

        args = _create_parser().parse_args(["report", "latest", "--evidence-dir", str(tmp_path)])
        result = report_cmd(args)
        assert result == 1  # no summary.json, but should list runs


class TestCLIEntryPoint:
    """Main CLI function."""

    def test_cli_function_exists(self) -> None:
        from sts2_autotest.cli.main import cli
        assert callable(cli)


# ── resume / progress tests (Story 4.5, AC1-AC4) ────────────


class TestResume:
    """CLI resume/no-resume/corruption behavior."""

    @patch("sts2_autotest.cli.main._get_progress_path")
    @patch("sts2_autotest.cli.main._run_orchestrator_with_adapter")
    def test_normal_run_passes_progress_path(
        self, mock_run: patch, mock_path: patch, tmp_path: Path,
    ) -> None:
        """Normal run passes the default progress path to orchestrator."""
        progress_file = tmp_path / "progress.json"
        mock_path.return_value = progress_file

        args = _create_parser().parse_args(["run", "--all"])
        # progress file does not exist, so no auto-detect prompt
        run_cmd(args)

        # adapter is first positional arg; verify case_ids and kwargs
        call_args = mock_run.call_args
        assert call_args is not None
        args_list, kwargs = call_args[0], call_args[1]
        assert args_list[1] == ["all"]  # case_ids is 2nd positional
        assert kwargs.get("timeout") == 30
        assert kwargs.get("progress_path") == str(progress_file)

    @patch("sts2_autotest.cli.main._get_progress_path")
    @patch("sts2_autotest.cli.main._run_orchestrator_with_adapter")
    def test_resume_with_valid_progress(
        self, mock_run: patch, mock_path: patch, tmp_path: Path,
    ) -> None:
        """--resume with valid progress file uses pending cases."""
        from sts2_autotest.core.progress import ProgressRecord, save_progress

        progress_file = tmp_path / "progress.json"
        mock_path.return_value = progress_file

        # Save a valid progress record
        record = ProgressRecord(
            session_id="sess-1",
            completed_cases=["TC-001"],
            pending_cases=["TC-002", "TC-003"],
        )
        save_progress(record, progress_file)

        args = _create_parser().parse_args(["run", "--resume"])
        run_cmd(args)

        call_args = mock_run.call_args
        assert call_args is not None
        args_list, kwargs = call_args[0], call_args[1]
        assert args_list[1] == ["TC-002", "TC-003"]
        assert kwargs.get("timeout") == 30
        assert kwargs.get("resumed_from") == "sess-1"

    @patch("sts2_autotest.cli.main._get_progress_path")
    @patch("sts2_autotest.cli.main._run_orchestrator_with_adapter")
    def test_resume_corrupted_degrades_to_full_run(
        self, mock_run: patch, mock_path: patch, tmp_path: Path,
    ) -> None:
        """AC4: corrupted progress with --resume --all runs full suite."""
        progress_file = tmp_path / "progress.json"
        mock_path.return_value = progress_file
        progress_file.write_text("corrupted data")

        args = _create_parser().parse_args(["run", "--resume", "--all"])
        run_cmd(args)

        # Degrades to full run with warning (AC4)
        call_args = mock_run.call_args
        assert call_args is not None
        args_list, kwargs = call_args[0], call_args[1]
        assert args_list[1] == ["all"]
        assert kwargs.get("timeout") == 30

    @patch("sts2_autotest.cli.main._get_progress_path")
    def test_auto_detect_prompts_user(self, mock_path: patch, tmp_path: Path) -> None:
        """Progress exists without --resume/--no-resume → prompt and return 1."""
        progress_file = tmp_path / "progress.json"
        mock_path.return_value = progress_file
        progress_file.write_text("{}")

        args = _create_parser().parse_args(["run", "--all"])
        result = run_cmd(args)
        assert result == 1

    @patch("sts2_autotest.cli.main._get_progress_path")
    @patch("sts2_autotest.cli.main._run_orchestrator_with_adapter")
    def test_no_resume_clears_progress(
        self, mock_run: patch, mock_path: patch, tmp_path: Path,
    ) -> None:
        """--no-resume deletes old progress and runs fresh."""
        from sts2_autotest.core.progress import ProgressRecord, save_progress

        progress_file = tmp_path / "progress.json"
        mock_path.return_value = progress_file

        # Save a valid progress record
        record = ProgressRecord(session_id="old", completed_cases=[], pending_cases=["TC-001"])
        save_progress(record, progress_file)
        assert progress_file.is_file()

        args = _create_parser().parse_args(["run", "--no-resume", "--all"])
        run_cmd(args)

        # Progress file should be deleted
        assert not progress_file.exists()
        # Runs normally — first arg is adapter, second is case_ids
        call_args = mock_run.call_args
        assert call_args is not None
        args_list, kwargs = call_args[0], call_args[1]
        assert args_list[1] == ["all"]
        assert kwargs.get("timeout") == 30
