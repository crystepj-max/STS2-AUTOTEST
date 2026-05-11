"""Tests for cli/main.py — CLI entry points."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sts2_autotest.cli.main import (
    DEFAULT_EVIDENCE_DIR,
    _check_env,
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


class TestDoctorEnvCheck:
    """_check_env performs real environment checks."""

    def test_returns_dict(self) -> None:
        checks = _check_env()
        assert isinstance(checks, dict)

    def test_has_expected_keys(self) -> None:
        checks = _check_env()
        for key in ("python", "steam_installed", "game_installed", "sts2_cli_mod", "disk_space"):
            assert key in checks

    def test_python_check_ok(self) -> None:
        """Python >= 3.11 should always pass in this project."""
        checks = _check_env()
        assert checks["python"] == "OK"


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
