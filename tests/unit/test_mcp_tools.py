"""Unit tests for MCP tool implementations."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import pytest

from sts2_autotest.cli.mcp_protocol import McpError, McpTool
from sts2_autotest.cli.mcp_tools import (
    ToolRegistry,
    handle_health_check,
    handle_review_spec,
    handle_compile_spec,
    handle_run_test,
    handle_get_report,
    handle_list_specs,
    handle_run_pipeline,
)


class TestToolRegistry:
    def test_registry_has_all_tools(self):
        registry = ToolRegistry()
        tools = registry.list_tools()
        names = {t.name for t in tools}
        expected = {
            "health_check", "review_spec", "compile_spec",
            "run_test", "get_report", "list_specs", "run_pipeline",
        }
        assert names == expected

    def test_registry_dispatch_known_tool(self):
        registry = ToolRegistry()
        result = registry.dispatch("health_check", {})
        assert result["status"] == "ok"

    def test_registry_dispatch_unknown_tool(self):
        registry = ToolRegistry()
        with pytest.raises(McpError, match="Unknown tool"):
            registry.dispatch("nonexistent", {})


class TestHealthCheck:
    def test_health_check_returns_ok(self):
        result = handle_health_check({})
        assert "status" in result
        assert "service" in result
        assert result["service"] == "sts2-autotest-mcp"


class TestReviewSpec:
    @patch("sts2_autotest.cli.mcp_tools.review_spec_file")
    def test_review_spec_calls_reviewer(self, mock_review, tmp_path):
        from sts2_autotest.common.spec_models import ReviewReport, ReviewIssue, IssueCategory
        mock_review.return_value = ReviewReport(
            spec_id="test",
            issues=[ReviewIssue(
                category=IssueCategory.AMBIGUITY,
                location="Step 1",
                description="Ambiguous",
                suggestion="Clarify",
            )],
        )
        # Create a real temp file so the existence check passes
        spec_file = tmp_path / "TC-TEST.md"
        spec_file.write_text("# Test")
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


class TestRunTest:
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
    def test_list_specs_finds_markdown(self, tmp_path):
        # Create real markdown files in tmp_path
        (tmp_path / "TC-TEST.md").write_text("# Test spec")
        (tmp_path / "SUITE-SMOKE.md").write_text("# Suite spec")
        result = handle_list_specs({"spec_dir": str(tmp_path)})
        assert len(result["specs"]) == 2


class TestRunPipeline:
    @patch("sts2_autotest.cli.mcp_tools.compile_spec_file")
    @patch("sts2_autotest.cli.mcp_tools.run_tests_in_dir")
    def test_run_pipeline_executes_stages(self, mock_run, mock_compile, tmp_path):
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
