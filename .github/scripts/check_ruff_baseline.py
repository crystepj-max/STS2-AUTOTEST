#!/usr/bin/env python3
"""Fail CI only when a pull request introduces new Ruff findings."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

IssueKey = tuple[str, str, str, str]


def _run_ruff(root: Path) -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "ruff",
            "check",
            "src",
            "tests",
            "--output-format",
            "json",
            "--exit-zero",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return json.loads(result.stdout)


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--current-dir", type=Path, required=True)
    args = parser.parse_args()

    baseline_dir = args.baseline_dir.resolve()
    current_dir = args.current_dir.resolve()

    baseline_findings = _run_ruff(baseline_dir)
    current_findings = _run_ruff(current_dir)
    baseline_counts, _ = _fingerprints(baseline_dir, baseline_findings)
    current_counts, current_details = _fingerprints(current_dir, current_findings)

    new_findings = current_counts - baseline_counts
    resolved_findings = baseline_counts - current_counts

    print(f"Ruff historical baseline: {sum(baseline_counts.values())} finding(s)")
    print(f"Current PR: {sum(current_counts.values())} finding(s)")
    print(f"Resolved by this PR: {sum(resolved_findings.values())}")
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
