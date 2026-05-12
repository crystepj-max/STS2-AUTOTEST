"""Unit tests for evidence.logs — LogCollector (Story 3-2)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_crash_info_field(self) -> None:
        r = LogCollectionResult(crash_info="[t] ERROR: crash msg")
        assert r.crash_info is not None
        assert "crash msg" in r.crash_info


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
        assert lc._custom_paths == []
        assert lc._lock_retries == 5
        assert lc._lock_base_delay == 0.1
        assert lc._retention_days == 7
        assert lc._retention_max_bytes == 10 * 1024 * 1024 * 1024

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

    def test_custom_paths(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path, custom_paths="/a/b.log,/c/d.log")
        assert len(lc._custom_paths) == 2

    def test_custom_paths_empty(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path, custom_paths="")
        assert lc._custom_paths == []

    def test_custom_paths_whitespace(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path, custom_paths=" /a.log , /b.log ")
        assert len(lc._custom_paths) == 2


# ── LogCollector.from_config ────────────────────────────────

class TestLogCollectorFromConfig:
    def test_from_config_defaults(self, tmp_path: Path) -> None:
        from sts2_autotest.config.schema import FrameworkConfig

        cfg = FrameworkConfig()
        lc = LogCollector.from_config(tmp_path, cfg)
        assert lc._log_levels == {"ERROR", "WARN", "WARNING"}
        assert lc._max_entries == 10000
        assert lc._lock_retries == 5

    def test_from_config_custom(self, tmp_path: Path) -> None:
        from sts2_autotest.config.schema import FrameworkConfig

        cfg = FrameworkConfig(
            log_levels="ERROR,CRITICAL",
            log_max_entries=500,
            log_custom_paths="/custom/mod.log",
            log_lock_retries=3,
            log_retention_days=14,
        )
        lc = LogCollector.from_config(tmp_path, cfg)
        assert lc._log_levels == {"ERROR", "CRITICAL"}
        assert lc._max_entries == 500
        assert len(lc._custom_paths) == 1
        assert lc._lock_retries == 3
        assert lc._retention_days == 14

    def test_config_affects_log_level_filter(self, tmp_path: Path) -> None:
        from sts2_autotest.config.schema import FrameworkConfig

        log_dir = tmp_path / "godot"
        log_dir.mkdir()
        (log_dir / "game.log").write_text(
            "[2025-01-15 10:30:45.123] ERROR: bad\n"
            "[2025-01-15 10:30:45.124] INFO: ok\n",
            encoding="utf-8",
        )

        # Only ERROR
        cfg = FrameworkConfig(log_levels="ERROR")
        lc = LogCollector.from_config(tmp_path, cfg)
        lc._log_dir = log_dir
        result = lc.collect_on_failure("test")
        assert result.matched_lines == 1

        # ERROR + INFO
        cfg2 = FrameworkConfig(log_levels="ERROR,INFO")
        lc2 = LogCollector.from_config(tmp_path, cfg2)
        lc2._log_dir = log_dir
        result2 = lc2.collect_on_failure("test2")
        assert result2.matched_lines == 2

    def test_framework_config_satisfies_protocol(self) -> None:
        from sts2_autotest.common.types import LogCollectorSettings
        from sts2_autotest.config.schema import FrameworkConfig

        cfg = FrameworkConfig()
        settings: LogCollectorSettings = cfg
        assert settings.log_levels == "ERROR,WARN,WARNING"


# ── _find_latest_log ────────────────────────────────────────

class TestFindLatestLog:
    def test_finds_latest(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "godot_logs"
        log_dir.mkdir()
        (log_dir / "old.log").write_text("old", encoding="utf-8")
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


# ── _extract_crash_info (AC2/FR22) ─────────────────────────

class TestExtractCrashInfo:
    def test_extracts_last_error(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path)
        entries = [
            LogEntry(timestamp="2025-01-15 10:30:45.123", level="ERROR", message="first"),
            LogEntry(timestamp="2025-01-15 10:30:45.124", level="WARN", message="warn"),
            LogEntry(timestamp="2025-01-15 10:30:45.125", level="ERROR", message="crash"),
        ]
        result = lc._extract_crash_info(entries)
        assert result is not None
        assert "crash" in result
        assert "2025-01-15 10:30:45.125" in result

    def test_extracts_critical(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path)
        entries = [
            LogEntry(timestamp="t", level="CRITICAL", message="fatal"),
        ]
        result = lc._extract_crash_info(entries)
        assert result is not None
        assert "CRITICAL" in result

    def test_no_errors_returns_none(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path)
        entries = [
            LogEntry(timestamp="t", level="WARN", message="just warn"),
        ]
        result = lc._extract_crash_info(entries)
        assert result is None

    def test_empty_entries_returns_none(self, tmp_path: Path) -> None:
        lc = LogCollector(tmp_path)
        result = lc._extract_crash_info([])
        assert result is None


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


# ── _read_with_retry (AC4/FR49) ─────────────────────────────

class TestReadWithRetry:
    def test_successful_read(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text("content", encoding="utf-8")
        lc = LogCollector(tmp_path)
        result = lc._read_with_retry(log_file)
        assert result == "content"

    def test_retry_on_oserror(self, tmp_path: Path) -> None:
        log_file = tmp_path / "locked.log"
        log_file.write_text("content", encoding="utf-8")
        lc = LogCollector(tmp_path, lock_retries=2, lock_base_delay=0.01)

        call_count = 0
        original_read = Path.read_text

        def _mock_read(self_path: Path, *args: object, **kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise OSError("locked")
            return original_read(self_path, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "read_text", _mock_read):
            result = lc._read_with_retry(log_file)

        assert result == "content"
        assert call_count == 3  # 2 failures + 1 success

    def test_fails_after_max_retries(self, tmp_path: Path) -> None:
        log_file = tmp_path / "stuck.log"
        log_file.write_text("content", encoding="utf-8")
        lc = LogCollector(tmp_path, lock_retries=1, lock_base_delay=0.01)

        with patch.object(Path, "read_text", side_effect=OSError("permalock")):
            result = lc._read_with_retry(log_file)

        assert result is None

    def test_fallback_to_backup_dir(self, tmp_path: Path) -> None:
        backup = tmp_path / "backup"
        backup.mkdir()
        backup_file = backup / "game.log"
        backup_file.write_text("backup content", encoding="utf-8")

        lc = LogCollector(
            tmp_path,
            backup_dir=str(backup),
            lock_retries=0,
            lock_base_delay=0.01,
        )

        # Mock only the primary read to fail; backup should still work
        original_read = Path.read_text

        def _mock_read(self_path: Path, *args: object, **kwargs: object) -> str:
            if str(self_path) == "game.log":
                raise OSError("locked")
            return original_read(self_path, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "read_text", _mock_read):
            result = lc._read_with_retry(Path("game.log"))

        assert result == "backup content"


# ── _cleanup_old_logs (AC5/NFR41) ───────────────────────────

class TestCleanupOldLogs:
    def test_removes_old_files_by_age(self, tmp_path: Path) -> None:
        out = tmp_path / "evidence"
        out.mkdir()

        # Create a file and make it old
        old_file = out / "old.log"
        old_file.write_text("old content", encoding="utf-8")
        # Set mtime to 10 days ago
        old_time = time.time() - 10 * 86400
        os.utime(str(old_file), (old_time, old_time))

        # Create a recent file
        new_file = out / "new.log"
        new_file.write_text("new content", encoding="utf-8")

        lc = LogCollector(out, retention_days=7)
        lc._cleanup_old_logs()

        assert not old_file.exists()
        assert new_file.exists()

    def test_removes_files_by_total_size(self, tmp_path: Path) -> None:
        out = tmp_path / "evidence"
        out.mkdir()

        # Create files that exceed size limit
        for i in range(5):
            f = out / f"log_{i}.log"
            f.write_text("x" * 1000, encoding="utf-8")

        lc = LogCollector(out, retention_max_bytes=1500)
        lc._cleanup_old_logs()

        remaining = list(out.glob("*.log"))
        total_size = sum(f.stat().st_size for f in remaining)
        assert total_size <= 1500

    def test_no_cleanup_when_within_policy(self, tmp_path: Path) -> None:
        out = tmp_path / "evidence"
        out.mkdir()
        f = out / "recent.log"
        f.write_text("data", encoding="utf-8")

        lc = LogCollector(out, retention_days=7, retention_max_bytes=10 * 1024 * 1024 * 1024)
        lc._cleanup_old_logs()

        assert f.exists()

    def test_skips_non_log_files(self, tmp_path: Path) -> None:
        out = tmp_path / "evidence"
        out.mkdir()
        txt = out / "readme.txt"
        txt.write_text("keep me", encoding="utf-8")

        lc = LogCollector(out, retention_days=0)
        lc._cleanup_old_logs()

        assert txt.exists()


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

    def test_collect_with_custom_path(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "godot"
        log_dir.mkdir()
        (log_dir / "game.log").write_text(
            "[2025-01-15 10:30:45.123] ERROR: Main\n", encoding="utf-8"
        )

        custom = tmp_path / "mod.log"
        custom.write_text(
            "[2025-01-15 10:30:45.124] ERROR: Mod error\n", encoding="utf-8"
        )

        out = tmp_path / "evidence"
        lc = LogCollector(
            out,
            log_dir=log_dir,
            log_levels="ERROR",
            custom_paths=str(custom),
        )
        result = lc.collect("case1")
        assert result.ok

        # Should have both main log and custom log in output
        output_files = list(out.glob("*.log"))
        assert len(output_files) >= 2

    def test_collect_custom_only_when_default_missing(self, tmp_path: Path) -> None:
        """AC3: Custom path succeeds even when default log dir is missing."""
        custom = tmp_path / "mod.log"
        custom.write_text(
            "[2025-01-15 10:30:45.123] ERROR: Mod error\n", encoding="utf-8"
        )

        out = tmp_path / "evidence"
        lc = LogCollector(
            out,
            log_dir=tmp_path / "nonexistent",
            log_levels="ERROR",
            custom_paths=str(custom),
        )
        result = lc.collect("case1")
        assert result.ok
        assert result.dest_path is not None
        assert result.dest_path.exists()

    def test_collect_locked_default_no_backup_returns_not_ok(self, tmp_path: Path) -> None:
        """AC4: Locked primary with no backup → ok=False, no fake dest_path."""
        log_dir = tmp_path / "godot"
        log_dir.mkdir()
        (log_dir / "game.log").write_text(
            "[2025-01-15 10:30:45.123] ERROR: x\n", encoding="utf-8"
        )

        lc = LogCollector(
            tmp_path / "evidence",
            log_dir=log_dir,
            log_levels="ERROR",
            lock_retries=0,
            lock_base_delay=0.01,
        )

        with patch.object(Path, "read_text", side_effect=OSError("permalock")):
            result = lc.collect("case1")

        assert not result.ok
        assert result.dest_path is None


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

    def test_failure_records_crash_info(self, tmp_path: Path) -> None:
        """AC2: Crash state is recorded from error entries."""
        log_dir = tmp_path / "godot"
        log_dir.mkdir()
        (log_dir / "game.log").write_text(
            "[2025-01-15 10:30:45.123] ERROR: Engine crash\n"
            "[2025-01-15 10:30:45.124] INFO: After\n",
            encoding="utf-8",
        )
        lc = LogCollector(tmp_path / "out", log_dir=log_dir, log_levels="ERROR")
        result = lc.collect_on_failure("crash-case")
        assert result.crash_info is not None
        assert "Engine crash" in result.crash_info

    def test_failure_with_custom_path(self, tmp_path: Path) -> None:
        """AC3: Custom paths collected as supplement on failure."""
        log_dir = tmp_path / "godot"
        log_dir.mkdir()
        (log_dir / "game.log").write_text(
            "[2025-01-15 10:30:45.123] ERROR: Main\n", encoding="utf-8"
        )

        custom = tmp_path / "mod.log"
        custom.write_text(
            "[2025-01-15 10:30:45.124] ERROR: Mod\n", encoding="utf-8"
        )

        out = tmp_path / "evidence"
        lc = LogCollector(
            out,
            log_dir=log_dir,
            log_levels="ERROR",
            custom_paths=str(custom),
        )
        result = lc.collect_on_failure("case1")
        assert result.ok
        assert result.crash_info is not None

        # Should have filtered logs for both main and custom
        filtered_files = [f for f in out.iterdir() if "_filtered_" in f.name]
        assert len(filtered_files) >= 2

    def test_failure_custom_only_when_default_missing(self, tmp_path: Path) -> None:
        """AC3: Custom path succeeds on failure even without default log."""
        custom = tmp_path / "mod.log"
        custom.write_text(
            "[2025-01-15 10:30:45.123] ERROR: Mod crash\n", encoding="utf-8"
        )

        out = tmp_path / "evidence"
        lc = LogCollector(
            out,
            log_dir=tmp_path / "nonexistent",
            log_levels="ERROR",
            custom_paths=str(custom),
        )
        result = lc.collect_on_failure("case1")
        assert result.ok
        assert result.crash_info is not None
        assert "Mod crash" in result.crash_info

    def test_failure_locked_no_backup_returns_not_ok(self, tmp_path: Path) -> None:
        """AC4: Locked primary on failure with no backup → ok=False, no dest_path."""
        log_dir = tmp_path / "godot"
        log_dir.mkdir()
        (log_dir / "game.log").write_text(
            "[2025-01-15 10:30:45.123] ERROR: x\n", encoding="utf-8"
        )

        out = tmp_path / "evidence"
        lc = LogCollector(
            out,
            log_dir=log_dir,
            log_levels="ERROR",
            lock_retries=0,
            lock_base_delay=0.01,
        )

        with patch.object(Path, "read_text", side_effect=OSError("permalock")):
            result = lc.collect_on_failure("case1")

        assert not result.ok
        assert result.dest_path is None
        # No evidence file should have been created
        assert not list(out.glob("*.log")) if out.is_dir() else True

    def test_failure_locked_empty_backup_dir_ignores_cwd_file(self, tmp_path: Path) -> None:
        """AC4 regression: empty backup_dir="" must not fall through to cwd file."""
        log_dir = tmp_path / "primary"
        log_dir.mkdir()
        (log_dir / "game.log").write_text(
            "[2025-01-15 10:30:45.123] ERROR: locked content\n", encoding="utf-8"
        )

        # Create a same-named file in tmp_path (simulates cwd pollution)
        (tmp_path / "game.log").write_text(
            "[2025-01-15 10:30:45.123] ERROR: cwd backup\n", encoding="utf-8"
        )

        out = tmp_path / "evidence"
        # backup_dir="" (default) — must not resolve to cwd/game.log
        lc = LogCollector(
            out,
            log_dir=log_dir,
            log_levels="ERROR",
            lock_retries=0,
            lock_base_delay=0.01,
            backup_dir="",
        )

        # Lock only the primary log file by name
        original_read = Path.read_text

        def _mock_read(self_path: Path, *args: object, **kwargs: object) -> str:
            if str(self_path).endswith("primary" + os.sep + "game.log"):
                raise OSError("locked primary")
            return original_read(self_path, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "read_text", _mock_read):
            result = lc.collect_on_failure("case1")

        assert not result.ok
        assert result.dest_path is None


# ── FrameworkConfig log fields ──────────────────────────────

class TestFrameworkConfigLog:
    def test_log_defaults(self) -> None:
        from sts2_autotest.config.schema import FrameworkConfig

        cfg = FrameworkConfig()
        assert cfg.log_levels == "ERROR,WARN,WARNING"
        assert cfg.log_max_entries == 10000
        assert cfg.log_custom_paths == ""
        assert cfg.log_backup_dir == ""
        assert cfg.log_lock_retries == 5
        assert cfg.log_lock_base_delay == 0.1
        assert cfg.log_retention_days == 7
        assert cfg.log_retention_max_bytes == 10 * 1024 * 1024 * 1024

    def test_log_custom(self) -> None:
        from sts2_autotest.config.schema import FrameworkConfig

        cfg = FrameworkConfig(
            log_levels="ERROR,CRITICAL",
            log_max_entries=5000,
            log_custom_paths="/a.log,/b.log",
            log_backup_dir="/tmp/backup",
            log_lock_retries=3,
            log_lock_base_delay=0.2,
            log_retention_days=14,
            log_retention_max_bytes=5 * 1024 * 1024 * 1024,
        )
        assert cfg.log_levels == "ERROR,CRITICAL"
        assert cfg.log_max_entries == 5000
        assert cfg.log_custom_paths == "/a.log,/b.log"
        assert cfg.log_lock_retries == 3
        assert cfg.log_retention_days == 14

    def test_invalid_log_max_entries(self) -> None:
        from pydantic import ValidationError
        from sts2_autotest.config.schema import FrameworkConfig

        with pytest.raises(ValidationError):
            FrameworkConfig(log_max_entries=0)

    def test_invalid_lock_retries(self) -> None:
        from pydantic import ValidationError
        from sts2_autotest.config.schema import FrameworkConfig

        with pytest.raises(ValidationError):
            FrameworkConfig(log_lock_retries=-1)
