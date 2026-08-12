"""Code generator — converts TestSpec/SuiteSpec into pytest + Fluent DSL code.

Outputs syntactically valid Python files that use the framework's
main chain (FluentBuilder -> ActionDescriptor -> Orchestrator).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sts2_autotest.common.spec_models import SuiteSpec, TestSpec

_ASSERTION_IMPORTS = (
    "advance_dialogue",
    "choose_event",
    "choose_game_mode",
    "choose_map_node",
    "combat_basic_policy",
    "embark",
    "end_turn",
    "enemy_hp_decreased_by",
    "enemy_took_exact_hits",
    "enter_combat",
    "game_reached_state",
    "give_card",
    "hand_size_changed_by",
    "minion_queue_ids_are",
    "no_crash_detected",
    "has_travelable_node",
    "play_card",
    "player_block_increased_by",
    "player_energy_decreased_by",
    "player_hp_changed_by",
    "return_to_menu",
    "select_character",
    "set_hp",
    "set_seed",
    "skip_card_reward",
    "start_new_run",
)


def _build_import_block(body: str) -> str:
    """只导入生成内容实际使用的名称，避免生成文件依赖无关功能。"""
    imports: list[str] = []
    if "json." in body:
        imports.append("import json")
    if "Path(" in body:
        imports.append("from pathlib import Path")
    if "pytest." in body:
        imports.append("import pytest")
    imports.append("from sts2_autotest.dsl.fluent import define")
    used = [name for name in _ASSERTION_IMPORTS if f"{name}(" in body]
    if used:
        imports.append(
            "from sts2_autotest.dsl.assertions import (\n"
            + "\n".join(f"    {name}," for name in used)
            + "\n)"
        )
    if "GameScreen." in body:
        imports.append("from sts2_autotest.common.state import GameScreen")
    if "ActionDescriptor(" in body:
        imports.append("from sts2_autotest.core.action_model import ActionDescriptor")
    return "\n".join(imports)


_STEP_TO_ACTION: dict[str, str] = {
    "结束回合": "end_turn()",
    "使用": "play_card",
}

_CHARACTER_IDS: dict[str, str] = {
    "Ironclad": "IRONCLAD",
    "战士": "IRONCLAD",
    "铁甲战士": "IRONCLAD",
}

_ASCII_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _assertion_to_call(assertion: str) -> str:
    """Convert a natural-language Then assertion into a DSL assertion call.

    Shared by case- and suite-level generation so the recognized pattern set
    never drifts between the two. Falls back to a TODO placeholder (no crash,
    no silent pass) when nothing matches.
    """
    assertion_lower = assertion.lower()

    exact_hits_match = re.search(r"造成\s*(\d+)\s*点伤害\s*(\d+)\s*次", assertion)
    if exact_hits_match:
        return f"enemy_took_exact_hits({exact_hits_match.group(1)}, {exact_hits_match.group(2)})"

    if "deal" in assertion_lower and "damage" in assertion_lower and "twice" in assertion_lower:
        damage_match = re.search(r"deal\s+(\d+)\s+damage", assertion_lower)
        if damage_match:
            return f"enemy_took_exact_hits({damage_match.group(1)}, 2)"
        return f"# TODO: implement assertion for '{assertion}'"

    enemy_damage_match = re.search(r"敌人受到\s*(\d+)\s*点伤害", assertion)
    if enemy_damage_match:
        return f"enemy_hp_decreased_by({enemy_damage_match.group(1)})"

    energy_match = re.search(r"玩家能量减少\s*(\d+)", assertion)
    if energy_match:
        return f"player_energy_decreased_by({energy_match.group(1)})"

    block_match = re.search(r"玩家格挡增加\s*(\d+)", assertion)
    if block_match:
        return f"player_block_increased_by({block_match.group(1)})"

    heal_cn_match = re.search(r"(?:玩家)?回复\s*(\d+)\s*点生命", assertion)
    if heal_cn_match:
        return f"player_hp_changed_by({heal_cn_match.group(1)})"

    hp_changed_match = re.search(r"player hp changed by\s*([+-]?\d+)", assertion_lower)
    if hp_changed_match:
        return f"player_hp_changed_by({hp_changed_match.group(1)})"

    hand_match = re.search(r"手牌(?:数量)?(增加|减少)\s*(\d+)", assertion)
    if hand_match:
        sign = "" if hand_match.group(1) == "增加" else "-"
        return f"hand_size_changed_by({sign}{hand_match.group(2)})"

    minion_queue_match = re.search(r"仆从队列(?:为|等于)\s*\[([A-Za-z0-9_:\-,\s]*)\]", assertion)
    if minion_queue_match is not None:
        ids = [
            item.strip().strip("'").strip('"')
            for item in minion_queue_match.group(1).split(",")
            if item.strip()
        ]
        quoted_ids = ", ".join(json.dumps(item, ensure_ascii=False) for item in ids)
        return f"minion_queue_ids_are([{quoted_ids}])"

    if "crash" in assertion_lower:
        return "no_crash_detected()"
    if "rest" in assertion_lower or "营火" in assertion or "休息" in assertion:
        return "game_reached_state(GameScreen.REST)"
    if "map" in assertion_lower or "地图" in assertion:
        return "game_reached_state(GameScreen.MAP)"
    if "event" in assertion_lower or "事件" in assertion:
        return "game_reached_state(GameScreen.EVENT)"
    if "combat" in assertion_lower or "战斗" in assertion:
        return "game_reached_state(GameScreen.COMBAT)"
    if "节点" in assertion or "node" in assertion_lower:
        return "has_travelable_node()"

    return f"# TODO: implement assertion for '{assertion}'"


def _step_to_action_call(step: str, character_ids: dict[str, str] | None = None) -> str:
    """Convert a natural-language step into a DSL call or ActionDescriptor.

    character_ids：角色别名到运行时角色标识的映射。默认仅含原游戏角色；
    MOD 项目可经 project_extension.character_aliases 注入自己的别名。
    未命中映射的 ASCII 角色名按大写原样透传（原游戏角色命名约定）。
    """
    if character_ids is None:
        character_ids = _CHARACTER_IDS
    step = step.strip()

    if step in {"启动游戏", "开始新局", "开始新 run"}:
        return "start_new_run()"
    if step == "开始冒险":
        return "embark()"
    if step == "返回主菜单":
        return "return_to_menu()"
    if step in {"进入首次战斗", "进入首场战斗", "战斗"}:
        return "enter_combat()"
    if step == "推进事件对话":
        return "advance_dialogue()"
    if "基础策略" in step and "战斗" in step:
        return "combat_basic_policy()"
    if step == "收取奖励并继续":
        return 'ActionDescriptor(action_type="collect_rewards_and_proceed")'
    if "跳过卡牌奖励" in step:
        return "skip_card_reward()"
    if step == "直接获胜当前战斗":
        return 'ActionDescriptor(action_type="win_combat")'
    if step == "启用地图穿行":
        return 'ActionDescriptor(action_type="enable_travel")'
    if step == "选择首个营火节点":
        return 'ActionDescriptor(action_type="choose_map_node_by_type", params={"node_type": "RestSite"})'
    if step == "选择首个普通战斗节点" or step == "选择首个战斗节点":
        return 'ActionDescriptor(action_type="nav_to_screen", params={"target": "COMBAT"})'
    if step == "选择涅奥祝福":
        return 'ActionDescriptor(action_type="choose_neow_blessing")'
    if step in {"点击 Proceed", "点击继续前进", "选择 Proceed"}:
        return "choose_event(0)"

    rest_option_match = re.search(r"选择营火[^\d]*(\d+)", step)
    if rest_option_match:
        return (
            'ActionDescriptor(action_type="choose_rest_option", '
            f'params={{"option_index": {rest_option_match.group(1)}}})'
        )
    if step == "离开营火返回地图":
        return 'ActionDescriptor(action_type="proceed")'

    event_match = re.search(r"开局事件(?:的)?\s*第\s*(\d+)\s*个选项", step)
    if event_match:
        return f"choose_event({event_match.group(1)})"

    map_node_match = re.search(r"地图节点\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", step)
    if map_node_match:
        return f"choose_map_node({map_node_match.group(1)}, {map_node_match.group(2)})"
    if "首个可走地图节点" in step or "第一个可走地图节点" in step:
        return 'ActionDescriptor(action_type="choose_map_node", params={"index": 0})'

    deck_card_match = re.search(r"选择待变化的第\s*(\d+)\s*张牌", step)
    if deck_card_match:
        return (
            'ActionDescriptor(action_type="select_deck_card", '
            f'params={{"index": {deck_card_match.group(1)}}})'
        )

    deck_option_match = re.search(r"牌堆选牌.*第\s*(\d+)\s*个选项", step)
    if deck_option_match:
        return (
            'ActionDescriptor(action_type="select_deck_card", '
            f'params={{"index": {deck_option_match.group(1)}}})'
        )

    character_match = re.search(
        r"选择\s*([A-Za-z][A-Za-z0-9_-]*|战士|铁甲战士)", step
    )
    if character_match:
        character_name = character_match.group(1)
        character_id = character_ids.get(character_name)
        if character_id is None and _ASCII_NAME_PATTERN.match(character_name):
            # 未配置别名的 ASCII 角色名按大写透传（原游戏角色命名约定）；
            # MOD 角色应在 project_extension.character_aliases 中提供映射。
            character_id = character_name.upper()
        if character_id:
            return f'select_character("{character_id}")'

    give_card_match = re.search(r"添加\s+([A-Za-z0-9_:-]+)\s+到手牌", step)
    if give_card_match:
        return f'give_card("{give_card_match.group(1)}")'

    play_card_match = re.search(r"使用\s+([A-Za-z0-9_:-]+)", step)
    if play_card_match:
        return f'play_card("{play_card_match.group(1)}")'

    seed_match = re.search(r"设置种子\s*(-?\d+)", step)
    if seed_match:
        return f"set_seed({seed_match.group(1)})"

    hp_match = re.search(r"设置玩家生命(?:值)?\s*(\d+)", step)
    if hp_match:
        return f"set_hp({hp_match.group(1)})"

    block_match = re.search(r"给予玩家\s*(\d+)\s*点格挡", step)
    if block_match:
        return (
            'ActionDescriptor(action_type="give_block", '
            f'params={{"amount": {block_match.group(1)}}})'
        )

    step_lower = step.lower()
    for keyword, action in _STEP_TO_ACTION.items():
        if step_lower.startswith(keyword):
            return action

    return f"ActionDescriptor(action_type={step!r})"


def _case_id_to_function_name(case_id: str) -> str:
    """Convert TC-PREPARE-NEW-RUN to tc_prepare_new_run."""
    return case_id.lower().replace("-", "_")


def _case_id_to_class_name(suite_id: str) -> str:
    """Convert SUITE-FIRST-BATTLE-SMOKE to TestSuiteFirstBattleSmoke."""
    parts = suite_id.replace("-", " ").title().split()
    return "TestSuite" + "".join(parts[1:])


class CodeGenerator:
    """Generates pytest test files from TestSpec/SuiteSpec models.

    Output code follows the standard framework chain:
        pytest fixture -> FluentBuilder -> ActionDescriptor -> DSL -> adapter

    character_aliases：项目提供的角色别名映射（project_extension.character_aliases），
    与原游戏角色映射合并后用于"选择 X"步骤解析；平台默认仅含原游戏角色。
    """

    def __init__(self, character_aliases: dict[str, str] | None = None) -> None:
        self._character_ids: dict[str, str] = {
            **_CHARACTER_IDS,
            **(character_aliases or {}),
        }

    def _generate_case_body(self, spec: TestSpec) -> str:
        """Generate the function definition body for a test case (no import block).

        Returns the function as a string without module-level imports.
        The caller is responsible for prepending _IMPORT_BLOCK.
        """
        func_name = _case_id_to_function_name(spec.id)
        steps = spec.steps
        assertions = spec.assertions

        # Empty steps -> generate a skipped test function
        if not steps:
            return "\n".join(
                [
                    f"def test_{func_name}():",
                    f'    """{spec.title}"""',
                    '    pytest.skip("No steps defined")',
                ]
            )

        # C2 fix: last step goes to .execute(), all others to .setup()
        setup_steps = steps[:-1] if len(steps) > 1 else []
        execute_step = steps[-1]

        # Build assertion calls (no leading whitespace -> indented at assembly time)
        assert_calls: list[str] = [_assertion_to_call(assertion) for assertion in assertions]

        # Build function body lines
        lines: list[str] = []

        if spec.givens:
            for g in spec.givens:
                lines.append(f"# Given: {g}")

        lines.append(f"def test_{func_name}(autotest, _session_loop):")
        lines.append(f'    """{spec.title}"""')
        lines.append("    result = (")
        lines.append(
            f'        define("{spec.id}", autotest, _session_loop)'
        )
        if spec.start_state:
            lines.append(
                f'        .require_start_state("""{spec.start_state}""")'
            )
        lines.append("        .setup(")
        for step in setup_steps:
            lines.append(f"            {_step_to_action_call(step, self._character_ids)},")
        lines.append("        )")
        lines.append("        .execute(")
        lines.append(
            f"            {_step_to_action_call(execute_step, self._character_ids)},"
        )
        lines.append("        )")
        lines.append("        .assert_that(")
        if assert_calls:
            for call in assert_calls:
                lines.append(f"            {call},")
        else:
            lines.append("            # no assertions defined")
        lines.append("        )")
        lines.append("    )")
        lines.append("    failure_context = {")
        lines.append(f'        "case_id": {json.dumps(spec.id, ensure_ascii=False)},')
        lines.append(f'        "title": {json.dumps(spec.title, ensure_ascii=False)},')
        lines.append(
            f'        "start_state": {json.dumps(spec.start_state, ensure_ascii=False)},'
        )
        lines.append(
            f'        "end_state": {json.dumps(spec.end_state, ensure_ascii=False)},'
        )
        lines.append(f'        "steps": {json.dumps(spec.steps, ensure_ascii=False)},')
        lines.append("        \"failures\": result.failures,")
        lines.append("        \"detail\": result.detail,")
        lines.append("    }")
        lines.append(
            "    assert result.passed, "
            "'规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)"
        )

        return "\n".join(lines)

    def generate_case_test(self, spec: TestSpec) -> str:
        """Generate a complete pytest test file for a single case."""
        body = self._generate_case_body(spec)
        return _build_import_block(body) + "\n\n" + body

    def generate_suite_test(
        self, suite: SuiteSpec, specs: dict[str, TestSpec]
    ) -> str:
        """Generate a single pytest test function for a shared-session suite."""
        func_name = _case_id_to_function_name(suite.id)
        lines: list[str] = [f"def test_{func_name}(autotest, _session_loop):"]
        lines.append(f'    """{suite.title}"""')
        if suite.goal:
            for goal_line in suite.goal.split("\n"):
                lines.append(f"    # Goal: {goal_line}")
        lines.append(f"    # Execution mode: {suite.execution_mode}")
        for assertion in suite.suite_assertions:
            lines.append(f"    # Suite assertion: {assertion}")
        lines.append("    suite_results = []")
        lines.append("    summary_path = Path(__file__).resolve().parent.parent / \"output\" / \"suite-summaries\" / "
                     f"{json.dumps(suite.id + '.json', ensure_ascii=False)}")
        lines.append("")
        lines.append("    def _write_suite_summary():")
        lines.append("        summary_path.parent.mkdir(parents=True, exist_ok=True)")
        lines.append("        first_failed = next((item for item in suite_results if not item['passed']), None)")
        lines.append("        summary = {")
        lines.append(f'            "suite_id": {json.dumps(suite.id, ensure_ascii=False)},')
        lines.append(f'            "title": {json.dumps(suite.title, ensure_ascii=False)},')
        lines.append("            \"total\": len(suite_results),")
        lines.append("            \"passed\": sum(1 for item in suite_results if item['passed']),")
        lines.append("            \"failed\": sum(1 for item in suite_results if not item['passed']),")
        lines.append("            \"first_failed_case_id\": first_failed['case_id'] if first_failed else None,")
        lines.append("            \"cases\": suite_results,")
        lines.append("        }")
        lines.append("        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')")

        included_specs = [specs[case_id] for case_id in suite.includes if case_id in specs]
        if not included_specs:
            lines.append('    pytest.skip("No included case specs resolved")')
            body = "\n".join(lines)
            return _build_import_block(body) + "\n\n" + body

        for spec in included_specs:
            result_var = f"result_{_case_id_to_function_name(spec.id)}"
            if spec.givens:
                for given in spec.givens:
                    lines.append(f"    # Given ({spec.id}): {given}")
            lines.append(f"    # Case: {spec.id} - {spec.title}")
            lines.append(f"    {result_var} = (")
            lines.append(f'        define("{spec.id}", autotest, _session_loop)')
            if spec.start_state:
                lines.append(
                    f'        .require_start_state("""{spec.start_state}""")'
                )
            lines.append("        .setup(")
            for step in (spec.steps[:-1] if len(spec.steps) > 1 else []):
                lines.append(f"            {_step_to_action_call(step, self._character_ids)},")
            lines.append("        )")
            lines.append("        .execute(")
            if spec.steps:
                lines.append(f"            {_step_to_action_call(spec.steps[-1], self._character_ids)},")
            lines.append("        )")
            lines.append("        .assert_that(")
            if spec.assertions:
                for assertion in spec.assertions:
                    lines.append(f"            {_assertion_to_call(assertion)},")
            else:
                lines.append("            # no assertions defined")
            lines.append("        )")
            lines.append("    )")
            lines.append("    case_summary = {")
            lines.append(f'        "case_id": {json.dumps(spec.id, ensure_ascii=False)},')
            lines.append(f'        "title": {json.dumps(spec.title, ensure_ascii=False)},')
            lines.append(f'        "start_state": {json.dumps(spec.start_state, ensure_ascii=False)},')
            lines.append(f'        "end_state": {json.dumps(spec.end_state, ensure_ascii=False)},')
            lines.append(f'        "steps": {json.dumps(spec.steps, ensure_ascii=False)},')
            lines.append(f"        \"passed\": {result_var}.passed,")
            lines.append(f"        \"failures\": {result_var}.failures,")
            lines.append(f"        \"detail\": {result_var}.detail,")
            lines.append("    }")
            lines.append("    suite_results.append(case_summary)")
            lines.append("    _write_suite_summary()")
            lines.append(
                f'    assert {result_var}.passed, '
                f'"TC-{spec.id.removeprefix("TC-")} failed: " '
                "+ json.dumps(case_summary, ensure_ascii=False)"
            )

        body = "\n".join(lines)
        return _build_import_block(body) + "\n\n" + body

    def generate_to_file(self, spec: TestSpec, output_dir: str) -> str:
        """Generate a test file on disk. Returns the output file path."""
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)

        func_name = _case_id_to_function_name(spec.id)
        out_file = path / f"test_{func_name}.py"

        code = self.generate_case_test(spec)
        out_file.write_text(code, encoding="utf-8")
        return str(out_file)

    def _generate_skipped_test(
        self, spec: TestSpec, func_name: str, reason: str
    ) -> str:
        """Generate a pytest skip test for specs that can't be executed."""
        return (
            "import pytest\n"
            "\n"
            f"def test_{func_name}():\n"
            f'    """{spec.title}"""\n'
            f'    pytest.skip("{reason}")\n'
        )
