"""Tests for code_generator.py — TestSpec → pytest code generation."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sts2_autotest.common.spec_models import SuiteSpec, TestSpec
from sts2_autotest.core.code_generator import CodeGenerator, _build_import_block


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

    def test_generate_case_test_imports_only_used_helpers(self) -> None:
        spec = TestSpec(
            id="TC-PREPARE-NEW-RUN",
            title="进入新局地图",
            steps=["开始新 run"],
            assertions=["不 crash"],
        )

        code = self.generator.generate_case_test(spec)

        assert "import pytest" not in code
        assert "from pathlib import Path" not in code
        assert "start_new_run," in code
        assert "no_crash_detected," in code
        assert "choose_game_mode," not in code
        assert "ActionDescriptor" not in code

    def test_generate_empty_case_keeps_pytest_import(self) -> None:
        spec = TestSpec(id="TC-EMPTY", title="Empty")

        code = self.generator.generate_case_test(spec)

        assert "import pytest" in code
        assert 'pytest.skip("No steps defined")' in code

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

    def test_gawain_character_selection_uses_mod_character_id(self) -> None:
        """MOD 角色别名由项目配置注入后，按配置解析为运行时角色标识。"""
        spec = TestSpec(
            id="TC-GAWAIN-PREPARE",
            title="Prepare Gawain",
            steps=["开始新局", "选择 Gawain", "开始冒险"],
        )

        generator = CodeGenerator(character_aliases={"Gawain": "GAWAINMOD-GAWAIN"})
        code = generator.generate_case_test(spec)

        assert 'select_character("GAWAINMOD-GAWAIN")' in code
        assert 'select_character("IRONCLAD")' not in code

    def test_unknown_ascii_character_passes_through_uppercased(self) -> None:
        """平台默认不含 MOD 角色别名：未配置的 ASCII 角色名按大写透传。"""
        spec = TestSpec(
            id="TC-GAWAIN-PREPARE",
            title="Prepare Gawain",
            steps=["开始新局", "选择 Gawain", "开始冒险"],
        )

        code = self.generator.generate_case_test(spec)

        assert 'select_character("GAWAIN")' in code
        assert 'select_character("GAWAINMOD-GAWAIN")' not in code

    def test_rest_option_step_maps_to_choose_rest_option(self) -> None:
        """通用营火选项写法映射为 choose_rest_option（项目机制词汇不入平台）。"""
        spec = TestSpec(
            id="TC-REST-OPTION",
            title="Rest option",
            steps=["选择营火选项 2", "离开营火返回地图"],
        )

        code = self.generator.generate_case_test(spec)

        assert 'ActionDescriptor(action_type="choose_rest_option", params={"option_index": 2})' in code

    def test_chinese_ironclad_selection_allows_no_space(self) -> None:
        spec = TestSpec(
            id="TC-IRONCLAD-PREPARE",
            title="Prepare Ironclad",
            steps=["开始新局", "选择战士", "开始冒险"],
        )

        code = self.generator.generate_case_test(spec)

        assert 'select_character("IRONCLAD")' in code

    def test_generate_case_test_maps_event_and_combat_state_assertions(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-SCREENS",
            title="Gawain screens",
            steps=["开始新局"],
            assertions=["game reached event", "game reached combat"],
        )

        code = self.generator.generate_case_test(spec)

        assert "game_reached_state(GameScreen.EVENT)" in code
        assert "game_reached_state(GameScreen.COMBAT)" in code

    def test_generate_case_test_maps_give_card_step(self) -> None:
        spec = TestSpec(
            id="TC-IRONCLAD-TWIN-STRIKE",
            title="Ironclad Twin Strike",
            steps=["添加 TWIN_STRIKE 到手牌", "使用 TWIN_STRIKE"],
        )

        code = self.generator.generate_case_test(spec)

        assert "give_card" in code
        assert 'give_card("TWIN_STRIKE")' in code
        assert 'play_card("TWIN_STRIKE")' in code

    def test_generate_case_test_maps_set_seed_step(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-SEEDED-RECRUIT",
            title="Seeded recruit",
            steps=["设置种子 35", "使用 gawain:emergency_recruit"],
        )

        code = self.generator.generate_case_test(spec)

        assert "set_seed" in code
        assert "set_seed(35)" in code
        assert 'play_card("gawain:emergency_recruit")' in code

    def test_generate_case_test_maps_first_available_map_node_step(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-NAVIGATE",
            title="Navigate first node",
            steps=["选择首个可走地图节点", "进入首场战斗"],
        )

        code = self.generator.generate_case_test(spec)

        assert 'ActionDescriptor(action_type="choose_map_node", params={"index": 0})' in code
        assert "enter_combat()" in code

    def test_generate_case_test_maps_transform_card_selection_step(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-NEOW-TRANSFORM",
            title="Transform a starter card",
            steps=["开局事件 第 0 个选项", "选择待变化的第 0 张牌"],
        )

        code = self.generator.generate_case_test(spec)

        assert "choose_event(0)" in code
        assert 'ActionDescriptor(action_type="select_deck_card", params={"index": 0})' in code

    def test_generate_case_test_maps_collect_rewards_and_proceed_step(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-NEOW-REWARD",
            title="Collect reward and leave neow",
            steps=["开局事件 第 1 个选项", "收取奖励并继续"],
        )

        code = self.generator.generate_case_test(spec)

        assert "choose_event(1)" in code
        assert 'ActionDescriptor(action_type="collect_rewards_and_proceed")' in code

    def test_generate_case_test_maps_proceed_step_to_choose_event(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-NEOW-PROCEED",
            title="Proceed from neow",
            steps=["点击 Proceed"],
        )

        code = self.generator.generate_case_test(spec)

        assert "choose_event(0)" in code

    def test_generate_case_test_maps_choose_neow_blessing_step(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-NEOW-BLESSING",
            title="Choose neow blessing",
            steps=["选择涅奥祝福"],
        )

        code = self.generator.generate_case_test(spec)

        assert 'ActionDescriptor(action_type="choose_neow_blessing")' in code

    def test_generate_case_test_maps_minion_queue_assertion(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-SEEDED-RESULT",
            title="Seeded result",
            steps=["设置种子 1", "使用 gawain:emergency_recruit"],
            assertions=["仆从队列为 [cecil_militia]"],
        )

        code = self.generator.generate_case_test(spec)

        assert "minion_queue_ids_are" in code
        assert 'minion_queue_ids_are(["cecil_militia"])' in code

    def test_generate_case_test_maps_empty_minion_queue_assertion(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-CLEAR-QUEUE",
            title="Clear queue",
            steps=["直接获胜当前战斗"],
            assertions=["仆从队列为 []"],
        )

        code = self.generator.generate_case_test(spec)

        assert "minion_queue_ids_are([])" in code

    def test_generate_case_test_maps_set_hp_and_debug_steps(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-HEAL-SETUP",
            title="Heal setup",
            steps=["设置玩家生命值 50", "给予玩家 99 点格挡", "直接获胜当前战斗"],
        )

        code = self.generator.generate_case_test(spec)

        assert "set_hp(50)" in code
        assert 'ActionDescriptor(action_type="give_block", params={"amount": 99})' in code
        assert 'ActionDescriptor(action_type="win_combat")' in code

    def test_generate_case_test_maps_rest_assertion(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-REST",
            title="Rest assertion",
            steps=["选择地图节点 (1, 0)"],
            assertions=["game reached state REST"],
        )

        code = self.generator.generate_case_test(spec)

        assert "game_reached_state(GameScreen.REST)" in code

    def test_generate_case_test_maps_choose_first_rest_node_step(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-REST-NODE",
            title="Rest node",
            steps=["选择首个营火节点"],
        )

        code = self.generator.generate_case_test(spec)

        assert 'ActionDescriptor(action_type="choose_map_node_by_type", params={"node_type": "RestSite"})' in code

    def test_generate_case_test_maps_exact_hit_assertion(self) -> None:
        spec = TestSpec(
            id="TC-IRONCLAD-TWIN-STRIKE",
            title="Ironclad Twin Strike",
            steps=["使用 TWIN_STRIKE"],
            assertions=["造成 5 点伤害 2 次"],
        )

        code = self.generator.generate_case_test(spec)

        assert "enemy_took_exact_hits" in code
        assert "enemy_took_exact_hits(5, 2)" in code

    def test_generate_case_test_maps_effect_assertions(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-EFFECTS",
            title="Gawain effects",
            steps=["使用 gawain:defend"],
            assertions=[
                "敌人受到 6 点伤害",
                "玩家格挡增加 5",
                "玩家能量减少 1",
                "玩家回复 1 点生命",
            ],
        )

        code = self.generator.generate_case_test(spec)

        assert "enemy_hp_decreased_by(6)" in code
        assert "player_block_increased_by(5)" in code
        assert "player_energy_decreased_by(1)" in code
        assert "player_hp_changed_by(1)" in code
        assert "# TODO: implement assertion" not in code

    def test_generate_case_test_maps_rest_assertion(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-NAVIGATE-TO-REST",
            title="Gawain rest navigation",
            steps=["选择地图节点 (1, 0)"],
            assertions=["game reached state REST", "进入营火界面"],
        )

        code = self.generator.generate_case_test(spec)

        assert "game_reached_state(GameScreen.REST)" in code
        assert "# TODO: implement assertion" not in code

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

    def test_generate_suite_test_maps_effect_assertions(self) -> None:
        suite = SuiteSpec(
            id="SUITE-GAWAIN-EFFECTS",
            title="Gawain effects",
            includes=["TC-GAWAIN-EFFECTS"],
        )
        specs = {
            "TC-GAWAIN-EFFECTS": TestSpec(
                id="TC-GAWAIN-EFFECTS",
                title="Gawain effects",
                steps=["使用 gawain:defend"],
                assertions=["敌人受到 6 点伤害", "玩家格挡增加 5"],
            ),
        }

        code = self.generator.generate_suite_test(suite, specs)

        assert "enemy_hp_decreased_by(6)" in code
        assert "player_block_increased_by(5)" in code
        assert "# TODO: implement assertion" not in code

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

    def test_generate_suite_test_writes_summary_next_to_generated_tree(self) -> None:
        suite = SuiteSpec(
            id="SUITE-SUMMARY",
            title="Summary",
            includes=["TC-ONE"],
        )
        specs = {
            "TC-ONE": TestSpec(id="TC-ONE", title="One", steps=["返回主菜单"]),
        }

        code = self.generator.generate_suite_test(suite, specs)

        assert "Path(__file__).resolve().parent.parent / \"output\" / \"suite-summaries\"" in code
        assert "Path('tests/output/suite-summaries')" not in code

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

    def test_import_block_groups_and_sorts_by_isort_default(self) -> None:
        """import 块按 标准库→第三方→本项目 分组，组间一个空行，断言名按字典序。"""
        body = (
            'json.dumps(x)\n'
            'Path("/tmp/x")\n'
            'pytest.skip("x")\n'
            'GameScreen.MAP\n'
            'ActionDescriptor(action_type="x")\n'
            'start_new_run()\n'
            'return_to_menu()\n'
            'no_crash_detected()\n'
        )

        block = _build_import_block(body)

        assert block == (
            "import json\n"
            "from pathlib import Path\n"
            "\n"
            "import pytest\n"
            "\n"
            "from sts2_autotest.common.state import GameScreen\n"
            "from sts2_autotest.core.action_model import ActionDescriptor\n"
            "from sts2_autotest.dsl.assertions import (\n"
            "    no_crash_detected,\n"
            "    return_to_menu,\n"
            "    start_new_run,\n"
            ")\n"
            "from sts2_autotest.dsl.fluent import define\n"
        )

    def test_import_block_sorts_assertion_names_alphabetically(self) -> None:
        """断言名在 import 块内按字典序输出，与 spec 声明顺序无关。"""
        spec = TestSpec(
            id="TC-SORT-ASSERTS",
            title="Sort assertions",
            steps=["开始新 run"],
            assertions=[
                "玩家格挡增加 5",        # player_block_increased_by
                "敌人受到 6 点伤害",      # enemy_hp_decreased_by
                "不 crash",               # no_crash_detected
                "game reached state REST",  # game_reached_state
            ],
        )

        code = self.generator.generate_case_test(spec)

        match = re.search(
            r"from sts2_autotest\.dsl\.assertions import \((.*?)\)\n",
            code,
            re.DOTALL,
        )
        assert match is not None
        names = [
            line.strip().rstrip(",")
            for line in match.group(1).splitlines()
            if line.strip()
        ]
        assert names == sorted(names)
        for expected in (
            "enemy_hp_decreased_by",
            "game_reached_state",
            "no_crash_detected",
            "player_block_increased_by",
        ):
            assert expected in names

    def test_import_block_and_body_separated_by_two_blank_lines(self) -> None:
        spec = TestSpec(
            id="TC-BLANK-LINES",
            title="Blank lines",
            steps=["开始新 run"],
            assertions=["不 crash"],
        )

        code = self.generator.generate_case_test(spec)

        # import 块以 define 收尾，随后恰好两个空行再进入 def
        assert (
            "from sts2_autotest.dsl.fluent import define\n\n\ndef test_tc_blank_lines"
            in code
        )

    def test_generate_case_test_is_idempotent(self) -> None:
        spec = TestSpec(
            id="TC-IDEMPOTENT",
            title="Idempotent",
            steps=["启动游戏", "选择 Ironclad", "开始新 run"],
            assertions=["不 crash", "位于 MAP"],
        )

        assert self.generator.generate_case_test(spec) == self.generator.generate_case_test(spec)

    def test_generate_suite_test_is_idempotent(self) -> None:
        suite = SuiteSpec(
            id="SUITE-IDEMPOTENT",
            title="Idempotent suite",
            includes=["TC-ONE"],
        )
        specs = {
            "TC-ONE": TestSpec(id="TC-ONE", title="One", steps=["开始新 run"]),
        }

        first = self.generator.generate_suite_test(suite, specs)
        second = self.generator.generate_suite_test(suite, specs)
        assert first == second

    def test_generated_code_satisfies_ruff_i001(self, tmp_path: Path) -> None:
        """生成产物应通过 Ruff I001（import 排序）；ruff 不可用时跳过。"""
        ruff = shutil.which("ruff")
        if ruff is None:
            pytest.skip("ruff 不在 PATH，跳过 I001 校验")

        spec = TestSpec(
            id="TC-RUFF-I001",
            title="Ruff I001",
            steps=["启动游戏", "选择 Ironclad", "开始新 run", "进入首次战斗"],
            assertions=["不 crash", "位于 MAP", "敌人受到 6 点伤害"],
        )
        out_file = tmp_path / "test_tc_ruff_i001.py"
        out_file.write_text(self.generator.generate_case_test(spec), encoding="utf-8")

        result = subprocess.run(
            [ruff, "check", "--select", "I001", "--output-format", "concise", str(out_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
