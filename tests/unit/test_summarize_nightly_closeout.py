"""summarize_nightly_closeout 关闭 streak 规则单测（issue #15 / #66）。"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "scripts"
    / "summarize_nightly_closeout.py"
)


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("summarize_nightly_closeout", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_early_diagnosis_alone_does_not_count_toward_closeout() -> None:
    mod = _load()
    runs = [
        {
            "databaseId": 1,
            "createdAt": "2026-08-24T03:00:00Z",
            "url": "https://example/1",
            "conclusion": "failure",
        },
        {
            "databaseId": 2,
            "createdAt": "2026-08-23T03:00:00Z",
            "url": "https://example/2",
            "conclusion": "failure",
        },
        {
            "databaseId": 3,
            "createdAt": "2026-08-22T03:00:00Z",
            "url": "https://example/3",
            "conclusion": "failure",
        },
    ]
    artifacts = {
        1: ["early-diagnosis-1-BLOCKED"],
        2: ["early-diagnosis-2-BLOCKED"],
        3: ["early-diagnosis-3-BLOCKED"],
    }
    text = mod.summarize_from_runs(runs, artifact_fetcher=lambda i: artifacts[i])
    assert "连续自然日（关闭资格）次数：0" in text
    assert "early-diagnosis 不算" in text or "无 evidence-nightly" in text
    assert "不得关闭" in text


def test_same_calendar_day_manual_runs_do_not_inflate_streak() -> None:
    mod = _load()
    runs = [
        {
            "databaseId": 11,
            "createdAt": "2026-08-24T18:00:00Z",
            "url": "https://example/11",
            "conclusion": "success",
        },
        {
            "databaseId": 12,
            "createdAt": "2026-08-24T12:00:00Z",
            "url": "https://example/12",
            "conclusion": "success",
        },
        {
            "databaseId": 13,
            "createdAt": "2026-08-24T06:00:00Z",
            "url": "https://example/13",
            "conclusion": "success",
        },
    ]
    artifacts = {
        11: ["evidence-nightly-11-PASSED"],
        12: ["evidence-nightly-12-PASSED"],
        13: ["evidence-nightly-13-PASSED"],
    }
    text = mod.summarize_from_runs(runs, artifact_fetcher=lambda i: artifacts[i])
    assert "连续自然日（关闭资格）次数：1" in text
    assert "同日" in text
    assert "不得关闭" in text


def test_three_consecutive_calendar_days_passed_is_eligible_for_manual_close() -> None:
    mod = _load()
    runs = [
        {
            "databaseId": 21,
            "createdAt": "2026-08-24T03:00:00Z",
            "url": "https://example/21",
            "conclusion": "success",
        },
        {
            "databaseId": 22,
            "createdAt": "2026-08-23T03:00:00Z",
            "url": "https://example/22",
            "conclusion": "success",
        },
        {
            "databaseId": 23,
            "createdAt": "2026-08-22T03:00:00Z",
            "url": "https://example/23",
            "conclusion": "success",
        },
    ]
    artifacts = {
        21: ["evidence-nightly-21-PASSED"],
        22: ["evidence-nightly-22-PASSED"],
        23: ["evidence-nightly-23-PASSED"],
    }
    text = mod.summarize_from_runs(runs, artifact_fetcher=lambda i: artifacts[i])
    assert "连续自然日（关闭资格）次数：3" in text
    assert "仍需人工核对" in text


def test_gap_day_resets_streak() -> None:
    mod = _load()
    assert mod.consecutive_calendar_days(["2026-08-24", "2026-08-22"]) == 1
    assert mod.consecutive_calendar_days(["2026-08-24", "2026-08-23", "2026-08-22"]) == 3


def test_run_gh_requires_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._run_gh(["api", "repos/x/y"])
    assert "timeout" in captured
    assert float(captured["timeout"]) > 0
