#!/usr/bin/env python3
"""夜间回归结论分类（issue #15 / #64 / #65）。

只使用步骤的原始执行结果 ``outcome``，不用 ``conclusion``：
``continue-on-error`` 步骤失败后 ``conclusion`` 会被改写成 success，
会把真实游戏失败误报为通过。

本脚本仅依赖 Python 标准库，checkout 成功后即使 pip install 失败也能运行。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

ENV_PHASES: tuple[str, ...] = ("checkout", "env_check", "setup_python", "install")
FUNCTIONAL_PHASES: tuple[str, ...] = (
    "lint",
    "typecheck",
    "import_check",
    "unit_tests",
    "cli_tests",
    "game_tests",
)
SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VALID_OUTCOMES = {"success", "failure", "cancelled", "skipped"}
PLACEHOLDER_MARKERS = ("$(date", "${{", "T%FT%TZ")
# issue #83：单测/CLI-only 的 pytest 运行也会写入 case-traces（fluent DSL 固定目录名），
# 这些 traces 不是真实游戏产物，不得计入 game_evidence。
UNIT_TRACE_DIR_NAMES = {"case-traces"}


def is_unit_trace_path(path: Path, evidence_dir: Path) -> bool:
    """文件是否位于单元测试痕迹目录（case-traces）之下，或证据目录本身就是该目录。"""
    if evidence_dir.name in UNIT_TRACE_DIR_NAMES:
        return True
    try:
        rel = path.relative_to(evidence_dir)
    except ValueError:
        return False
    return any(part in UNIT_TRACE_DIR_NAMES for part in rel.parts)


def utc_now_iso() -> str:
    """返回真实 UTC 时间，格式 YYYY-MM-DDTHH:MM:SSZ。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_outcome(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value in VALID_OUTCOMES:
        return value
    return "skipped"


def resolve_env_phase(
    phase: str,
    primary: str,
    retry: str,
) -> tuple[str, list[dict[str, Any]]]:
    """合并环境步骤的一次受控重试，保留两次 attempt 记录。"""
    first = normalize_outcome(primary)
    second = normalize_outcome(retry)
    records: list[dict[str, Any]] = [
        {"phase": phase, "attempt": 1, "outcome": first},
    ]
    if second != "skipped":
        records.append({"phase": phase, "attempt": 2, "outcome": second})

    if first == "success":
        return "success", records
    if second == "success":
        return "success", records
    if first == "cancelled" or second == "cancelled":
        return "cancelled", records
    if first == "failure" or second == "failure":
        return "failure", records
    return first, records


def count_screenshots(root: Path | None) -> int:
    if root is None or not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in SCREENSHOT_SUFFIXES:
            if is_unit_trace_path(path, root):
                continue
            total += 1
    return total


def junit_case_stats(junit_path: Path | None) -> dict[str, int]:
    """统计 JUnit 中已执行 / 跳过 / 失败用例数。"""
    stats = {"executed": 0, "skipped": 0, "failed": 0, "total": 0}
    if junit_path is None or not junit_path.is_file():
        return stats
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(junit_path).getroot()
    except (OSError, ET.ParseError):
        return stats

    cases = list(root.iter("testcase"))
    stats["total"] = len(cases)
    for case in cases:
        if case.find("skipped") is not None:
            stats["skipped"] += 1
            continue
        if case.find("failure") is not None or case.find("error") is not None:
            stats["failed"] += 1
        stats["executed"] += 1
    return stats


def game_evidence_present(
    evidence_dir: Path | None,
    *,
    screenshot_count: int,
) -> dict[str, Any]:
    """是否存在真实游戏验证产物（截图 / 状态 JSON / 日志）。

    单元测试痕迹目录（case-traces）下的文件是 pytest 运行留下的 traces，
    不是游戏产物，不计入（issue #83）。
    """
    shots = max(0, screenshot_count)
    state_json = 0
    logs = 0
    unit_traces_excluded = 0
    if evidence_dir is not None and evidence_dir.is_dir():
        for path in evidence_dir.rglob("*"):
            if not path.is_file():
                continue
            if is_unit_trace_path(path, evidence_dir):
                unit_traces_excluded += 1
                continue
            name = path.name.lower()
            suffix = path.suffix.lower()
            if suffix == ".json" and name not in {"classification.json", "early-diagnosis.json"}:
                state_json += 1
            elif suffix in {".log", ".txt"} or "log" in name:
                logs += 1
    present = shots > 0 or state_json > 0 or logs > 0
    return {
        "present": present,
        "screenshots": shots,
        "state_json": state_json,
        "logs": logs,
        "unit_traces_excluded": unit_traces_excluded,
    }


def screenshot_index(
    count: int,
    *,
    checkout_ok: bool,
    env_ok: bool,
    game_outcome: str,
) -> dict[str, Any]:
    if count > 0:
        return {"count": count, "available": True, "reason": None}
    if not checkout_ok:
        reason = "0 张/不可用：checkout 失败，工作区不可用"
    elif not env_ok:
        reason = "0 张/不可用：环境未就绪，未进入游戏验证"
    elif game_outcome in {"skipped", "cancelled"}:
        reason = "0 张/不可用：未执行真实游戏验证"
    else:
        reason = "0 张/不可用：真实游戏验证未产生截图"
    return {"count": 0, "available": False, "reason": reason}


def classify(
    outcomes: Mapping[str, str],
    *,
    run_id: str = "",
    screenshot_count: int | None = None,
    evidence_dir: Path | None = None,
    evidence_upload_ok: bool | None = None,
    timestamp: str | None = None,
    junit_game: Path | None = None,
) -> dict[str, Any]:
    """按 outcome 给出 PASSED / FAILED / BLOCKED / CANCELLED。"""
    attempts: list[dict[str, Any]] = []
    resolved: dict[str, str] = {}

    for phase in ENV_PHASES:
        outcome, records = resolve_env_phase(
            phase,
            outcomes.get(phase, ""),
            outcomes.get(f"{phase}_retry", ""),
        )
        resolved[phase] = outcome
        attempts.extend(records)

    for phase in FUNCTIONAL_PHASES:
        outcome = normalize_outcome(outcomes.get(phase))
        resolved[phase] = outcome
        attempts.append({"phase": phase, "attempt": 1, "outcome": outcome})

    classification = "PASSED"
    reason = "All stages completed successfully"
    failed_phase: str | None = None

    for phase in ENV_PHASES:
        outcome = resolved[phase]
        if outcome == "cancelled":
            classification = "CANCELLED"
            reason = f"{phase} cancelled"
            failed_phase = phase
            break
        if outcome != "success":
            classification = "BLOCKED"
            reason = f"{phase} failed" if outcome == "failure" else f"{phase} did not succeed ({outcome})"
            failed_phase = phase
            break
    else:
        for phase in FUNCTIONAL_PHASES:
            outcome = resolved[phase]
            if outcome == "cancelled":
                classification = "CANCELLED"
                reason = f"{phase} cancelled"
                failed_phase = phase
                break
            if outcome == "failure":
                classification = "FAILED"
                reason = f"{phase} failed"
                failed_phase = phase
                break
            if outcome == "skipped":
                classification = "BLOCKED"
                reason = f"{phase} skipped (game control or suite unavailable)"
                failed_phase = phase
                break
            if outcome != "success":
                classification = "CANCELLED"
                reason = f"{phase} did not complete ({outcome})"
                failed_phase = phase
                break

    checkout_ok = resolved["checkout"] == "success"
    env_ok = all(resolved[phase] == "success" for phase in ENV_PHASES)
    if screenshot_count is None:
        screenshot_count = count_screenshots(evidence_dir)

    game_junit = junit_case_stats(junit_game)
    game_artifacts = game_evidence_present(
        evidence_dir,
        screenshot_count=int(screenshot_count or 0),
    )

    # game_tests 报 success 但未真正执行 / 无证据 → BLOCKED（防 all-skip 假绿）
    if classification == "PASSED" and resolved.get("game_tests") == "success":
        if game_junit["executed"] <= 0:
            classification = "BLOCKED"
            reason = (
                "game_tests reported success but executed=0 "
                f"(skipped={game_junit['skipped']}, total={game_junit['total']})"
            )
            failed_phase = "game_tests"
        elif not game_artifacts["present"]:
            classification = "BLOCKED"
            reason = "game_tests ran but no screenshot/state JSON/log evidence"
            failed_phase = "game_tests"

    ts = timestamp or utc_now_iso()
    diagnosable = True
    if evidence_upload_ok is False:
        diagnosable = False

    screenshots = screenshot_index(
        int(screenshot_count or 0),
        checkout_ok=checkout_ok,
        env_ok=env_ok,
        game_outcome=resolved["game_tests"],
    )
    closeout_eligible = bool(
        diagnosable
        and classification == "PASSED"
        and resolved.get("game_tests") == "success"
        and game_junit["executed"] > 0
        and game_artifacts["present"]
    )

    payload: dict[str, Any] = {
        "run_id": run_id,
        "classification": classification,
        "reason": reason,
        "failed_phase": failed_phase,
        "last_status": resolved.get(failed_phase or "game_tests", classification.lower()),
        "timestamp": ts,
        "stages": {phase: resolved[phase] for phase in (*ENV_PHASES, *FUNCTIONAL_PHASES)},
        "attempts": attempts,
        "screenshots": screenshots,
        "game_junit": game_junit,
        "game_evidence": game_artifacts,
        "diagnosable": diagnosable,
        "closeout_eligible": closeout_eligible,
        "env_retry_recorded": any(item["attempt"] == 2 for item in attempts if item["phase"] in ENV_PHASES),
        "functional_retry_forbidden": all(
            item["attempt"] == 1 for item in attempts if item["phase"] in FUNCTIONAL_PHASES
        ),
    }
    return payload


def outcomes_from_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = env or os.environ
    prefix = "NIGHTLY_STEP_"
    outcomes: dict[str, str] = {}
    for key, value in source.items():
        if key.startswith(prefix):
            outcomes[key[len(prefix):].lower()] = value
    return outcomes


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, path)


def emit_github_output(payload: Mapping[str, Any]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    lines = [
        f"classification={payload['classification']}",
        f"reason={payload['reason']}",
        f"failed_phase={payload.get('failed_phase') or ''}",
        f"timestamp={payload['timestamp']}",
        f"diagnosable={'true' if payload['diagnosable'] else 'false'}",
        f"closeout_eligible={'true' if payload['closeout_eligible'] else 'false'}",
    ]
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def run_self_check() -> int:
    """可重复检查：四类终态、continue-on-error 误报、真实时间、截图与上传。"""
    failures: list[str] = []
    skipped = {phase: "skipped" for phase in (*ENV_PHASES, *FUNCTIONAL_PHASES)}

    env_blocked = dict(skipped)
    env_blocked.update(
        {
            "checkout": "success",
            "env_check": "failure",
            "env_check_retry": "failure",
        }
    )
    blocked = classify(env_blocked, run_id="self-check-blocked")
    _assert(blocked["classification"] == "BLOCKED", "环境失败应得到 BLOCKED", failures)
    _assert(blocked["failed_phase"] == "env_check", "环境失败阶段应为 env_check", failures)
    _assert(
        sum(1 for item in blocked["attempts"] if item["phase"] == "env_check") == 2,
        "环境失败应保留两次 attempt 记录",
        failures,
    )

    game_failed = {
        phase: "success" for phase in (*ENV_PHASES, *FUNCTIONAL_PHASES)
    }
    game_failed["game_tests"] = "failure"
    failed = classify(game_failed, run_id="self-check-failed")
    _assert(failed["classification"] == "FAILED", "游戏验证失败应得到 FAILED", failures)
    _assert(failed["failed_phase"] == "game_tests", "功能失败阶段应为 game_tests", failures)
    _assert(
        all(item["attempt"] == 1 for item in failed["attempts"] if item["phase"] == "game_tests"),
        "功能失败不得触发环境重试/第二次游戏尝试",
        failures,
    )

    cancelled_outcomes = {
        phase: "success" for phase in (*ENV_PHASES, *FUNCTIONAL_PHASES)
    }
    cancelled_outcomes["unit_tests"] = "cancelled"
    cancelled = classify(cancelled_outcomes, run_id="self-check-cancelled")
    _assert(cancelled["classification"] == "CANCELLED", "任务取消应得到 CANCELLED", failures)
    _assert(cancelled["classification"] != "PASSED", "取消不得被当作通过", failures)

    passed_outcomes = {
        phase: "success" for phase in (*ENV_PHASES, *FUNCTIONAL_PHASES)
    }
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        junit_ok = tmp_path / "junit-game.xml"
        junit_ok.write_text(
            """<?xml version="1.0"?>
<testsuite tests="2" skipped="0" failures="0">
  <testcase classname="t" name="a"/>
  <testcase classname="t" name="b"/>
</testsuite>
""",
            encoding="utf-8",
        )
        junit_all_skip = tmp_path / "junit-skip.xml"
        junit_all_skip.write_text(
            """<?xml version="1.0"?>
<testsuite tests="2" skipped="2" failures="0">
  <testcase classname="t" name="a"><skipped/></testcase>
  <testcase classname="t" name="b"><skipped/></testcase>
</testsuite>
""",
            encoding="utf-8",
        )
        evidence = tmp_path / "output"
        evidence.mkdir()
        (evidence / "state.json").write_text('{"screen":"MENU"}', encoding="utf-8")

        passed = classify(
            passed_outcomes,
            run_id="self-check-passed",
            screenshot_count=2,
            evidence_dir=evidence,
            junit_game=junit_ok,
        )
        _assert(passed["classification"] == "PASSED", "全部成功应得到 PASSED", failures)
        _assert(passed["screenshots"]["available"] is True, "有截图时应标记 available", failures)
        _assert(passed["closeout_eligible"] is True, "有执行+证据应 closeout_eligible", failures)

        ts = passed["timestamp"]
        _assert(bool(ISO_Z_RE.match(ts)), f"时间戳必须是真实 UTC ISO：{ts}", failures)
        _assert(all(marker not in ts for marker in PLACEHOLDER_MARKERS), "时间戳不得是未展开占位", failures)

        upload_fail = classify(
            passed_outcomes,
            run_id="self-check-upload",
            evidence_upload_ok=False,
            evidence_dir=evidence,
            junit_game=junit_ok,
            screenshot_count=1,
        )
        _assert(upload_fail["diagnosable"] is False, "证据上传失败不能算可诊断结果", failures)
        _assert(upload_fail["closeout_eligible"] is False, "不可诊断结果不得计入关闭证据", failures)

        all_skip = classify(
            passed_outcomes,
            run_id="self-check-all-skip",
            screenshot_count=0,
            junit_game=junit_all_skip,
        )
        _assert(
            all_skip["classification"] == "BLOCKED",
            "game_tests success 但 JUnit executed=0 应 BLOCKED",
            failures,
        )
        _assert(all_skip["closeout_eligible"] is False, "all-skip 不得 closeout", failures)

        no_evidence = classify(
            passed_outcomes,
            run_id="self-check-no-evidence",
            screenshot_count=0,
            junit_game=junit_ok,
            evidence_dir=tmp_path / "empty",
        )
        _assert(
            no_evidence["classification"] == "BLOCKED",
            "有执行但无截图/状态/日志应 BLOCKED",
            failures,
        )

        state_only = classify(
            passed_outcomes,
            run_id="self-check-state-only",
            screenshot_count=0,
            junit_game=junit_ok,
            evidence_dir=evidence,
        )
        _assert(
            state_only["classification"] == "PASSED",
            "无截图但有状态 JSON + 已执行用例可为 PASSED",
            failures,
        )
        _assert(state_only["closeout_eligible"] is True, "状态 JSON 证据应允许 closeout", failures)

        # issue #83：单元测试留下的 traces 不是游戏证据
        unit_output = tmp_path / "unit-output"
        traces = unit_output / "case-traces"
        traces.mkdir(parents=True)
        for idx in range(3):
            (traces / f"case-{idx}.log").write_text("trace", encoding="utf-8")
        (traces / "case-state.json").write_text("{}", encoding="utf-8")

        unit_only = classify(
            passed_outcomes,
            run_id="self-check-unit-traces",
            junit_game=junit_ok,
            evidence_dir=unit_output,
        )
        _assert(
            unit_only["classification"] == "BLOCKED",
            "冒烟全过但仅有单元 traces、0 截图不得给出 PASSED",
            failures,
        )
        _assert(
            unit_only["closeout_eligible"] is False,
            "单元 traces 不得计入 closeout",
            failures,
        )
        _assert(
            unit_only["game_evidence"]["unit_traces_excluded"] == 4,
            "被排除的单元 traces 数应记录在 game_evidence",
            failures,
        )

        traces_dir_only = classify(
            passed_outcomes,
            run_id="self-check-traces-dir",
            junit_game=junit_ok,
            evidence_dir=traces,
        )
        _assert(
            traces_dir_only["classification"] == "BLOCKED",
            "证据目录本身为 case-traces 时不得 PASSED",
            failures,
        )
        _assert(
            traces_dir_only["closeout_eligible"] is False,
            "证据目录本身为 case-traces 时不得 closeout",
            failures,
        )

        (unit_output / "game-final.png").write_bytes(b"\x89PNG\r\n")
        mixed = classify(
            passed_outcomes,
            run_id="self-check-mixed-evidence",
            junit_game=junit_ok,
            evidence_dir=unit_output,
        )
        _assert(
            mixed["classification"] == "PASSED" and mixed["closeout_eligible"] is True,
            "真实游戏截图不受单元 traces 排除影响",
            failures,
        )

    checkout_fail = dict(skipped)
    checkout_fail["checkout"] = "failure"
    checkout_fail["checkout_retry"] = "failure"
    early = classify(checkout_fail, run_id="self-check-checkout", screenshot_count=0)
    _assert(early["classification"] == "BLOCKED", "checkout 失败应 BLOCKED", failures)
    _assert(early["screenshots"]["count"] == 0, "无截图时应记录 count=0", failures)
    _assert("0 张/不可用" in str(early["screenshots"]["reason"]), "无截图应写明 0 张/不可用", failures)

    skipped_game = {
        phase: "success" for phase in (*ENV_PHASES, *FUNCTIONAL_PHASES)
    }
    skipped_game["game_tests"] = "skipped"
    skipped_result = classify(skipped_game, run_id="self-check-skipped-game", screenshot_count=0)
    _assert(
        skipped_result["classification"] == "BLOCKED",
        "游戏步骤 skipped 不得 PASSED，应 BLOCKED",
        failures,
    )
    _assert(
        skipped_result["closeout_eligible"] is False,
        "无真实游戏证据不得 closeout_eligible",
        failures,
    )

    early_blocked = classify(checkout_fail, run_id="self-check-early-closeout", screenshot_count=0)
    _assert(
        early_blocked["closeout_eligible"] is False,
        "early-diagnosis / checkout BLOCKED 不得计入关闭 streak",
        failures,
    )

    if failures:
        print("classify_nightly self-check FAILED:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("classify_nightly self-check PASSED")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按 GitHub Actions step outcome 分类夜间回归")
    parser.add_argument("--self-check", action="store_true", help="运行内置可重复场景检查")
    parser.add_argument("--write", action="append", type=Path, default=[], help="写入 classification.json（可重复）")
    parser.add_argument("--run-id", default="", help="GitHub run id")
    parser.add_argument("--evidence-dir", type=Path, default=None, help="用于统计截图的目录")
    parser.add_argument(
        "--junit-game",
        type=Path,
        default=None,
        help="游戏集成测试 JUnit XML（用于识别 all-skip 假绿）",
    )
    parser.add_argument(
        "--evidence-upload-ok",
        choices=["true", "false", "unknown"],
        default="unknown",
        help="证据上传是否成功；unknown 表示尚未上传",
    )
    parser.add_argument(
        "--mark-not-diagnosable",
        action="append",
        type=Path,
        default=[],
        help="将已有 classification.json 标记为不可诊断（证据上传失败）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_check:
        return run_self_check()
    if args.mark_not_diagnosable:
        upload_outcome = os.environ.get("UPLOAD_OUTCOME", "failure")
        for target in args.mark_not_diagnosable:
            if not target.is_file():
                continue
            data = json.loads(target.read_text(encoding="utf-8"))
            data["diagnosable"] = False
            data["closeout_eligible"] = False
            data["evidence_upload_outcome"] = upload_outcome
            write_json(target, data)
            print(f"marked not diagnosable: {target}")
        return 0

    upload_ok: bool | None
    if args.evidence_upload_ok == "true":
        upload_ok = True
    elif args.evidence_upload_ok == "false":
        upload_ok = False
    else:
        upload_ok = None

    run_id = args.run_id or os.environ.get("NIGHTLY_RUN_ID") or os.environ.get("GITHUB_RUN_ID") or ""
    payload = classify(
        outcomes_from_env(),
        run_id=run_id,
        evidence_dir=args.evidence_dir,
        evidence_upload_ok=upload_ok,
        junit_game=args.junit_game,
    )
    for target in args.write:
        write_json(target, payload)
    emit_github_output(payload)
    print(
        f"Classification: {payload['classification']} — {payload['reason']} "
        f"(timestamp={payload['timestamp']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
