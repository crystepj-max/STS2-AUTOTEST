"""Unit tests for evidence.packager — EvidencePackager (Story 3-3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sts2_autotest.common.evidence import FailureInfo
from sts2_autotest.evidence.packager import EvidencePackager


# ── create_pack ─────────────────────────────────────────────

class TestCreatePack:
    def test_creates_directory_structure(self, tmp_path: Path) -> None:
        evidence_dir = tmp_path / "evidence"
        pkgr = EvidencePackager(evidence_dir)
        pack_dir = pkgr.create_pack("run_001")

        assert pack_dir.is_dir()
        assert (pack_dir / "screenshots").is_dir()
        assert (pack_dir / "logs").is_dir()
        assert (pack_dir / "reports").is_dir()
        assert (pack_dir / "summary.json").is_file()

    def test_summary_json_content(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path, framework="fw", adapter="cli", game="game")
        pkgr.create_pack("run_002", run_result="passed", duration_ms=500)

        data = json.loads((tmp_path / "run_002" / "summary.json").read_text(encoding="utf-8"))
        assert data["pack_id"] == "run_002"
        assert data["test_run"]["result"] == "passed"
        assert data["test_run"]["duration_ms"] == 500
        assert data["environment"]["framework"] == "fw"
        assert data["environment"]["adapter"] == "cli"
        assert data["environment"]["game"] == "game"

    def test_auto_pack_id(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pack_dir = pkgr.create_pack()
        assert pack_dir.name.startswith("run_")
        assert (pack_dir / "summary.json").is_file()

    def test_with_failure_info(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        failure = FailureInfo(type="AssertionError", message="Expected 5 got 3")
        pkgr.create_pack("run_003", failure=failure)

        data = json.loads((tmp_path / "run_003" / "summary.json").read_text(encoding="utf-8"))
        assert data["failure"]["type"] == "AssertionError"
        assert data["failure"]["message"] == "Expected 5 got 3"

    def test_idempotent_create(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_004")
        pkgr.create_pack("run_004")  # should not raise
        assert (tmp_path / "run_004" / "summary.json").is_file()

    def test_summary_has_schema_version(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_005")
        data = json.loads((tmp_path / "run_005" / "summary.json").read_text(encoding="utf-8"))
        assert "schema_version" in data

    def test_summary_has_python_version(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_006")
        data = json.loads((tmp_path / "run_006" / "summary.json").read_text(encoding="utf-8"))
        import platform
        assert data["environment"]["python"] == platform.python_version()


# ── copy_artifacts ──────────────────────────────────────────

class TestCopyArtifacts:
    def test_copy_screenshots(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_010")

        ss = tmp_path / "ss.png"
        ss.write_bytes(b"\x89PNG")
        pkgr.copy_artifacts("run_010", screenshots=[ss])

        assert (tmp_path / "run_010" / "screenshots" / "ss.png").is_file()

    def test_copy_logs(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_011")

        log = tmp_path / "game.log"
        log.write_text("log data", encoding="utf-8")
        pkgr.copy_artifacts("run_011", logs=[log])

        assert (tmp_path / "run_011" / "logs" / "game.log").is_file()

    def test_updates_summary_artifacts(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_012")

        ss = tmp_path / "shot.png"
        ss.write_bytes(b"\x89PNG")
        log = tmp_path / "game.log"
        log.write_text("data", encoding="utf-8")
        pkgr.copy_artifacts("run_012", screenshots=[ss], logs=[log])

        data = json.loads((tmp_path / "run_012" / "summary.json").read_text(encoding="utf-8"))
        assert "shot.png" in data["artifacts"]["screenshots"]
        assert "game.log" in data["artifacts"]["logs"]

    def test_missing_pack_raises(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        with pytest.raises(FileNotFoundError, match="not found"):
            pkgr.copy_artifacts("nonexistent", screenshots=[])

    def test_skips_nonexistent_files(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_013")

        missing = tmp_path / "missing.png"
        pkgr.copy_artifacts("run_013", screenshots=[missing])
        # Should not raise, just skip
        assert not (tmp_path / "run_013" / "screenshots" / "missing.png").exists()


# ── read_summary ────────────────────────────────────────────

class TestReadSummary:
    def test_reads_valid_summary(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_020", run_result="passed", duration_ms=100)

        summary = pkgr.read_summary("run_020")
        assert summary is not None
        assert summary.pack_id == "run_020"
        assert summary.test_run.result == "passed"

    def test_missing_pack_returns_none(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        assert pkgr.read_summary("nonexistent") is None

    def test_invalid_json_returns_none(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "bad_pack"
        pack_dir.mkdir()
        (pack_dir / "summary.json").write_text("not json", encoding="utf-8")
        pkgr = EvidencePackager(tmp_path)
        assert pkgr.read_summary("bad_pack") is None


# ── list_packs ──────────────────────────────────────────────

class TestListPacks:
    def test_lists_existing_packs(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_a")
        pkgr.create_pack("run_b")
        packs = pkgr.list_packs()
        assert "run_a" in packs
        assert "run_b" in packs

    def test_empty_dir(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        assert pkgr.list_packs() == []

    def test_ignores_dirs_without_summary(self, tmp_path: Path) -> None:
        (tmp_path / "no_summary").mkdir()
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_valid")
        packs = pkgr.list_packs()
        assert packs == ["run_valid"]

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path / "missing")
        assert pkgr.list_packs() == []


# ── retention ───────────────────────────────────────────────

class TestRetention:
    def test_enforces_retention(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path, retention=2)
        pkgr.create_pack("run_01")
        pkgr.create_pack("run_02")
        pkgr.create_pack("run_03")

        # run_01 should be removed since retention=2
        assert not (tmp_path / "run_01").is_dir()
        assert (tmp_path / "run_02").is_dir()
        assert (tmp_path / "run_03").is_dir()

    def test_no_removal_when_under_limit(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path, retention=10)
        pkgr.create_pack("run_01")
        pkgr.create_pack("run_02")
        assert (tmp_path / "run_01").is_dir()
        assert (tmp_path / "run_02").is_dir()
