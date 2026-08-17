"""Unit tests for MCP tool implementations."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sts2_autotest.cli.mcp_protocol import McpError
from sts2_autotest.cli.mcp_tools import (
    ToolRegistry,
    handle_compile_spec,
    handle_get_report,
    handle_health_check,
    handle_list_specs,
    handle_review_spec,
    handle_run_pipeline,
    handle_run_test,
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
        from sts2_autotest.cli import mcp_tools

        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        project = tmp_path / "examplemod"
        project.mkdir()
        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [tmp_path])
        args = {
            "project": str(project),
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
        from sts2_autotest.cli import mcp_tools

        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        project = tmp_path / "examplemod"
        project.mkdir()
        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [tmp_path])
        handle_submit_run({"project": str(project), "suite": "smoke"})

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

    def test_submit_rejects_directory_project_outside_allowed_roots(self, monkeypatch, tmp_path):
        """目录型 project 必须位于允许范围内（与 spec_dir 同一白名单）。"""
        from sts2_autotest.cli import mcp_tools
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [allowed])
        outside = tmp_path / "outside" / "mod"
        outside.mkdir(parents=True)

        with pytest.raises(McpError, match="allowed roots"):
            handle_submit_run({"project": str(outside), "journey": "new_run"})

    def test_submit_rejects_project_config_outside_allowed_roots(self, monkeypatch, tmp_path):
        """项目声明指向的配置文件越出允许范围时同样拒绝。"""
        from sts2_autotest.cli import mcp_tools
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [allowed])
        # 项目目录在白名单内，但 manifest 指向白名单外的配置文件
        mod_dir = allowed / "mod"
        mod_dir.mkdir()
        outside_config = tmp_path / "outside-config.yaml"
        outside_config.write_text("project_extension: {}\n", encoding="utf-8")
        (mod_dir / "sts2-mod.yaml").write_text(
            f"mod:\n  id: mod\nautotest:\n  config: {outside_config}\n",
            encoding="utf-8",
        )

        with pytest.raises(McpError, match="allowed roots"):
            handle_submit_run({"project": str(mod_dir), "journey": "new_run"})

    def test_run_tests_in_dir_injects_project_dir_env(self, monkeypatch, tmp_path):
        """执行生成测试时，项目上下文经 STS2_PROJECT_DIR 传入子进程。"""
        from sts2_autotest.cli import mcp_tools

        captured: dict = {}

        class FakeCompleted:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            captured["cmd"] = cmd
            return FakeCompleted()

        monkeypatch.setattr(mcp_tools.subprocess, "run", fake_run)
        project_dir = tmp_path / "my-mod"
        project_dir.mkdir()

        mcp_tools.run_tests_in_dir(
            tmp_path, timeout=10, project_dir=project_dir, output_dir=tmp_path / "out"
        )

        assert captured["env"]["STS2_PROJECT_DIR"] == str(project_dir.resolve())

    def test_run_tests_in_dir_without_project_keeps_default_env(self, monkeypatch, tmp_path):
        from sts2_autotest.cli import mcp_tools

        captured: dict = {}

        class FakeCompleted:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return FakeCompleted()

        monkeypatch.setattr(mcp_tools.subprocess, "run", fake_run)

        mcp_tools.run_tests_in_dir(tmp_path, timeout=10, output_dir=tmp_path / "out")

        assert captured["env"] is None

    def test_submit_rejects_declared_spec_outside_allowed_roots(self, monkeypatch, tmp_path):
        """项目声明的规格目录指向允许范围外时拒绝（白名单覆盖声明内部路径）。"""
        from sts2_autotest.cli import mcp_tools
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [allowed])
        mod_dir = allowed / "mod"
        mod_dir.mkdir()
        outside_specs = tmp_path / "outside-specs"
        outside_specs.mkdir()
        (mod_dir / "sts2-mod.yaml").write_text(
            "mod:\n  id: mod\n"
            "autotest:\n"
            f"  spec_dirs:\n    - {outside_specs}\n"
            "  evidence_dir: automation/autotest/output\n",
            encoding="utf-8",
        )

        with pytest.raises(McpError, match="allowed roots"):
            handle_submit_run({"project": str(mod_dir), "journey": "new_run"})

    def test_submit_rejects_declared_output_outside_allowed_roots(self, monkeypatch, tmp_path):
        """项目声明的输出目录指向允许范围外时拒绝。"""
        from sts2_autotest.cli import mcp_tools
        from sts2_autotest.cli.mcp_tools import handle_submit_run

        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [allowed])
        mod_dir = allowed / "mod"
        mod_dir.mkdir()
        outside_output = tmp_path / "outside-output"
        outside_output.mkdir()
        (mod_dir / "sts2-mod.yaml").write_text(
            "mod:\n  id: mod\n"
            "autotest:\n"
            "  spec_dirs:\n    - automation/autotest/specs\n"
            f"  evidence_dir: {outside_output}\n",
            encoding="utf-8",
        )

        with pytest.raises(McpError, match="allowed roots"):
            handle_submit_run({"project": str(mod_dir), "journey": "new_run"})

    def test_run_test_rejects_project_outside_allowed_roots(self, monkeypatch, tmp_path):
        """run_test 与 submit_run 同一校验：白名单外项目目录拒绝。"""
        from sts2_autotest.cli import mcp_tools
        from sts2_autotest.cli.mcp_tools import handle_run_test

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [allowed])
        specs = allowed / "specs"
        specs.mkdir()
        outside = tmp_path / "outside" / "mod"
        outside.mkdir(parents=True)

        with pytest.raises(McpError, match="allowed roots"):
            handle_run_test({"spec_dir": str(specs), "project": str(outside)})

    def test_run_test_rejects_unresolvable_project_name(self, monkeypatch, tmp_path):
        """无法解析的登记名称结构化失败（PROJECT_CONFIG_INVALID），不静默中性执行。"""
        from sts2_autotest.cli import mcp_tools
        from sts2_autotest.cli.mcp_tools import handle_run_test

        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [tmp_path])
        monkeypatch.chdir(tmp_path)
        specs = tmp_path / "specs"
        specs.mkdir()

        with pytest.raises(McpError, match="PROJECT_CONFIG_INVALID"):
            handle_run_test({"spec_dir": str(specs), "project": "no-such-registered-name"})

    def test_registered_project_is_validated_after_resolution(self, monkeypatch, tmp_path):
        """登记名称解析出的真实目录同样必须位于允许范围内。"""
        from sts2_autotest.cli import mcp_tools
        from sts2_autotest.cli.mcp_tools import handle_run_test

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        specs = allowed / "specs"
        specs.mkdir()
        outside = tmp_path / "outside-project"
        outside.mkdir()
        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [allowed])
        monkeypatch.setattr(
            "sts2_autotest.cli.main._resolve_project_base_dir",
            lambda _project: outside,
        )

        with pytest.raises(McpError, match="PROJECT_CONFIG_INVALID.*allowed roots"):
            handle_run_test({"spec_dir": str(specs), "project": "registered-mod"})

    def test_all_public_project_entries_reject_unknown_name(self, monkeypatch, tmp_path):
        """提交、编译、执行、完整流水线对未知项目使用同一结构化失败。"""
        from sts2_autotest.cli import mcp_tools

        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [tmp_path])
        monkeypatch.chdir(tmp_path)
        specs = tmp_path / "specs"
        specs.mkdir()
        spec = specs / "TC-X.md"
        spec.write_text("# TC-X", encoding="utf-8")
        calls = [
            lambda: mcp_tools.handle_submit_run(
                {"project": "unknown-project", "journey": "new_run"}
            ),
            lambda: mcp_tools.handle_compile_spec(
                {"project": "unknown-project", "spec_path": str(spec)}
            ),
            lambda: mcp_tools.handle_run_test(
                {"project": "unknown-project", "spec_dir": str(specs)}
            ),
            lambda: mcp_tools.handle_run_pipeline(
                {"project": "unknown-project", "spec_dir": str(specs)}
            ),
        ]

        for call in calls:
            with pytest.raises(McpError, match="PROJECT_CONFIG_INVALID"):
                call()

    def test_run_test_and_pipeline_schemas_expose_project(self) -> None:
        """编译、执行与完整流水线均公开 project（Agent 可发现）。"""
        from sts2_autotest.cli.mcp_tools import ToolRegistry

        registry = ToolRegistry()
        tools = {tool.name: tool for tool in registry.list_tools()}

        for name in ("compile_spec", "run_test", "run_pipeline"):
            props = tools[name].input_schema.get("properties", {})
            assert "project" in props, f"{name} 的公开参数缺少 project"

    def test_run_pipeline_passes_project_to_compile_and_run(self, monkeypatch, tmp_path) -> None:
        """Agent 完整流程：project 贯穿编译（别名）与执行（项目上下文）。"""
        from sts2_autotest.cli import mcp_tools

        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [tmp_path])
        monkeypatch.delenv("STS2_PROJECT__CHARACTER_ALIASES", raising=False)
        mod_dir = tmp_path / "my-mod"
        config_dir = mod_dir / "automation/autotest/config"
        config_dir.mkdir(parents=True)
        (config_dir / "sts2-autotest.yaml").write_text(
            "project_extension:\n"
            "  character_aliases:\n"
            "    MyChar: MYMOD-MYCHAR\n",
            encoding="utf-8",
        )
        (mod_dir / "sts2-mod.yaml").write_text(
            "mod:\n  id: mymod\nautotest:\n  config: automation/autotest/config/sts2-autotest.yaml\n",
            encoding="utf-8",
        )
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        (spec_dir / "TC-X.md").write_text(
            "# TC-X\n\n## Metadata\n- id: TC-X\n- level: case\n- priority: P0\n\n"
            "## Start State\n- MAIN_MENU\n\n## End State\n- EVENT\n\n"
            "## When\n1. 选择 MyChar\n2. 开始冒险\n\n## Then\n- 不 crash\n",
            encoding="utf-8",
        )
        captured: dict = {}

        def fake_run_tests_in_dir(spec_dir_arg, suite="", timeout=60, **kwargs):
            captured["project_dir"] = kwargs.get("project_dir")
            return {"run_id": "fake", "status": "OK"}

        monkeypatch.setattr(mcp_tools, "run_tests_in_dir", fake_run_tests_in_dir)

        result = mcp_tools.handle_run_pipeline({
            "spec_dir": str(spec_dir),
            "project": str(mod_dir),
            "stages": ["compile", "run"],
        })

        # 编译产物使用了项目别名（MyChar → MYMOD-MYCHAR）
        generated = result["compiled_files"][0]
        code = open(generated, encoding="utf-8").read()
        assert 'select_character("MYMOD-MYCHAR")' in code
        # 执行阶段收到项目上下文
        assert captured["project_dir"] is not None
        assert str(mod_dir) in str(captured["project_dir"])

    @patch("sts2_autotest.cli.mcp_tools.spawn_worker")
    def test_resume_run_preserves_resume_mode_in_worker_argv(self, mock_worker, monkeypatch, tmp_path):
        from sts2_autotest.cli import mcp_tools

        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_tools import (
            _run_store,
            handle_resume_run,
            handle_submit_run,
        )

        project = tmp_path / "examplemod"
        project.mkdir()
        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [tmp_path])
        original = handle_submit_run({"project": str(project), "suite": "smoke"})
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
        from sts2_autotest.cli import mcp_tools

        monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(tmp_path / "runs"))
        from sts2_autotest.cli.mcp_protocol import McpError
        from sts2_autotest.cli.mcp_tools import handle_resume_run, handle_submit_run

        project = tmp_path / "examplemod"
        project.mkdir()
        monkeypatch.setattr(mcp_tools, "_ALLOWED_ROOTS", [tmp_path])
        original = handle_submit_run({"project": str(project), "suite": "smoke"})
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
        from sts2_autotest.common.spec_models import (
            IssueCategory,
            ReviewIssue,
            ReviewReport,
        )
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
