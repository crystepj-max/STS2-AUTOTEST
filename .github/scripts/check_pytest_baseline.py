"""Run unit tests and fail CI only when new test failures appear.

Existing cross-platform failures are tracked in ``.github/pytest-baseline.json``.
The baseline is intentionally keyed by ``sys.platform`` because several current
failures are platform-specific. Historical failures stay visible in pytest/JUnit
output, while any new failed test node ID blocks the PR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest


class FailureCollector:
    """Collect unique pytest node IDs that fail in setup, call, or teardown."""

    def __init__(self) -> None:
        self.failed: set[str] = set()

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.failed:
            self.failed.add(str(report.nodeid))


def _load_baseline() -> set[str] | None:
    baseline_path = Path(__file__).resolve().parents[1] / "pytest-baseline.json"
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    platform_baseline = data.get(sys.platform)
    if platform_baseline is None:
        return None
    return {str(node_id) for node_id in platform_baseline}


def main() -> int:
    allowed = _load_baseline()
    if allowed is None:
        print(f"::error::No unit-test baseline for platform: {sys.platform}")
        return 2

    collector = FailureCollector()
    exit_code = int(
        pytest.main(
            ["tests/unit/", "-v", "--junitxml=junit-unit.xml"],
            plugins=[collector],
        )
    )

    if exit_code == int(pytest.ExitCode.OK):
        current: set[str] = set()
    elif exit_code == int(pytest.ExitCode.TESTS_FAILED):
        current = collector.failed
        if not current:
            print("::error::Pytest reported failed tests but no failed node IDs were collected.")
            return 2
    else:
        print(f"::error::Pytest exited with collection/infrastructure error: {exit_code}")
        return exit_code or 2

    new_failures = current - allowed
    resolved_failures = allowed - current
    historical_failures = current & allowed

    print(f"Unit-test platform: {sys.platform}")
    print(f"Historical baseline: {len(allowed)} failed test(s)")
    print(f"Current failures: {len(current)}")
    print(f"Still historical: {len(historical_failures)}")
    print(f"Resolved by current code: {len(resolved_failures)}")
    print(f"New failures: {len(new_failures)}")

    if resolved_failures:
        print("Resolved historical failures:")
        for node_id in sorted(resolved_failures):
            print(f"  - {node_id}")

    if new_failures:
        for node_id in sorted(new_failures):
            print(f"::error::New unit-test failure: {node_id}")
        return 1

    print("CI passed: this PR introduces no new unit-test failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
