"""Unit tests for evidence.packager — EvidencePackager (Story 3-3)."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from sts2_autotest.common.evidence import FailureInfo, SCHEMA_VERSION
from sts2_autotest.common.types import EvidencePackagerSettings
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

    def test_create_pack_auto_generates_summary_md(self, tmp_path: Path) -> None:
        """AC6 regression: create_pack() automatically produces summary.md."""
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_ac6_auto")
        assert (tmp_path / "run_ac6_auto" / "summary.md").is_file()

    def test_create_pack_with_failure_auto_generates_md(self, tmp_path: Path) -> None:
        """AC6 regression: even failed packs get summary.md automatically."""
        pkgr = EvidencePackager(tmp_path)
        failure = FailureInfo(type="AssertionError", message="x != y")
        pkgr.create_pack("run_ac6_fail", run_result="failed", failure=failure)
        assert (tmp_path / "run_ac6_fail" / "summary.md").is_file()
        content = (tmp_path / "run_ac6_fail" / "summary.md").read_text(encoding="utf-8")
        assert "FAIL" in content


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

    def test_copy_artifacts_refreshes_summary_md(self, tmp_path: Path) -> None:
        """AC6 regression: copy_artifacts() refreshes summary.md with artifact info."""
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_art_refresh")

        ss = tmp_path / "shot.png"
        ss.write_bytes(b"\x89PNG")
        pkgr.copy_artifacts("run_art_refresh", screenshots=[ss])

        content = (tmp_path / "run_art_refresh" / "summary.md").read_text(encoding="utf-8")
        assert "shot.png" in content


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


# ── schema version negotiation (AC3/FR64) ───────────────────

class TestSchemaVersionNegotiation:
    def test_same_major_version_loads(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_v1")
        summary = pkgr.load_pack("run_v1")
        assert summary.pack_id == "run_v1"
        assert summary.schema_version == SCHEMA_VERSION

    def test_higher_major_version_rejected(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "run_future"
        pack_dir.mkdir()
        (pack_dir / "screenshots").mkdir()
        (pack_dir / "logs").mkdir()
        (pack_dir / "reports").mkdir()
        # Write a summary with a future major version
        data = {
            "schema_version": "99.0.0",
            "pack_id": "run_future",
            "test_run": {"run_id": "run_future", "result": "passed", "duration_ms": 100},
            "environment": {
                "framework": "fw", "adapter": "cli", "game": "game",
                "os": "test", "python": "3.11",
            },
        }
        (pack_dir / "summary.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        pkgr = EvidencePackager(tmp_path)
        with pytest.raises(ValueError, match="upgrade the framework"):
            pkgr.load_pack("run_future")

    def test_lower_major_version_loads(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "run_old"
        pack_dir.mkdir()
        (pack_dir / "screenshots").mkdir()
        (pack_dir / "logs").mkdir()
        (pack_dir / "reports").mkdir()
        data = {
            "schema_version": "0.5.0",
            "pack_id": "run_old",
            "test_run": {"run_id": "run_old", "result": "passed", "duration_ms": 50},
            "environment": {
                "framework": "fw", "adapter": "cli", "game": "game",
                "os": "test", "python": "3.10",
            },
        }
        (pack_dir / "summary.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        pkgr = EvidencePackager(tmp_path)
        summary = pkgr.load_pack("run_old")
        assert summary.pack_id == "run_old"

    def test_missing_pack_raises_file_not_found(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        with pytest.raises(FileNotFoundError, match="not found"):
            pkgr.load_pack("nonexistent")

    def test_invalid_schema_version_treated_as_zero(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "run_bad_ver"
        pack_dir.mkdir()
        data = {
            "schema_version": "not.a.version",
            "pack_id": "run_bad_ver",
            "test_run": {"run_id": "run_bad_ver", "result": "passed", "duration_ms": 10},
            "environment": {
                "framework": "fw", "adapter": "cli", "game": "game",
                "os": "test", "python": "3.11",
            },
        }
        (pack_dir / "summary.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        pkgr = EvidencePackager(tmp_path)
        # version treated as major=0, which is <= current, should load fine
        summary = pkgr.load_pack("run_bad_ver")
        assert summary.pack_id == "run_bad_ver"


# ── generate_report (AC6/FR24) ──────────────────────────────

class TestGenerateReport:
    def test_generates_summary_md(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_r1", run_result="passed", duration_ms=500)
        report_path = pkgr.generate_report("run_r1")

        assert report_path.is_file()
        assert report_path.name == "summary.md"
        content = report_path.read_text(encoding="utf-8")
        assert "# Evidence Report: run_r1" in content
        assert "PASS" in content
        assert "500 ms" in content

    def test_report_contains_environment(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path, framework="test-fw", adapter="cli")
        pkgr.create_pack("run_r2")
        pkgr.generate_report("run_r2")

        content = (tmp_path / "run_r2" / "summary.md").read_text(encoding="utf-8")
        assert "test-fw" in content
        assert "cli" in content

    def test_report_includes_failure_details(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        failure = FailureInfo(
            type="AssertionError",
            message="Expected 5 got 3",
            stack_trace="file.py:42\nassert x == 5",
        )
        pkgr.create_pack("run_r3", run_result="failed", failure=failure)
        pkgr.generate_report("run_r3")

        content = (tmp_path / "run_r3" / "summary.md").read_text(encoding="utf-8")
        assert "FAIL" in content
        assert "AssertionError" in content
        assert "Expected 5 got 3" in content
        assert "file.py:42" in content

    def test_report_includes_artifacts(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_r4")
        ss = tmp_path / "shot.png"
        ss.write_bytes(b"\x89PNG")
        lg = tmp_path / "game.log"
        lg.write_text("log", encoding="utf-8")
        pkgr.copy_artifacts("run_r4", screenshots=[ss], logs=[lg])
        pkgr.generate_report("run_r4")

        content = (tmp_path / "run_r4" / "summary.md").read_text(encoding="utf-8")
        assert "shot.png" in content
        assert "game.log" in content

    def test_report_atomic_write(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_r5")
        pkgr.generate_report("run_r5")
        # No .tmp file left behind
        assert not (tmp_path / "run_r5" / "summary.md.tmp").exists()

    def test_report_fails_on_missing_pack(self, tmp_path: Path) -> None:
        pkgr = EvidencePackager(tmp_path)
        with pytest.raises(FileNotFoundError):
            pkgr.generate_report("nonexistent")

    def test_report_includes_expected_actual_comparison(self, tmp_path: Path) -> None:
        """AC6 regression: FAIL report contains structured expected/actual table."""
        pkgr = EvidencePackager(tmp_path)
        failure = FailureInfo(
            type="AssertionError",
            message="HP mismatch",
            expected="5",
            actual="3",
        )
        pkgr.create_pack("run_exp_act", run_result="failed", failure=failure)

        content = (tmp_path / "run_exp_act" / "summary.md").read_text(encoding="utf-8")
        assert "**Expected**" in content
        assert "**Actual**" in content
        assert "`5`" in content
        assert "`3`" in content

    def test_report_without_expected_actual_still_works(self, tmp_path: Path) -> None:
        """FailureInfo without expected/actual does not produce empty table."""
        pkgr = EvidencePackager(tmp_path)
        failure = FailureInfo(type="RuntimeError", message="timeout")
        pkgr.create_pack("run_no_ea", run_result="failed", failure=failure)

        content = (tmp_path / "run_no_ea" / "summary.md").read_text(encoding="utf-8")
        assert "FAIL" in content
        assert "**Expected**" not in content


# ── from_config ─────────────────────────────────────────────

class TestFromConfig:
    def test_constructs_from_config(self, tmp_path: Path) -> None:
        class _Cfg:
            evidence_dir = str(tmp_path)
            evidence_retention = 15

        pkgr = EvidencePackager.from_config(_Cfg())
        assert pkgr._evidence_dir == tmp_path
        assert pkgr._retention == 15

    def test_from_config_creates_pack(self, tmp_path: Path) -> None:
        class _Cfg:
            evidence_dir = str(tmp_path / "ev")
            evidence_retention = 5

        pkgr = EvidencePackager.from_config(_Cfg())
        pack_dir = pkgr.create_pack("cfg_test")
        assert pack_dir.is_dir()
        assert (pack_dir / "summary.json").is_file()


# ── disk guard integration (Story 4.4, AC3) ─────────────────

class TestDiskGuardIntegration:
    def test_low_disk_skips_summary_json(self, tmp_path: Path) -> None:
        """AC3: low disk space skips summary.json write, dirs still created."""
        pkgr = EvidencePackager(tmp_path)
        with patch(
            "sts2_autotest.evidence.packager.check_disk_space", return_value=False,
        ):
            pack_dir = pkgr.create_pack("run_guard")

        assert pack_dir.is_dir()
        assert (pack_dir / "screenshots").is_dir()
        assert (pack_dir / "logs").is_dir()
        assert (pack_dir / "reports").is_dir()
        # summary.json should NOT exist (skipped due to low disk)
        assert not (pack_dir / "summary.json").is_file()
        # summary.md should also be skipped
        assert not (pack_dir / "summary.md").is_file()

    def test_disk_ok_writes_summary(self, tmp_path: Path) -> None:
        """AC3: with enough disk space, summary files are written normally."""
        pkgr = EvidencePackager(tmp_path)
        pack_dir = pkgr.create_pack("run_guard_ok")
        assert (pack_dir / "summary.json").is_file()
        assert (pack_dir / "summary.md").is_file()


# ── artifact export (Story 4.7, FR54) ───────────────────────


class TestArtifactExport:
    def test_export_creates_zip(self, tmp_path: Path) -> None:
        """export_artifact creates a ZIP file."""
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_art", run_result="passed")
        zip_path = pkgr.export_artifact("run_art", result="passed")
        assert zip_path is not None
        assert zip_path.suffix == ".zip"
        assert zip_path.exists()

    def test_export_zip_contains_expected_files(self, tmp_path: Path) -> None:
        """ZIP contains summary.json, summary.md, screenshots/, logs/."""
        import zipfile

        pkgr = EvidencePackager(tmp_path)
        pack_dir = pkgr.create_pack("run_zip", run_result="passed")

        # Add a screenshot and log file to the pack
        (pack_dir / "screenshots" / "shot.png").write_bytes(b"\x89PNG")
        (pack_dir / "logs" / "game.log").write_text("log data", encoding="utf-8")

        zip_path = pkgr.export_artifact("run_zip", result="passed")
        assert zip_path is not None

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert "summary.json" in names
            assert "summary.md" in names
            assert "screenshots/shot.png" in names or "screenshots\\shot.png" in names
            assert "logs/game.log" in names or "logs\\game.log" in names

    def test_export_updates_summary_with_artifact_path(self, tmp_path: Path) -> None:
        """summary.json receives artifact_path after export."""
        import json

        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_artp", run_result="passed")
        zip_path = pkgr.export_artifact("run_artp", result="passed")
        assert zip_path is not None

        summary = pkgr.read_summary("run_artp")
        assert summary is not None
        assert summary.artifact_path is not None
        assert str(zip_path) in str(summary.artifact_path)

    def test_export_missing_pack_returns_none(self, tmp_path: Path) -> None:
        """Exporting a non-existent pack returns None."""
        pkgr = EvidencePackager(tmp_path)
        result = pkgr.export_artifact("nonexistent", result="passed")
        assert result is None

    def test_export_creates_junit_xml_in_reports(self, tmp_path: Path) -> None:
        """JUnit XML is generated inside the reports/ directory."""
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_junit", run_result="passed")
        pkgr.export_artifact("run_junit", result="passed")

        pack_dir = tmp_path / "run_junit"
        junit = pack_dir / "reports" / "junit.xml"
        assert junit.is_file()
        content = junit.read_text(encoding="utf-8")
        assert "testsuites" in content
        assert "testcase" in content

    def test_export_non_blocking_on_error(self, tmp_path: Path) -> None:
        """export_artifact returns None on error without raising."""
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_err", run_result="passed")

        with patch("shutil.make_archive", side_effect=OSError("mock error")):
            result = pkgr.export_artifact("run_err", result="passed")

        assert result is None

    def test_export_artifact_async_returns_job_before_zip_completion(
        self, tmp_path: Path,
    ) -> None:
        """export_artifact_async returns immediately while the pack remains readable."""
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_async", run_result="passed")
        summary_path = tmp_path / "run_async" / "summary.json"
        original_make_archive = shutil.make_archive
        archive_started = threading.Event()
        allow_archive = threading.Event()

        def delayed_make_archive(
            base_name: str,
            format: str,
            root_dir: str | None = None,
            base_dir: str | None = None,
        ) -> str:
            archive_started.set()
            allow_archive.wait(timeout=5.0)
            return original_make_archive(
                base_name, format, root_dir=root_dir, base_dir=base_dir,
            )

        with patch(
            "sts2_autotest.evidence.packager.shutil.make_archive",
            side_effect=delayed_make_archive,
        ):
            job = pkgr.export_artifact_async("run_async", result="passed")
            assert job.pack_id == "run_async"
            assert job.original_pack_dir == tmp_path / "run_async"
            assert job.status == "PENDING"
            assert summary_path.is_file()

            assert archive_started.wait(timeout=2.0)
            assert job.status == "PENDING"
            allow_archive.set()
            zip_path = job.wait(timeout=5.0)

        assert zip_path is not None
        assert zip_path.suffix == ".zip"
        assert zip_path.exists()
        assert job.status == "DONE"
        assert job.error is None
        assert summary_path.is_file()

    def test_export_artifact_async_failure_preserves_original_pack(
        self, tmp_path: Path,
    ) -> None:
        """Async export reports failures and keeps summary.json in place."""
        pkgr = EvidencePackager(tmp_path)
        pkgr.create_pack("run_async_err", run_result="passed")
        summary_path = tmp_path / "run_async_err" / "summary.json"

        with patch(
            "sts2_autotest.evidence.packager.shutil.make_archive",
            side_effect=OSError("mock zip failure"),
        ):
            job = pkgr.export_artifact_async("run_async_err", result="failed")
            result = job.wait(timeout=5.0)

        assert result is None
        assert job.status == "FAILED"
        assert job.error is not None
        assert "mock zip failure" in job.error
        assert summary_path.is_file()
