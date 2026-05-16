"""Code generator — converts TestSpec/SuiteSpec into pytest + Fluent DSL code.

Outputs syntactically valid Python files that use the framework's
main chain (FluentBuilder -> ActionDescriptor -> Orchestrator).
"""

from __future__ import annotations

from pathlib import Path

from sts2_autotest.common.spec_models import SuiteSpec, TestSpec

_IMPORT_BLOCK = """\
import pytest

from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    game_reached_state,
    no_crash_detected,
    has_travelable_node,
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
        lines.append("    assert result.passed, result.failures")

        return "\n".join(lines)

    def generate_case_test(self, spec: TestSpec) -> str:
        """Generate a complete pytest test file for a single case."""
        body = self._generate_case_body(spec)
        return _IMPORT_BLOCK.rstrip("\n") + "\n\n" + body

    def generate_suite_test(
        self, suite: SuiteSpec, specs: dict[str, TestSpec]
    ) -> str:
        """Generate a pytest test class for a suite of test cases.

        Builds the class body manually (no textwrap.dedent) to avoid
        indentation conflicts when combining multiple methods.
        """
        class_name = _case_id_to_class_name(suite.id)

        methods: list[str] = []
        for case_id in suite.includes:
            spec = specs.get(case_id)
            if spec:
                body = self._generate_case_body(spec)
                # Indent each non-empty line by 4 spaces for class method scope
                indented = "\n".join(
                    f"    {line}" if line.strip() else line
                    for line in body.splitlines()
                )
                methods.append(indented)

        methods_code = "\n\n".join(methods)

        # Build class body
        parts: list[str] = [f"class {class_name}:"]
        parts.append(f'    """{suite.title}"""')
        if suite.suite_assertions:
            parts.append("")
            for a in suite.suite_assertions:
                parts.append(f"    # Suite assertion: {a}")
        parts.append("")
        parts.append(methods_code)

        body = "\n".join(parts)
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
