"""Progress persistence module — saves and resumes test session progress (FR63)."""

from __future__ import annotations

import json
import os
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, UTC
from pathlib import Path

from sts2_autotest.common.logging import get_logger
from sts2_autotest.core.disk_guard import check_disk_space

logger = get_logger("core.progress")

_PROGRESS_SCHEMA_VERSION = 1


@dataclass
class ProgressRecord:
    """Serialisable snapshot of session progress for resume."""

    schema_version: int = _PROGRESS_SCHEMA_VERSION
    session_id: str = ""
    completed_cases: list[str] = field(default_factory=list)
    pending_cases: list[str] = field(default_factory=list)
    current_case: str | None = None
    current_step: str | None = None
    game_screen: str | None = None
    recovery_status: str | None = None
    paused: bool = False
    last_updated: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "_schema_version": self.schema_version,
            "_checksum": 0,  # placeholder; recomputed by save_progress
            "session_id": self.session_id,
            "completed_cases": self.completed_cases,
            "pending_cases": self.pending_cases,
            "current_case": self.current_case,
            "current_step": self.current_step,
            "game_screen": self.game_screen,
            "recovery_status": self.recovery_status,
            "paused": self.paused,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ProgressRecord:
        raw_completed: object = data.get("completed_cases", [])
        raw_pending: object = data.get("pending_cases", [])
        raw_current: object | None = data.get("current_case")
        raw_step: object | None = data.get("current_step")
        raw_screen: object | None = data.get("game_screen")
        raw_recovery: object | None = data.get("recovery_status")
        raw_paused: object = data.get("paused", False)
        return cls(
            schema_version=int(str(data.get("_schema_version", 1))),
            session_id=str(data.get("session_id", "")),
            completed_cases=list(raw_completed) if isinstance(raw_completed, list) else [],
            pending_cases=list(raw_pending) if isinstance(raw_pending, list) else [],
            current_case=str(raw_current) if raw_current is not None else None,
            current_step=str(raw_step) if raw_step is not None else None,
            game_screen=str(raw_screen) if raw_screen is not None else None,
            recovery_status=str(raw_recovery) if raw_recovery is not None else None,
            paused=bool(raw_paused) if isinstance(raw_paused, bool) else False,
            last_updated=str(data.get("last_updated", "")),
        )


def compute_checksum(data: bytes) -> int:
    """Compute zlib.crc32 checksum of the given bytes."""
    return zlib.crc32(data) & 0xFFFFFFFF


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(UTC).microsecond // 1000:03d}Z"


def save_progress(record: ProgressRecord, path: Path) -> bool:
    """Save ProgressRecord to disk with CRC32 checksum and atomic write.

    Returns True on success, False on failure (disk space, I/O error).
    """
    record.last_updated = _now_utc()

    payload = record.to_dict()
    # Serialise to JSON bytes without checksum first
    json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    # Compute checksum over the payload bytes (with placeholder checksum)
    # We compute checksum on the serialised bytes, then re-serialise with the real checksum
    # To avoid the chicken-and-egg problem, compute over the body and inject checksum
    payload["_checksum"] = compute_checksum(json_bytes)
    final_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    if not check_disk_space(str(path.parent)):
        logger.warning(
            "Insufficient disk space for progress file %s — skipping",
            path,
        )
        return False

    path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to temp then os.replace
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(final_bytes)
        os.replace(str(tmp), str(path))
        return True
    except OSError:
        logger.warning("Failed to save progress to %s", path)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def load_progress(path: Path) -> ProgressRecord | None:
    """Load ProgressRecord from disk with CRC32 validation.

    Returns None if the file is missing, corrupted, or schema_version is
    newer than the current framework version.
    """
    if not path.is_file():
        return None

    try:
        raw = path.read_bytes()
    except OSError:
        logger.warning("Cannot read progress file: %s", path)
        return None

    try:
        data: dict[str, object] = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Progress file corrupted (invalid JSON): %s", path)
        return None

    # Validate schema version
    schema_ver = int(str(data.get("_schema_version", "0")))
    if schema_ver > _PROGRESS_SCHEMA_VERSION:
        logger.warning(
            "Progress file has newer schema version %d (framework supports %d)",
            schema_ver, _PROGRESS_SCHEMA_VERSION,
        )
        return None

    # Validate checksum
    raw_checksum: object = data.get("_checksum", 0)
    stored_checksum = int(str(raw_checksum))
    # Temporarily zero the checksum field to recompute
    data["_checksum"] = 0
    recomputed_json = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    expected = compute_checksum(recomputed_json)

    if stored_checksum != expected:
        logger.warning(
            "Progress file checksum mismatch (expected %d, got %d) — file corrupted",
            expected, stored_checksum,
        )
        return None

    # Restore checksum and deserialise
    data["_checksum"] = stored_checksum

    try:
        return ProgressRecord.from_dict(data)
    except (ValueError, TypeError) as exc:
        logger.warning("Progress file parse error: %s", exc)
        return None


def clear_progress(path: Path) -> None:
    """Delete the progress file. No-op if the file does not exist."""
    try:
        path.unlink(missing_ok=True)
        logger.info("Cleared progress file: %s", path)
    except OSError as exc:
        logger.warning("Failed to clear progress file %s: %s", path, exc)
