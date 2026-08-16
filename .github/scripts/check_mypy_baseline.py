#!/usr/bin/env python3
"""Fail CI only when a pull request introduces new mypy errors."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

IssueKey = tuple[str, str, str, str]
_ERROR_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<column>\d+))?: "
    r"error: (?P<message>.*?)(?:  \[(?P<code>[^\]]+)\])?$"
)
DEFAULT_CONFIG_FILE = Path(".github/mypy-policy.ini")


def _resolve_tool_bin(bin_arg: str | None, name: str) -> str:
    """解析工具可执行文件路径：显式传入优先；否则从当前 Python 同 venv 推导（Unix bin/、Windows Scripts/），最后回退 PATH。

    注意：不用 resolve() 展开符号链接——.venv/bin/python 常是指向 uv/pyenv 缓存的链接，
    resolve 后会偏离 venv 目录，导致推导不到同 venv 工具。
    """
    if bin_arg:
        return bin_arg
    python_dir = Path(sys.executable).parent
    for candidate in (python_dir / name, python_dir / f"{name}.exe"):
        if candidate.is_file():
            return str(candidate)
    return name


def _run_mypy(root: Path, bin_path: str, config_file: Path) -> list[tuple[str, int, int, str, str]]:
    result = subprocess.run(
        [
            bin_path,
            "--config-file",
            str(config_file),
            "src/sts2_autotest",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )

    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"mypy failed to run in {root} with exit code {result.returncode}:\n"
            f"{result.stdout}\n{result.stderr}"
        )

    findings: list[tuple[str, int, int, str, str]] = []
    for line in result.stdout.splitlines():
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--current-dir", type=Path, required=True)
    parser.add_argument(
        "--config-file",
        type=Path,
        default=DEFAULT_CONFIG_FILE,
        help="mypy 政策配置文件（默认 .github/mypy-policy.ini）。",
    )
    parser.add_argument(
        "--mypy-bin",
        default=None,
        help="当前目录使用的 mypy 可执行文件（默认从当前 Python venv 推导，回退 PATH）。",
    )
    parser.add_argument(
        "--baseline-mypy-bin",
        default=None,
        help="基线目录使用的 mypy 可执行文件（默认同 --mypy-bin）。",
    )
    args = parser.parse_args(argv)

    baseline_dir = args.baseline_dir.resolve()
    current_dir = args.current_dir.resolve()
    config_file = args.config_file.resolve()

    mypy_bin = _resolve_tool_bin(args.mypy_bin, "mypy")
    baseline_mypy_bin = _resolve_tool_bin(args.baseline_mypy_bin or args.mypy_bin, "mypy")

    baseline_counts, _ = _fingerprints(baseline_dir, _run_mypy(baseline_dir, baseline_mypy_bin, config_file))
    current_counts, current_locations = _fingerprints(current_dir, _run_mypy(current_dir, mypy_bin, config_file))

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
