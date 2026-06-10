"""Tests for scripts/run-test-agent.ps1 — 报告模板的版本可观测性与兼容性阻塞约定。"""

from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run-test-agent.ps1"


class TestRunTestAgentReport:
    """run-test-agent.ps1 报告模板约定测试。"""

    def test_run_test_agent_report_mentions_autotest_version(self) -> None:
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        assert "Autotest version:" in content

    def test_run_test_agent_report_mentions_platform_compatibility_block(self) -> None:
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        assert "autotest_compatibility_blocked" in content

    def test_run_test_agent_version_read_from_package_source(self) -> None:
        """版本必须从 src/sts2_autotest/__init__.py 单一来源解析，不允许硬编码。"""
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        assert "src/sts2_autotest/__init__.py" in content
        assert "__version__" in content
