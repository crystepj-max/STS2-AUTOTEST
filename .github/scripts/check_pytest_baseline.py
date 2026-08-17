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

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

JUNIT_PATH = Path("junit-unit.xml")
PYTEST_OK = 0
PYTEST_TESTS_FAILED = 1
DEFAULT_BASELINE_JSON = Path(".github/pytest-baseline.json")


def _load_baseline(baseline_json: Path) -> set[str] | None:
    data = json.loads(baseline_json.read_text(encoding="utf-8"))
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
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/",
            "-v",
            f"--junitxml={JUNIT_PATH}",
        ],
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    try:
        return process.wait()
    except KeyboardInterrupt:
        if sys.platform != "win32":
            raise
        exit_code = process.poll()
        if exit_code is None:
            raise
        return exit_code


def _classify(
    allowed: set[str],
    current: set[str],
) -> tuple[set[str], set[str], set[str]]:
    """将历史允许失败清单与当前最终失败集比对，返回三类结果。

    返回三元组 (新增, 仍存在, 已清偿)：
    - 新增：当前失败但不在清单内，按新增回归处理并阻止合并；
    - 仍存在：清单内且当前仍失败，属合法历史豁免；
    - 已清偿：清单内但当前已恢复，须从清单同步移除（否则豁免继续生效）。
    """
    new_failures = current - allowed
    historical_failures = current & allowed
    resolved_failures = allowed - current
    return new_failures, historical_failures, resolved_failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline-json",
        type=Path,
        default=None,
        help=(
            "允许失败清单路径；CI 必须显式传入 base SHA 版本（.ci-baseline/.github/pytest-baseline.json），"
            "防止 PR 通过修改当前清单扩大允许范围。"
        ),
    )
    args = parser.parse_args(argv)

    baseline_json = args.baseline_json
    if baseline_json is None:
        # fail-closed：CI 场景未显式传参时直接失败，杜绝「修改当前清单即扩大允许范围」的退化路径；
        # 本地便捷模式仅在明确无 CI 环境（无 GITHUB_ACTIONS）时回退到默认文件并告警。
        if "GITHUB_ACTIONS" in os.environ:
            print("::error::CI must pass --baseline-json (base SHA 版本).", file=sys.stderr)
            return 2
        baseline_json = DEFAULT_BASELINE_JSON
        print(
            f"WARNING: --baseline-json 未指定，使用当前工作区默认 {baseline_json}（本地模式）。",
            file=sys.stderr,
        )

    allowed = _load_baseline(baseline_json)
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

    new_failures, historical_failures, resolved_failures = _classify(allowed, current)
    effective_allowed = allowed - resolved_failures

    print(f"Unit-test platform: {sys.platform}")
    print(f"允许失败清单（历史）: {len(allowed)} 项")
    print(f"有效允许集（历史 - 已清偿）: {len(effective_allowed)} 项")
    print(f"当前最终失败: {len(current)} 项")
    print(f"  新增（本次引入的失败，阻止合并）: {len(new_failures)} 项")
    print(f"  仍存在（历史豁免且仍失败）: {len(historical_failures)} 项")
    print(f"  已清偿（已恢复，须从清单移除）: {len(resolved_failures)} 项")

    if resolved_failures:
        print("清偿记录（已恢复的历史豁免，须从 .github/pytest-baseline.json 移除）:")
        for node_id in sorted(resolved_failures):
            print(f"  - {node_id}")

    failed = False

    if new_failures:
        for node_id in sorted(new_failures):
            print(f"::error::New unit-test failure: {node_id}")
        failed = True

    if resolved_failures:
        for node_id in sorted(resolved_failures):
            print(
                f"::error::Recovered historical failure still listed in baseline; "
                f"remove it to expire the exemption: {node_id}"
            )
        failed = True

    if failed:
        return 1

    print("CI passed: no new unit-test failures and no stale baseline items.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
