"""JSONL metrics collector — session-level telemetry for test runs (FR60)."""

from __future__ import annotations

__test__ = False

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sts2_autotest.common.logging import get_logger

logger = get_logger("evidence.metrics")


@dataclass
class MetricEvent:
    """A single metric event."""

    timestamp: str
    event_type: str
    data: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "data": self.data,
        }


class MetricsCollector:
    """JSONL telemetry collector for test session metrics.

    Writes one JSON object per line to a metrics file. Supports
    session lifecycle events and case result tracking.
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        filename: str = "metrics.jsonl",
    ) -> None:
        self._output_dir = output_dir
        self._filename = filename
        self._file_path = output_dir / filename
        self._buffer: list[MetricEvent] = []
        self._session_id: str | None = None

    @property
    def file_path(self) -> Path:
        return self._file_path

    # ── public API ──────────────────────────────────────────

    def start_session(self, session_id: str) -> None:
        """Record session start event."""
        self._session_id = session_id
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._record("session_start", {"session_id": session_id})

    def record(self, event_type: str, data: dict[str, object] | None = None) -> None:
        """Record a generic metric event."""
        self._record(event_type, data or {})

    def record_case_result(
        self,
        case_id: str,
        status: str,
        *,
        duration_ms: int = 0,
        error_message: str | None = None,
    ) -> None:
        """Record a test case result event."""
        data: dict[str, object] = {
            "case_id": case_id,
            "status": status,
            "duration_ms": duration_ms,
        }
        if error_message is not None:
            data["error_message"] = error_message
        self._record("case_result", data)

    def end_session(self, summary: dict[str, object] | None = None) -> None:
        """Record session end event and flush to disk."""
        self._record("session_end", summary or {})
        self.flush()

    def flush(self) -> None:
        """Flush buffered events to the JSONL file."""
        if not self._buffer:
            return

        lines: list[str] = []
        for event in self._buffer:
            lines.append(json.dumps(event.to_dict(), ensure_ascii=False))

        tmp = self._file_path.with_suffix(".jsonl.tmp")
        try:
            # Append mode: read existing content if file exists
            existing = ""
            if self._file_path.is_file():
                existing = self._file_path.read_text(encoding="utf-8")

            tmp.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")
            os.replace(str(tmp), str(self._file_path))
            self._buffer.clear()
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # ── internal ────────────────────────────────────────────

    def _record(self, event_type: str, data: dict[str, object]) -> None:
        """Create and buffer a metric event."""
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
        event = MetricEvent(timestamp=timestamp, event_type=event_type, data=data)
        self._buffer.append(event)
