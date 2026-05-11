"""Godot log collector — scan, parse, filter, copy (FR20, FR25)."""

from __future__ import annotations

__test__ = False

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sts2_autotest.common.logging import get_logger

logger = get_logger("evidence.logs")

# Godot log line: [YYYY-MM-DD HH:MM:SS.mmm] LEVEL: message
_GODOT_PATTERN = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\]\s+"
    r"(DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL):\s+(.*)$"
)

# Default Godot log directory (Windows)
_DEFAULT_LOG_DIR = Path(
    os.environ.get(
        "STS2_GODOT_LOG_DIR",
        os.path.join(os.environ.get("APPDATA", ""), "Godot", "app_userdata",
                     "Slay the Spire 2", "logs"),
    )
)


@dataclass
class LogEntry:
    """A single parsed Godot log line."""

    timestamp: str
    level: str
    message: str


@dataclass
class LogCollectionResult:
    """Result of a log collection operation."""

    source_path: Path | None = None
    dest_path: Path | None = None
    total_lines: int = 0
    matched_lines: int = 0
    entries: list[LogEntry] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.dest_path is not None


class LogCollector:
    """Collect and parse Godot log files.

    Scans the Godot log directory for the latest log file, parses
    entries by log level filter, and copies (or writes filtered) to
    the evidence output directory.
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        log_dir: Path | None = None,
        log_levels: str = "ERROR,WARN,WARNING",
        max_entries: int = 10000,
    ) -> None:
        self._output_dir = output_dir
        self._log_dir = log_dir or _DEFAULT_LOG_DIR
        self._log_levels = set(log_levels.split(",")) if log_levels else set()
        self._max_entries = max_entries

    # ── public API ──────────────────────────────────────────

    def collect(self, case_id: str) -> LogCollectionResult:
        """Collect the latest Godot log and copy to evidence directory.

        Returns LogCollectionResult with source/dest paths and counts.
        """
        log_path = self._find_latest_log()
        if log_path is None:
            logger.warning("No Godot log file found in %s", self._log_dir)
            return LogCollectionResult()

        entries, total = self._parse_log(log_path)
        dest = self._copy_log(log_path, case_id)

        logger.info(
            "Collected log: %s (%d lines, %d matched)",
            log_path.name, total, len(entries),
        )
        return LogCollectionResult(
            source_path=log_path,
            dest_path=dest,
            total_lines=total,
            matched_lines=len(entries),
            entries=entries[: self._max_entries],
        )

    def collect_on_failure(self, case_id: str) -> LogCollectionResult:
        """Collect and write a filtered log (only matching levels) on failure.

        Writes a filtered copy containing only entries matching the
        configured log levels — useful for quick diagnosis.
        """
        log_path = self._find_latest_log()
        if log_path is None:
            logger.warning("No Godot log file found for failure collection")
            return LogCollectionResult()

        entries, total = self._parse_log(log_path)
        filtered = [e for e in entries if e.level in self._log_levels]
        dest = self._write_filtered_log(filtered, case_id)

        logger.info(
            "Failure log for %s: %d/%d entries from %s",
            case_id, len(filtered), total, log_path.name,
        )
        return LogCollectionResult(
            source_path=log_path,
            dest_path=dest,
            total_lines=total,
            matched_lines=len(filtered),
            entries=filtered[: self._max_entries],
        )

    # ── internal: parsing ───────────────────────────────────

    def _parse_log(self, log_path: Path) -> tuple[list[LogEntry], int]:
        """Parse a Godot log file into LogEntry list.

        Returns (filtered_entries, total_line_count).
        Only entries matching _log_levels are included.
        """
        entries: list[LogEntry] = []
        total = 0

        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read log file %s: %s", log_path, exc)
            return entries, 0

        lines = text.splitlines()
        total = len(lines)

        for line in lines:
            m = _GODOT_PATTERN.match(line.strip())
            if m is None:
                continue
            timestamp_str, level, message = m.group(1), m.group(2), m.group(3)
            if self._log_levels and level not in self._log_levels:
                continue
            entries.append(LogEntry(timestamp=timestamp_str, level=level, message=message))
            if len(entries) >= self._max_entries:
                break

        return entries, total

    # ── internal: file discovery ────────────────────────────

    def _find_latest_log(self) -> Path | None:
        """Find the most recently modified .log file in log_dir."""
        if not self._log_dir.is_dir():
            return None

        log_files = list(self._log_dir.glob("*.log"))
        if not log_files:
            return None

        return max(log_files, key=lambda p: p.stat().st_mtime)

    # ── internal: file I/O ──────────────────────────────────

    def _copy_log(self, log_path: Path, case_id: str) -> Path:
        """Copy log file to evidence output directory."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%dT%H%M%S")
        ms = now.microsecond // 1000
        dest = self._output_dir / f"{case_id}_{timestamp}_{ms:03d}.log"
        shutil.copy2(str(log_path), str(dest))
        return dest

    def _write_filtered_log(
        self, entries: list[LogEntry], case_id: str
    ) -> Path:
        """Write filtered log entries to evidence output directory."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%dT%H%M%S")
        ms = now.microsecond // 1000
        dest = self._output_dir / f"{case_id}_filtered_{timestamp}_{ms:03d}.log"

        lines = [
            f"[{e.timestamp}] {e.level}: {e.message}" for e in entries
        ]
        tmp = dest.with_suffix(".log.tmp")
        try:
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(str(tmp), str(dest))
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        return dest
