"""Run unit tests and fail CI only when new final test failures appear.

Existing cross-platform failures are tracked in ``.github/pytest-baseline.json``.
The baseline is keyed by ``sys.platform`` because several current failures are
platform-specific. Historical failures stay visible in pytest/JUnit output,
while any new final failed test node ID blocks the PR.

The comparison intentionally parses the final JUnit file instead of listening
to ``pytest_runtest_logreport``. Some unit tests invoke pytest internally; a
runtime hook can therefore observe failures from those nested sessions even
when they are not failures of this outer unit-test run.
"""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

JUNIT_PATH = Path("junit-unit.xml")
PYTEST_OK = 0
PYTEST_TESTS_FAILED = 1


def _load_baseline() -> set[str] | None:
    baseline_path = Path(__file__).resolve().parents[1] / "pytest-baseline.json"
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    platform_baseline = data.get(sys.platform)
    if platform_baseline is None:
        return None
    return {str(node_id) for node_id in platform_baseline}


def _junit_case_to_nodeid(testcase: ET.Element) -> str:
    """Convert pytest's default JUnit classname/name pair back to a node ID."""
    classname = testcase.get("classname", "")
    name = testcase.get("name", "")
    parts = classname.split(".") if classname else []

    # Pytest emits classnames such as:
    #   tests.unit.test_module
    #   tests.unit.test_module.TestClass
    # Locate the test module, then translate remaining parts to ``::`` scopes.
    module_index = next(
        (index for index in range(len(parts) - 1, -1, -1) if parts[index].startswith("test_")),
        None,
    )
    if module_index is None or not name:
        raise ValueError(
            f"cannot reconstruct pytest node ID from JUnit case: "
            f"classname={classname!r}, name={name!r}"
        )

    path = "/".join(parts[: module_index + 1]) + ".py"
    scopes = parts[module_index + 1 :]
    return "::".join([path, *scopes, name])


def _load_final_failures() -> set[str]:
    if not JUNIT_PATH.is_file():
        raise RuntimeError(f"JUnit file was not produced: {JUNIT_PATH}")

    try:
        root = ET.parse(JUNIT_PATH).getroot()
    except (ET.ParseError, OSError) as exc:
        raise RuntimeError(f"JUnit file is unreadable: {JUNIT_PATH}") from exc

    failures: set[str] = set()
    for testcase in root.iter("testcase"):
        if testcase.find("failure") is None and testcase.find("error") is None:
            continue
        failures.add(_junit_case_to_nodeid(testcase))
    return failures


def _run_pytest() -> int:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/",
            "-v",
            f"--junitxml={JUNIT_PATH}",
        ],
        check=False,
    )
    return result.returncode


def main() -> int:
    allowed = _load_baseline()
    if allowed is None:
        print(f"::error::No unit-test baseline for platform: {sys.platform}")
        return 2

    exit_code = _run_pytest()

    if exit_code not in (PYTEST_OK, PYTEST_TESTS_FAILED):
        print(f"::error::Pytest exited with collection/infrastructure error: {exit_code}")
        return exit_code or 2

    try:
        current = _load_final_failures()
    except (RuntimeError, ValueError) as exc:
        print(f"::error::{exc}")
        return 2

    if exit_code == PYTEST_TESTS_FAILED and not current:
        print("::error::Pytest reported failed tests but JUnit contains no final failures.")
        return 2
    if exit_code == PYTEST_OK and current:
        print("::error::Pytest exited successfully but JUnit contains failed tests.")
        return 2

    new_failures = current - allowed
    resolved_failures = allowed - current
    historical_failures = current & allowed

    print(f"Unit-test platform: {sys.platform}")
    print(f"Historical baseline: {len(allowed)} failed test(s)")
    print(f"Current final failures: {len(current)}")
    print(f"Still historical: {len(historical_failures)}")
    print(f"Resolved by current code: {len(resolved_failures)}")
    print(f"New final failures: {len(new_failures)}")

    if resolved_failures:
        print("Resolved historical failures:")
        for node_id in sorted(resolved_failures):
            print(f"  - {node_id}")

    if new_failures:
        for node_id in sorted(new_failures):
            print(f"::error::New unit-test failure: {node_id}")
        return 1

    print("CI passed: this PR introduces no new final unit-test failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
