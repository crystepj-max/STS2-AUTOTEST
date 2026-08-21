#!/usr/bin/env python3
"""汇总 ci-nightly 最近运行，供 issue #15 / #66 连续三晚关闭证据使用。

本脚本只整理已发生的 GitHub Actions 运行，不代替真实游戏回归。
任一晚缺可下载证据、误分类或超时，连续验证必须重新计数。

用法：
  python .github/scripts/summarize_nightly_closeout.py [--limit 10] [--repo OWNER/NAME]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from typing import Any


def _run_gh(args: list[str]) -> Any:
    completed = subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"gh {' '.join(args)} failed")
    return json.loads(completed.stdout) if completed.stdout.strip() else None


def _artifact_names(run_id: int, repo: str) -> list[str]:
    data = _run_gh(
        [
            "api",
            f"repos/{repo}/actions/runs/{run_id}/artifacts",
            "--jq",
            "[.artifacts[].name]",
        ]
    )
    if isinstance(data, list):
        return [str(item) for item in data]
    return []


def summarize(repo: str, limit: int) -> str:
    runs = _run_gh(
        [
            "run",
            "list",
            "--workflow",
            "ci-nightly.yml",
            "--repo",
            repo,
            "--limit",
            str(limit),
            "--json",
            "databaseId,displayTitle,headSha,url,status,conclusion,createdAt,updatedAt,event",
        ]
    )
    if not isinstance(runs, list):
        raise RuntimeError("gh run list 未返回列表")

    lines = [
        "# Nightly closeout evidence (issue #15 / #66)",
        "",
        "成功运行必须保留真实游戏验证证据；日常基础验收或手工截图不能替代。",
        "任一晚缺少可下载证据、出现错误分类或超过整体上限，连续验证重新计数。",
        "",
        "| 日期 | Run | 结论 | 状态 | 证据 artifacts | 可诊断 |",
        "|---|---|---|---|---|---|",
    ]
    consecutive = 0
    streak_open = True
    notes: list[str] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        run_id = int(run["databaseId"])
        artifacts = _artifact_names(run_id, repo)
        evidence = [name for name in artifacts if name.startswith("evidence-nightly-") or name.startswith("early-diagnosis-")]
        classification = "UNKNOWN"
        for name in evidence:
            parts = name.split("-")
            if parts and parts[-1] in {"PASSED", "FAILED", "BLOCKED", "CANCELLED", "UNKNOWN"}:
                classification = parts[-1]
        diagnosable = bool(evidence)
        created = str(run.get("createdAt") or "")[:10]
        conclusion = str(run.get("conclusion") or run.get("status") or "")
        if streak_open:
            if diagnosable:
                consecutive += 1
            else:
                streak_open = False
                notes.append(f"run {run_id} 缺下载证据，连续验证从最近一次重新计数")
        lines.append(
            f"| {created} | [{run_id}]({run.get('url')}) | {classification} | {conclusion} | "
            f"{', '.join(evidence) or '无'} | {'yes' if diagnosable else 'no'} |"
        )

    lines.extend(["", f"扫描到的从新到旧连续可诊断次数：{consecutive}", ""])
    if consecutive < 3:
        lines.append("关闭判定：未满连续三个自然日可诊断结果，父 issue 不得关闭。")
    else:
        lines.append("关闭判定：已观察到至少三份连续可诊断结果，仍需人工核对分类与真实游戏证据。")
    if notes:
        lines.extend(["", "## 重新计数记录", *[f"- {item}" for item in notes]])
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="汇总夜间回归关闭证据")
    parser.add_argument("--repo", default="crystepj-max/STS2-AUTOTEST")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        print(summarize(args.repo, args.limit), end="")
    except (RuntimeError, json.JSONDecodeError, OSError) as exc:
        print(f"summarize_nightly_closeout FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
