"""Code generator — converts TestSpec/SuiteSpec into pytest + Fluent DSL code.

Outputs syntactically valid Python files that use the framework's
main chain (FluentBuilder -> ActionDescriptor -> Orchestrator).
"""

from __future__ import annotations

import re
import json
from pathlib import Path

from sts2_autotest.common.spec_models import SuiteSpec, TestSpec

_IMPORT_BLOCK = """\
import json
from pathlib import Path

import pytest

from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    advance_dialogue,
    choose_event,
    choose_game_mode,
    choose_map_node,
    combat_basic_policy,
    embark,
    end_turn,
    enter_combat,
    game_reached_state,
    no_crash_detected,
    has_travelable_node,
    play_card,
    return_to_menu,
    select_character,
    skip_card_reward,
    start_new_run,
)
from sts2_autotest.common.state import GameScreen
from sts2_autotest.core.action_model import ActionDescriptor
"""


_STEP_TO_ACTION: dict[str, str] = {
    "结束回合": "end_turn()",
    "使用": "play_card",
}


def _step_to_action_call(step: str) -> str:
    """Convert a natural language step to a DSL function call.

    Falls back to ActionDescriptor if no direct DSL function mapping exists.
    """
    event_match = re.search(r"第\s*(\d+)\s*个选项", step)
    if "开局事件" in step and event_match:
        return f"choose_event({event_match.group(1)})"

    map_node_match = re.search(r"\(\s*(\d+)\s*,\s*(\d+)\s*\)", step)
    if "地图节点" in step and map_node_match:
        return f"choose_map_node({map_node_match.group(1)}, {map_node_match.group(2)})"

    if "返回主菜单" in step:
        return "return_to_menu()"
    if "开始新 run" in step or "开始新局" in step:
        return "start_new_run()"
    if "选择 Ironclad" in step:
        return 'select_character("IRONCLAD")'
    if "开始冒险" in step:
        return "embark()"
    if "推进事件对话" in step:
        # advance_dialogue only needed when the event requires it;
        # NEOW's blessing transitions directly to MAP after choosing.
        return "advance_dialogue()"
    if "进入首次战斗" in step or "进入首场战斗" in step:
        return "enter_combat()"
    if "基础策略" in step and "战斗" in step:
        return "combat_basic_policy()"
    if "跳过卡牌奖励" in step:
        return "skip_card_reward()"

    step_lower = step.lower()
    for keyword, action in _STEP_TO_ACTION.items():
        if step_lower.startswith(keyword):
            if keyword == "使用":
                rest = step[len(keyword) :].strip().strip("'").strip('"')
                if rest:
                    return f'{action}("{rest}")'
                return f"ActionDescriptor(action_type={keyword!r})"
            return action
    # Fallback: use ActionDescriptor for unrecognized steps
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
    """

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
                    f'    pytest.skip("No steps defined")',
                ]
            )

        # C2 fix: last step goes to .execute(), all others to .setup()
        setup_steps = steps[:-1] if len(steps) > 1 else []
        execute_step = steps[-1]

        # Build assertion calls (no leading whitespace -> indented at assembly time)
        assert_calls: list[str] = []
        for assertion in assertions:
            assertion_lower = assertion.lower()
            if "crash" in assertion_lower:
                assert_calls.append("no_crash_detected()")
            elif "map" in assertion_lower or "地图" in assertion:
                assert_calls.append("game_reached_state(GameScreen.MAP)")
            elif "节点" in assertion or "node" in assertion_lower:
                assert_calls.append("has_travelable_node()")
            else:
                assert_calls.append(
                    f"# TODO: implement assertion for '{assertion}'"
                )

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
            lines.append(f"            {_step_to_action_call(step)},")
        lines.append("        )")
        lines.append("        .execute(")
        lines.append(
            f"            {_step_to_action_call(execute_step)},"
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
        return _IMPORT_BLOCK.rstrip("\n") + "\n\n" + body

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
        lines.append("    summary_path = Path('tests/output/suite-summaries') / "
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
            return _IMPORT_BLOCK.rstrip("\n") + "\n\n" + body

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
                lines.append(f"            {_step_to_action_call(step)},")
            lines.append("        )")
            lines.append("        .execute(")
            if spec.steps:
                lines.append(f"            {_step_to_action_call(spec.steps[-1])},")
            lines.append("        )")
            lines.append("        .assert_that(")
            if spec.assertions:
                for assertion in spec.assertions:
                    assertion_lower = assertion.lower()
                    if "crash" in assertion_lower:
                        lines.append("            no_crash_detected(),")
                    elif "map" in assertion_lower or "鍦板浘" in assertion:
                        lines.append("            game_reached_state(GameScreen.MAP),")
                    elif "鑺傜偣" in assertion or "node" in assertion_lower:
                        lines.append("            has_travelable_node(),")
                    else:
                        lines.append(f"            # TODO: implement assertion for '{assertion}'")
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
        return _IMPORT_BLOCK.rstrip("\n") + "\n\n" + body

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
