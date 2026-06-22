"""Unit tests for evidence.metrics — MetricsCollector (Story 3-4)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


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

    def test_flush_true_append_does_not_reread_file(self, tmp_path: Path) -> None:
        """AC3 regression: flush cost is O(buffer) not O(file_size).

        Pre-populate a large JSONL file, then flush new events and verify
        only new lines are added (file grows, old content untouched).
        """
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.flush()

        # Simulate existing large file by writing 1000 lines directly
        for i in range(1000):
            mc.file_path.open("a", encoding="utf-8").write(
                json.dumps({"event_type": "bulk", "data": {"i": i}}) + "\n"
            )

        file_size_before = mc.file_path.stat().st_size

        # Flush one new event
        mc.record("after_bulk")
        mc.flush()

        file_size_after = mc.file_path.stat().st_size
        # File should have grown by only ~one line, not rewritten
        assert file_size_after > file_size_before

        # Original first line untouched
        all_lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        assert json.loads(all_lines[0])["event_type"] == "session_start"
        # Last line is the new event
        assert json.loads(all_lines[-1])["event_type"] == "after_bulk"


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


# ── record_adapter_command (AC1) ────────────────────────────

class TestRecordAdapterCommand:
    def test_success_command(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_adapter_command("get_state", duration_ms=50, success=True)
        mc.flush()
        data = json.loads(mc.file_path.read_text(encoding="utf-8").strip().splitlines()[1])
        assert data["event_type"] == "adapter_command"
        assert data["data"]["command"] == "get_state"
        assert data["data"]["duration_ms"] == 50
        assert data["data"]["success"] is True

    def test_failed_command(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_adapter_command("click", duration_ms=120, success=False)
        mc.flush()
        data = json.loads(mc.file_path.read_text(encoding="utf-8").strip().splitlines()[1])
        assert data["data"]["success"] is False


# ── record_state_transition (AC1) ────────────────────────────

class TestRecordStateTransition:
    def test_transition_event(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_state_transition("MAIN_MENU", "COMBAT", duration_ms=200)
        mc.flush()
        data = json.loads(mc.file_path.read_text(encoding="utf-8").strip().splitlines()[1])
        assert data["event_type"] == "state_transition"
        assert data["data"]["from_state"] == "MAIN_MENU"
        assert data["data"]["to_state"] == "COMBAT"
        assert data["data"]["duration_ms"] == 200

    def test_transition_no_duration(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_state_transition("A", "B")
        mc.flush()
        data = json.loads(mc.file_path.read_text(encoding="utf-8").strip().splitlines()[1])
        assert data["data"]["duration_ms"] == 0


# ── record_screenshot (AC1) ──────────────────────────────────

class TestRecordScreenshot:
    def test_screenshot_event(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_screenshot("case_1", duration_ms=300, status="ok")
        mc.flush()
        data = json.loads(mc.file_path.read_text(encoding="utf-8").strip().splitlines()[1])
        assert data["event_type"] == "screenshot"
        assert data["data"]["case_id"] == "case_1"
        assert data["data"]["duration_ms"] == 300
        assert data["data"]["status"] == "ok"


# ── record_resource_usage (AC1) ──────────────────────────────

class TestRecordResourceUsage:
    def test_resource_event(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_resource_usage("memory_mb", 256.5)
        mc.flush()
        data = json.loads(mc.file_path.read_text(encoding="utf-8").strip().splitlines()[1])
        assert data["event_type"] == "resource_usage"
        assert data["data"]["metric"] == "memory_mb"
        assert data["data"]["value"] == 256.5


# ── get_summary (AC1) ────────────────────────────────────────

class TestGetSummary:
    def test_empty_summary(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        summary = mc.get_summary()
        assert summary["total_cases"] == 0
        assert summary["passed"] == 0
        assert summary["failed"] == 0
        assert summary["adapter_errors"] == 0

    def test_summary_with_mixed_events(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_case_result("c1", "passed", duration_ms=100)
        mc.record_case_result("c2", "failed", duration_ms=200, error_message="err")
        mc.record_case_result("c3", "passed", duration_ms=50)
        mc.record_adapter_command("get_state", 10, success=True)
        mc.record_adapter_command("click", 20, success=False)
        mc.record_state_transition("A", "B")
        mc.record_state_transition("B", "C")
        mc.record_screenshot("c1", 300, "ok")
        mc.record_screenshot("c2", 500, "ok")

        summary = mc.get_summary()
        assert summary["total_cases"] == 3
        assert summary["passed"] == 2
        assert summary["failed"] == 1
        assert summary["total_duration_ms"] == 350
        assert summary["adapter_commands"] == 2
        assert summary["adapter_errors"] == 1
        assert summary["state_transitions"] == 2
        assert summary["screenshots"] == 2
        assert summary["avg_screenshot_ms"] == 400.0

    def test_summary_no_avg_when_no_screenshots(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_case_result("c1", "passed", duration_ms=100)
        summary = mc.get_summary()
        assert "avg_screenshot_ms" not in summary

    def test_summary_survives_flush(self, tmp_path: Path) -> None:
        """NB2 regression: running counters persist across flush()."""
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_case_result("c1", "passed", duration_ms=100)
        mc.flush()
        summary = mc.get_summary()
        assert summary["total_cases"] == 1
        assert summary["passed"] == 1

    def test_summary_includes_resource_usage(self, tmp_path: Path) -> None:
        """NB1 regression: resource_usage events appear in summary."""
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_resource_usage("memory_mb", 256.0)
        mc.record_resource_usage("memory_mb", 300.0)
        mc.record_resource_usage("cpu_pct", 45.5)
        summary = mc.get_summary()
        ru = summary["resource_usage"]
        assert isinstance(ru, dict)
        assert ru["memory_mb"]["latest"] == 300.0
        assert ru["memory_mb"]["avg"] == 278.0
        assert ru["memory_mb"]["count"] == 2
        assert ru["cpu_pct"]["latest"] == 45.5

    def test_summary_no_resource_usage_when_none(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record_case_result("c1", "passed", duration_ms=10)
        summary = mc.get_summary()
        assert "resource_usage" not in summary


class TestSceneCoverage:
    def test_scene_coverage_summary_counts_known_scenes(self, tmp_path: Path) -> None:
        mc = MetricsCollector(tmp_path)
        mc.record_scene_visit("TC-1", "COMBAT")
        mc.record_scene_visit("TC-1", "MAP")
        mc.record_scene_visit("TC-2", "COMBAT")

        coverage = mc.get_scene_coverage()

        assert coverage["COMBAT"]["visits"] == 2
        assert coverage["COMBAT"]["cases"] == ["TC-1", "TC-2"]
        assert coverage["MAP"]["visits"] == 1
        assert coverage["MAP"]["cases"] == ["TC-1"]


# ── from_config ──────────────────────────────────────────────

class TestFromConfig:
    def test_constructs_from_config(self, tmp_path: Path) -> None:
        class _Cfg:
            evidence_dir = str(tmp_path)
            metrics_filename = "custom_metrics.jsonl"

        mc = MetricsCollector.from_config(_Cfg())
        assert mc.file_path == tmp_path / "custom_metrics.jsonl"

    def test_from_config_records_events(self, tmp_path: Path) -> None:
        class _Cfg:
            evidence_dir = str(tmp_path / "ev")
            metrics_filename = "metrics.jsonl"

        mc = MetricsCollector.from_config(_Cfg())
        mc.start_session("s1")
        mc.record_case_result("c1", "passed", duration_ms=50)
        mc.flush()
        assert mc.file_path.is_file()
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        data = json.loads(lines[0])
        assert data["event_type"] == "session_start"
        assert json.loads(lines[1])["event_type"] == "case_result"


# ── pending buffer (Story 4.4, AC5) ─────────────────────────

class TestPendingBuffer:
    def test_flush_failure_caches_to_pending(self, tmp_path: Path) -> None:
        """When flush fails, events are cached to pending buffer."""
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record("event1")

        # Force flush failure
        with patch("builtins.open", side_effect=OSError("no space")):
            mc.flush()

        assert len(mc._pending_buffer) == 1
        assert len(mc._buffer) == 0

    def test_pending_retried_on_next_flush(self, tmp_path: Path) -> None:
        """Pending events are retried on the next flush."""
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record("event1")

        # First flush fails
        with patch("builtins.open", side_effect=OSError("no space")):
            mc.flush()
        assert len(mc._pending_buffer) == 1

        # Second flush succeeds — pending should drain
        mc.flush()
        assert len(mc._pending_buffer) == 0
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2  # session_start + event1

    def test_pending_buffer_maxlen_discards_oldest(self, tmp_path: Path) -> None:
        """When pending buffer is full, oldest entries are discarded."""
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")

        # Fill pending buffer to near capacity
        from sts2_autotest.evidence.metrics import MetricEvent
        for i in range(99):
            mc._pending_buffer.append([MetricEvent(timestamp="t", event_type="old")])

        with patch("builtins.open", side_effect=OSError("no space")):
            mc.flush()

        # Should have entries (oldest discarded, newest kept)
        assert len(mc._pending_buffer) > 0

    def test_pending_buffer_capacity(self, tmp_path: Path) -> None:
        """Pending buffer maxlen=100, old entries discarded when full."""
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")

        for i in range(150):
            mc.record(f"event_{i}")
            with patch("builtins.open", side_effect=OSError("no space")):
                mc.flush()

        # Buffer should be at most 100
        assert len(mc._pending_buffer) <= 100

    def test_low_disk_space_caches_to_pending(self, tmp_path: Path) -> None:
        """AC3: low disk space during flush caches events to pending buffer."""
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record("event1")

        with patch(
            "sts2_autotest.evidence.metrics.check_disk_space", return_value=False,
        ):
            mc.flush()

        assert len(mc._pending_buffer) == 1
        assert len(mc._buffer) == 0
        # Must NOT create the metrics file
        assert not mc.file_path.is_file()

    def test_low_disk_space_pending_retried(self, tmp_path: Path) -> None:
        """Events cached due to low disk are retried when space returns."""
        mc = MetricsCollector(tmp_path)
        mc.start_session("s1")
        mc.record("event1")

        # First flush — low disk space, caches to pending
        with patch(
            "sts2_autotest.evidence.metrics.check_disk_space", return_value=False,
        ):
            mc.flush()
        assert len(mc._pending_buffer) == 1

        # Second flush — space returned, pending drains
        mc.flush()
        assert len(mc._pending_buffer) == 0
        lines = mc.file_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2  # session_start + event1


# ── resource_usage cap (Story 4.4, debt #5) ────────────────

class TestResourceUsageCap:
    def test_resource_usage_capped(self, tmp_path: Path) -> None:
        """_resource_usage entries are capped at max_resource_entries."""
        mc = MetricsCollector(tmp_path, max_resource_entries=5)
        mc.start_session("s1")

        for i in range(10):
            mc.record_resource_usage("memory_mb", float(i * 10))

        assert len(mc._resource_usage["memory_mb"]) == 5
        # Should have the LAST 5 entries (oldest discarded)
        assert mc._resource_usage["memory_mb"] == [50.0, 60.0, 70.0, 80.0, 90.0]

    def test_resource_usage_below_cap(self, tmp_path: Path) -> None:
        """Entries below cap are kept in full."""
        mc = MetricsCollector(tmp_path, max_resource_entries=100)
        mc.start_session("s1")

        for i in range(3):
            mc.record_resource_usage("cpu", float(i))

        assert len(mc._resource_usage["cpu"]) == 3
