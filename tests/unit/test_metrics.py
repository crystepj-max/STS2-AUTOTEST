"""Unit tests for evidence.metrics — MetricsCollector (Story 3-4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sts2_autotest.evidence.metrics import MetricEvent, MetricsCollector


# ── MetricEvent ─────────────────────────────────────────────

class TestMetricEvent:
    def test_create(self) -> None:
        event = MetricEvent(timestamp="2025-01-15T10:30:45.123Z", event_type="test")
        assert event.event_type == "test"
        assert event.data == {}

    def test_with_data(self) -> None:
        event = MetricEvent(
            timestamp="t", event_type="case_result", data={"case_id": "c1", "status": "passed"}
        )
        assert event.data["case_id"] == "c1"

    def test_to_dict(self) -> None:
        event = MetricEvent(timestamp="t", event_type="e", data={"k": "v"})
        d = event.to_dict()
        assert d["timestamp"] == "t"
        assert d["event_type"] == "e"
        assert d["data"] == {"k": "v"}


# ── MetricsCollector init ───────────────────────────────────

class TestMetricsCollectorInit:
    def test_default_filename(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        assert mc.file_path == tmp_path / "metrics.jsonl"

    def test_custom_filename(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path, filename="custom.jsonl")
        assert mc.file_path == tmp_path / "custom.jsonl"


# ── start_session ───────────────────────────────────────────

class TestStartSession:
    def test_creates_output_dir(self, tmp_path: Path) -> None:
        out = tmp_path / "deep" / "metrics"
        mc = MetricsCollector(out)
        mc.start_session("sess_001")
        assert out.is_dir()

    def test_records_session_start(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("sess_001")
        mc.flush()
        assert mc.file_path.is_file()
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["event_type"] == "session_start"
        assert data["data"]["session_id"] == "sess_001"


# ── record ──────────────────────────────────────────────────

class TestRecord:
    def test_record_generic_event(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record("custom_event", {"key": "value"})
        mc.flush()
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        data = json.loads(lines[1])
        assert data["event_type"] == "custom_event"
        assert data["data"]["key"] == "value"

    def test_record_no_data(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record("simple")
        mc.flush()
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        data = json.loads(lines[1])
        assert data["data"] == {}


# ── record_case_result ──────────────────────────────────────

class TestRecordCaseResult:
    def test_pass_result(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_case_result("case_1", "passed", duration_ms=150)
        mc.flush()
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        data = json.loads(lines[1])
        assert data["event_type"] == "case_result"
        assert data["data"]["case_id"] == "case_1"
        assert data["data"]["status"] == "passed"
        assert data["data"]["duration_ms"] == 150

    def test_fail_with_error_message(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_case_result("case_2", "failed", error_message="assertion failed")
        mc.flush()
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        data = json.loads(lines[1])
        assert data["data"]["status"] == "failed"
        assert data["data"]["error_message"] == "assertion failed"

    def test_no_error_message_when_none(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_case_result("case_3", "passed", duration_ms=10)
        mc.flush()
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        data = json.loads(lines[1])
        assert "error_message" not in data["data"]


# ── end_session ─────────────────────────────────────────────

class TestEndSession:
    def test_records_end_and_flushes(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.end_session({"passed": 3, "failed": 1})
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        data = json.loads(lines[1])
        assert data["event_type"] == "session_end"
        assert data["data"]["passed"] == 3

    def test_end_without_summary(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.end_session()
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        data = json.loads(lines[1])
        assert data["data"] == {}


# ── flush ───────────────────────────────────────────────────

class TestFlush:
    def test_flush_writes_to_file(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record("event1")
        mc.record("event2")
        mc.flush()
        assert mc.file_path.is_file()
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_flush_clears_buffer(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.flush()
        assert len(mc._buffer) == 0

    def test_flush_empty_buffer_no_file(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.flush()
        assert not mc.file_path.is_file()

    def test_multiple_flushes_append(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.flush()
        mc.record("event_after_flush")
        mc.flush()
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["event_type"] == "event_after_flush"

    def test_no_temp_file_left(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.flush()
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0


# ── JSONL format ────────────────────────────────────────────

class TestJsonlFormat:
    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record("e1", {"k": "v"})
        mc.record_case_result("c1", "passed", duration_ms=50)
        mc.end_session({"total": 1})
        for line in mc.file_path.read_text(encoding="utf-8").strip().splitlines():
            data = json.loads(line)
            assert "timestamp" in data
            assert "event_type" in data
            assert "data" in data

    def test_timestamp_format(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.flush()
        data = json.loads(mc.file_path.read_text(encoding="utf-8").strip())
        ts = data["timestamp"]
        assert ts.endswith("Z")
        assert "T" in ts
