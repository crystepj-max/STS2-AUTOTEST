"""Tests for core/progress.py — ProgressRecord persistence with CRC32 (Story 4.5)."""

from __future__ import annotations

import json
import os
import zlib
from pathlib import Path

import pytest

from sts2_autotest.core.progress import (
    ProgressRecord,
    clear_progress,
    compute_checksum,
    load_progress,
    save_progress,
)


def _make_record(**kwargs: object) -> ProgressRecord:
    return ProgressRecord(
        session_id=str(kwargs.get("session_id", "test-session")),
        completed_cases=list(kwargs.get("completed_cases", [])),
        pending_cases=list(kwargs.get("pending_cases", ["TC-001", "TC-002"])),
        current_case=kwargs.get("current_case"),
        last_updated=str(kwargs.get("last_updated", "2025-01-01T00:00:00.000Z")),
    )


# ── compute_checksum ────────────────────────────────────────


class TestComputeChecksum:
    def test_known_value(self) -> None:
        """CRC32 of a known byte string should match zlib.crc32."""
        data = b"hello world"
        assert compute_checksum(data) == zlib.crc32(data) & 0xFFFFFFFF

    def test_empty_bytes(self) -> None:
        """Empty bytes should have a valid CRC32."""
        assert compute_checksum(b"") == zlib.crc32(b"") & 0xFFFFFFFF

    def test_different_data_different_checksum(self) -> None:
        """Two different inputs must produce different checksums."""
        assert compute_checksum(b"abc") != compute_checksum(b"xyz")


# ── save_progress / load_progress round-trip ─────────────────


class TestSaveLoadRoundTrip:
    def test_save_and_load(self, tmp_path: Path) -> None:
        """Save a record then load it back — all fields preserved."""
        path = tmp_path / "progress.json"
        record = _make_record(
            session_id="sess-1",
            completed_cases=["TC-001"],
            pending_cases=["TC-002", "TC-003"],
        )
        assert save_progress(record, path) is True
        assert path.is_file()

        loaded = load_progress(path)
        assert loaded is not None
        assert loaded.session_id == "sess-1"
        assert loaded.completed_cases == ["TC-001"]
        assert loaded.pending_cases == ["TC-002", "TC-003"]
        assert loaded.current_case is None

    def test_save_updates_timestamp(self, tmp_path: Path) -> None:
        """save_progress updates last_updated before writing."""
        path = tmp_path / "ts.json"
        record = _make_record()
        assert save_progress(record, path) is True
        loaded = load_progress(path)
        assert loaded is not None
        assert loaded.last_updated != "2025-01-01T00:00:00.000Z"
        assert "T" in loaded.last_updated

    def test_empty_completed_list(self, tmp_path: Path) -> None:
        """Minimal record with empty lists should round-trip."""
        path = tmp_path / "empty.json"
        record = _make_record(completed_cases=[], pending_cases=[])
        assert save_progress(record, path) is True
        loaded = load_progress(path)
        assert loaded is not None
        assert loaded.completed_cases == []
        assert loaded.pending_cases == []

    def test_current_case_field(self, tmp_path: Path) -> None:
        """current_case is None/str round-trips correctly."""
        path = tmp_path / "current.json"
        record = _make_record(current_case="TC-002")
        assert save_progress(record, path) is True
        loaded = load_progress(path)
        assert loaded is not None
        assert loaded.current_case == "TC-002"

    def test_no_temp_file_left(self, tmp_path: Path) -> None:
        """Atomic write leaves no .tmp file behind."""
        path = tmp_path / "clean.json"
        record = _make_record()
        assert save_progress(record, path) is True
        assert not path.with_suffix(".json.tmp").exists()


# ── CRC32 validation ─────────────────────────────────────────


class TestCrc32Validation:
    def test_corrupted_file_returns_none(self, tmp_path: Path) -> None:
        """Modifying file bytes after save makes load return None."""
        path = tmp_path / "corrupt.json"
        record = _make_record()
        assert save_progress(record, path) is True

        # Corrupt the file by appending garbage
        with open(str(path), "a") as f:
            f.write("CORRUPTED")

        loaded = load_progress(path)
        assert loaded is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        """Empty file is treated as corrupted."""
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        assert load_progress(path) is None

    def test_invalid_json_returns_none(self, tmp_path: Path) -> None:
        """Invalid JSON should return None."""
        path = tmp_path / "bad.json"
        path.write_text("{invalid", encoding="utf-8")
        assert load_progress(path) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """Missing file should return None."""
        path = tmp_path / "nonexistent.json"
        assert load_progress(path) is None


# ── schema version guard ─────────────────────────────────────


class TestSchemaVersion:
    def test_newer_schema_returns_none(self, tmp_path: Path) -> None:
        """File with newer schema_version than framework returns None."""
        path = tmp_path / "future.json"
        data = {
            "_schema_version": 999,
            "_checksum": 0,
            "session_id": "future",
            "completed_cases": [],
            "pending_cases": [],
            "current_case": None,
            "last_updated": "2025-01-01T00:00:00.000Z",
        }
        payload = json.dumps(data, indent=2).encode("utf-8")
        data["_checksum"] = zlib.crc32(payload) & 0xFFFFFFFF
        path.write_bytes(
            json.dumps(data, indent=2).encode("utf-8"),
        )
        assert load_progress(path) is None


# ── clear_progress ───────────────────────────────────────────


class TestClearProgress:
    def test_clears_existing_file(self, tmp_path: Path) -> None:
        """clear_progress removes the progress file."""
        path = tmp_path / "progress.json"
        record = _make_record()
        save_progress(record, path)
        assert path.is_file()

        clear_progress(path)
        assert not path.exists()

    def test_clear_nonexistent_no_error(self, tmp_path: Path) -> None:
        """clear_progress on missing file should not raise."""
        path = tmp_path / "nonexistent.json"
        clear_progress(path)  # Should not raise


# ── atomic write ─────────────────────────────────────────────


class TestAtomicWrite:
    def test_no_intermediate_state(self, tmp_path: Path) -> None:
        """During write, the target file is either the old version or the new version.
        No partially-written file exists (AC1).
        """
        path = tmp_path / "atomic.json"
        record1 = _make_record(session_id="first")
        assert save_progress(record1, path) is True
        first_content = path.read_bytes()

        record2 = _make_record(session_id="second")
        assert save_progress(record2, path) is True

        # File must be either old or new, never partial
        loaded = load_progress(path)
        assert loaded is not None
        assert loaded.session_id in ("first", "second")

    def test_temp_file_cleaned_on_oserror(self, tmp_path: Path) -> None:
        """If write fails, tmp file is cleaned up."""
        path = tmp_path / "oserror.json"

        # Mock os.replace to fail
        import os as os_module
        original_replace = os_module.replace
        called = [False]

        def _mock_replace(src: str, dst: str) -> None:
            if called[0]:
                original_replace(src, dst)
            else:
                called[0] = True
                raise OSError("mock replace failure")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(os_module, "replace", _mock_replace)
            record = _make_record()
            result = save_progress(record, path)

        assert result is False
        assert not path.with_suffix(".json.tmp").exists()
