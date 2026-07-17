"""P0 证据封存与历史报告读取回归检查。"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from sts2_autotest.cli.main import _write_journey_evidence
from sts2_autotest.cli.mcp_tools import read_run_report
from sts2_autotest.core.run_service import RunRequest, RunStore
from sts2_autotest.evidence.packager import EvidencePackager


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_final_manifest_matches_the_resealed_archive(tmp_path: Path) -> None:
    packager = EvidencePackager(tmp_path)
    run_id = "run-p0-manifest"
    pack_dir = packager.create_pack(run_id, run_result="passed", duration_ms=1234)
    (pack_dir / "screenshots" / "scene.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (pack_dir / "screenshots" / "scene.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (pack_dir / "logs" / "game.log").write_text("log\n", encoding="utf-8")
    _write_json(pack_dir / "reports" / "run-result.json", {"status": "PASSED"})

    first_zip = packager.export_artifact(run_id, result="passed")
    assert first_zip is not None
    _write_journey_evidence(
        tmp_path,
        run_id,
        journey="goal_scene",
        target_scene="MAP",
        evidence={"scene_trajectory": ["MAP"], "operations": [], "map_route": []},
        duration_ms=1234,
    )
    final_zip = packager.export_artifact(run_id, result="passed")
    assert final_zip == first_zip

    manifest = json.loads(
        (pack_dir / "reports" / "evidence-manifest.json").read_text(encoding="utf-8")
    )
    with zipfile.ZipFile(final_zip) as archive:
        names = archive.namelist()
    zip_screenshots = [
        name for name in names
        if name.startswith("screenshots/") and Path(name).suffix.lower() in {".png", ".jpg", ".jpeg"}
    ]
    zip_logs = [name for name in names if name.startswith("logs/") and name.endswith(".log")]
    assert manifest["screenshot_count"] == 2
    assert manifest["log_count"] == 1
    assert len(zip_screenshots) == manifest["screenshot_count"]
    assert len(zip_logs) == manifest["log_count"]
    assert manifest["archive"]["counts_match"] is True
    assert manifest["artifact_path"] == str(final_zip.resolve())


def test_get_report_reads_from_archive_after_source_directory_cleanup(
    tmp_path: Path, monkeypatch,
) -> None:
    run_id = "run-p0-persistent"
    run_root = tmp_path / "runs"
    evidence_root = tmp_path / "evidence"
    monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(run_root))
    monkeypatch.setenv("STS2_AUTOTEST_EVIDENCE_DIR", str(evidence_root))

    store = RunStore(run_root)
    record = store.create(
        RunRequest(timeout=60, evidence="full"),
        run_id=run_id,
    )
    packager = EvidencePackager(evidence_root)
    pack_dir = packager.create_pack(run_id, run_result="passed", duration_ms=4321)
    _write_json(
        pack_dir / "reports" / "journey-trace.json",
        {
            "journey": "goal_scene",
            "target_scene": "MAP",
            "duration_ms": 4321,
            "scene_trajectory": ["MAIN_MENU", "MAP"],
            "operations": [{"state": {"large": "x" * 100000}}],
            "map_route": [],
        },
    )
    _write_json(
        pack_dir / "reports" / "evidence-manifest.json",
        {"screenshot_count": 0, "log_count": 0, "existing_files": []},
    )
    _write_json(pack_dir / "reports" / "run-result.json", {"status": "PASSED"})
    artifact = packager.export_artifact(run_id, result="passed")
    assert artifact is not None and artifact.is_file()
    store.update(
        record.run_id,
        status="PASSED",
        phase="COMPLETED",
        evidence_dir=str(pack_dir),
        result={"status": "PASSED", "artifact_path": str(artifact)},
    )

    shutil.rmtree(pack_dir)
    report = read_run_report(run_id)

    assert report["evidence_pack_url"] == str(artifact.resolve())
    assert report["artifact_status"] == {
        "exists": True,
        "path": str(artifact.resolve()),
        "readable": True,
    }
    assert report["journey_trace"]["operation_count"] == 1
    assert "operations" not in report["journey_trace"]
    assert report["summary"]["test_run"]["duration_ms"] == 4321


def test_get_report_keeps_scalar_final_state_but_drops_embedded_state(
    tmp_path: Path, monkeypatch,
) -> None:
    run_id = "run-p0-final-state"
    run_root = tmp_path / "runs"
    evidence_root = tmp_path / "evidence"
    monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(run_root))
    monkeypatch.setenv("STS2_AUTOTEST_EVIDENCE_DIR", str(evidence_root))

    store = RunStore(run_root)
    record = store.create(
        RunRequest(timeout=60, evidence="full"),
        run_id=run_id,
    )
    store.update(
        record.run_id,
        status="PASSED",
        phase="COMPLETED",
        result={
            "status": "PASSED",
            "final_state": "GAME_OVER",
            "last_state": {"screen": "GAME_OVER", "blob": "x" * 100000},
        },
    )

    report = read_run_report(run_id)

    result = report["run"]["result"]
    # 标量 final_state 是精简报告的关键结果字段，必须保留；
    # dict 形态的 last_state/final_state 可能嵌入完整巨大状态，必须剔除。
    assert result["final_state"] == "GAME_OVER"
    assert "last_state" not in result
