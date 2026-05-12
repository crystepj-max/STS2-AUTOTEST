"""Godot log collector — scan, parse, filter, copy (FR21, FR22, FR49)."""

from __future__ import annotations

__test__ = False

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.types import LogCollectorSettings

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
    crash_info: str | None = None

    @property
    def ok(self) -> bool:
        return self.dest_path is not None


class LogCollector:
    """Collect and parse Godot log files.

    Supports:
    - Default Godot log path discovery
    - Custom log paths (AC3: collects both custom + default)
    - File lock retry with exponential backoff (AC4/FR49)
    - Backup directory fallback on persistent lock failure
    - Log retention policy auto-cleanup (AC5/NFR41)
    - Crash state recording (AC2/FR22)
    - Config-driven construction via from_config()
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        log_dir: Path | None = None,
        log_levels: str = "ERROR,WARN,WARNING",
        max_entries: int = 10000,
        custom_paths: str = "",
        backup_dir: str = "",
        lock_retries: int = 5,
        lock_base_delay: float = 0.1,
        retention_days: int = 7,
        retention_max_bytes: int = 10 * 1024 * 1024 * 1024,
    ) -> None:
        self._output_dir = output_dir
        self._log_dir = log_dir or _DEFAULT_LOG_DIR
        self._log_levels = set(log_levels.split(",")) if log_levels else set()
        self._max_entries = max_entries
        self._custom_paths = [
            Path(p.strip()) for p in custom_paths.split(",") if p.strip()
        ] if custom_paths else []
        self._backup_dir = Path(backup_dir) if backup_dir else None
        self._lock_retries = lock_retries
        self._lock_base_delay = lock_base_delay
        self._retention_days = retention_days
        self._retention_max_bytes = retention_max_bytes

    @classmethod
    def from_config(
        cls, output_dir: Path, settings: LogCollectorSettings
    ) -> "LogCollector":
        """Construct LogCollector from a LogCollectorSettings protocol instance."""
        return cls(
            output_dir,
            log_levels=settings.log_levels,
            max_entries=settings.log_max_entries,
            custom_paths=settings.log_custom_paths,
            backup_dir=settings.log_backup_dir,
            lock_retries=settings.log_lock_retries,
            lock_base_delay=settings.log_lock_base_delay,
            retention_days=settings.log_retention_days,
            retention_max_bytes=settings.log_retention_max_bytes,
        )

    # ── public API ──────────────────────────────────────────

    def collect(self, case_id: str) -> LogCollectionResult:
        """Collect the latest Godot log and copy to evidence directory.

        Also collects from custom paths if configured (AC3).
        Custom paths are processed independently — collection succeeds
        from custom paths even when the default log is missing.
        Returns LogCollectionResult with source/dest paths and counts.
        """
        self._cleanup_old_logs()

        log_path = self._find_latest_log()
        result_source: Path | None = None
        result_dest: Path | None = None
        result_entries: list[LogEntry] = []
        result_total = 0

        if log_path is not None:
            entries, total = self._parse_log(log_path)
            dest = self._read_and_copy(log_path, case_id)
            if dest is not None:
                result_source = log_path
                result_dest = dest
                result_entries = entries
                result_total = total
                logger.info(
                    "Collected log: %s (%d lines, %d matched)",
                    log_path.name, total, len(entries),
                )
        else:
            logger.warning("No Godot log file found in %s", self._log_dir)

        # Collect custom paths as supplements (AC3)
        # Custom paths are independent — succeed even without default log
        for custom_path in self._custom_paths:
            if custom_path.exists():
                custom_dest = self._read_and_copy(custom_path, f"{case_id}_custom")
                if custom_dest is not None:
                    logger.info("Collected custom log: %s", custom_path)
                    # If no default log was found, use first custom as primary result
                    if result_dest is None:
                        custom_entries, custom_total = self._parse_log(custom_path)
                        result_source = custom_path
                        result_dest = custom_dest
                        result_entries = custom_entries
                        result_total = custom_total

        return LogCollectionResult(
            source_path=result_source,
            dest_path=result_dest,
            total_lines=result_total,
            matched_lines=len(result_entries),
            entries=result_entries[: self._max_entries],
        )

    def collect_on_failure(self, case_id: str) -> LogCollectionResult:
        """Collect and write a filtered log (only matching levels) on failure.

        AC2: Also records crash state info from the log.
        AC3: Also collects custom log paths as supplements.
        AC4: Uses _read_with_retry() so locked logs don't produce false ok.
        Custom paths are processed independently — succeed even without default log.
        """
        self._cleanup_old_logs()

        log_path = self._find_latest_log()
        result_source: Path | None = None
        result_dest: Path | None = None
        result_entries: list[LogEntry] = []
        result_total = 0
        crash_info: str | None = None

        if log_path is not None:
            # AC4: read through retry/backup path, not raw _parse_log
            content = self._read_with_retry(log_path)
            if content is not None:
                entries, total = self._parse_log_text(content)
                filtered = [e for e in entries if e.level in self._log_levels]
                dest = self._write_filtered_log(filtered, case_id)
                crash_info = self._extract_crash_info(filtered)

                result_source = log_path
                result_dest = dest
                result_entries = filtered
                result_total = total

                logger.info(
                    "Failure log for %s: %d/%d entries from %s",
                    case_id, len(filtered), total, log_path.name,
                )
            else:
                logger.warning(
                    "Could not read default log %s for failure collection",
                    log_path,
                )
        else:
            logger.warning("No Godot log file found for failure collection")

        # Collect custom paths as supplements (AC3)
        # Custom paths are independent — succeed even without default log
        for custom_path in self._custom_paths:
            if custom_path.exists():
                custom_content = self._read_with_retry(custom_path)
                if custom_content is not None:
                    custom_entries, custom_total = self._parse_log_text(custom_content)
                    custom_filtered = [e for e in custom_entries if e.level in self._log_levels]
                    self._write_filtered_log(
                        custom_filtered, f"{case_id}_custom"
                    )
                    logger.info(
                        "Custom failure log for %s: %d/%d from %s",
                        case_id, len(custom_filtered), custom_total, custom_path,
                    )
                    # If no default log result, use first custom as primary
                    if result_dest is None:
                        custom_dest = self._write_filtered_log(
                            custom_filtered, case_id
                        )
                        result_source = custom_path
                        result_dest = custom_dest
                        result_entries = custom_filtered
                        result_total = custom_total
                        crash_info = self._extract_crash_info(custom_filtered)

        return LogCollectionResult(
            source_path=result_source,
            dest_path=result_dest,
            total_lines=result_total,
            matched_lines=len(result_entries),
            entries=result_entries[: self._max_entries],
            crash_info=crash_info,
        )

    # ── internal: parsing ───────────────────────────────────

    def _parse_log(self, log_path: Path) -> tuple[list[LogEntry], int]:
        """Parse a Godot log file into LogEntry list.

        Returns (filtered_entries, total_line_count).
        Only entries matching _log_levels are included.
        """
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read log file %s: %s", log_path, exc)
            return [], 0
        return self._parse_log_text(text)

    def _parse_log_text(self, text: str) -> tuple[list[LogEntry], int]:
        """Parse Godot log text into LogEntry list.

        Returns (filtered_entries, total_line_count).
        Only entries matching _log_levels are included.
        """
        entries: list[LogEntry] = []
        total = 0

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

    def _extract_crash_info(self, entries: list[LogEntry]) -> str | None:
        """Extract crash information from error-level log entries (AC2/FR22)."""
        error_entries = [e for e in entries if e.level in ("ERROR", "CRITICAL")]
        if not error_entries:
            return None
        # Take the last error as the crash state
        last_error = error_entries[-1]
        return f"[{last_error.timestamp}] {last_error.level}: {last_error.message}"

    # ── internal: file discovery ────────────────────────────

    def _find_latest_log(self) -> Path | None:
        """Find the most recently modified .log file in log_dir."""
        if not self._log_dir.is_dir():
            return None

        log_files = list(self._log_dir.glob("*.log"))
        if not log_files:
            return None

        return max(log_files, key=lambda p: p.stat().st_mtime)

    # ── internal: file I/O with lock retry (AC4/FR49) ───────

    def _read_with_retry(self, path: Path) -> str | None:
        """Read file with exponential backoff retry on lock failure.

        AC4/FR49: max 5 retries, total delay ≤ 3.1s.
        Falls back to backup_dir on persistent failure.
        """
        for attempt in range(self._lock_retries + 1):
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                if attempt < self._lock_retries:
                    delay = self._lock_base_delay * (2 ** attempt)
                    logger.debug(
                        "Log read attempt %d/%d failed: %s — retrying in %.2fs",
                        attempt + 1, self._lock_retries + 1, exc, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.warning(
                        "Log read failed after %d retries: %s",
                        self._lock_retries + 1, exc,
                    )
                    # Try backup directory if configured (AC4/FR49)
                    if self._backup_dir:
                        backup_path = self._backup_dir / path.name
                        if backup_path.exists():
                            logger.info(
                                "Trying backup log path: %s", backup_path
                            )
                            try:
                                return backup_path.read_text(
                                    encoding="utf-8", errors="replace"
                                )
                            except OSError:
                                pass
                    return None
        return None  # pragma: no cover

    def _read_and_copy(self, log_path: Path, case_id: str) -> Path | None:
        """Read log with retry and copy to evidence output directory.

        Returns None if the file could not be read (locked + no backup).
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%dT%H%M%S")
        ms = now.microsecond // 1000
        dest = self._output_dir / f"{case_id}_{timestamp}_{ms:03d}.log"

        content = self._read_with_retry(log_path)
        if content is None:
            logger.warning("Could not read log %s — no evidence file created", log_path)
            return None

        # Atomic write
        tmp = dest.with_suffix(".log.tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(str(tmp), str(dest))
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            # Fallback: try direct copy
            try:
                shutil.copy2(str(log_path), str(dest))
            except OSError as exc:
                logger.warning("Failed to copy log: %s", exc)
                return None
        return dest

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

    # ── internal: retention (AC5/NFR41) ─────────────────────

    def _cleanup_old_logs(self) -> None:
        """Remove old log files exceeding retention policy.

        AC5: Remove logs older than retention_days or when total size
        exceeds retention_max_bytes (NFR41: 7 days or 10GB).
        """
        if not self._output_dir.is_dir():
            return

        now = time.time()
        cutoff = now - (self._retention_days * 86400)
        files: list[Path] = []
        total_bytes = 0

        for f in self._output_dir.iterdir():
            if not f.is_file() or f.suffix != ".log":
                continue
            try:
                stat = f.stat()
                files.append(f)
                total_bytes += stat.st_size
            except OSError:
                continue

        # Sort by mtime oldest first
        files.sort(key=lambda p: p.stat().st_mtime)

        # Remove files older than retention_days
        removed = 0
        for f in list(files):
            try:
                if f.stat().st_mtime < cutoff:
                    size = f.stat().st_size
                    f.unlink()
                    total_bytes -= size
                    files.remove(f)
                    removed += 1
            except OSError:
                continue

        # Remove oldest files until total size is within limit
        for f in list(files):
            if total_bytes <= self._retention_max_bytes:
                break
            try:
                size = f.stat().st_size
                f.unlink()
                total_bytes -= size
                files.remove(f)
                removed += 1
            except OSError:
                continue

        if removed > 0:
            logger.info("Cleaned up %d old log file(s)", removed)
