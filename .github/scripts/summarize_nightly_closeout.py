#!/usr/bin/env python3
"""汇总 ci-nightly 最近运行，供 issue #15 / #66 连续三晚关闭证据使用。

本脚本只整理已发生的 GitHub Actions 运行，不代替真实游戏回归。
任一晚缺可下载证据、误分类或超时，连续验证必须重新计数。

关闭 streak 规则（与 classify_nightly.closeout_eligible 对齐）：
- 只认 ``evidence-nightly-*``（``early-diagnosis-*`` 不算关闭证据）
- 证据名末段须为 PASSED，且按 UTC 自然日去重
- 连续三个自然日（日期间隔为 1）才提示可进入人工关闭核对
- 所有 ``gh`` 子进程必须带 timeout

用法：
  python .github/scripts/summarize_nightly_closeout.py [--limit 10] [--repo OWNER/NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

GH_TIMEOUT_SECONDS = float(os.environ.get("NIGHTLY_CLOSEOUT_GH_TIMEOUT_SECONDS", "60"))
EVIDENCE_PREFIX = "evidence-nightly-"
EARLY_PREFIX = "early-diagnosis-"


def _run_gh(args: list[str], *, timeout: float = GH_TIMEOUT_SECONDS) -> Any:
    try:
        completed = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"gh timed out after {timeout:g}s: gh {' '.join(args)}"
        ) from exc
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


def classification_from_evidence_names(names: Sequence[str]) -> str:
    """从 evidence-nightly-* 名称末段解析分类；early-diagnosis 忽略。"""
    for name in names:
        if not name.startswith(EVIDENCE_PREFIX):
            continue
        parts = name.split("-")
        if parts and parts[-1] in {"PASSED", "FAILED", "BLOCKED", "CANCELLED", "UNKNOWN"}:
            return parts[-1]
    return "UNKNOWN"


def is_closeout_evidence(names: Sequence[str]) -> bool:
    """仅正式 evidence + PASSED 可计入关闭 streak（early-diagnosis 不算）。"""
    evidence = [n for n in names if n.startswith(EVIDENCE_PREFIX)]
    if not evidence:
        return False
    return classification_from_evidence_names(evidence) == "PASSED"


def consecutive_calendar_days(days_newest_first: Sequence[str]) -> int:
    """从新到旧统计连续自然日数；日期间隔不为 1 则中断。"""
    if not days_newest_first:
        return 0
    streak = 1
    previous = date.fromisoformat(days_newest_first[0])
    for raw in days_newest_first[1:]:
        current = date.fromisoformat(raw)
        if previous - current != timedelta(days=1):
            break
        streak += 1
        previous = current
    return streak


def summarize_from_runs(
    runs: Sequence[Mapping[str, Any]],
    *,
    artifact_fetcher: Callable[[int], list[str]],
) -> str:
    """纯函数汇总：便于单测注入假 run / artifact 列表。"""
    lines = [
        "# Nightly closeout evidence (issue #15 / #66)",
        "",
        "成功运行必须保留真实游戏验证证据；日常基础验收或手工截图不能替代。",
        "任一晚缺少可下载证据、出现错误分类或超过整体上限，连续验证重新计数。",
        "计数口径：UTC 自然日去重；仅 `evidence-nightly-*-PASSED` 计入；`early-diagnosis-*` 不计。",
        "",
        "| 日期 | Run | 结论 | 状态 | 证据 artifacts | 关闭资格 |",
        "|---|---|---|---|---|---|",
    ]
    notes: list[str] = []
    eligible_days: list[str] = []
    seen_days: set[str] = set()
    streak_open = True

    for run in runs:
        run_id = int(run["databaseId"])
        artifacts = artifact_fetcher(run_id)
        evidence = [
            name
            for name in artifacts
            if name.startswith(EVIDENCE_PREFIX) or name.startswith(EARLY_PREFIX)
        ]
        classification = classification_from_evidence_names(artifacts)
        eligible = is_closeout_evidence(artifacts)
        created = str(run.get("createdAt") or "")[:10]
        conclusion = str(run.get("conclusion") or run.get("status") or "")

        if streak_open and created:
            if created in seen_days:
                notes.append(f"run {run_id} 与同日更近 run 合并计数，忽略重复触发")
            else:
                seen_days.add(created)
                if eligible:
                    eligible_days.append(created)
                else:
                    streak_open = False
                    if not any(n.startswith(EVIDENCE_PREFIX) for n in artifacts):
                        notes.append(
                            f"run {run_id} 无 evidence-nightly 正式证据"
                            f"（仅有 early-diagnosis 不算），连续验证重新计数"
                        )
                    else:
                        notes.append(
                            f"run {run_id} 分类={classification} 不具备关闭资格，连续验证重新计数"
                        )

        lines.append(
            f"| {created} | [{run_id}]({run.get('url')}) | {classification} | {conclusion} | "
            f"{', '.join(evidence) or '无'} | {'yes' if eligible else 'no'} |"
        )

    consecutive = consecutive_calendar_days(eligible_days)
    lines.extend(["", f"扫描到的从新到旧连续自然日（关闭资格）次数：{consecutive}", ""])
    if consecutive < 3:
        lines.append("关闭判定：未满连续三个自然日可关闭证据，父 issue 不得关闭。")
    else:
        lines.append(
            "关闭判定：已观察到至少三个连续自然日的 PASSED 正式证据，"
            "仍需人工核对分类与真实游戏证据。"
        )
    if notes:
        lines.extend(["", "## 重新计数记录", *[f"- {item}" for item in notes]])
    return "\n".join(lines) + "\n"


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

    def fetch(run_id: int) -> list[str]:
        return _artifact_names(run_id, repo)

    return summarize_from_runs(runs, artifact_fetcher=fetch)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="汇总夜间回归关闭证据")
    parser.add_argument("--repo", default="crystepj-max/STS2-AUTOTEST")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        print(summarize(args.repo, args.limit), end="")
    except (RuntimeError, json.JSONDecodeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"summarize_nightly_closeout FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
