"""Tests for code_generator.py — TestSpec → pytest code generation."""
from __future__ import annotations

from pathlib import Path

import pytest

from sts2_autotest.common.spec_models import SuiteSpec, TestSpec
from sts2_autotest.core.code_generator import CodeGenerator


class TestCodeGenerator:
    def setup_method(self) -> None:
        self.generator = CodeGenerator()

    def test_generate_case_test_basic(self) -> None:
        spec = TestSpec(
            id="TC-PREPARE-NEW-RUN",
            title="进入新局地图",
            tags=["smoke", "bootstrap"],
            priority="P0",
            steps=["启动游戏", "选择 Ironclad", "开始新 run"],
            assertions=["不 crash", "位于 MAP"],
        )
        code = self.generator.generate_case_test(spec)
        assert "def test_tc_prepare_new_run" in code
        assert "TC-PREPARE-NEW-RUN" in code
        assert "autotest" in code
        assert "_session_loop" in code
        assert "define(" in code or "from sts2_autotest.dsl.fluent import define" in code

    def test_generate_case_test_with_givens(self) -> None:
        spec = TestSpec(
            id="TC-SETUP",
            title="Setup test",
            givens=["已安装 MOD", "游戏可被启动"],
            steps=["启动游戏"],
            assertions=["游戏运行中"],
        )
        code = self.generator.generate_case_test(spec)
        assert "TC-SETUP" in code
        # Givens should appear as comments in the test
        assert "# Given:" in code or "已安装 MOD" in code

    def test_generate_case_test_empty_steps(self) -> None:
        spec = TestSpec(id="TC-EMPTY", title="Empty")
        code = self.generator.generate_case_test(spec)
        assert "def test_tc_empty" in code
        # Should generate valid code even with no steps
        assert "skip" in code or "no steps" in code

    def test_generate_suite_test_basic(self) -> None:
        suite = SuiteSpec(
            id="SUITE-FIRST-BATTLE-SMOKE",
            title="首次战斗冒烟",
            includes=["TC-PREPARE-NEW-RUN", "TC-RESOLVE-NEOW", "TC-FINISH-FIRST-BATTLE"],
            suite_assertions=["链路应可连续完成"],
        )
        specs = {
            "TC-PREPARE-NEW-RUN": TestSpec(id="TC-PREPARE-NEW-RUN", title="启动游戏", steps=["启动游戏"]),
            "TC-RESOLVE-NEOW": TestSpec(id="TC-RESOLVE-NEOW", title="选择祝福", steps=["选择祝福"]),
            "TC-FINISH-FIRST-BATTLE": TestSpec(id="TC-FINISH-FIRST-BATTLE", title="战斗", steps=["战斗"]),
        }
        code = self.generator.generate_suite_test(suite, specs)
        assert "SUITE-FIRST-BATTLE-SMOKE" in code or "TestSuiteFirstBattleSmoke" in code
        assert "TC-PREPARE-NEW-RUN" in code
        assert "TC-RESOLVE-NEOW" in code
        assert "TC-FINISH-FIRST-BATTLE" in code

    def test_generate_to_file(self, tmp_path: Path) -> None:
        spec = TestSpec(id="TC-FILE", title="File output", steps=["test"])
        output_dir = tmp_path / "generated"
        output_dir.mkdir()
        out_path = self.generator.generate_to_file(spec, str(output_dir))
        assert Path(out_path).exists()
        content = Path(out_path).read_text(encoding="utf-8")
        assert "TC-FILE" in content

    def test_generated_code_syntax(self, tmp_path: Path) -> None:
        """Verify the generated code can be parsed by Python."""
        spec = TestSpec(id="TC-SYNTAX", title="Syntax check", steps=["步骤1"], assertions=["检查1"])
        code = self.generator.generate_case_test(spec)
        try:
            compile(code, "<test>", "exec")
        except SyntaxError as e:
            pytest.fail(f"Generated code has syntax error: {e}")
