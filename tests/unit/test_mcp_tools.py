"""Unit tests for MCP tool implementations."""

import sys
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from sts2_autotest.cli.mcp_protocol import McpError
from sts2_autotest.cli.mcp_tools import (
    ToolRegistry,
    handle_health_check,
    handle_review_spec,
    handle_compile_spec,
    handle_run_test,
    handle_get_report,
    handle_list_specs,
    handle_run_pipeline,
    run_tests_in_dir,
)


class TestToolRegistry:
    def test_registry_has_all_tools(self):
        registry = ToolRegistry()
        tools = registry.list_tools()
        names = {t.name for t in tools}
        expected = {
            "health_check", "review_spec", "compile_spec",
            "run_test", "get_report", "list_specs", "run_pipeline",
            "capabilities", "submit_run", "get_run", "cancel_run", "resume_run",
        }
        assert names == expected

    def test_registry_dispatch_known_tool(self):
        registry = ToolRegistry()
        result = registry.dispatch("health_check", {})
        assert result["status"] == "ok"

    def test_capabilities_describe_persistent_run_contract(self):
        result = ToolRegistry().dispatch("capabilities", {})
        assert "submit_run" in result["operations"]
        assert "BLOCKED_ENVIRONMENT" in result["run_statuses"]


class TestPersistentRunTools:
    def test_get_run_returns_not_found_for_unknown_id(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_get_run

        result = handle_get_run({"run_id": "missing"})
        assert result["status"] == "NOT_FOUND"

    def test_run_id_rejects_path_traversal(self):
        from sts2_autotest.cli.mcp_tools import handle_get_run

        with pytest.raises(McpError, match="invalid characters"):
            handle_get_run({"run_id": "../run.json"})

    def test_registry_dispatch_unknown_tool(self):
        registry = ToolRegistry()
        with pytest.raises(McpError, match="Unknown tool"):
            registry.dispatch("nonexistent", {})

    @patch("sts2_autotest.cli.mcp_tools.spawn_worker")
    def test_submit_run_persists_and_is_idempotent(self, mock_worker, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        args = {
            "project": "examplemod",
            "suite": "smoke",
            "timeout": 120,
            "idempotency_key": "examplemod-smoke-1",
        }
        first = handle_submit_run(args)
        second = handle_submit_run(args)

        assert first["run_id"] == second["run_id"]
        assert first["status"] == "QUEUED"
        mock_worker.assert_called_once()

    @patch("sts2_autotest.cli.mcp_tools.spawn_worker")
    def test_submit_suite_does_not_append_all_target(self, mock_worker, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        handle_submit_run({"project": "examplemod", "suite": "smoke"})

        argv = mock_worker.call_args.args[2]
        assert "--suite" in argv
        assert "--all" not in argv

    def test_submit_rejects_invalid_evidence_level(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        with pytest.raises(McpError, match="evidence"):
            handle_submit_run({"evidence": "verbose"})

    @patch("sts2_autotest.cli.mcp_tools.spawn_worker")
    def test_resume_run_preserves_resume_mode_in_worker_argv(self, mock_worker, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_resume_run, handle_submit_run

        original = handle_submit_run({"project": "examplemod", "suite": "smoke"})
        resumed = handle_resume_run({"run_id": original["run_id"]})

        assert resumed["request"]["mode"] == "resume"
        assert "--resume" in resumed["request"]["argv"]
        assert mock_worker.call_count == 2


class TestHealthCheck:
    def test_health_check_returns_ok(self):
        result = handle_health_check({})
        assert "status" in result
        assert "service" in result
        assert result["service"] == "sts2-autotest-mcp"


class TestReviewSpec:
    @patch("sts2_autotest.cli.mcp_tools._validate_path")
    @patch("sts2_autotest.cli.mcp_tools.review_spec_file")
    def test_review_spec_calls_reviewer(self, mock_review, mock_validate, tmp_path):
        from sts2_autotest.common.spec_models import ReviewReport, ReviewIssue, IssueCategory
        spec_file = tmp_path / "TC-TEST.md"
        spec_file.write_text("# Test")
        mock_validate.return_value = spec_file
        mock_review.return_value = ReviewReport(
            spec_id="test",
            issues=[ReviewIssue(
                category=IssueCategory.AMBIGUITY,
                location="Step 1",
                description="Ambiguous",
                suggestion="Clarify",
            )],
        )
        result = handle_review_spec({"spec_path": str(spec_file)})
        assert "issues" in result
        assert len(result["issues"]) == 1
        assert "revised_draft" in result


class TestCompileSpec:
    @patch("sts2_autotest.cli.mcp_tools.compile_spec_file")
    def test_compile_spec_calls_generator(self, mock_compile):
        import shutil
        # Create output dir within workspace so path whitelist validation passes
        ws_dir = Path("/Users/chris/STS2-WORKSPACE/STS2-AUTOTEST/tests")
        output_dir = ws_dir / "tmp_mcp_output"
        output_dir.mkdir(exist_ok=True)
        try:
            mock_compile.return_value = output_dir / "test_tc.py"
            result = handle_compile_spec({
                "spec_path": (
                    "/Users/chris/STS2-WORKSPACE/STS2-AUTOTEST"
                    "/docs/superpowers/specs/2026-05-31-b11-cicd-design.md"
                ),
                "output_dir": str(output_dir),
            })
            assert "generated_file" in result
            assert result["warnings"] == []
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_compile_spec_rejects_external_output_dir(self):
        """output_dir outside whitelist should raise McpError."""
        from sts2_autotest.cli.mcp_protocol import McpError
        with pytest.raises(McpError, match="not within allowed roots"):
            handle_compile_spec({
                "spec_path": (
                    "/Users/chris/STS2-WORKSPACE/STS2-AUTOTEST"
                    "/docs/superpowers/specs/2026-05-31-b11-cicd-design.md"
                ),
                "output_dir": "/tmp/evil_output",
            })


class TestRunTest:
    @patch("sts2_autotest.cli.mcp_tools.subprocess.run")
    def test_run_tests_in_dir_uses_current_python(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="test_example.py::test_ok PASSED\n",
            stderr="",
        )

        result = run_tests_in_dir(tmp_path, timeout=60)

        cmd = mock_run.call_args.args[0]
        assert cmd[0] == sys.executable
        assert "--timeout" not in cmd
        assert result["status"] == "OK"

    @patch("sts2_autotest.cli.mcp_tools.run_tests_in_dir")
    def test_run_test_returns_result(self, mock_run):
        mock_run.return_value = {
            "run_id": "run-001",
            "passed": 3,
            "failed": 0,
            "status": "OK",
            "duration_ms": 1234,
            "junit_xml_url": "file:///tmp/junit.xml",
            "stderr": None,
        }
        result = handle_run_test({"spec_dir": "/Users/chris/STS2-WORKSPACE/STS2-AUTOTEST/tests/"})
        assert result["passed"] == 3
        assert result["failed"] == 0
        assert result["status"] == "OK"

    @patch("sts2_autotest.cli.mcp_tools.run_tests_in_dir")
    def test_run_test_reports_timeout(self, mock_run):
        """Timeout should report status=TIMEOUT."""
        mock_run.return_value = {
            "run_id": "run-002",
            "passed": 0,
            "failed": 0,
            "status": "TIMEOUT",
            "duration_ms": 90000,
            "junit_xml_url": "file:///tmp/junit.xml",
            "stderr": "Test execution timed out after 90s",
        }
        result = handle_run_test({"spec_dir": "/Users/chris/STS2-WORKSPACE/STS2-AUTOTEST/tests/"})
        assert result["status"] == "TIMEOUT"
        assert result["passed"] == 0

    @patch("sts2_autotest.cli.mcp_tools.run_tests_in_dir")
    def test_run_test_reports_failure(self, mock_run):
        """Non-zero exit should report status=FAILED with stderr."""
        mock_run.return_value = {
            "run_id": "run-003",
            "passed": 2,
            "failed": 3,
            "status": "FAILED",
            "duration_ms": 5678,
            "junit_xml_url": "file:///tmp/junit.xml",
            "stderr": "Exit code: 1",
        }
        result = handle_run_test({"spec_dir": "/Users/chris/STS2-WORKSPACE/STS2-AUTOTEST/tests/"})
        assert result["status"] == "FAILED"
        assert result["failed"] == 3
        assert result["stderr"] is not None


class TestGetReport:
    @patch("sts2_autotest.cli.mcp_tools.read_run_report")
    def test_get_report_returns_summary(self, mock_read):
        mock_read.return_value = {
            "summary": {"tests": 5, "failures": 0},
            "failures": [],
            "evidence_pack_url": "file:///tmp/evidence.zip",
        }
        result = handle_get_report({"run_id": "run-001"})
        assert "summary" in result
        assert result["failures"] == []


class TestListSpecs:
    @patch("sts2_autotest.cli.mcp_tools._validate_path")
    def test_list_specs_finds_markdown(self, mock_validate, tmp_path):
        mock_validate.return_value = tmp_path
        # Create real markdown files in tmp_path
        (tmp_path / "TC-TEST.md").write_text("# Test spec")
        (tmp_path / "SUITE-SMOKE.md").write_text("# Suite spec")
        result = handle_list_specs({"spec_dir": str(tmp_path)})
        assert len(result["specs"]) == 2


class TestRunPipeline:
    @patch("sts2_autotest.cli.mcp_tools._validate_path")
    @patch("sts2_autotest.cli.mcp_tools.compile_spec_file")
    @patch("sts2_autotest.cli.mcp_tools.run_tests_in_dir")
    def test_run_pipeline_executes_stages(self, mock_run, mock_compile, mock_validate, tmp_path):
        mock_validate.side_effect = lambda p: Path(p)
        fake_path = Path("/tmp/test_tc.py")
        fake_path.touch()
        mock_compile.return_value = fake_path
        mock_run.return_value = {"run_id": "run-001", "passed": 1, "failed": 0, "status": "OK", "duration_ms": 0, "junit_xml_url": ""}

        spec_file = tmp_path / "TC-TEST.md"
        spec_file.write_text("""# TC-TEST

## Metadata
- id: TC-TEST
- level: case

## Given
- Test given

## When
- Test step

## Then
- Test assertion
""")
        result = handle_run_pipeline({"spec_dir": str(tmp_path)})
        assert "review_issues" in result
        assert "compiled_files" in result
        assert "test_result" in result

        fake_path.unlink()
