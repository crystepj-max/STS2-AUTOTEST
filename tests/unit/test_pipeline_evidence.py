"""通用项目证据工具的完整性检查。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/pipeline_evidence.py"
_SPEC = importlib.util.spec_from_file_location("pipeline_evidence_script", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
pipeline_evidence = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = pipeline_evidence
_SPEC.loader.exec_module(pipeline_evidence)


def _passed_summary(case_ids: list[str] | None = None) -> dict[str, object]:
    ids = case_ids or ["TC-A"]
    return {
        "suite_id": "SUITE-X",
        "total": len(ids),
        "passed": len(ids),
        "failed": 0,
        "cases": [{"case_id": case_id} for case_id in ids],
    }


def test_default_run_id_is_unique() -> None:
    suite = Path("test_suite_x.py")
    assert pipeline_evidence._default_run_id(suite) != pipeline_evidence._default_run_id(
        suite
    )


def test_fresh_summary_rejects_missing_or_stale_file(tmp_path: Path) -> None:
    summary_dir = tmp_path / "suite-summaries"
    summary_dir.mkdir()
    summary = summary_dir / "SUITE-X.json"
    summary.write_text(json.dumps(_passed_summary()), encoding="utf-8")
    before = pipeline_evidence._snapshot_summaries(summary_dir)

    with pytest.raises(
        pipeline_evidence.EvidenceValidationError,
        match="not produced in this run",
    ):
        pipeline_evidence._find_fresh_summary(
            summary_dir,
            before,
            time.time_ns(),
        )


def test_fresh_summary_accepts_file_changed_by_this_run(tmp_path: Path) -> None:
    summary_dir = tmp_path / "suite-summaries"
    summary_dir.mkdir()
    summary = summary_dir / "SUITE-X.json"
    summary.write_text(json.dumps(_passed_summary(["TC-A"])), encoding="utf-8")
    before_mtime_ns = summary.stat().st_mtime_ns
    before_size = summary.stat().st_size
    before = pipeline_evidence._snapshot_summaries(summary_dir)
    run_started_ns = time.time_ns()

    summary.write_text(json.dumps(_passed_summary(["TC-B"])), encoding="utf-8")
    os.utime(
        summary,
        ns=(summary.stat().st_atime_ns, before_mtime_ns),
    )

    assert summary.stat().st_mtime_ns == before_mtime_ns
    assert summary.stat().st_size == before_size

    assert (
        pipeline_evidence._find_fresh_summary(
            summary_dir,
            before,
            run_started_ns,
        )
        == summary
    )


def test_suite_summary_rejects_failed_case(tmp_path: Path) -> None:
    summary = tmp_path / "SUITE-X.json"
    payload = _passed_summary()
    payload.update({"passed": 0, "failed": 1})
    summary.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        pipeline_evidence.EvidenceValidationError,
        match="did not fully pass",
    ):
        pipeline_evidence._read_suite_summary(summary)


def test_case_trace_validation_requires_every_case(tmp_path: Path) -> None:
    trace_root = tmp_path / "case-traces"
    case_dir = trace_root / "suite" / "TC-A"
    case_dir.mkdir(parents=True)
    (case_dir / "case.log").write_text("结果：passed\n", encoding="utf-8")

    with pytest.raises(
        pipeline_evidence.EvidenceValidationError,
        match="TC-B",
    ):
        pipeline_evidence._collect_case_logs(
            trace_root,
            ["TC-A", "TC-B"],
            time.time_ns(),
        )


def test_archive_validation_rejects_missing_member(tmp_path: Path) -> None:
    artifact = tmp_path / "run_passed.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("summary.json", "{}")

    with pytest.raises(
        pipeline_evidence.EvidenceValidationError,
        match="missing members",
    ):
        pipeline_evidence._validate_archive(
            artifact,
            run_id="run",
            suite_summary_name="SUITE-X.json",
            case_ids=["TC-A"],
        )


def _write_archive_member(
    archive: zipfile.ZipFile,
    name: str,
    content: str,
    *,
    date_time: tuple[int, int, int, int, int, int] | None = None,
) -> None:
    if date_time is None:
        archive.writestr(name, content)
        return
    info = zipfile.ZipInfo(name, date_time)
    archive.writestr(info, content)


def _write_complete_archive(
    artifact: Path,
    *,
    suite_passed: int = 1,
    date_time: tuple[int, int, int, int, int, int] | None = None,
) -> None:
    run_started_ns = time.time_ns()
    manifest = {
        "run_id": "run",
        "suite_total": 1,
        "suite_passed": 1,
        "case_ids": ["TC-A"],
        "trace_files": ["reports/case-traces/suite/TC-A/case.log"],
        "run_started_ns": run_started_ns,
    }
    contents = {
        "summary.json": "{}",
        "summary.md": "ok\n",
        "junit.xml": "<testsuites/>",
        "reports/junit.xml": "<testsuites/>",
        "reports/run-result.json": json.dumps({"run_id": "run", "status": "OK"}),
        "reports/pipeline-evidence.json": json.dumps(manifest),
        "reports/SUITE-X.json": json.dumps(
            {
                "total": 1,
                "passed": suite_passed,
                "failed": 1 - suite_passed,
                "cases": [{"case_id": "TC-A"}],
            }
        ),
        "reports/case-traces/suite/TC-A/case.log": "结果：passed\n",
    }
    with zipfile.ZipFile(artifact, "w") as archive:
        for name, content in contents.items():
            _write_archive_member(
                archive,
                name,
                content,
                date_time=date_time,
            )


def test_archive_validation_rejects_failed_suite_summary(tmp_path: Path) -> None:
    artifact = tmp_path / "run_passed.zip"
    _write_complete_archive(artifact, suite_passed=0)

    with pytest.raises(
        pipeline_evidence.EvidenceValidationError,
        match="suite summary",
    ):
        pipeline_evidence._validate_archive(
            artifact,
            run_id="run",
            suite_summary_name="SUITE-X.json",
            case_ids=["TC-A"],
        )


def test_archive_validation_rejects_stale_member(tmp_path: Path) -> None:
    artifact = tmp_path / "run_passed.zip"
    _write_complete_archive(artifact, date_time=(2020, 1, 1, 0, 0, 0))

    with pytest.raises(
        pipeline_evidence.EvidenceValidationError,
        match="stale evidence",
    ):
        pipeline_evidence._validate_archive(
            artifact,
            run_id="run",
            suite_summary_name="SUITE-X.json",
            case_ids=["TC-A"],
        )


def test_existing_run_id_is_rejected_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    suite = project / "generated/test_suite_x.py"
    suite.parent.mkdir(parents=True)
    suite.write_text("# suite\n", encoding="utf-8")
    platform_root = tmp_path / "platform"
    (platform_root / "tests/output/existing-run").mkdir(parents=True)
    monkeypatch.setattr(pipeline_evidence, "PROJECT_ROOT", platform_root)
    args = argparse.Namespace(
        project_dir=str(project),
        suite=str(suite),
        run_id="existing-run",
        timeout=60,
        debug_actions=False,
    )

    with pytest.raises(
        pipeline_evidence.EvidenceValidationError,
        match="already has evidence",
    ):
        pipeline_evidence.run_pipeline_evidence(args)


def test_execution_defers_archive_until_evidence_is_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    suite = project / "generated/test_suite_x.py"
    suite.parent.mkdir(parents=True)
    suite.write_text("# suite\n", encoding="utf-8")
    platform_root = tmp_path / "platform"
    monkeypatch.setattr(pipeline_evidence, "PROJECT_ROOT", platform_root)
    captured: dict[str, object] = {}

    def fake_run_tests(*_args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "FAILED", "passed": 0, "failed": 1}

    monkeypatch.setattr(pipeline_evidence, "run_tests_in_dir", fake_run_tests)
    args = argparse.Namespace(
        project_dir=str(project),
        suite=str(suite),
        run_id="new-run",
        timeout=60,
        debug_actions=False,
    )

    with pytest.raises(
        pipeline_evidence.EvidenceValidationError,
        match="suite did not pass",
    ):
        pipeline_evidence.run_pipeline_evidence(args)
    assert captured["export_artifact"] is False


def test_execution_accepts_relative_evidence_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    suite = project / "generated/test_suite_x.py"
    suite.parent.mkdir(parents=True)
    suite.write_text("# suite\n", encoding="utf-8")
    summary_dir = project / "automation/autotest/output/suite-summaries"
    summary_dir.mkdir(parents=True)
    platform_root = tmp_path / "platform"
    platform_root.mkdir()
    output_dir = platform_root / "tests/output/new-run"
    trace_dir = output_dir / "reports/case-traces/suite/TC-A"

    monkeypatch.setattr(pipeline_evidence, "PROJECT_ROOT", platform_root)
    monkeypatch.chdir(platform_root)

    def fake_run_tests(*_args: object, **_kwargs: object) -> dict[str, object]:
        output_dir.mkdir(parents=True)
        (output_dir / "summary.json").write_text("{}", encoding="utf-8")
        (output_dir / "summary.md").write_text("ok\n", encoding="utf-8")
        (output_dir / "junit.xml").write_text("<testsuites/>", encoding="utf-8")
        reports = output_dir / "reports"
        reports.mkdir()
        (reports / "junit.xml").write_text("<testsuites/>", encoding="utf-8")
        (reports / "run-result.json").write_text(
            json.dumps({"run_id": "new-run", "status": "OK"}),
            encoding="utf-8",
        )
        trace_dir.mkdir(parents=True)
        (trace_dir / "case.log").write_text("结果：passed\n", encoding="utf-8")
        summary = summary_dir / "SUITE-X.json"
        summary.write_text(json.dumps(_passed_summary()), encoding="utf-8")
        return {
            "status": "OK",
            "passed": 1,
            "failed": 0,
            "duration_ms": 1,
            "evidence_dir": "tests/output/new-run",
        }

    class FakePackager:
        def __init__(self, _root: Path) -> None:
            pass

        def export_artifact(self, _run_id: str, *, result: str) -> Path:
            assert result == "passed"
            artifact = platform_root / "tests/output/artifacts/new-run_passed.zip"
            artifact.parent.mkdir(parents=True)
            with zipfile.ZipFile(artifact, "w") as archive:
                for path in output_dir.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(output_dir))
            return artifact

    monkeypatch.setattr(pipeline_evidence, "run_tests_in_dir", fake_run_tests)
    monkeypatch.setattr(
        pipeline_evidence,
        "_validate_archive",
        lambda *_args, **_kwargs: 7,
    )
    monkeypatch.setattr(
        "sts2_autotest.evidence.packager.EvidencePackager",
        FakePackager,
    )
    args = argparse.Namespace(
        project_dir=str(project),
        suite=str(suite),
        run_id="new-run",
        timeout=60,
        debug_actions=False,
    )

    result = pipeline_evidence.run_pipeline_evidence(args)

    assert result["status"] == "PASSED"
    manifest = json.loads(
        (output_dir / "reports/pipeline-evidence.json").read_text(encoding="utf-8")
    )
    assert manifest["trace_files"] == [
        "reports/case-traces/suite/TC-A/case.log"
    ]


def test_generic_script_has_no_project_specific_defaults() -> None:
    source = _SCRIPT.read_text(encoding="utf-8").upper()
    assert "GAWAIN" not in source
    assert "STS2-GAWAIN" not in source
