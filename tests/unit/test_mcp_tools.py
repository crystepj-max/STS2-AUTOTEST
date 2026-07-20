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


class TestCapabilitiesRuntimeVerification:
    """capabilities 必须反映真实运行能力，而非只按配置声明（修复二）。"""

    def test_capabilities_include_runtime_verification_fields(self, monkeypatch):
        """探测确认可用时，暴露真实验证字段且快速结束战斗开启。"""
        import sts2_autotest.cli.mcp_tools as mt

        monkeypatch.setattr(
            mt,
            "_probe_runtime_capabilities",
            lambda: {
                "game_control_ready": True,
                "debug_actions_configured": True,
                "debug_actions_verified": True,
                "debug_actions_reason": None,
                "runtime_capabilities_checked_at": "2026-07-17T12:00:00+00:00",
            },
        )
        result = mt.handle_capabilities({})
        assert result["game_control_ready"] is True
        assert result["debug_actions_configured"] is True
        assert result["debug_actions_verified"] is True
        assert result["debug_actions_reason"] is None
        assert result["runtime_capabilities_checked_at"] == "2026-07-17T12:00:00+00:00"
        # 配置 + 验证双真 → 快速结束战斗真实可用
        assert result["combat_capabilities"]["traversal_fast_end_enabled"] is True
        assert result["card_test"]["debug_actions_enabled"] is True

    def test_fast_end_requires_config_and_verification(self, monkeypatch):
        """只配置未验证时，快速结束战斗必须保持关闭（不能只按配置声明）。"""
        import sts2_autotest.cli.mcp_tools as mt

        monkeypatch.setattr(
            mt,
            "_probe_runtime_capabilities",
            lambda: {
                "game_control_ready": True,
                "debug_actions_configured": True,
                "debug_actions_verified": False,
                "debug_actions_reason": "DEBUG_CONSOLE_UNAVAILABLE",
                "runtime_capabilities_checked_at": "2026-07-17T12:00:00+00:00",
            },
        )
        result = mt.handle_capabilities({})
        assert result["debug_actions_configured"] is True
        assert result["debug_actions_verified"] is False
        assert result["combat_capabilities"]["traversal_fast_end_enabled"] is False
        assert result["card_test"]["debug_actions_enabled"] is False

    def test_probe_is_safe_and_fields_are_well_typed(self):
        """无论游戏是否在跑，能力探测都必须安全返回且字段类型正确。"""
        import sts2_autotest.cli.mcp_tools as mt

        result = mt.handle_capabilities({})
        assert isinstance(result["game_control_ready"], bool)
        assert isinstance(result["debug_actions_configured"], bool)
        assert isinstance(result["debug_actions_verified"], bool)
        assert "runtime_capabilities_checked_at" in result
        # 快速结束战斗永远等于"配置 AND 验证"双真。
        expected_fast_end = (
            result["debug_actions_configured"] and result["debug_actions_verified"]
        )
        assert (
            result["combat_capabilities"]["traversal_fast_end_enabled"]
            is expected_fast_end
        )
        assert result["card_test"]["debug_actions_enabled"] is expected_fast_end

    def test_probe_never_raises_when_control_unavailable(self, monkeypatch):
        """控制入口不可达时（探测失败）安全返回未就绪，绝不抛错或阻塞。"""
        import sts2_autotest.cli.mcp_tools as mt

        def _boom(_type: str):
            raise RuntimeError("adapter build failed")

        monkeypatch.setattr("sts2_autotest.cli.main._create_adapter", _boom)
        result = mt.handle_capabilities({})
        assert result["game_control_ready"] is False
        assert result["debug_actions_verified"] is False
        assert result["combat_capabilities"]["traversal_fast_end_enabled"] is False


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

    @patch("sts2_autotest.cli.mcp_tools.spawn_worker")
    def test_target_scene_defaults_to_agent_adapter(self, mock_worker, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        handle_submit_run({
            "journey": "act_traversal",
            "character_id": "IRONCLAD",
            "target_scene": "NEXT_ACT",
            "route_policy": "leftmost",
            "combat_mode": "traversal",
            "timeout": 60,
            "evidence": "full",
            "idempotency_key": "act-traversal-agent-default-1",
        })

        argv = mock_worker.call_args.args[2]
        assert "--adapter" in argv
        assert argv[argv.index("--adapter") + 1] == "agent"

    def test_submit_rejects_invalid_evidence_level(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        with pytest.raises(McpError, match="evidence"):
            handle_submit_run({"evidence": "verbose"})

    @patch("sts2_autotest.cli.mcp_tools.spawn_worker")
    def test_resume_run_preserves_resume_mode_in_worker_argv(self, mock_worker, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import (
            _run_store,
            handle_resume_run,
            handle_submit_run,
        )

        original = handle_submit_run({"project": "examplemod", "suite": "smoke"})
        # 修复四：只有取消/失败收尾完成（终态 + 证据封存）的任务才可恢复。
        _run_store().finish_cancel(original["run_id"], reason=None, sealed=True)
        resumed = handle_resume_run({"run_id": original["run_id"]})

        assert resumed["request"]["mode"] == "resume"
        assert "--resume" in resumed["request"]["argv"]
        assert resumed["resumed_from"] == original["run_id"]
        assert resumed["run_id"] != original["run_id"]
        assert mock_worker.call_count == 2

    @patch("sts2_autotest.cli.mcp_tools.spawn_worker")
    def test_resume_run_rejected_while_original_not_finished(
        self, mock_worker, monkeypatch, tmp_path
    ):
        """修复四：原任务还在跑（未终态/证据未封存）时，恢复必须被拒。"""
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_protocol import McpError
        from sts2_autotest.cli.mcp_tools import handle_resume_run, handle_submit_run

        original = handle_submit_run({"project": "examplemod", "suite": "smoke"})
        with pytest.raises(McpError, match="cannot be resumed"):
            handle_resume_run({"run_id": original["run_id"]})


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


class TestDeathAndCardTestContract:
    """combat_mode=death 与 journey=card_test 的公共契约检查。"""

    def test_capabilities_expose_death_mode_and_card_test(self):
        result = ToolRegistry().dispatch("capabilities", {})
        assert "death" in result["combat_modes"]
        death_mode = result["combat_capabilities"]["death_mode"]
        assert death_mode["end_turn_only"] is True
        assert death_mode["success_screen"] == "GAME_OVER"
        assert "card_test" in result["supported_journeys"]
        assert result["card_test"]["requires_debug_actions"] is True
        assert "card_id" in result["submit_parameters"]

    @patch("sts2_autotest.cli.mcp_tools.spawn_worker")
    def test_submit_accepts_death_combat_mode(self, mock_worker, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        handle_submit_run({
            "journey": "goal_scene",
            "character_id": "IRONCLAD",
            "target_scene": "COMBAT",
            "combat_mode": "death",
            "timeout": 600,
            "evidence": "full",
            "idempotency_key": "death-mode-contract-1",
        })

        argv = mock_worker.call_args.args[2]
        assert argv[argv.index("--combat-mode") + 1] == "death"

    def test_submit_rejects_card_test_without_card_id(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        with pytest.raises(McpError, match="card_id"):
            handle_submit_run({"journey": "card_test", "character_id": "IRONCLAD"})

    def test_submit_rejects_card_test_with_target_scene(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        with pytest.raises(McpError, match="target_scene"):
            handle_submit_run({
                "journey": "card_test",
                "character_id": "IRONCLAD",
                "card_id": "STRIKE",
                "target_scene": "COMBAT",
            })

    @patch("sts2_autotest.cli.mcp_tools.spawn_worker")
    def test_submit_card_test_passes_card_id_to_worker(self, mock_worker, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        handle_submit_run({
            "journey": "card_test",
            "character_id": "IRONCLAD",
            "card_id": "STRIKE",
            "timeout": 120,
            "evidence": "full",
            "idempotency_key": "card-test-contract-1",
        })

        argv = mock_worker.call_args.args[2]
        assert argv[argv.index("--journey") + 1] == "card_test"
        assert argv[argv.index("--card-id") + 1] == "STRIKE"
        # card_test 依赖调试控制台（give_card），必须默认走 agent 适配器，
        # 否则 worker 会退化成 cli 适配器并误报调试能力不可用。
        assert argv[argv.index("--adapter") + 1] == "agent"

    @patch("sts2_autotest.cli.mcp_tools.spawn_worker")
    def test_resume_run_preserves_card_id(self, mock_worker, monkeypatch, tmp_path):
        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import (
            _run_store,
            handle_resume_run,
            handle_submit_run,
        )

        original = handle_submit_run({
            "journey": "card_test",
            "character_id": "IRONCLAD",
            "card_id": "STRIKE",
            "idempotency_key": "card-test-resume-1",
        })
        _run_store().finish_cancel(original["run_id"], reason=None, sealed=True)
        resumed = handle_resume_run({"run_id": original["run_id"]})

        argv = resumed["request"]["argv"]
        assert argv[argv.index("--card-id") + 1] == "STRIKE"
