#!/usr/bin/env python3
"""汇总 ci-nightly 最近运行，供 issue #15 / #66 连续三晚关闭证据使用。

本脚本只整理已发生的 GitHub Actions 运行，不代替真实游戏回归。
任一晚缺可下载证据、误分类或超时，连续验证必须重新计数。

关闭 streak 规则（与 classify_nightly.closeout_eligible 对齐）：
- 只认 ``evidence-nightly-*``（``early-diagnosis-*`` 不算关闭证据）
- 下载正式 evidence 内的 ``classification.json``，校验
  ``closeout_eligible`` / ``diagnosable`` / ``classification``
- 按 UTC 自然日去重；连续三个自然日才提示可进入人工关闭核对
- 所有 ``gh`` 子进程必须带 timeout

用法：
  python .github/scripts/summarize_nightly_closeout.py [--limit 10] [--repo OWNER/NAME]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import zipfile
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

GH_TIMEOUT_SECONDS = float(os.environ.get("NIGHTLY_CLOSEOUT_GH_TIMEOUT_SECONDS", "60"))
EVIDENCE_PREFIX = "evidence-nightly-"
EARLY_PREFIX = "early-diagnosis-"
CLASSIFICATION_NAMES = ("classification.json", "tests/output/classification.json")


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


def _run_gh_bytes(args: list[str], *, timeout: float = GH_TIMEOUT_SECONDS) -> bytes:
    try:
        completed = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"gh timed out after {timeout:g}s: gh {' '.join(args)}"
        ) from exc
    if completed.returncode != 0:
        err = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or f"gh {' '.join(args)} failed")
    return completed.stdout or b""


def list_artifacts(run_id: int, repo: str) -> list[dict[str, Any]]:
    data = _run_gh(
        [
            "api",
            f"repos/{repo}/actions/runs/{run_id}/artifacts",
            "--jq",
            "[.artifacts[] | {id, name}]",
        ]
    )
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def load_classification_from_zip(blob: bytes) -> dict[str, Any] | None:
    """从 evidence zip 中读取 classification.json。"""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = zf.namelist()
            chosen: str | None = None
            for preferred in CLASSIFICATION_NAMES:
                if preferred in names:
                    chosen = preferred
                    break
            if chosen is None:
                for name in names:
                    if name.endswith("classification.json"):
                        chosen = name
                        break
            if chosen is None:
                return None
            raw = zf.read(chosen).decode("utf-8")
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
    except (zipfile.BadZipFile, json.JSONDecodeError, OSError, UnicodeError):
        return None


def fetch_classification_json(
    repo: str,
    artifact_id: int,
) -> dict[str, Any] | None:
    blob = _run_gh_bytes(
        [
            "api",
            f"repos/{repo}/actions/artifacts/{artifact_id}/zip",
        ]
    )
    return load_classification_from_zip(blob)


def evaluate_closeout(
    artifacts: Sequence[Mapping[str, Any]],
    *,
    classification_loader: Callable[[int], dict[str, Any] | None],
) -> tuple[bool, str, dict[str, Any] | None]:
    """返回 (eligible, display_classification, payload)。

    early-diagnosis 单独存在时不得放行；必须有 evidence-nightly 且 JSON
    closeout_eligible=true。
    """
    evidence = [
        item
        for item in artifacts
        if str(item.get("name", "")).startswith(EVIDENCE_PREFIX)
    ]
    if not evidence:
        return False, "UNKNOWN", None

    payload: dict[str, Any] | None = None
    for item in evidence:
        artifact_id = int(item["id"])
        payload = classification_loader(artifact_id)
        if payload is not None:
            break

    if payload is None:
        # 无法读取 JSON 时不凭 artifact 名放行
        name = str(evidence[0].get("name") or "")
        parts = name.split("-")
        label = parts[-1] if parts else "UNKNOWN"
        return False, label if label in {"PASSED", "FAILED", "BLOCKED", "CANCELLED", "UNKNOWN"} else "UNKNOWN", None

    classification = str(payload.get("classification") or "UNKNOWN")
    eligible = bool(
        payload.get("closeout_eligible") is True
        and payload.get("diagnosable") is True
        and classification == "PASSED"
    )
    return eligible, classification, payload


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
    artifact_fetcher: Callable[[int], list[dict[str, Any]]],
    classification_loader: Callable[[int], dict[str, Any] | None],
) -> str:
    """纯函数汇总：便于单测注入假 run / artifact / classification。"""
    lines = [
        "# Nightly closeout evidence (issue #15 / #66)",
        "",
        "成功运行必须保留真实游戏验证证据；日常基础验收或手工截图不能替代。",
        "任一晚缺少可下载证据、出现错误分类或超过整体上限，连续验证重新计数。",
        "计数口径：UTC 自然日去重；下载 `evidence-nightly-*` 内 classification.json，",
        "仅 `closeout_eligible=true` 计入；`early-diagnosis-*` 不计。",
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
        names = [str(item.get("name") or "") for item in artifacts]
        evidence_names = [
            name
            for name in names
            if name.startswith(EVIDENCE_PREFIX) or name.startswith(EARLY_PREFIX)
        ]
        eligible, classification, _payload = evaluate_closeout(
            artifacts,
            classification_loader=classification_loader,
        )
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
                    if not any(n.startswith(EVIDENCE_PREFIX) for n in names):
                        notes.append(
                            f"run {run_id} 无 evidence-nightly 正式证据"
                            f"（仅有 early-diagnosis 不算），连续验证重新计数"
                        )
                    else:
                        notes.append(
                            f"run {run_id} 分类={classification} / closeout_eligible=false，"
                            f"连续验证重新计数"
                        )

        lines.append(
            f"| {created} | [{run_id}]({run.get('url')}) | {classification} | {conclusion} | "
            f"{', '.join(evidence_names) or '无'} | {'yes' if eligible else 'no'} |"
        )

    consecutive = consecutive_calendar_days(eligible_days)
    lines.extend(["", f"扫描到的从新到旧连续自然日（关闭资格）次数：{consecutive}", ""])
    if consecutive < 3:
        lines.append("关闭判定：未满连续三个自然日可关闭证据，父 issue 不得关闭。")
    else:
        lines.append(
            "关闭判定：已观察到至少三个连续自然日的 closeout_eligible 证据，"
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

    def fetch(run_id: int) -> list[dict[str, Any]]:
        return list_artifacts(run_id, repo)

    def load(artifact_id: int) -> dict[str, Any] | None:
        try:
            return fetch_classification_json(repo, artifact_id)
        except RuntimeError as exc:
            print(f"warn: artifact {artifact_id} 读取失败: {exc}", file=sys.stderr)
            return None

    return summarize_from_runs(
        runs,
        artifact_fetcher=fetch,
        classification_loader=load,
    )


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
