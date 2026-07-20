"""Tests for cli/main.py — CLI entry points."""

import os
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from sts2_autotest.cli.main import (
    _check_env,
    _create_adapter,
    _create_parser,
    doctor_cmd,
    progress_cmd,
    queue_cmd,
    report_cmd,
    run_cmd,
    visual_qa_cmd,
)
from sts2_autotest.common.visual_qa import (
    DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_VARIANCE_THRESHOLD,
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

    def test_run_journey_contract(self) -> None:
        args = _create_parser().parse_args(
            ["run", "--journey", "first_battle", "--character-id", "IRONCLAD"]
        )
        assert args.journey == "first_battle"
        assert args.character_id == "IRONCLAD"

    def test_doctor_json(self) -> None:
        args = _create_parser().parse_args(["doctor", "--json"])
        assert args.json is True

    def test_report_with_id(self) -> None:
        args = _create_parser().parse_args(["report", "run-001"])
        assert args.run_id == "run-001"

    def test_capabilities_command_parses(self) -> None:
        args = _create_parser().parse_args(["capabilities", "--json"])
        assert args.command == "capabilities"
        assert args.json is True

    def test_report_evidence_dir(self) -> None:
        args = _create_parser().parse_args(["report", "--evidence-dir", "/tmp/evidence"])
        assert args.evidence_dir == "/tmp/evidence"

    def test_report_coverage_flag(self) -> None:
        args = _create_parser().parse_args(["report", "run-001", "--coverage"])
        assert args.run_id == "run-001"
        assert args.coverage is True

    def test_queue_pause_command_parses(self) -> None:
        args = _create_parser().parse_args(["queue", "pause"])
        assert args.command == "queue"
        assert args.queue_action == "pause"

    def test_queue_resume_command_parses(self) -> None:
        args = _create_parser().parse_args(["queue", "resume"])
        assert args.command == "queue"
        assert args.queue_action == "resume"

    def test_queue_status_command_parses(self) -> None:
        args = _create_parser().parse_args(["queue", "status"])
        assert args.command == "queue"
        assert args.queue_action == "status"

    def test_progress_command_parses(self) -> None:
        args = _create_parser().parse_args(["progress"])
        assert args.command == "progress"

    def test_visual_qa_command_parses(self) -> None:
        args = _create_parser().parse_args(
            [
                "visual-qa",
                "--image",
                "/tmp/example.png",
                "--ocr-provider",
                "tesseract",
                "--output",
                "/tmp/visual-qa.json",
            ]
        )
        assert args.command == "visual-qa"
        assert args.image == "/tmp/example.png"
        assert args.ocr_provider == "tesseract"
        assert args.health_provider == "disabled"
        assert args.low_variance_threshold == DEFAULT_LOW_VARIANCE_THRESHOLD
        assert args.low_brightness_threshold == DEFAULT_LOW_BRIGHTNESS_THRESHOLD
        assert args.high_brightness_threshold == DEFAULT_HIGH_BRIGHTNESS_THRESHOLD
        assert args.output == "/tmp/visual-qa.json"


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

    def test_detached_run_persists_run_id_without_blocking(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        args = _create_parser().parse_args(["run", "--all", "--detach"])
        with patch("sts2_autotest.core.run_service.spawn_worker") as worker:
            assert run_cmd(args) == 0
        worker.assert_called_once()
        run_id = worker.call_args.args[1].run_id
        from sts2_autotest.core.run_service import RunStore

        saved = RunStore(tmp_path / "runs").load(run_id)
        assert saved is not None
        assert saved.status == "QUEUED"
        assert saved.request.argv[-2:] == ["--internal-run-id", run_id]

    def test_journey_run_uses_common_journey_entrypoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        args = _create_parser().parse_args(
            ["run", "--journey", "first_battle", "--character-id", "IRONCLAD"]
        )
        adapter = object()
        monkeypatch.setattr("sts2_autotest.cli.main._create_adapter", lambda _: adapter)
        with patch(
            "sts2_autotest.cli.main._run_journey_foreground", return_value=0
        ) as journey:
            assert run_cmd(args) == 0
        journey.assert_called_once_with(
            adapter,
            journey="first_battle",
            character_id="IRONCLAD",
            timeout=30.0,
            run_id=None,
            precheck=True,
        )

    def test_run_all_pipeline_targets_suite_files_when_suites_exist(
        self, tmp_path: Path
    ) -> None:
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        (spec_dir / "SUITE-FIRST-BATTLE-SMOKE.md").write_text(
            """\
# SUITE-FIRST-BATTLE-SMOKE 首次战斗冒烟

## Metadata
- id: SUITE-FIRST-BATTLE-SMOKE
- level: suite

## Includes
1. TC-PREPARE-NEW-RUN
""",
            encoding="utf-8",
        )
        output_dir = tmp_path / "generated"

        args = _create_parser().parse_args(
            [
                "run",
                "--all",
                "--spec-dir",
                str(spec_dir),
                "--output-dir",
                str(output_dir),
            ]
        )

        with (
            patch("sts2_autotest.cli.main._get_progress_path") as mock_progress,
            patch("sts2_autotest.cli.main._create_adapter"),
            patch("sts2_autotest.cli.main.review_cmd", return_value=0),
            patch("sts2_autotest.cli.main.compile_cmd", return_value=0),
            patch(
                "sts2_autotest.cli.mcp_tools.run_tests_in_dir",
                return_value={"status": "OK", "duration_ms": 1},
            ) as mock_runner,
        ):
            mock_progress.return_value = tmp_path / "progress.json"

            assert run_cmd(args) == 0

        kwargs = mock_runner.call_args.kwargs
        assert str(output_dir / "test_suite_first_battle_smoke.py") in {
            str(path) for path in kwargs["targets"]
        }
        assert kwargs["output_dir"] == str(output_dir)

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

    def test_queue_cmd_returns_zero(self) -> None:
        args = _create_parser().parse_args(["queue", "status"])
        assert queue_cmd(args) == 0

    @patch("sts2_autotest.cli.main._get_progress_path")
    def test_progress_cmd_reads_runtime_status(
        self, mock_path: patch, tmp_path: Path,
    ) -> None:
        from sts2_autotest.core.progress import ProgressRecord, save_progress

        progress_file = tmp_path / "progress.json"
        mock_path.return_value = progress_file
        save_progress(
            ProgressRecord(
                session_id="sess-1",
                completed_cases=["TC-1"],
                pending_cases=["TC-2"],
                current_case="TC-2",
                current_step="play-card",
                game_screen="COMBAT",
                recovery_status="FAST_PATH",
                paused=True,
            ),
            progress_file,
        )

        args = _create_parser().parse_args(["progress"])
        assert progress_cmd(args) == 0

    @patch("sts2_autotest.cli.main._get_progress_path")
    def test_progress_cmd_returns_one_for_corrupted_progress(
        self, mock_path: patch, tmp_path: Path,
    ) -> None:
        progress_file = tmp_path / "progress.json"
        mock_path.return_value = progress_file
        progress_file.write_text("not-json", encoding="utf-8")

        args = _create_parser().parse_args(["progress"])
        assert progress_cmd(args) == 1

    def test_visual_qa_cmd_outputs_json_for_single_image(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        image = tmp_path / "single.png"
        image.write_bytes(b"png")
        args = _create_parser().parse_args(["visual-qa", "--image", str(image)])

        class FakeEngine:
            def analyze_screenshot(self, image_path: Path) -> object:
                from sts2_autotest.common.visual_qa import ScreenshotOcrAnalysis

                assert image_path == image
                return ScreenshotOcrAnalysis(status="passed", provider="disabled")

        with patch("sts2_autotest.cli.main._build_visual_qa_engine", return_value=FakeEngine()):
            result = visual_qa_cmd(args)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"] == {
            "total": 1,
            "passed": 1,
            "warning": 0,
            "skipped": 0,
            "findings_total": 0,
            "screenshots_with_findings": 0,
            "providers": {"disabled": 1},
            "status_by_provider": {
                "disabled": {"passed": 1, "warning": 0, "skipped": 0},
            },
            "findings": {},
            "findings_by_severity": {},
        }
        assert payload["screenshots"][str(image)]["status"] == "passed"

    def test_visual_qa_cmd_writes_output_file_when_requested(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        image = tmp_path / "single.png"
        image.write_bytes(b"png")
        output = tmp_path / "visual-qa.json"
        args = _create_parser().parse_args(
            ["visual-qa", "--image", str(image), "--output", str(output)]
        )

        class FakeEngine:
            def analyze_screenshot(self, image_path: Path) -> object:
                from sts2_autotest.common.visual_qa import ScreenshotOcrAnalysis

                assert image_path == image
                return ScreenshotOcrAnalysis(status="passed", provider="disabled")

        with patch("sts2_autotest.cli.main._build_visual_qa_engine", return_value=FakeEngine()):
            result = visual_qa_cmd(args)

        assert result == 0
        stdout_payload = json.loads(capsys.readouterr().out)
        file_payload = json.loads(output.read_text(encoding="utf-8"))
        assert stdout_payload == file_payload

    def test_visual_qa_cmd_returns_one_when_image_missing(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        image = tmp_path / "missing.png"
        args = _create_parser().parse_args(["visual-qa", "--image", str(image)])

        result = visual_qa_cmd(args)

        assert result == 1
        assert "Image file not found" in capsys.readouterr().out


class TestCreateAdapter:
    """_create_adapter reads agent transport env vars."""

    def test_agent_http_defaults_use_loopback_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from sts2_autotest.adapters.agent import AgentAdapter

        monkeypatch.delenv("STS2_ADAPTER__AGENT__TRANSPORT", raising=False)
        monkeypatch.delenv("STS2_ADAPTER__AGENT__ENDPOINT", raising=False)

        adapter = _create_adapter("agent")

        assert isinstance(adapter, AgentAdapter)
        assert adapter.endpoint == "http://127.0.0.1:8080"

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

    def test_report_coverage_reads_markdown(self, tmp_path: Path) -> None:
        report_path = tmp_path / "run-cov" / "reports" / "scene-coverage.md"
        report_path.parent.mkdir(parents=True)
        report_path.write_text("# Scene Coverage\n\n| COMBAT | 2 |", encoding="utf-8")

        args = _create_parser().parse_args(
            ["report", "run-cov", "--coverage", "--evidence-dir", str(tmp_path)]
        )
        assert report_cmd(args) == 0

    def test_report_coverage_missing_file_returns_one(self, tmp_path: Path) -> None:
        (tmp_path / "run-cov" / "reports").mkdir(parents=True)

        args = _create_parser().parse_args(
            ["report", "run-cov", "--coverage", "--evidence-dir", str(tmp_path)]
        )
        assert report_cmd(args) == 1

    def test_report_discovers_runs_store_run(self, tmp_path: Path) -> None:
        """detach 任务状态存于 .runs/{rid}/run.json，report 应回退发现并合成。"""
        store_run = tmp_path / ".runs" / "run-detached" / "run.json"
        store_run.parent.mkdir(parents=True)
        store_run.write_text(json.dumps({
            "run_id": "run-detached",
            "status": "CANCELLED",
            "result": {"pre_cancel_screen": "EVENT", "recovered_screen": "MAIN_MENU"},
        }))

        args = _create_parser().parse_args(
            ["report", "run-detached", "--evidence-dir", str(tmp_path)]
        )
        assert report_cmd(args) == 0
        # 应落盘 run-result.json 且状态一致
        rr = tmp_path / "run-detached" / "reports" / "run-result.json"
        assert rr.is_file()
        payload = json.loads(rr.read_text(encoding="utf-8"))
        assert payload["status"] == "CANCELLED"
        assert payload["pre_cancel_screen"] == "EVENT"

    def test_report_reads_run_result_json_without_summary(self, tmp_path: Path) -> None:
        """仅有 run-result.json、无 summary.json 时直接打印。"""
        rr = tmp_path / "run-only" / "reports" / "run-result.json"
        rr.parent.mkdir(parents=True)
        rr.write_text(json.dumps({"run_id": "run-only", "status": "PASSED"}))

        args = _create_parser().parse_args(
            ["report", "run-only", "--evidence-dir", str(tmp_path)]
        )
        assert report_cmd(args) == 0


class TestCLIEntryPoint:
    """Main CLI function."""

    def test_cli_function_exists(self) -> None:
        from sts2_autotest.cli.main import cli
        assert callable(cli)

    def test_module_entrypoint_dispatches_visual_qa(self, tmp_path: Path) -> None:
        image = tmp_path / "single.png"
        image.write_bytes(b"png")
        output = tmp_path / "visual-qa.json"
        env = {
            **os.environ,
            "PYTHONPATH": str(Path.cwd() / "src"),
        }

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "sts2_autotest.cli.main",
                "visual-qa",
                "--image",
                str(image),
                "--output",
                str(output),
            ],
            check=False,
            capture_output=True,
            env=env,
            text=True,
        )

        assert result.returncode == 0
        assert output.is_file()


# ── resume / progress tests (Story 4.5, AC1-AC4) ────────────


class TestResume:
    """CLI resume/no-resume/corruption behavior."""

    @patch("sts2_autotest.cli.main._get_progress_path")
    @patch("sts2_autotest.cli.main._run_orchestrator_with_adapter")
    @patch("sts2_autotest.cli.main._resolve_spec_dir", return_value=None)
    def test_normal_run_passes_progress_path(
        self, _mock_spec: patch, mock_run: patch, mock_path: patch, tmp_path: Path,
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
    @patch("sts2_autotest.cli.main._resolve_spec_dir", return_value=None)
    def test_resume_corrupted_degrades_to_full_run(
        self, _mock_spec: patch, mock_run: patch, mock_path: patch, tmp_path: Path,
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
    @patch("sts2_autotest.cli.main._resolve_spec_dir", return_value=None)
    def test_no_resume_clears_progress(
        self, _mock_spec: patch, mock_run: patch, mock_path: patch, tmp_path: Path,
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


class TestCancelLifecycle:
    """修复三：取消是完整生命周期，收尾失败要正确归类。"""

    def test_classify_control_loss_maps_to_blocked_environment_reason(self) -> None:
        from sts2_autotest.cli.main import _classify_cancel_cleanup_error
        from sts2_autotest.common.errors import CancelFailureReason

        exc = RuntimeError("connection refused while abandoning run")
        assert (
            _classify_cancel_cleanup_error(exc)
            == CancelFailureReason.GAME_CONTROL_UNAVAILABLE.value
        )

    def test_classify_generic_cleanup_failure_maps_to_cleanup_failed(self) -> None:
        from sts2_autotest.cli.main import _classify_cancel_cleanup_error
        from sts2_autotest.common.errors import CancelFailureReason

        exc = RuntimeError("could not reach MAIN_MENU: unexpected screen")
        assert (
            _classify_cancel_cleanup_error(exc)
            == CancelFailureReason.CANCEL_CLEANUP_FAILED.value
        )

    def test_cancel_cmd_requests_graceful_cancel_without_terminating(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cancel_cmd 只发起优雅取消：任务进入 CANCELLING 非终态，不立即杀进程。"""
        from argparse import Namespace

        from sts2_autotest.cli import main as cli_main
        from sts2_autotest.core.run_service import RunRequest, RunStore

        store = RunStore(tmp_path / "runs")
        record = store.create(RunRequest(), run_id="run-graceful")
        store.update(record.run_id, status="RUNNING", phase="RUNNING", pid=99999)
        monkeypatch.setattr(cli_main, "_run_store", lambda: store)
        killed: list[int] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))

        rc = cli_main.cancel_cmd(Namespace(run_id="run-graceful"))

        assert rc == 0
        after = store.load("run-graceful")
        assert after is not None
        assert after.cancel_requested is True
        assert after.is_terminal is False
        assert after.phase == "CANCELLING"
        assert killed == []

    def test_status_cmd_reaps_run_whose_worker_disappeared(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """修复残留风险：status_cmd 查询时懒回收，worker 进程已消失的非终态运行
        被标为终态，避免僵尸记录污染审计。"""
        from argparse import Namespace

        from sts2_autotest.cli import main as cli_main
        from sts2_autotest.core import run_service as rs
        from sts2_autotest.core.run_service import RunRequest, RunStore

        store = RunStore(tmp_path / "runs")
        record = store.create(RunRequest(), run_id="run-zombie")
        store.update(record.run_id, status="RUNNING", phase="RUNNING", pid=99999)
        monkeypatch.setattr(cli_main, "_run_store", lambda: store)

        def _raise_lookup_error(pid: int, sig: int) -> None:
            raise ProcessLookupError("no such process")

        monkeypatch.setattr(os, "kill", _raise_lookup_error)
        # 本测试仅模拟 worker 进程消失，隐含游戏控制链路仍可用；固定控制可达，
        # 使终态判定确定（否则在无游戏环境下 _game_control_reachable 返回 False，
        # 会被判 BLOCKED_ENVIRONMENT，掩盖本测试真正要验证的「懒回收」行为）。
        monkeypatch.setattr(rs, "_game_control_reachable", lambda *a, **k: True)

        rc = cli_main.status_cmd(Namespace(run_id="run-zombie", json=False))

        assert rc == 0
        reaped = store.load("run-zombie")
        assert reaped is not None
        assert reaped.status == "FAILED_PLATFORM"
        assert reaped.is_terminal is True


class TestCombatModeDebugDoubleCheck:
    """修复二运行期双真：旅程启动前再验证调试能力，据此决定有效战斗模式。"""

    @staticmethod
    def _resolve(adapter: object, combat_mode: str) -> tuple[str, str | None]:
        import asyncio

        from sts2_autotest.cli.main import _resolve_combat_mode_with_debug_check

        loop = asyncio.new_event_loop()
        try:
            return _resolve_combat_mode_with_debug_check(adapter, combat_mode, loop)  # type: ignore[arg-type]
        finally:
            loop.close()

    def test_traversal_downgrades_when_debug_not_verified(self) -> None:
        """配置声明 traversal 但调试探测未确认可用 → 降级为 basic 并给出原因。"""
        from sts2_autotest.adapters.base import DebugVerification

        class _Adapter:
            async def verify_debug_actions(self) -> DebugVerification:
                return DebugVerification(
                    configured=True,
                    verified=False,
                    reason="DEBUG_CONSOLE_UNAVAILABLE",
                )

        mode, reason = self._resolve(_Adapter(), "traversal")

        assert mode == "basic"
        assert reason == "DEBUG_CONSOLE_UNAVAILABLE"

    def test_traversal_kept_when_debug_verified(self) -> None:
        """配置 + 实际探测双真 → 保留 traversal，不降级。"""
        from sts2_autotest.adapters.base import DebugVerification

        class _Adapter:
            async def verify_debug_actions(self) -> DebugVerification:
                return DebugVerification(configured=True, verified=True)

        mode, reason = self._resolve(_Adapter(), "traversal")

        assert mode == "traversal"
        assert reason is None

    def test_non_traversal_modes_never_downgraded(self) -> None:
        """basic / death 等非 traversal 模式不受调试能力影响，保持原样。"""

        class _Adapter:
            async def verify_debug_actions(self) -> object:  # pragma: no cover - 不应被调用
                raise AssertionError("非 traversal 模式不应触发调试验证")

        for original in ("basic", "death"):
            mode, reason = self._resolve(_Adapter(), original)
            assert mode == original
            assert reason is None

    def test_missing_verify_method_downgrades_safely(self) -> None:
        """适配器没有 verify_debug_actions（老实现）→ 保守降级为 basic。"""

        class _Adapter:
            pass

        mode, reason = self._resolve(_Adapter(), "traversal")

        assert mode == "basic"
        assert reason == "DEBUG_VERIFY_UNSUPPORTED"

    def test_verify_error_downgrades_safely(self) -> None:
        """验证探测抛错绝不冒泡 → 降级为 basic，任务继续。"""

        class _Adapter:
            async def verify_debug_actions(self) -> object:
                raise RuntimeError("control channel blew up")

        mode, reason = self._resolve(_Adapter(), "traversal")

        assert mode == "basic"
        assert reason == "DEBUG_VERIFY_ERROR"


class TestEnvironmentPrecheck:
    """修复一串接：旅程启动前的环境预检。"""

    def test_skips_when_environment_not_managed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无法构造生命周期管理（游戏非本框架管理）→ 跳过预检，返回 None。"""
        from sts2_autotest.cli import main as cli_main

        monkeypatch.setattr(
            "sts2_autotest.core.runtime_factory.build_lifecycle_manager",
            lambda *a, **k: None,
        )
        assert cli_main._run_environment_precheck(object()) is None  # type: ignore[arg-type]

    def test_returns_none_when_environment_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """生命周期报告 ready=True → 预检通过，返回 None。"""
        from sts2_autotest.cli import main as cli_main
        from sts2_autotest.core.lifecycle import EnvironmentReadiness

        class _Lifecycle:
            async def ensure_environment_ready(self) -> EnvironmentReadiness:
                return EnvironmentReadiness(ready=True)

        monkeypatch.setattr(
            "sts2_autotest.core.runtime_factory.build_lifecycle_manager",
            lambda *a, **k: _Lifecycle(),
        )
        assert cli_main._run_environment_precheck(object()) is None  # type: ignore[arg-type]

    def test_returns_reason_when_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """生命周期报告 ready=False → 返回阻塞原因字符串。"""
        from sts2_autotest.cli import main as cli_main
        from sts2_autotest.common.errors import EnvironmentBlockReason
        from sts2_autotest.core.lifecycle import EnvironmentReadiness

        class _Lifecycle:
            async def ensure_environment_ready(self) -> EnvironmentReadiness:
                return EnvironmentReadiness(
                    ready=False,
                    reason=EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE,
                )

        monkeypatch.setattr(
            "sts2_autotest.core.runtime_factory.build_lifecycle_manager",
            lambda *a, **k: _Lifecycle(),
        )
        reason = cli_main._run_environment_precheck(object())  # type: ignore[arg-type]
        assert reason == EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE.value

    def test_precheck_error_never_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ensure_environment_ready 抛错也绝不冒泡 → 归类为 PRECHECK_ERROR。"""
        from sts2_autotest.cli import main as cli_main

        class _Lifecycle:
            async def ensure_environment_ready(self) -> object:
                raise RuntimeError("boom")

        monkeypatch.setattr(
            "sts2_autotest.core.runtime_factory.build_lifecycle_manager",
            lambda *a, **k: _Lifecycle(),
        )
        reason = cli_main._run_environment_precheck(object())  # type: ignore[arg-type]
        assert reason is not None
        assert reason.startswith("PRECHECK_ERROR:")


class TestJourneyPrecheckGate:
    """预检开关：仅真实 CLI 入口（precheck=True）执行预检；内部单测默认跳过。

    precheck=False 的跳过行为由 test_journey_foreground.py 的端到端旅程用例覆盖
    （本机/CI 自托管 runner 均设 STS2_GAME_DIR，若预检误运行这些用例会返回 2）。
    """

    def test_precheck_true_blocks_before_journey(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """precheck=True 且环境不就绪 → 旅程执行前返回 2（BLOCKED_ENVIRONMENT）。"""
        from sts2_autotest.cli import main as cli_main

        monkeypatch.setenv("STS2_AUTOTEST_EVIDENCE", "none")
        monkeypatch.setenv("STS2_AUTOTEST_EVIDENCE_DIR", str(tmp_path / "evidence"))
        monkeypatch.setattr(
            cli_main, "_run_environment_precheck", lambda _adapter: "GAME_CONTROL_UNAVAILABLE"
        )

        rc = cli_main._run_journey_foreground(
            object(),  # type: ignore[arg-type]  # 预检在触碰 adapter 前就返回，无需真实适配器
            journey="first_battle",
            character_id="IRONCLAD",
            timeout=1.0,
            run_id="test-precheck-gate",
            precheck=True,
        )
        assert rc == 2

    def test_recover_main_menu_via_restart_returns_main_menu(self) -> None:
        """受控重启恢复：回到干净主菜单 → ok / 恰好启动一次 / 结构化返回值。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([
            _menu_state(_CLEAN_ACTIONS),                       # _wait_for_main_menu 首帧
            *[_menu_state(_CLEAN_ACTIONS) for _ in range(3)],  # 稳定读取
        ])
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=2,
        )
        assert result["final_screen"] == "MAIN_MENU"
        assert result["ok"] is True
        assert result["blocked"] is False
        assert result["clean_main_menu"] is True
        assert result["restart_count"] == 1
        assert lifecycle.terminate_calls == 1
        assert lifecycle.ensure_calls == 1

    def test_recover_main_menu_via_restart_blocked_when_menu_unreachable(self) -> None:
        """受控重启恢复：一次启动后仍读不到主菜单 → 环境阻塞（不返回 None、不再启动）。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([RuntimeError("game control lost")] * 50)
        lifecycle = _CountingLifecycle(ready=False, reason="game_control_lost")
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(), sleep=_no_sleep, menu_timeout=0.05
        )
        assert result is not None
        assert result["blocked"] is True
        assert result["ok"] is False
        assert lifecycle.ensure_calls == 1
        assert result["restart_count"] == 1


def _menu_state(
    actions: list[str], *, has_run_save: bool | None = False, screen: str = "MAIN_MENU"
) -> dict:
    """构造与真实游戏控制接口一致的主菜单状态帧（has_run_save 嵌套在 menu 下）。

    has_run_save=None 表示该帧未发布内省字段（菜单初始化期可能缺省）。
    """
    return {
        "screen": screen,
        "timestamp": 1784436951406,
        "menu": {} if has_run_save is None else {"has_run_save": has_run_save},
        "available_actions": list(actions),
    }


_CLEAN_ACTIONS = ["start_new_run", "new_run", "choose_game_mode", "probe", "return_to_menu"]
_DIRTY_ACTIONS = [
    "start_new_run", "new_run", "continue_run", "abandon_run",
    "choose_game_mode", "probe", "return_to_menu",
]


class _ScriptedAdapter:
    """按脚本依次返回状态帧；act 仅记录调用。脚本耗尽后 get_state 抛异常。

    adapter_actions：适配器协议方法返回的动作列表（模拟 CliMod 适配器——
    其状态不内嵌 available_actions，动作须由协议方法单独提供）。
    """

    def __init__(self, states: list, adapter_actions: list[str] | None = None) -> None:
        self._states = list(states)
        self.acts: list[str] = []
        self._cache_stale = False
        self.adapter_actions = list(adapter_actions or [])

    def reset_http_client(self) -> None:
        pass

    async def get_state(self):
        if not self._states:
            raise RuntimeError("script exhausted")
        item = self._states.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def get_available_actions(self):
        return list(self.adapter_actions)

    async def act(self, name: str, params: dict | None = None):
        self.acts.append(name)
        return {"status": "success"}


class _CountingLifecycle:
    """记录 terminate / ensure_environment_ready 真实调用次数。"""

    def __init__(self, ready: bool = True, reason: str | None = None) -> None:
        self.terminate_calls = 0
        self.ensure_calls = 0
        self._ready = ready
        self._reason = reason

    def terminate(self) -> None:
        self.terminate_calls += 1

    def _game_process_present(self) -> bool:
        return False

    async def ensure_environment_ready(self):
        import types

        self.ensure_calls += 1
        return types.SimpleNamespace(ready=self._ready, reason=self._reason)


class _SyncLoop:
    def run_until_complete(self, coro):
        import asyncio

        return asyncio.run(coro)


def _no_sleep(_seconds: float) -> None:
    """替换真实等待：检查不得单条等待数分钟（P1 复核 P1-2）。"""


class TestCleanMainMenuRecovery:
    """V11：干净主菜单判定必须基于最终稳定完整状态（V10 假阳性修复）。

    复现对象（P1 复核结论）：
    - P0-1 首帧干净但稳定帧出现旧局时漏判；放弃后单帧瞬态干净造成假 clean。
    - P0-2 取消成功只要求 screen=MAIN_MENU，不要求干净。
    - P0-3 实际可能启动两次但 restart_count 恒为 1。
    - P1-1 恢复后完整状态未入报告。
    """

    def test_late_saved_run_signal_triggers_abandon_and_clean(self) -> None:
        """首帧无旧局、稳定帧晚到 continue_run：必须执行放弃并最终判干净。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([
            _menu_state(_CLEAN_ACTIONS),                       # _wait_for_main_menu 首帧
            _menu_state(_CLEAN_ACTIONS),                       # 稳定帧 1
            _menu_state(_DIRTY_ACTIONS, has_run_save=True),    # 稳定帧 2：旧局晚到
            _menu_state(_CLEAN_ACTIONS),                       # 稳定帧 3（信号可能闪退，仍须放弃）
            # _abandon_saved_run：abandon 后读取确认框 → 确认删除读取（均干净）
            _menu_state(_CLEAN_ACTIONS),
            _menu_state(_CLEAN_ACTIONS),
            # 放弃后稳定读取 3 帧（连续 3 帧干净才判干净）
            _menu_state(_CLEAN_ACTIONS),
            _menu_state(_CLEAN_ACTIONS),
            _menu_state(_CLEAN_ACTIONS),
        ])
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=3,
        )
        assert "abandon_run" in adapter.acts
        assert result["ok"] is True
        assert result["clean_main_menu"] is True
        assert result["old_run_abandoned"] is True
        assert result["blocked"] is False

    def test_transient_clean_frame_after_abandon_is_not_false_clean(self) -> None:
        """放弃后第一帧瞬态干净、随后旧局复现：不得判干净（V10 假阳性复现）。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([
            _menu_state(_DIRTY_ACTIONS, has_run_save=True),    # _wait_for_main_menu 首帧
            *[_menu_state(_DIRTY_ACTIONS, has_run_save=True) for _ in range(3)],  # 稳定帧
            # _abandon_saved_run：abandon 后两次读取均瞬态干净（菜单重建中）
            _menu_state(_CLEAN_ACTIONS),
            _menu_state(_CLEAN_ACTIONS),
            # 放弃后稳定读取：旧局复现
            _menu_state(_DIRTY_ACTIONS, has_run_save=True),
            _menu_state(_DIRTY_ACTIONS, has_run_save=True),
            _menu_state(_DIRTY_ACTIONS, has_run_save=True),
        ])
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=3,
        )
        assert "abandon_run" in adapter.acts
        assert result["clean_main_menu"] is False
        assert result["ok"] is False, "clean_main_menu=false 时不得判取消成功"

    def test_unclearable_saved_run_is_platform_failure_not_env_block(self) -> None:
        """能控制游戏但旧局清不掉 → FAILED_PLATFORM（blocked=False），不是环境阻塞。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([
            _menu_state(_DIRTY_ACTIONS, has_run_save=True),
            *[_menu_state(_DIRTY_ACTIONS, has_run_save=True) for _ in range(3)],
            _menu_state(_DIRTY_ACTIONS, has_run_save=True),    # abandon 后确认框读取仍脏
            _menu_state(_DIRTY_ACTIONS, has_run_save=True),    # abandon 后确认删除读取仍脏
            *[_menu_state(_DIRTY_ACTIONS, has_run_save=True) for _ in range(3)],
        ])
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=3,
        )
        assert result["ok"] is False
        assert result["blocked"] is False, "可控制但清不干净是平台失败，不是环境阻塞"
        assert result["clean_main_menu"] is False

    def test_ok_requires_new_run_capability_not_just_main_menu_screen(self) -> None:
        """主菜单可操作但无开新局能力（且无旧局）→ 无法确认干净，判平台失败。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([
            _menu_state(["open_timeline", "probe"]),           # 可操作但无开新局能力
            *[_menu_state(["open_timeline", "probe"]) for _ in range(3)],
        ])
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=3,
        )
        assert result["final_screen"] == "MAIN_MENU"
        assert result["clean_main_menu"] is False
        assert result["ok"] is False, "只有 screen=MAIN_MENU 不算干净，不得判 CANCELLED"
        assert result["blocked"] is False

    def test_stale_continue_abandon_actions_tolerated_when_save_field_false(self) -> None:
        """V11 实测：has_run_save 显式 False 时 continue/abandon 动作是菜单重建
        陈旧伪影（此时 start_new_run 可直接开局无确认框）→ 必须判干净。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([
            _menu_state(_DIRTY_ACTIONS, has_run_save=False),   # 陈旧动作 + 内省无存档
            *[_menu_state(_DIRTY_ACTIONS, has_run_save=False) for _ in range(3)],
        ])
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=3,
        )
        assert "abandon_run" not in adapter.acts, "内省无存档时不得误执行放弃"
        assert result["clean_main_menu"] is True
        assert result["ok"] is True

    def test_actions_only_dirty_when_save_field_unpublished(self) -> None:
        """内省字段未发布时退回动作列表判断：出现 continue_run 即旧局，须放弃。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([
            _menu_state(_DIRTY_ACTIONS, has_run_save=None),    # 字段缺失 + continue_run
            *[_menu_state(_DIRTY_ACTIONS, has_run_save=None) for _ in range(3)],
            # abandon 后两次读取：字段缺失但动作已干净
            _menu_state(_CLEAN_ACTIONS, has_run_save=None),
            _menu_state(_CLEAN_ACTIONS, has_run_save=None),
            # 放弃后稳定读取 3 帧
            _menu_state(_CLEAN_ACTIONS, has_run_save=None),
            _menu_state(_CLEAN_ACTIONS, has_run_save=None),
            _menu_state(_CLEAN_ACTIONS, has_run_save=None),
        ])
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=3,
        )
        assert "abandon_run" in adapter.acts
        assert result["ok"] is True
        assert result["clean_main_menu"] is True
        assert result["old_run_abandoned"] is True

    def test_menu_republish_after_abandon_eventually_clean(self) -> None:
        """V11 假阴性复现：放弃后先遇空动作重建帧、随后菜单发布 → 必须等到干净。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([
            _menu_state(_DIRTY_ACTIONS, has_run_save=True),
            *[_menu_state(_DIRTY_ACTIONS, has_run_save=True) for _ in range(3)],
            # abandon 后两次读取
            _menu_state(_CLEAN_ACTIONS),
            _menu_state(_CLEAN_ACTIONS),
            # 放弃后稳定读取：空动作重建帧 → 菜单发布（含陈旧动作但内省无存档）
            _menu_state([]),
            _menu_state(_DIRTY_ACTIONS, has_run_save=False),
            _menu_state(_DIRTY_ACTIONS, has_run_save=False),
            _menu_state(_DIRTY_ACTIONS, has_run_save=False),
        ])
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=4,
        )
        assert result["clean_main_menu"] is True
        assert result["ok"] is True
        assert result["blocked"] is False

    def test_mod_loading_empty_menu_waits_until_operational(self) -> None:
        """V11 实测：重启后画面先到主菜单但模组仍在加载（动作空）→ 必须等到可操作。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([
            _menu_state([]),                                   # 到达帧：主菜单但模组加载中
            _menu_state([]),                                   # 可操作等待：仍加载中
            _menu_state(_CLEAN_ACTIONS),                       # 模组加载完成，动作发布
            *[_menu_state(_CLEAN_ACTIONS) for _ in range(3)],  # 稳定读取
        ])
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=3,
        )
        assert result["ok"] is True
        assert result["clean_main_menu"] is True
        assert result["blocked"] is False

    def test_mod_never_operational_is_environment_blocked(self) -> None:
        """模组始终不发布动作（菜单永不可操作）→ 环境阻塞，不得判平台失败。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([_menu_state([])] * 60)
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=3,
            operational_timeout=0.05,
        )
        assert result["blocked"] is True
        assert result["ok"] is False
        assert lifecycle.ensure_calls == 1


_CLI_STATIC_ACTIONS = [
    "start_new_run", "new_run", "continue_run", "abandon_run",
    "choose_game_mode", "probe", "return_to_menu",
]


def _cli_menu_state(*, has_run_save: bool) -> dict:
    """构造 CliMod 适配器形态的主菜单状态：无内嵌动作、menu 携带内省字段。"""
    return {
        "screen": "MAIN_MENU",
        "timestamp": 1784436951406,
        "menu": {"has_run_save": has_run_save},
    }


class TestCleanMainMenuRecoveryCliAdapter:
    """CliMod 适配器路径（生产路径）：状态不内嵌动作，判定必须走协议方法。"""

    def test_cli_clean_menu_judged_clean_via_adapter_actions(self) -> None:
        """V11 根因：CliMod 状态无内嵌动作 → 必须经 get_available_actions 判干净。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter(
            [_cli_menu_state(has_run_save=False) for _ in range(4)],
            adapter_actions=_CLI_STATIC_ACTIONS,
        )
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=3,
        )
        assert result["clean_main_menu"] is True
        assert result["ok"] is True
        assert result["blocked"] is False
        assert "abandon_run" not in adapter.acts, "内省无存档时不得误执行放弃"

    def test_cli_dirty_menu_abandoned_and_cleaned(self) -> None:
        """CliMod 路径：内省字段有旧局 → 放弃 → 字段转无 → 判干净。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter(
            [
                _cli_menu_state(has_run_save=True),
                *[_cli_menu_state(has_run_save=True) for _ in range(3)],
                # abandon 后两次读取（CliMod 放弃由 CLI 直接完成，无确认弹窗）
                _cli_menu_state(has_run_save=False),
                _cli_menu_state(has_run_save=False),
                # 放弃后稳定读取 3 帧
                _cli_menu_state(has_run_save=False),
                _cli_menu_state(has_run_save=False),
                _cli_menu_state(has_run_save=False),
            ],
            adapter_actions=_CLI_STATIC_ACTIONS,
        )
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=3,
        )
        assert "abandon_run" in adapter.acts
        assert result["old_run_abandoned"] is True
        assert result["clean_main_menu"] is True
        assert result["ok"] is True
        final_state = result["final_state"]
        assert final_state["has_run_save"] is False
        assert final_state["has_new_run_action"] is True
        # CliMod 动作为静态派生：continue/abandon 标志置 None 并附说明，避免误读
        assert final_state["actions_source"] == "adapter_derived"
        assert final_state["has_continue_run"] is None
        assert final_state["has_abandon_run"] is None
        assert "静态" in (final_state.get("actions_note") or "")

    def test_cli_actions_unavailable_during_mod_loading(self) -> None:
        """CliMod 模组加载期协议方法返回空 → 等到可操作（不误判也不误放弃）。"""
        from sts2_autotest.cli import main as cli_main

        class _LoadingAdapter(_ScriptedAdapter):
            def __init__(self, states):
                super().__init__(states)
                self._action_calls = 0

            async def get_available_actions(self):
                self._action_calls += 1
                # 前两次协议调用模拟模组仍在加载（返回空），之后就绪
                if self._action_calls <= 2:
                    return []
                return list(self.adapter_actions)

        adapter = _LoadingAdapter(
            [_cli_menu_state(has_run_save=False) for _ in range(6)]
        )
        adapter.adapter_actions = list(_CLI_STATIC_ACTIONS)
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=3,
        )
        assert result["ok"] is True
        assert result["clean_main_menu"] is True
        assert "abandon_run" not in adapter.acts

    def test_single_restart_only_and_restart_count_is_honest(self) -> None:
        """启动后未到主菜单：禁止第二次启动；restart_count 必须等于真实启动次数。"""
        from sts2_autotest.cli import main as cli_main

        # get_state 始终抛异常（端口失联）→ 首启动后永远等不到主菜单。
        adapter = _ScriptedAdapter([RuntimeError("port dead")] * 50)
        lifecycle = _CountingLifecycle(ready=False, reason="game_control_lost")
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(), sleep=_no_sleep, menu_timeout=0.05
        )
        assert lifecycle.ensure_calls == 1, "一次取消最多启动一次游戏，禁止再次启动"
        assert result["restart_count"] == lifecycle.ensure_calls
        assert result["blocked"] is True, "一次启动后仍不可操作 → 环境阻塞"

    def test_restart_count_matches_actual_starts_on_success(self) -> None:
        """成功路径：恰好启动一次，restart_count=1。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([
            _menu_state(_CLEAN_ACTIONS),
            *[_menu_state(_CLEAN_ACTIONS) for _ in range(3)],
        ])
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=2,
        )
        assert lifecycle.ensure_calls == 1
        assert result["restart_count"] == 1
        assert result["ok"] is True

    def test_report_keeps_full_recovered_state(self) -> None:
        """取消报告必须保留恢复后完整状态，供审计独立核对（P1-1）。"""
        from sts2_autotest.cli import main as cli_main

        adapter = _ScriptedAdapter([
            _menu_state(_CLEAN_ACTIONS),
            *[_menu_state(_CLEAN_ACTIONS) for _ in range(3)],
        ])
        lifecycle = _CountingLifecycle()
        result = cli_main._recover_main_menu_via_restart(
            lifecycle, adapter, _SyncLoop(),
            sleep=_no_sleep, settle_tries=3, post_abandon_tries=2,
        )
        final_state = result.get("final_state")
        assert isinstance(final_state, dict), "报告不得只存 screen，必须存完整恢复后状态"
        assert final_state.get("screen") == "MAIN_MENU"
        assert final_state.get("has_run_save") is False
        assert final_state.get("actions_source") == "state_reported"
        assert final_state.get("has_continue_run") is False
        assert final_state.get("has_abandon_run") is False
        assert final_state.get("has_new_run_action") is True
        assert "start_new_run" in (final_state.get("available_actions") or [])
        assert final_state.get("captured_at"), "必须记录最终状态时间戳"
