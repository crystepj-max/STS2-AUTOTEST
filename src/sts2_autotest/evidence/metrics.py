"""JSONL metrics collector — session-level telemetry for test runs (FR31, FR32)."""

from __future__ import annotations

__test__ = False

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.types import MetricsCollectorSettings

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
        # Running counters — survive flush() (NB2)
        self._total_cases = 0
        self._passed = 0
        self._failed = 0
        self._total_duration_ms = 0
        self._adapter_commands = 0
        self._adapter_errors = 0
        self._state_transitions = 0
        self._screenshots = 0
        self._screenshot_total_ms = 0
        self._resource_usage: dict[str, list[float]] = {}

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

    def record_adapter_command(
        self,
        command: str,
        duration_ms: int,
        success: bool,
    ) -> None:
        """Record an adapter command execution with latency and success status (AC1/FR31)."""
        self._record("adapter_command", {
            "command": command,
            "duration_ms": duration_ms,
            "success": success,
        })

    def record_state_transition(
        self,
        from_state: str,
        to_state: str,
        *,
        duration_ms: int = 0,
    ) -> None:
        """Record a game state transition event (AC1/FR31)."""
        self._record("state_transition", {
            "from_state": from_state,
            "to_state": to_state,
            "duration_ms": duration_ms,
        })

    def record_screenshot(
        self,
        case_id: str,
        duration_ms: int,
        status: str,
    ) -> None:
        """Record a screenshot capture event with timing (AC1/FR31)."""
        self._record("screenshot", {
            "case_id": case_id,
            "duration_ms": duration_ms,
            "status": status,
        })

    def record_resource_usage(
        self,
        metric: str,
        value: float,
    ) -> None:
        """Record a framework resource usage metric (AC1/FR31)."""
        self._record("resource_usage", {
            "metric": metric,
            "value": value,
        })

    def get_summary(self) -> dict[str, object]:
        """Return session-level summary statistics from running counters (AC1/FR31).

        Counters are maintained across flush() calls so the summary always
        reflects the full session, not just the pending buffer.

        Returns a dict with:
        - total_cases, passed, failed, total_duration_ms
        - adapter_commands, adapter_errors
        - state_transitions
        - screenshots, avg_screenshot_ms
        - resource_usage: {metric_name: {latest, avg, count}}
        """
        summary: dict[str, object] = {
            "total_cases": self._total_cases,
            "passed": self._passed,
            "failed": self._failed,
            "total_duration_ms": self._total_duration_ms,
            "adapter_commands": self._adapter_commands,
            "adapter_errors": self._adapter_errors,
            "state_transitions": self._state_transitions,
            "screenshots": self._screenshots,
        }
        if self._screenshots > 0:
            summary["avg_screenshot_ms"] = self._screenshot_total_ms / self._screenshots
        if self._resource_usage:
            ru: dict[str, dict[str, object]] = {}
            for name, values in self._resource_usage.items():
                ru[name] = {
                    "latest": values[-1],
                    "avg": sum(values) / len(values),
                    "count": len(values),
                }
            summary["resource_usage"] = ru
        return summary

    @classmethod
    def from_config(cls, settings: MetricsCollectorSettings) -> MetricsCollector:
        """Construct MetricsCollector from a MetricsCollectorSettings protocol instance."""
        return cls(
            output_dir=Path(settings.evidence_dir),
            filename=settings.metrics_filename,
        )

    def end_session(self, summary: dict[str, object] | None = None) -> None:
        """Record session end event and flush to disk."""
        self._record("session_end", summary or {})
        self.flush()

    def flush(self) -> None:
        """Flush buffered events to the JSONL file using true append (AC3).

        Appends new events to the file without reading existing content,
        so flush cost is O(buffer_size) regardless of file size.
        """
        if not self._buffer:
            return

        self._output_dir.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        for event in self._buffer:
            lines.append(json.dumps(event.to_dict(), ensure_ascii=False))

        # True append: open in append mode, write only new lines
        try:
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            self._buffer.clear()
        except OSError:
            raise

    # ── internal ────────────────────────────────────────────

    def _record(self, event_type: str, data: dict[str, object]) -> None:
        """Create and buffer a metric event, updating running counters."""
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
        event = MetricEvent(timestamp=timestamp, event_type=event_type, data=data)
        self._buffer.append(event)
        self._update_counters(event)

    def _update_counters(self, event: MetricEvent) -> None:
        """Update running counters for get_summary(). Survives flush()."""
        if event.event_type == "case_result":
            self._total_cases += 1
            status = str(event.data.get("status", ""))
            if status == "passed":
                self._passed += 1
            elif status == "failed":
                self._failed += 1
            self._total_duration_ms += int(str(event.data.get("duration_ms", "0")))
        elif event.event_type == "adapter_command":
            self._adapter_commands += 1
            if not event.data.get("success", True):
                self._adapter_errors += 1
        elif event.event_type == "state_transition":
            self._state_transitions += 1
        elif event.event_type == "screenshot":
            self._screenshots += 1
            self._screenshot_total_ms += int(str(event.data.get("duration_ms", "0")))
        elif event.event_type == "resource_usage":
            metric_name = str(event.data.get("metric", "unknown"))
            value = float(str(event.data.get("value", "0")))
            self._resource_usage.setdefault(metric_name, []).append(value)
