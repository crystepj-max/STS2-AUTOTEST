"""Unit tests for evidence.logs — LogCollector (Story 3-2)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from sts2_autotest.evidence.logs import (
    LogCollectionResult,
    LogCollector,
    LogEntry,
    _GODOT_PATTERN,
)


# ── LogEntry / LogCollectionResult ──────────────────────────

class TestLogEntry:
    def test_create(self) -> None:
        entry = LogEntry(timestamp="2025-01-01 12:00:00.000", level="ERROR", message="oops")
        assert entry.level == "ERROR"
        assert entry.message == "oops"

    def test_equality(self) -> None:
        a = LogEntry(timestamp="t", level="ERROR", message="m")
        b = LogEntry(timestamp="t", level="ERROR", message="m")
        assert a == b


class TestLogCollectionResult:
    def test_ok_when_dest_path(self) -> None:
        r = LogCollectionResult(dest_path=Path("/tmp/x.log"))
        assert r.ok

    def test_not_ok_when_no_dest(self) -> None:
        r = LogCollectionResult()
        assert not r.ok


# ── Godot pattern ───────────────────────────────────────────

class TestGodotPattern:
    def test_error_line(self) -> None:
        m = _GODOT_PATTERN.match("[2025-01-15 10:30:45.123] ERROR: Something broke")
        assert m is not None
        assert m.group(2) == "ERROR"
        assert m.group(3) == "Something broke"

    def test_warn_line(self) -> None:
        m = _GODOT_PATTERN.match("[2025-01-15 10:30:45.123] WARN: Low memory")
        assert m is not None
        assert m.group(2) == "WARN"

    def test_warning_level(self) -> None:
        m = _GODOT_PATTERN.match("[2025-01-15 10:30:45.123] WARNING: Deprecated")
        assert m is not None
        assert m.group(2) == "WARNING"

    def test_info_line(self) -> None:
        m = _GODOT_PATTERN.match("[2025-01-15 10:30:45.123] INFO: Game started")
        assert m is not None
        assert m.group(2) == "INFO"

    def test_non_matching_line(self) -> None:
        m = _GODOT_PATTERN.match("random text without format")
        assert m is None

    def test_debug_line(self) -> None:
        m = _GODOT_PATTERN.match("[2025-01-15 10:30:45.123] DEBUG: var=42")
        assert m is not None
        assert m.group(2) == "DEBUG"

    def test_timestamp_format(self) -> None:
        m = _GODOT_PATTERN.match("[2025-01-15 10:30:45.123] ERROR: x")
        assert m is not None
        assert m.group(1) == "2025-01-15 10:30:45.123"


# ── LogCollector init ───────────────────────────────────────

class TestLogCollectorInit:
    def test_defaults(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path)
        assert lc._log_levels == {"ERROR", "WARN", "WARNING"}
        assert lc._max_entries == 10000

    def test_custom_levels(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path, log_levels="ERROR,INFO")
        assert lc._log_levels == {"ERROR", "INFO"}

    def test_custom_log_dir(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom_logs"
        lc = LogCollector(tmp_path, log_dir=custom)
        assert lc._log_dir == custom

    def test_empty_levels_collects_all(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path, log_levels="")
        assert lc._log_levels == set()


# ── _find_latest_log ────────────────────────────────────────

class TestFindLatestLog:
    def test_finds_latest(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "godot_logs"
        log_dir.mkdir()
        (log_dir / "old.log").write_text("old", encoding="utf-8")
        import time
        time.sleep(0.05)
        (log_dir / "new.log").write_text("new", encoding="utf-8")

        lc = LogCollector(tmp_path, log_dir=log_dir)
        result = lc._find_latest_log()
        assert result is not None
        assert result.name == "new.log"

    def test_no_log_dir(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path, log_dir=tmp_path / "nonexistent")
        assert lc._find_latest_log() is None

    def test_empty_log_dir(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "empty"
        log_dir.mkdir()
        lc = LogCollector(tmp_path, log_dir=log_dir)
        assert lc._find_latest_log() is None

    def test_ignores_non_log_files(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "mixed"
        log_dir.mkdir()
        (log_dir / "readme.txt").write_text("hi", encoding="utf-8")
        lc = LogCollector(tmp_path, log_dir=log_dir)
        assert lc._find_latest_log() is None


# ── _parse_log ──────────────────────────────────────────────

class TestParseLog:
    def test_parse_error_lines(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(
            "[2025-01-15 10:30:45.123] ERROR: Crash\n"
            "[2025-01-15 10:30:46.456] INFO: Ignored\n"
            "[2025-01-15 10:30:47.789] WARN: Warning\n",
            encoding="utf-8",
        )
        lc = LogCollector(tmp_path, log_levels="ERROR,WARN")
        entries, total = lc._parse_log(log_file)
        assert total == 3
        assert len(entries) == 2
        assert entries[0].level == "ERROR"
        assert entries[1].level == "WARN"

    def test_empty_log_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "empty.log"
        log_file.write_text("", encoding="utf-8")
        lc = LogCollector(tmp_path)
        entries, total = lc._parse_log(log_file)
        assert total == 0
        assert len(entries) == 0

    def test_non_godot_lines_skipped(self, tmp_path: Path) -> None:
        log_file = tmp_path / "mixed.log"
        log_file.write_text(
            "random line\n"
            "[2025-01-15 10:30:45.123] ERROR: Real entry\n"
            "another random\n",
            encoding="utf-8",
        )
        lc = LogCollector(tmp_path, log_levels="ERROR")
        entries, total = lc._parse_log(log_file)
        assert total == 3
        assert len(entries) == 1
        assert entries[0].message == "Real entry"

    def test_max_entries_limit(self, tmp_path: Path) -> None:
        lines = [
            f"[2025-01-15 10:30:45.{i:03d}] ERROR: Error {i}"
            for i in range(20)
        ]
        log_file = tmp_path / "big.log"
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        lc = LogCollector(tmp_path, log_levels="ERROR", max_entries=5)
        entries, total = lc._parse_log(log_file)
        assert total == 20
        assert len(entries) == 5

    def test_unreadable_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.log"
        lc = LogCollector(tmp_path)
        entries, total = lc._parse_log(missing)
        assert total == 0
        assert len(entries) == 0

    def test_all_levels_collected_when_no_filter(self, tmp_path: Path) -> None:
        log_file = tmp_path / "all.log"
        log_file.write_text(
            "[2025-01-15 10:30:45.123] DEBUG: d\n"
            "[2025-01-15 10:30:45.124] INFO: i\n"
            "[2025-01-15 10:30:45.125] ERROR: e\n",
            encoding="utf-8",
        )
        lc = LogCollector(tmp_path, log_levels="")
        entries, total = lc._parse_log(log_file)
        assert total == 3
        assert len(entries) == 3


# ── _copy_log ───────────────────────────────────────────────

class TestCopyLog:
    def test_copies_file(self, tmp_path: Path) -> None:
        src = tmp_path / "source.log"
        src.write_text("log content", encoding="utf-8")
        out = tmp_path / "output"
        lc = LogCollector(out, log_dir=tmp_path)
        dest = lc._copy_log(src, "case1")
        assert dest.is_file()
        assert dest.read_text(encoding="utf-8") == "log content"

    def test_filename_contains_case_id(self, tmp_path: Path) -> None:
        src = tmp_path / "source.log"
        src.write_text("x", encoding="utf-8")
        lc = LogCollector(tmp_path / "out", log_dir=tmp_path)
        dest = lc._copy_log(src, "my_case")
        assert dest.name.startswith("my_case_")

    def test_creates_output_dir(self, tmp_path: Path) -> None:
        src = tmp_path / "source.log"
        src.write_text("x", encoding="utf-8")
        out = tmp_path / "deep" / "nested" / "output"
        lc = LogCollector(out, log_dir=tmp_path)
        dest = lc._copy_log(src, "case1")
        assert out.is_dir()
        assert dest.is_file()


# ── _write_filtered_log ─────────────────────────────────────

class TestWriteFilteredLog:
    def test_writes_entries(self, tmp_path: Path) -> None:
        entries = [
            LogEntry(timestamp="2025-01-15 10:30:45.123", level="ERROR", message="crash"),
            LogEntry(timestamp="2025-01-15 10:30:46.456", level="WARN", message="low mem"),
        ]
        lc = LogCollector(tmp_path)
        dest = lc._write_filtered_log(entries, "case1")
        assert dest.is_file()
        content = dest.read_text(encoding="utf-8")
        assert "ERROR" in content
        assert "WARN" in content
        assert "crash" in content

    def test_filename_has_filtered(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path)
        dest = lc._write_filtered_log([], "case1")
        assert "_filtered_" in dest.name

    def test_empty_entries(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path)
        dest = lc._write_filtered_log([], "case1")
        assert dest.is_file()
        content = dest.read_text(encoding="utf-8")
        assert content.strip() == ""

    def test_atomic_write_no_temp_left(self, tmp_path: Path) -> None:
        entries = [LogEntry(timestamp="t", level="ERROR", message="m")]
        lc = LogCollector(tmp_path)
        lc._write_filtered_log(entries, "case1")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0


# ── collect (integration) ───────────────────────────────────

class TestCollect:
    def test_collect_with_log_file(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "godot"
        log_dir.mkdir()
        (log_dir / "game.log").write_text(
            "[2025-01-15 10:30:45.123] ERROR: Crash\n", encoding="utf-8"
        )
        out = tmp_path / "evidence"
        lc = LogCollector(out, log_dir=log_dir, log_levels="ERROR")
        result = lc.collect("case1")
        assert result.ok
        assert result.source_path is not None
        assert result.dest_path is not None
        assert result.total_lines == 1
        assert result.matched_lines == 1

    def test_collect_no_log_file(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path, log_dir=tmp_path / "missing")
        result = lc.collect("case1")
        assert not result.ok


# ── collect_on_failure ──────────────────────────────────────

class TestCollectOnFailure:
    def test_failure_collects_filtered(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "godot"
        log_dir.mkdir()
        (log_dir / "game.log").write_text(
            "[2025-01-15 10:30:45.123] ERROR: Crash\n"
            "[2025-01-15 10:30:46.456] INFO: Ignored\n"
            "[2025-01-15 10:30:47.789] WARN: Warning\n",
            encoding="utf-8",
        )
        out = tmp_path / "evidence"
        lc = LogCollector(out, log_dir=log_dir, log_levels="ERROR,WARN")
        result = lc.collect_on_failure("case1")
        assert result.ok
        assert result.matched_lines == 2
        assert "_filtered_" in str(result.dest_path)

    def test_failure_no_log(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path, log_dir=tmp_path / "missing")
        result = lc.collect_on_failure("case1")
        assert not result.ok
