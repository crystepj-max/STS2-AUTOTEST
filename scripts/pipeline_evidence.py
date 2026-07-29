#!/usr/bin/env python3
"""执行项目套件并一次性生成可复核的标准证据包。

项目目录、套件文件、任务编号和时限均由调用方提供。工具不会预置任何
角色、Mod、卡牌或业务套件规则。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sts2_autotest.cli.mcp_tools import run_tests_in_dir  # noqa: E402

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REQUIRED_MEMBERS = {
    "summary.json",
    "summary.md",
    "junit.xml",
    "reports/junit.xml",
    "reports/run-result.json",
    "reports/pipeline-evidence.json",
}


class EvidenceValidationError(RuntimeError):
    """本轮证据不完整、不一致或混入历史产物。"""


def _default_run_id(suite_file: Path) -> str:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{suite_file.stem}-{timestamp}-{uuid.uuid4().hex[:8]}"


def _snapshot_summaries(summary_dir: Path) -> dict[Path, int]:
    if not summary_dir.is_dir():
        return {}
    return {
        path.resolve(): path.stat().st_mtime_ns
        for path in summary_dir.glob("*.json")
        if path.is_file()
    }


def _find_fresh_summary(
    summary_dir: Path,
    before: dict[Path, int],
    run_started_ns: int,
) -> Path:
    if not summary_dir.is_dir():
        raise EvidenceValidationError("suite summary directory was not produced")
    fresh: list[Path] = []
    for path in summary_dir.glob("*.json"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        mtime_ns = path.stat().st_mtime_ns
        if mtime_ns != before.get(resolved) and mtime_ns >= run_started_ns - 2_000_000_000:
            fresh.append(path)
    if not fresh:
        raise EvidenceValidationError("suite summary was not produced in this run")
    if len(fresh) != 1:
        names = ", ".join(sorted(path.name for path in fresh))
        raise EvidenceValidationError(
            f"multiple fresh suite summaries were produced; cannot identify this run: {names}"
        )
    return fresh[0]


def _read_suite_summary(path: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceValidationError(f"suite summary is unreadable: {path}") from exc
    if not isinstance(summary, dict):
        raise EvidenceValidationError("suite summary must be a JSON object")
    total = int(summary.get("total", 0))
    passed = int(summary.get("passed", 0))
    failed = int(summary.get("failed", 0))
    cases = summary.get("cases")
    if total < 1 or passed != total or failed != 0:
        raise EvidenceValidationError(
            f"suite summary did not fully pass: total={total}, passed={passed}, failed={failed}"
        )
    if not isinstance(cases, list) or len(cases) != total:
        raise EvidenceValidationError(
            f"suite case count does not match total: cases={len(cases) if isinstance(cases, list) else 0}, total={total}"
        )
    case_ids = [
        str(case.get("case_id"))
        for case in cases
        if isinstance(case, dict) and case.get("case_id")
    ]
    if len(case_ids) != total or len(set(case_ids)) != total:
        raise EvidenceValidationError("suite summary contains missing or duplicate case_id values")
    return summary, case_ids


def _collect_case_logs(
    trace_root: Path,
    case_ids: list[str],
    run_started_ns: int,
) -> list[Path]:
    logs = sorted(path for path in trace_root.rglob("*.log") if path.is_file())
    stale = [
        path
        for path in logs
        if path.stat().st_mtime_ns < run_started_ns - 2_000_000_000
    ]
    if stale:
        raise EvidenceValidationError(
            f"case trace directory contains stale files: {', '.join(path.name for path in stale)}"
        )
    case_logs = {
        path.parent.name: path
        for path in logs
        if path.name == "case.log"
    }
    missing = [case_id for case_id in case_ids if case_id not in case_logs]
    if missing:
        raise EvidenceValidationError(f"case traces missing for: {missing}")
    return logs


def _validate_archive(
    artifact: Path,
    *,
    run_id: str,
    suite_summary_name: str,
    case_ids: list[str],
) -> int:
    try:
        with zipfile.ZipFile(artifact) as archive:
            damaged = archive.testzip()
            if damaged:
                raise EvidenceValidationError(f"artifact contains damaged member: {damaged}")
            members = set(archive.namelist())
            missing = sorted(_REQUIRED_MEMBERS - members)
            if missing:
                raise EvidenceValidationError(f"artifact missing members: {missing}")
            summary_member = f"reports/{suite_summary_name}"
            if summary_member not in members:
                raise EvidenceValidationError(
                    f"artifact missing suite summary: {summary_member}"
                )
            for case_id in case_ids:
                suffix = f"/{case_id}/case.log"
                if not any(member.endswith(suffix) for member in members):
                    raise EvidenceValidationError(
                        f"artifact missing case trace for: {case_id}"
                    )
            run_result = json.loads(
                archive.read("reports/run-result.json").decode("utf-8")
            )
            manifest = json.loads(
                archive.read("reports/pipeline-evidence.json").decode("utf-8")
            )
            suite_summary = json.loads(archive.read(summary_member).decode("utf-8"))
            if run_result.get("run_id") != run_id or run_result.get("status") != "OK":
                raise EvidenceValidationError("artifact run-result does not match this run")
            if manifest.get("run_id") != run_id:
                raise EvidenceValidationError("artifact evidence manifest has wrong run_id")
            if manifest.get("case_ids") != case_ids:
                raise EvidenceValidationError("artifact evidence manifest has wrong case list")
            suite_total = int(suite_summary.get("total", 0))
            suite_passed = int(suite_summary.get("passed", 0))
            if (
                suite_total != len(case_ids)
                or suite_passed != suite_total
                or int(suite_summary.get("failed", 0)) != 0
                or manifest.get("suite_total") != suite_total
                or manifest.get("suite_passed") != suite_passed
            ):
                raise EvidenceValidationError(
                    "artifact suite summary does not match the passed case list"
                )
            run_started_ns = int(manifest.get("run_started_ns", 0))
            if run_started_ns <= 0:
                raise EvidenceValidationError("artifact evidence manifest has no run start")
            evidence_members = (
                _REQUIRED_MEMBERS
                | {summary_member}
                | {str(member) for member in manifest.get("trace_files") or []}
            )
            stale_members: list[str] = []
            for member in evidence_members:
                try:
                    info = archive.getinfo(member)
                except KeyError as exc:
                    raise EvidenceValidationError(
                        f"artifact manifest references missing member: {member}"
                    ) from exc
                archived_ns = int(datetime(*info.date_time).timestamp() * 1_000_000_000)
                if archived_ns < run_started_ns - 2_000_000_000:
                    stale_members.append(member)
            if stale_members:
                raise EvidenceValidationError(
                    f"artifact contains stale evidence members: {sorted(stale_members)}"
                )
            return len(members)
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        if isinstance(exc, EvidenceValidationError):
            raise
        raise EvidenceValidationError(f"artifact is unreadable: {artifact}") from exc


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


def run_pipeline_evidence(args: argparse.Namespace) -> dict[str, Any]:
    project_dir = Path(args.project_dir).expanduser().resolve()
    suite_file = Path(args.suite).expanduser().resolve()
    run_id = args.run_id or _default_run_id(suite_file)
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise EvidenceValidationError(f"invalid run_id: {run_id}")
    if not project_dir.is_dir():
        raise EvidenceValidationError(f"project directory does not exist: {project_dir}")
    if not suite_file.is_file():
        raise EvidenceValidationError(f"suite file does not exist: {suite_file}")
    try:
        suite_file.relative_to(project_dir)
    except ValueError as exc:
        raise EvidenceValidationError(
            "suite file must be located inside the declared project directory"
        ) from exc

    output_dir = PROJECT_ROOT / "tests/output" / run_id
    artifact = PROJECT_ROOT / "tests/output/artifacts" / f"{run_id}_passed.zip"
    if output_dir.exists() or artifact.exists():
        raise EvidenceValidationError(
            f"run_id already has evidence; choose a new run_id: {run_id}"
        )

    summary_dir = project_dir / "automation/autotest/output/suite-summaries"
    summary_before = _snapshot_summaries(summary_dir)
    trace_root = output_dir / "reports/case-traces"
    run_started_ns = time.time_ns()

    previous_trace_root = os.environ.get("STS2_CASE_TRACE_ROOT")
    previous_agent_enabled = os.environ.get("STS2_ADAPTER__AGENT__ENABLED")
    previous_debug_actions = os.environ.get("STS2_ADAPTER__AGENT__DEBUG_ACTIONS")
    os.environ["STS2_CASE_TRACE_ROOT"] = str(trace_root)
    os.environ["STS2_ADAPTER__AGENT__ENABLED"] = "true"
    if args.debug_actions:
        os.environ["STS2_ADAPTER__AGENT__DEBUG_ACTIONS"] = "true"
    try:
        result = run_tests_in_dir(
            suite_file.parent,
            timeout=args.timeout,
            targets=[suite_file],
            output_dir=output_dir,
            run_id=run_id,
            project_dir=project_dir,
            export_artifact=False,
        )
    finally:
        _restore_env("STS2_CASE_TRACE_ROOT", previous_trace_root)
        _restore_env("STS2_ADAPTER__AGENT__ENABLED", previous_agent_enabled)
        _restore_env("STS2_ADAPTER__AGENT__DEBUG_ACTIONS", previous_debug_actions)

    if (
        result.get("status") != "OK"
        or int(result.get("passed", 0)) < 1
        or int(result.get("failed", 0)) != 0
    ):
        raise EvidenceValidationError(
            f"suite did not pass: {json.dumps(result, ensure_ascii=False)[:400]}"
        )
    pack_dir = Path(result.get("evidence_dir") or "").resolve()
    if pack_dir != output_dir.resolve() or not pack_dir.is_dir():
        raise EvidenceValidationError(f"evidence pack directory is invalid: {pack_dir}")

    suite_summary = _find_fresh_summary(
        summary_dir,
        summary_before,
        run_started_ns,
    )
    summary, case_ids = _read_suite_summary(suite_summary)
    trace_files = _collect_case_logs(trace_root, case_ids, run_started_ns)

    reports_dir = pack_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    summary_target = reports_dir / suite_summary.name
    shutil.copy2(suite_summary, summary_target)
    manifest = {
        "run_id": run_id,
        "status": result.get("status"),
        "project_dir": str(project_dir),
        "suite": str(suite_file),
        "suite_id": summary.get("suite_id"),
        "suite_total": int(summary["total"]),
        "suite_passed": int(summary["passed"]),
        "case_ids": case_ids,
        "trace_files": [
            str(path.relative_to(pack_dir))
            for path in trace_files
        ],
        "run_started_ns": run_started_ns,
        "junit": "junit.xml",
        "run_result": "reports/run-result.json",
    }
    (reports_dir / "pipeline-evidence.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    from sts2_autotest.evidence.packager import EvidencePackager

    exported = EvidencePackager(PROJECT_ROOT / "tests/output").export_artifact(
        run_id,
        result="passed",
    )
    if exported is None:
        raise EvidenceValidationError("artifact export failed")
    try:
        member_count = _validate_archive(
            exported,
            run_id=run_id,
            suite_summary_name=suite_summary.name,
            case_ids=case_ids,
        )
    except EvidenceValidationError:
        exported.unlink(missing_ok=True)
        raise

    return {
        "run_id": run_id,
        "status": "PASSED",
        "suite_total": int(summary["total"]),
        "suite_passed": int(summary["passed"]),
        "case_ids": case_ids,
        "duration_ms": int(result.get("duration_ms", 0)),
        "evidence_dir": str(pack_dir),
        "artifact": str(exported),
        "artifact_members": member_count,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--debug-actions",
        action="store_true",
        help="Enable development-only debug actions for suites that explicitly require them",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        result = run_pipeline_evidence(_build_parser().parse_args(argv))
    except EvidenceValidationError as exc:
        print(f"[evidence] FAILED: {exc}", file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
