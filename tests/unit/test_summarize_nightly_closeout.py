"""summarize_nightly_closeout 关闭 streak 规则单测（issue #15 / #66）。"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import zipfile
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


def _art(artifact_id: int, name: str) -> dict[str, Any]:
    return {"id": artifact_id, "name": name}


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "classification": "PASSED",
        "diagnosable": True,
        "closeout_eligible": True,
        "screenshots": {"count": 0, "available": False, "reason": "0 张/不可用：未产生截图"},
    }
    base.update(overrides)
    return base


def test_early_diagnosis_alone_does_not_count_toward_closeout() -> None:
    mod = _load()
    runs = [
        {"databaseId": 1, "createdAt": "2026-08-24T03:00:00Z", "url": "https://example/1", "conclusion": "failure"},
        {"databaseId": 2, "createdAt": "2026-08-23T03:00:00Z", "url": "https://example/2", "conclusion": "failure"},
        {"databaseId": 3, "createdAt": "2026-08-22T03:00:00Z", "url": "https://example/3", "conclusion": "failure"},
    ]
    artifacts = {
        1: [_art(101, "early-diagnosis-1-BLOCKED")],
        2: [_art(102, "early-diagnosis-2-BLOCKED")],
        3: [_art(103, "early-diagnosis-3-BLOCKED")],
    }
    text = mod.summarize_from_runs(
        runs,
        artifact_fetcher=lambda i: artifacts[i],
        classification_loader=lambda _aid: None,
    )
    assert "连续自然日（关闭资格）次数：0" in text
    assert "early-diagnosis" in text or "无 evidence-nightly" in text
    assert "不得关闭" in text


def test_same_calendar_day_manual_runs_do_not_inflate_streak() -> None:
    mod = _load()
    runs = [
        {"databaseId": 11, "createdAt": "2026-08-24T18:00:00Z", "url": "https://example/11", "conclusion": "success"},
        {"databaseId": 12, "createdAt": "2026-08-24T12:00:00Z", "url": "https://example/12", "conclusion": "success"},
        {"databaseId": 13, "createdAt": "2026-08-24T06:00:00Z", "url": "https://example/13", "conclusion": "success"},
    ]
    artifacts = {
        11: [_art(201, "evidence-nightly-11-PASSED")],
        12: [_art(202, "evidence-nightly-12-PASSED")],
        13: [_art(203, "evidence-nightly-13-PASSED")],
    }
    text = mod.summarize_from_runs(
        runs,
        artifact_fetcher=lambda i: artifacts[i],
        classification_loader=lambda _aid: _payload(),
    )
    assert "连续自然日（关闭资格）次数：1" in text
    assert "同日" in text
    assert "不得关闭" in text


def test_three_consecutive_calendar_days_passed_is_eligible_for_manual_close() -> None:
    mod = _load()
    runs = [
        {"databaseId": 21, "createdAt": "2026-08-24T03:00:00Z", "url": "https://example/21", "conclusion": "success"},
        {"databaseId": 22, "createdAt": "2026-08-23T03:00:00Z", "url": "https://example/22", "conclusion": "success"},
        {"databaseId": 23, "createdAt": "2026-08-22T03:00:00Z", "url": "https://example/23", "conclusion": "success"},
    ]
    artifacts = {
        21: [_art(301, "evidence-nightly-21-PASSED")],
        22: [_art(302, "evidence-nightly-22-PASSED")],
        23: [_art(303, "evidence-nightly-23-PASSED")],
    }
    text = mod.summarize_from_runs(
        runs,
        artifact_fetcher=lambda i: artifacts[i],
        classification_loader=lambda _aid: _payload(),
    )
    assert "连续自然日（关闭资格）次数：3" in text
    assert "仍需人工核对" in text


def test_artifact_name_passed_but_json_not_eligible_does_not_count() -> None:
    """名称带 PASSED 但 JSON closeout_eligible=false 时不得计入。"""
    mod = _load()
    runs = [
        {"databaseId": 31, "createdAt": "2026-08-24T03:00:00Z", "url": "https://example/31", "conclusion": "success"},
        {"databaseId": 32, "createdAt": "2026-08-23T03:00:00Z", "url": "https://example/32", "conclusion": "success"},
        {"databaseId": 33, "createdAt": "2026-08-22T03:00:00Z", "url": "https://example/33", "conclusion": "success"},
    ]
    artifacts = {
        31: [_art(401, "evidence-nightly-31-PASSED")],
        32: [_art(402, "evidence-nightly-32-PASSED")],
        33: [_art(403, "evidence-nightly-33-PASSED")],
    }
    text = mod.summarize_from_runs(
        runs,
        artifact_fetcher=lambda i: artifacts[i],
        classification_loader=lambda _aid: _payload(closeout_eligible=False, screenshots={"count": 0}),
    )
    assert "连续自然日（关闭资格）次数：0" in text
    assert "closeout_eligible=false" in text


def test_load_classification_from_zip() -> None:
    mod = _load()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "tests/output/classification.json",
            json.dumps(_payload(classification="PASSED", closeout_eligible=True)),
        )
    payload = mod.load_classification_from_zip(buf.getvalue())
    assert payload is not None
    assert payload["closeout_eligible"] is True


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
