"""Code generator — converts TestSpec/SuiteSpec into pytest + Fluent DSL code.

Outputs syntactically valid Python files that use the framework's
main chain (FluentBuilder -> ActionDescriptor -> Orchestrator).
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Optional

from sts2_autotest.common.spec_models import SuiteSpec, TestSpec

_IMPORT_BLOCK = """\
from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    game_reached_state,
    no_crash_detected,
    has_travelable_node,
)
from sts2_autotest.common.state import GameScreen
"""


def _step_to_action_call(step: str) -> str:
    """Convert a natural language step to a DSL function call.

    Falls back to a generic execute_action call if no mapping exists.
    """
    step_lower = step.lower()
    for keyword, action in _STEP_TO_ACTION.items():
        if keyword in step_lower:
            if keyword in ("选择", "使用"):
                # Extract the parameter after the keyword
                rest = step[len(keyword) :].strip().strip("'").strip('"')
                return f'{action}("{rest}")'
            return action
    return f"execute_action({step!r})"


_STEP_TO_ACTION: dict[str, str] = {
    "启动游戏": "ensure_game_running()",
    "启动 steam": "ensure_steam_running()",
    "选择": "select_mode",
    "开始新 run": "embark_new_run()",
    "战斗": "combat_loop(max_turns=15)",
    "重置": "reset_to_main_menu()",
    "返回主菜单": "reset_to_main_menu()",
    "跳过": "skip_reward()",
    "推进": "advance_until_map()",
    "结束回合": "end_turn()",
    "使用": "play_card",
    "等待": "wait_for_state",
}


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

    def generate_case_test(self, spec: TestSpec) -> str:
        """Generate a complete pytest test function for a single case."""
        func_name = _case_id_to_function_name(spec.id)
        steps = spec.steps
        assertions = spec.assertions

        if not steps:
            return self._generate_skipped_test(spec, func_name, "No steps defined")

        # Build setup actions
        setup_calls = []
        for step in steps:
            setup_calls.append(f"            {_step_to_action_call(step)},")

        setup_block = "\n".join(setup_calls)

        # Build assertions
        assert_calls = []
        for assertion in assertions:
            assertion_lower = assertion.lower()
            if "crash" in assertion_lower:
                assert_calls.append("            no_crash_detected(),")
            elif "map" in assertion_lower or "地图" in assertion:
                assert_calls.append("            game_reached_state(GameScreen.MAP),")
            elif "节点" in assertion or "node" in assertion_lower:
                assert_calls.append("            has_travelable_node(),")
            else:
                assert_calls.append(
                    f"            # TODO: implement assertion for '{assertion}'"
                )

        assert_block = "\n".join(assert_calls) if assert_calls else "            # no assertions defined"

        givens_comment = ""
        if spec.givens:
            givens_lines = "\n".join(f"    # Given: {g}" for g in spec.givens)
            givens_comment = f"{givens_lines}\n"

        body = textwrap.dedent(f"""\
            {givens_comment}
            def test_{func_name}(autotest, _session_loop):
                \"\"\"{spec.title}\"\"\"
                result = (
                    define("{spec.id}", autotest, _session_loop)
                    .setup(
            {setup_block}
                    )
                    .execute(
                        advance_until_map(),
                    )
                    .assert_that(
            {assert_block}
                    )
                )
                assert result.passed, result.failures
            """)
        return _IMPORT_BLOCK.rstrip("\n") + "\n" + body

    def generate_suite_test(self, suite: SuiteSpec, specs: dict[str, TestSpec]) -> str:
        """Generate a pytest test class for a suite of test cases."""
        class_name = _case_id_to_class_name(suite.id)

        methods = []
        for case_id in suite.includes:
            spec = specs.get(case_id)
            if spec:
                methods.append(self.generate_case_test(spec))

        suite_assertions_comment = ""
        if suite.suite_assertions:
            suite_assertions_comment = "\n".join(
                f"    # Suite assertion: {a}" for a in suite.suite_assertions
            )

        methods_code = "\n\n".join(methods)
        body = textwrap.dedent(f"""\
            class {class_name}:
                \"\"\"{suite.title}\"\"\"

            {suite_assertions_comment}
            {methods_code}
            """)
        return _IMPORT_BLOCK.rstrip("\n") + "\n" + body

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
        return textwrap.dedent(f"""\
            import pytest

            def test_{func_name}():
                \"\"\"{spec.title}\"\"\"
                pytest.skip("{reason}")
            """)
