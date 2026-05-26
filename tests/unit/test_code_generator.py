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

    def test_generate_case_test_with_start_state_guard(self) -> None:
        spec = TestSpec(
            id="TC-START-GUARD",
            title="Start guard test",
            start_state="- 当前位于地图界面\n- 存在至少一个可到达的普通战斗节点",
            steps=["选择地图节点 (2, 1)", "进入首次战斗"],
        )
        code = self.generator.generate_case_test(spec)
        assert '.require_start_state("""- 当前位于地图界面' in code

    def test_first_battle_smoke_steps_use_dsl_primitives(self) -> None:
        spec = TestSpec(
            id="TC-FIRST-BATTLE-SMOKE",
            title="First battle smoke",
            steps=[
                "返回主菜单",
                "选择标准模式",
                "开始新 run",
                "选择 Ironclad",
                "开始冒险",
                "选择开局事件的第 0 个选项",
                "推进事件对话",
                "选择地图节点 (2, 1)",
                "进入首次战斗",
                "按基础策略完成战斗",
                "跳过卡牌奖励",
            ],
        )

        code = self.generator.generate_case_test(spec)

        assert "return_to_menu()" in code
        # choose_game_mode was removed because 'sts2 choose_game_mode' requires
        # SINGLEPLAYER_SUBMENU but the game starts at MENU. new_run works directly.
        assert 'start_new_run()' in code
        assert "start_new_run()" in code
        assert 'select_character("IRONCLAD")' in code
        assert "embark()" in code
        assert "choose_event(0)" in code
        assert "advance_dialogue()" in code
        assert "choose_map_node(2, 1)" in code
        assert "enter_combat()" in code
        assert "combat_basic_policy()" in code
        assert "skip_card_reward()" in code

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
        assert "def test_suite_first_battle_smoke" in code
        assert "TC-PREPARE-NEW-RUN" in code
        assert "TC-RESOLVE-NEOW" in code
        assert "TC-FINISH-FIRST-BATTLE" in code

    def test_generate_suite_test_keeps_sequential_failure_context(self) -> None:
        suite = SuiteSpec(
            id="SUITE-SMOKE",
            title="Smoke",
            includes=["TC-ONE", "TC-TWO"],
        )
        specs = {
            "TC-ONE": TestSpec(id="TC-ONE", title="One", steps=["鍚姩娓告垙"]),
            "TC-TWO": TestSpec(id="TC-TWO", title="Two", steps=["閫夋嫨 Ironclad"]),
        }
        code = self.generator.generate_suite_test(suite, specs)
        assert "assert result_tc_one.passed" in code
        assert "assert result_tc_two.passed" in code
        assert "TC-ONE failed" in code
        assert "TC-TWO failed" in code

    def test_generate_case_test_uses_spec_semantic_failure_context(self) -> None:
        spec = TestSpec(
            id="TC-SEMANTIC-FAIL",
            title="Semantic failure",
            start_state="MAIN_MENU",
            end_state="MAP",
            steps=["返回主菜单"],
        )
        code = self.generator.generate_case_test(spec)
        assert "failure_context" in code
        assert '"case_id": "TC-SEMANTIC-FAIL"' in code
        assert '"start_state":' in code
        assert '"steps":' in code
        assert "规格执行失败" in code

    def test_generate_suite_test_writes_suite_summary(self) -> None:
        suite = SuiteSpec(
            id="SUITE-SUMMARY",
            title="Summary",
            includes=["TC-ONE"],
        )
        specs = {
            "TC-ONE": TestSpec(id="TC-ONE", title="One", steps=["返回主菜单"]),
        }
        code = self.generator.generate_suite_test(suite, specs)
        assert "suite_results" in code
        assert "suite-summaries" in code
        assert "_write_suite_summary()" in code
        assert '"first_failed_case_id"' in code

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
