#!/usr/bin/env python3
"""Fail CI only when a pull request introduces new mypy errors."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from runner_utils import TIMEOUT_EXIT_CODE, run_timed, timeout_from_env

IssueKey = tuple[str, str, str, str]
_ERROR_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<column>\d+))?: "
    r"error: (?P<message>.*?)(?:  \[(?P<code>[^\]]+)\])?$"
)

MYPY_LOG_PATH = Path("mypy-check.log")
MYPY_TIMEOUT_DEFAULT = 900.0  # 15 分钟；可用环境变量 MYPY_TIMEOUT_SECONDS 覆盖（受控验证用）


def _run_mypy(root: Path, timeout: float) -> list[tuple[str, int, int, str, str]] | None:
    """运行 mypy 并解析错误行；超时返回 None（输出已保留到日志）。"""
    result = run_timed(
        "mypy check",
        [
            "mypy",
            "src/sts2_autotest",
            "--strict",
            "--show-error-codes",
            "--no-error-summary",
        ],
        MYPY_LOG_PATH,
        timeout=timeout,
        cwd=root,
    )

    if result.timed_out:
        print(f"Partial mypy output preserved in {MYPY_LOG_PATH}", file=sys.stderr)
        return None
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"mypy failed to run in {root} with exit code {result.returncode}:\n"
            f"{result.output}\n{result.error}"
        )

    findings: list[tuple[str, int, int, str, str]] = []
    for line in result.output.splitlines():
        match = _ERROR_RE.match(line)
        if not match:
            continue
        findings.append(
            (
                match.group("file"),
                int(match.group("line")),
                int(match.group("column") or 0),
                match.group("code") or "unknown",
                match.group("message"),
            )
        )
    return findings


def _relative_path(root: Path, filename: str) -> str:
    path = Path(filename)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _source_line(root: Path, filename: str, row: int) -> str:
    path = Path(filename)
    if not path.is_absolute():
        path = root / path
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if 1 <= row <= len(lines):
        return lines[row - 1].strip()
    return ""


def _fingerprints(
    root: Path,
    findings: list[tuple[str, int, int, str, str]],
) -> tuple[Counter[IssueKey], dict[IssueKey, tuple[int, int]]]:
    counts: Counter[IssueKey] = Counter()
    locations: dict[IssueKey, tuple[int, int]] = {}

    for filename, row, column, code, message in findings:
        key: IssueKey = (
            _relative_path(root, filename),
            code,
            message,
            _source_line(root, filename, row),
        )
        counts[key] += 1
        locations.setdefault(key, (row, column))

    return counts, locations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--current-dir", type=Path, required=True)
    args = parser.parse_args()

    baseline_dir = args.baseline_dir.resolve()
    current_dir = args.current_dir.resolve()

    timeout = timeout_from_env(MYPY_TIMEOUT_DEFAULT, "MYPY_TIMEOUT_SECONDS")
    baseline_findings = _run_mypy(baseline_dir, timeout)
    if baseline_findings is None:
        return TIMEOUT_EXIT_CODE
    current_findings = _run_mypy(current_dir, timeout)
    if current_findings is None:
        return TIMEOUT_EXIT_CODE
    baseline_counts, _ = _fingerprints(baseline_dir, baseline_findings)
    current_counts, current_locations = _fingerprints(current_dir, current_findings)

    new_findings = current_counts - baseline_counts
    resolved_findings = baseline_counts - current_counts

    print(f"mypy historical baseline: {sum(baseline_counts.values())} error(s)")
    print(f"Current PR: {sum(current_counts.values())} error(s)")
    print(f"Resolved by this PR: {sum(resolved_findings.values())}")
    print(f"New in this PR: {sum(new_findings.values())}")

    if new_findings:
        print("\nNew mypy errors introduced by this PR:")
        for key, count in sorted(new_findings.items()):
            path, code, message, source = key
            row, column = current_locations.get(key, (0, 0))
            suffix = f" x{count}" if count > 1 else ""
            position = f":{row}" + (f":{column}" if column else "")
            print(f"- {path}{position} {code} {message}{suffix}")
            if source:
                print(f"  {source}")
        print("\nCI failed: new mypy debt is not allowed.")
        return 1

    print("CI passed: this PR introduces no new mypy debt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
