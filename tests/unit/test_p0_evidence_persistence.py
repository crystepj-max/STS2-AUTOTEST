"""P0 证据封存与历史报告读取回归检查。"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from sts2_autotest.cli.main import _write_journey_evidence
from sts2_autotest.cli.mcp_tools import read_run_report
from sts2_autotest.core.evidence_hooks import build_evidence_hooks
from sts2_autotest.core.run_service import RunRequest, RunStore
from sts2_autotest.evidence.packager import EvidencePackager


def _place_declared_files(pack_dir: Path, *, with_log: bool = True) -> None:
    """把 _write_journey_evidence 声明的文件全部预置到 pack_dir，避免清单记 missing。"""
    _write_json(pack_dir / "summary.json", {"status": "CANCELLED"})
    _write_json(pack_dir / "summary.md", {"note": "x"})
    _write_json(pack_dir / "reports" / "journey-trace.json", {"journey": "first_battle"})
    _write_json(pack_dir / "reports" / "junit.xml", {"x": 1})
    _write_json(pack_dir / "reports" / "run-result.json", {"status": "CANCELLED"})
    if with_log:
        (pack_dir / "logs").mkdir(parents=True, exist_ok=True)
        (pack_dir / "logs" / "game.log").write_text("log content", encoding="utf-8")


def test_cancel_seal_order_leaves_consistent_internal_manifest(tmp_path: Path) -> None:
    """P0 修复回归：取消收尾的最终封存必须让压缩包内证据清单与真实内容一致。

    复刻取消 handler 顺序：写 run-result.json → 生成清单 → on_session_end 封存
    → persist → 最终清单 → refresh_artifact 最终封存。最终解压核对内部清单
    missing_files==[] 且 archive.status=='verified'、日志数与压缩包一致。
    """
    run_id = "run-p0-cancel-seal"
    packager = EvidencePackager(tmp_path)
    pack_dir = packager.create_pack(run_id, run_result="cancelled", duration_ms=999)
    _place_declared_files(pack_dir, with_log=True)

    hooks = build_evidence_hooks(tmp_path, pack_id=run_id)
    # 1) 首次清单（run-result.json 已存在）
    _write_journey_evidence(
        tmp_path, run_id, journey="first_battle", target_scene="MAP",
        evidence={"scene_trajectory": ["MAIN_MENU", "MAP"]}, duration_ms=999,
    )
    # 2) on_session_end → 封存（SEAL #1）
    hooks.on_session_end({
        "total": 1, "passed": 0, "failed": 0, "crashed": 0, "skipped": 1,
        "duration_ms": 999, "status": "CANCELLED",
    })
    # 3) persist_artifact_path：把压缩包路径写回 run-result.json
    zips = sorted(tmp_path.glob(f"artifacts/{run_id}_*.zip"))
    assert zips, "on_session_end 应已生成压缩包"
    payload = json.loads((pack_dir / "reports" / "run-result.json").read_text(encoding="utf-8"))
    payload["artifact_path"] = str(zips[-1].resolve())
    payload["evidence_pack_url"] = str(zips[-1].resolve())
    (pack_dir / "reports" / "run-result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    # 4) 最终清单（压缩包已存在 → archive 可校验）
    _write_journey_evidence(
        tmp_path, run_id, journey="first_battle", target_scene="MAP",
        evidence={"scene_trajectory": ["MAIN_MENU", "MAP"]}, duration_ms=999,
    )
    # 5) refresh_artifact → 最终封存（SEAL #2，必须排在最终清单之后）
    hooks.refresh_artifact()

    final_zip = sorted(tmp_path.glob(f"artifacts/{run_id}_*.zip"))[-1]
    with zipfile.ZipFile(final_zip) as archive:
        inner = next(
            n for n in archive.namelist() if n.endswith("evidence-manifest.json")
        )
        manifest = json.loads(archive.read(inner))

    assert manifest["missing_files"] == [], manifest["missing_files"]
    assert manifest["log_count"] == 1
    assert manifest["archive"]["status"] == "verified"
    assert manifest["archive"]["counts_match"] is True
    # 清单自身也应被封入压缩包
    assert any(n.endswith("evidence-manifest.json") for n in archive.namelist())


def test_cancel_seal_before_final_manifest_is_stale_regression(tmp_path: Path) -> None:
    """反向守卫：复刻原始 bug 顺序，证明压缩包内清单会停留在陈旧快照。

    原始 bug：先生成清单（此时 run-result.json 尚不存在 → 被记为缺失），
    再 on_session_end 封存 + refresh_artifact 二次封存（均未重算清单），
    最后才落盘 run-result.json 并重算清单（但不再 reseal）。结果压缩包内
    清单声称 run-result.json 缺失，与压缩包真实内容矛盾。
    """
    run_id = "run-p0-cancel-stale"
    packager = EvidencePackager(tmp_path)
    pack_dir = packager.create_pack(run_id, run_result="cancelled", duration_ms=999)
    # 预置除 run-result.json 外的声明文件，复刻「清单在 run-result.json 落盘前生成」
    _write_json(pack_dir / "summary.json", {"status": "CANCELLED"})
    _write_json(pack_dir / "summary.md", {"note": "x"})
    _write_json(pack_dir / "reports" / "journey-trace.json", {"journey": "first_battle"})
    _write_json(pack_dir / "reports" / "junit.xml", {"x": 1})
    (pack_dir / "logs").mkdir(parents=True, exist_ok=True)
    (pack_dir / "logs" / "game.log").write_text("log content", encoding="utf-8")

    hooks = build_evidence_hooks(tmp_path, pack_id=run_id)
    # 1) 先生成清单（run-result.json 尚不存在 → 记缺失）
    _write_journey_evidence(
        tmp_path, run_id, journey="first_battle", target_scene="MAP",
        evidence={"scene_trajectory": ["MAIN_MENU", "MAP"]}, duration_ms=999,
    )
    # 2) on_session_end 封存（SEAL #1，含陈旧清单）
    hooks.on_session_end({
        "total": 1, "passed": 0, "failed": 0, "crashed": 0, "skipped": 1,
        "duration_ms": 999, "status": "CANCELLED",
    })
    # 3) refresh_artifact 再次封存（SEAL #2，仍是陈旧清单，未重算）
    hooks.refresh_artifact()
    # 4) 此刻才落盘 run-result.json + 重算清单，但不再 reseal
    _write_json(pack_dir / "reports" / "run-result.json", {"status": "CANCELLED"})
    _write_journey_evidence(
        tmp_path, run_id, journey="first_battle", target_scene="MAP",
        evidence={"scene_trajectory": ["MAIN_MENU", "MAP"]}, duration_ms=999,
    )

    stale_zip = sorted(tmp_path.glob(f"artifacts/{run_id}_*.zip"))[-1]
    with zipfile.ZipFile(stale_zip) as archive:
        inner = next(
            n for n in archive.namelist() if n.endswith("evidence-manifest.json")
        )
        manifest = json.loads(archive.read(inner))
    # 压缩包内清单停留在陈旧快照：run-result.json 被记为缺失（或 archive 不可用）
    inconsistent = (
        "reports/run-result.json" in manifest["missing_files"]
        or manifest["archive"]["status"] != "verified"
    )
    assert inconsistent, "坏顺序竟产生了自洽清单，说明守卫假设失效"



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
