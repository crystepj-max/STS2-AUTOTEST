#!/usr/bin/env python3
"""Fail CI only when a pull request introduces new Ruff findings.

During Ruff debt cleanup, also print the current finding count by rule so each
batch can be selected by risk instead of applying every available auto-fix.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from baseline_common import IssueKey, move_aware_difference
from runner_utils import TIMEOUT_EXIT_CODE, run_timed, timeout_from_env

RUFF_LOG_PATH = Path("ruff-check.log")
RUFF_TIMEOUT_DEFAULT = 600.0  # 10 分钟；可用环境变量 RUFF_TIMEOUT_SECONDS 覆盖（受控验证用）


def _resolve_tool_bin(bin_arg: str | None, name: str) -> str:
    """解析工具可执行文件路径：显式传入优先；否则从当前 Python 同 venv 推导（Unix bin/、Windows Scripts/），最后回退 PATH。

    注意：不用 resolve() 展开符号链接——.venv/bin/python 常是指向 uv/pyenv 缓存的链接，
    resolve 后会偏离 venv 目录，导致推导不到同 venv 工具。
    """
    if bin_arg:
        return _absolute_bin(bin_arg)
    python_dir = Path(sys.executable).parent
    for candidate in (python_dir / name, python_dir / f"{name}.exe"):
        if candidate.is_file():
            return str(candidate)
    return name


def _absolute_bin(bin_path: str) -> str:
    """把含路径分隔符的相对路径转成绝对路径（基于当前工作目录）。

    基线比较时子进程的 cwd 是基线目录，相对 bin 路径会在错误的目录下解析，
    因此任何含分隔符的路径都必须先转绝对；纯命令名（无分隔符）保持 PATH 解析。
    """
    if "/" in bin_path or "\\" in bin_path:
        return str(Path(bin_path).resolve())
    return bin_path


def _run_ruff(root: Path, bin_path: str, timeout: float) -> list[dict[str, Any]] | None:
    """运行 ruff 并解析 JSON 发现；超时返回 None（输出已保留到日志）。"""
    result = run_timed(
        "ruff check",
        [
            bin_path,
            "check",
            "src",
            "tests",
            "--output-format",
            "json",
            "--exit-zero",
        ],
        RUFF_LOG_PATH,
        timeout=timeout,
        cwd=root,
    )
    if result.timed_out:
        print(f"Partial ruff output preserved in {RUFF_LOG_PATH}", file=sys.stderr)
        return None
    if result.returncode != 0:
        raise RuntimeError(
            f"ruff failed to run in {root} with exit code {result.returncode}:\n"
            f"{result.output}\n{result.error}"
        )
    if result.error:
        print(result.error, file=sys.stderr, end="")
    return json.loads(result.output)


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
    findings: list[dict[str, Any]],
) -> tuple[Counter[IssueKey], dict[IssueKey, dict[str, Any]]]:
    counts: Counter[IssueKey] = Counter()
    details: dict[IssueKey, dict[str, Any]] = {}

    for finding in findings:
        filename = str(finding.get("filename", ""))
        location = finding.get("location") or {}
        row = int(location.get("row") or 0)
        key: IssueKey = (
            _relative_path(root, filename),
            str(finding.get("code") or "unknown"),
            str(finding.get("message") or ""),
            _source_line(root, filename, row),
        )
        counts[key] += 1
        details.setdefault(key, finding)

    return counts, details


def _rule_counts(findings: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(finding.get("code") or "unknown") for finding in findings)


def _print_rule_counts(label: str, findings: list[dict[str, Any]]) -> None:
    counts = _rule_counts(findings)
    print(f"{label} Ruff findings by rule:")
    for code, count in counts.most_common():
        print(f"  {code}: {count}")


def _print_new_findings(
    new_findings: Counter[IssueKey],
    details: dict[IssueKey, dict[str, Any]],
) -> None:
    print("\nNew Ruff findings introduced by this PR:")
    for key, count in sorted(new_findings.items()):
        path, code, message, source = key
        detail = details.get(key, {})
        location = detail.get("location") or {}
        row = location.get("row") or "?"
        column = location.get("column") or "?"
        suffix = f" x{count}" if count > 1 else ""
        print(f"- {path}:{row}:{column} {code} {message}{suffix}")
        if source:
            print(f"  {source}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--current-dir", type=Path, required=True)
    parser.add_argument(
        "--ruff-bin",
        default=None,
        help="当前目录使用的 ruff 可执行文件（默认从当前 Python venv 推导，回退 PATH）。",
    )
    parser.add_argument(
        "--baseline-ruff-bin",
        default=None,
        help="基线目录使用的 ruff 可执行文件（默认同 --ruff-bin）。",
    )
    args = parser.parse_args(argv)

    baseline_dir = args.baseline_dir.resolve()
    current_dir = args.current_dir.resolve()

    timeout = timeout_from_env(RUFF_TIMEOUT_DEFAULT, "RUFF_TIMEOUT_SECONDS")
    ruff_bin = _resolve_tool_bin(args.ruff_bin, "ruff")
    baseline_ruff_bin = _resolve_tool_bin(args.baseline_ruff_bin or args.ruff_bin, "ruff")

    baseline_findings = _run_ruff(baseline_dir, baseline_ruff_bin, timeout)
    if baseline_findings is None:
        return TIMEOUT_EXIT_CODE
    current_findings = _run_ruff(current_dir, ruff_bin, timeout)
    if current_findings is None:
        return TIMEOUT_EXIT_CODE
    baseline_counts, _ = _fingerprints(baseline_dir, baseline_findings)
    current_counts, current_details = _fingerprints(current_dir, current_findings)

    new_findings, resolved_findings, moved_count = move_aware_difference(
        baseline_counts, current_counts
    )

    print(f"Ruff historical baseline: {sum(baseline_counts.values())} finding(s)")
    print(f"Current PR: {sum(current_counts.values())} finding(s)")
    print(f"Resolved by this PR: {sum(resolved_findings.values())}")
    print(f"Moved with files (not new): {moved_count}")
    print(f"New in this PR: {sum(new_findings.values())}")
    _print_rule_counts("Current", current_findings)

    if new_findings:
        _print_new_findings(new_findings, current_details)
        print("\nCI failed: new Ruff debt is not allowed.")
        return 1

    print("CI passed: this PR introduces no new Ruff debt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
