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

    def test_run_test_agent_version_regex_matches_current_version(self) -> None:
        """验证脚本中的正则模式能匹配当前 __init__.py 的真实版本号。"""
        import re

        init_path = Path(__file__).resolve().parents[2] / "src" / "sts2_autotest" / "__init__.py"
        init_content = init_path.read_text(encoding="utf-8")

        # 提取 run-test-agent.ps1 实际使用的版本正则，避免测试和脚本各写一套规则。
        script_content = SCRIPT_PATH.read_text(encoding="utf-8")
        match = re.search(r"Select-String.*?-Pattern\s+'([^']+)'", script_content)
        assert match is not None, "未在 run-test-agent.ps1 中找到 Select-String 版本正则"

        pattern = match.group(1)
        vm = re.search(pattern, init_content)
        assert vm is not None, (
            f"run-test-agent.ps1 中的正则 {pattern!r} 未能匹配 __init__.py"
        )
        # 确认提取结果至少符合语义化版本的基本形态。
        version = vm.group(1)
        assert re.match(r"^\d+\.\d+\.\d+", version), f"提取到的版本 {version!r} 不是有效 semver"
