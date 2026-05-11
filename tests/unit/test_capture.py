"""Tests for evidence/capture.py — ScreenCapture, CaptureResult, RGB validation."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sts2_autotest.common.types import CaptureResult
from sts2_autotest.evidence.capture import (
    ScreenCapture,
    _parse_resolution,
)


# ── Helpers ──────────────────────────────────────────────────


def _make_bgra_data(
    width: int = 1920, height: int = 1080, color: tuple[int, int, int] = (128, 64, 32)
) -> bytes:
    """Create fake BGRA pixel data with a single color."""
    b, g, r = color
    pixel = bytes([b, g, r, 255])  # BGRA
    return pixel * (width * height)


def _make_bgra_varied(
    width: int = 1920, height: int = 1080, num_colors: int = 10
) -> bytes:
    """Create BGRA data with multiple distinct colors."""
    pixels = bytearray()
    for i in range(width * height):
        color_idx = i % num_colors
        r = color_idx * 25
        g = (color_idx * 17) % 256
        b = (color_idx * 31) % 256
        pixels.extend([b, g, r, 255])  # BGRA
    return bytes(pixels)


def _make_screenshot_mock(
    bgra_data: bytes, width: int, height: int
) -> MagicMock:
    """Create a mock mss screenshot object."""
    shot = MagicMock()
    shot.bgra = bgra_data
    shot.width = width
    shot.height = height
    return shot


# ── _parse_resolution ────────────────────────────────────────


class TestParseResolution:
    def test_valid(self) -> None:
        assert _parse_resolution("1920x1080") == (1920, 1080)

    def test_case_insensitive(self) -> None:
        assert _parse_resolution("1280X720") == (1280, 720)

    def test_invalid_format(self) -> None:
        with pytest.raises(ValueError, match="Invalid resolution"):
            _parse_resolution("1920")

    def test_invalid_format_extra(self) -> None:
        with pytest.raises(ValueError, match="Invalid resolution"):
            _parse_resolution("1920x1080x32")


# ── CaptureResult ────────────────────────────────────────────


class TestCaptureResult:
    def test_ok_property(self) -> None:
        r = CaptureResult(status="ok", path=Path("/tmp/test.png"))
        assert r.ok

    def test_not_ok_property(self) -> None:
        r = CaptureResult(status="error", message="validation failed")
        assert not r.ok

    def test_skipped_not_ok(self) -> None:
        r = CaptureResult(status="skipped")
        assert not r.ok


# ── ScreenCapture init ───────────────────────────────────────


class TestScreenCaptureInit:
    def test_defaults(self, tmp_path: Path) -> None:
        sc = ScreenCapture(tmp_path)
        assert sc._rgb_threshold == 3
        assert sc._target_resolution == (1920, 1080)
        assert sc._resolution_tolerance == 2
        assert sc._min_file_bytes == 1024
        assert sc._max_retries == 3

    def test_custom(self, tmp_path: Path) -> None:
        sc = ScreenCapture(
            tmp_path,
            rgb_threshold=5,
            target_resolution="1280x720",
            resolution_tolerance=5,
            min_file_bytes=2048,
            max_retries=5,
        )
        assert sc._rgb_threshold == 5
        assert sc._target_resolution == (1280, 720)
        assert sc._resolution_tolerance == 5
        assert sc._min_file_bytes == 2048
        assert sc._max_retries == 5


# ── _count_distinct_rgb ──────────────────────────────────────


class TestCountDistinctRgb:
    def test_single_color_invalid(self, tmp_path: Path) -> None:
        sc = ScreenCapture(tmp_path, rgb_threshold=3)
        bgra = _make_bgra_data(color=(0, 0, 0))  # All black
        ok, count = sc._count_distinct_rgb(bgra)
        assert not ok
        assert count == 1

    def test_two_colors_invalid(self, tmp_path: Path) -> None:
        sc = ScreenCapture(tmp_path, rgb_threshold=3)
        bgra = _make_bgra_varied(num_colors=2)
        ok, count = sc._count_distinct_rgb(bgra)
        assert not ok
        assert count == 2

    def test_many_colors_valid(self, tmp_path: Path) -> None:
        sc = ScreenCapture(tmp_path, rgb_threshold=3)
        bgra = _make_bgra_varied(num_colors=10)
        ok, count = sc._count_distinct_rgb(bgra)
        assert ok
        assert count >= 3

    def test_threshold_exactly_met(self, tmp_path: Path) -> None:
        sc = ScreenCapture(tmp_path, rgb_threshold=3)
        # Use num_colors coprime with sampling step to ensure all colors are sampled
        bgra = _make_bgra_varied(num_colors=7)
        ok, count = sc._count_distinct_rgb(bgra)
        assert ok
        assert count >= 3

    def test_empty_data(self, tmp_path: Path) -> None:
        sc = ScreenCapture(tmp_path, rgb_threshold=3)
        ok, count = sc._count_distinct_rgb(b"")
        assert not ok
        assert count == 0

    def test_custom_threshold(self, tmp_path: Path) -> None:
        sc = ScreenCapture(tmp_path, rgb_threshold=5)
        bgra = _make_bgra_varied(num_colors=4)
        ok, count = sc._count_distinct_rgb(bgra)
        assert not ok
        assert count == 4


# ── _check_resolution ────────────────────────────────────────


class TestCheckResolution:
    def test_exact_match(self, tmp_path: Path) -> None:
        sc = ScreenCapture(tmp_path)
        assert sc._check_resolution((1920, 1080))

    def test_within_tolerance(self, tmp_path: Path) -> None:
        sc = ScreenCapture(tmp_path, resolution_tolerance=2)
        assert sc._check_resolution((1918, 1080))
        assert sc._check_resolution((1920, 1082))

    def test_outside_tolerance(self, tmp_path: Path) -> None:
        sc = ScreenCapture(tmp_path, resolution_tolerance=2)
        assert not sc._check_resolution((1917, 1080))
        assert not sc._check_resolution((1920, 1083))

    def test_custom_target(self, tmp_path: Path) -> None:
        sc = ScreenCapture(tmp_path, target_resolution="1280x720")
        assert sc._check_resolution((1280, 720))
        assert not sc._check_resolution((1920, 1080))


# ── _save_screenshot (atomic write) ─────────────────────────


class TestSaveScreenshot:
    @patch("sts2_autotest.evidence.capture.mss.tools.to_png")
    def test_creates_file(self, mock_to_png: MagicMock, tmp_path: Path) -> None:
        # mss.tools.to_png returns PNG bytes
        mock_to_png.return_value = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000
        sc = ScreenCapture(tmp_path)
        bgra = _make_bgra_data()
        path = sc._save_screenshot(bgra, 1920, 1080, "test-case")
        assert path.exists()
        assert path.name.startswith("test-case_")
        assert path.suffix == ".png"
        assert path.stat().st_size >= 2000

    @patch("sts2_autotest.evidence.capture.mss.tools.to_png")
    def test_filename_has_timestamp(self, mock_to_png: MagicMock, tmp_path: Path) -> None:
        mock_to_png.return_value = b"\x89PNG" + b"\x00" * 2000
        sc = ScreenCapture(tmp_path)
        bgra = _make_bgra_data()
        path = sc._save_screenshot(bgra, 1920, 1080, "my-test")
        # Should contain case_id + UTC timestamp + ms
        name = path.stem
        assert name.startswith("my-test_")
        parts = name.split("_")
        assert len(parts) >= 3  # case_id, date, time, ms

    @patch("sts2_autotest.evidence.capture.mss.tools.to_png")
    def test_no_temp_file_left(self, mock_to_png: MagicMock, tmp_path: Path) -> None:
        mock_to_png.return_value = b"\x89PNG" + b"\x00" * 2000
        sc = ScreenCapture(tmp_path)
        bgra = _make_bgra_data()
        sc._save_screenshot(bgra, 1920, 1080, "clean")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    @patch("sts2_autotest.evidence.capture.mss.tools.to_png")
    def test_creates_output_dir(self, mock_to_png: MagicMock, tmp_path: Path) -> None:
        mock_to_png.return_value = b"\x89PNG" + b"\x00" * 2000
        nested = tmp_path / "sub" / "dir"
        sc = ScreenCapture(nested)
        bgra = _make_bgra_data()
        path = sc._save_screenshot(bgra, 1920, 1080, "nested")
        assert nested.exists()
        assert path.exists()


# ── capture (simple capture, no validation) ──────────────────


class TestCapture:
    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_success(self, mock_mss_cls: MagicMock, tmp_path: Path) -> None:
        bgra = _make_bgra_varied()
        shot = _make_screenshot_mock(bgra, 1920, 1080)
        sct = MagicMock()
        sct.monitors = [{}, {"left": 0, "top": 0, "width": 1920, "height": 1080}]
        sct.grab.return_value = shot
        sct.__enter__ = MagicMock(return_value=sct)
        sct.__exit__ = MagicMock(return_value=False)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        result = sc.capture("TestWindow", "test-case")

        assert result.status == "ok"
        assert result.path is not None
        assert result.resolution == (1920, 1080)

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_no_monitor(self, mock_mss_cls: MagicMock, tmp_path: Path) -> None:
        sct = MagicMock()
        sct.monitors = [{}]  # Only virtual screen, no physical monitors
        sct.__enter__ = MagicMock(return_value=sct)
        sct.__exit__ = MagicMock(return_value=False)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        result = sc.capture("TestWindow", "test-case")

        assert result.status == "skipped"

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_mss_exception(self, mock_mss_cls: MagicMock, tmp_path: Path) -> None:
        mock_mss_cls.side_effect = RuntimeError("mss init failed")

        sc = ScreenCapture(tmp_path)
        result = sc.capture("TestWindow", "test-case")

        assert result.status == "skipped"


# ── capture_with_validation (full flow) ──────────────────────


class TestCaptureWithValidation:
    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_valid_capture(self, mock_mss_cls: MagicMock, tmp_path: Path) -> None:
        bgra = _make_bgra_varied(num_colors=10)
        shot = _make_screenshot_mock(bgra, 1920, 1080)
        sct = MagicMock()
        sct.monitors = [{}, {"left": 0, "top": 0, "width": 1920, "height": 1080}]
        sct.grab.return_value = shot
        sct.__enter__ = MagicMock(return_value=sct)
        sct.__exit__ = MagicMock(return_value=False)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        result = sc.capture_with_validation("TestWindow", "test-case")

        assert result.status == "ok"
        assert result.path is not None
        assert result.rgb_count is not None
        assert result.rgb_count >= 3
        assert result.resolution == (1920, 1080)

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_solid_color_retries_then_error(
        self, mock_mss_cls: MagicMock, tmp_path: Path
    ) -> None:
        bgra = _make_bgra_data(color=(0, 0, 0))  # All black
        shot = _make_screenshot_mock(bgra, 1920, 1080)
        sct = MagicMock()
        sct.monitors = [{}, {"left": 0, "top": 0, "width": 1920, "height": 1080}]
        sct.grab.return_value = shot
        sct.__enter__ = MagicMock(return_value=sct)
        sct.__exit__ = MagicMock(return_value=False)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path, max_retries=3)
        result = sc.capture_with_validation("TestWindow", "test-case")

        assert result.status == "error"
        assert "RGB validation failed" in (result.message or "")
        # Should have attempted 3 captures (retries)
        assert sct.grab.call_count == 3

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_bad_resolution_marks_error(
        self, mock_mss_cls: MagicMock, tmp_path: Path
    ) -> None:
        bgra = _make_bgra_varied(num_colors=10)
        # Wrong resolution (800x600)
        shot = _make_screenshot_mock(bgra, 800, 600)
        sct = MagicMock()
        sct.monitors = [{}, {"left": 0, "top": 0, "width": 800, "height": 600}]
        sct.grab.return_value = shot
        sct.__enter__ = MagicMock(return_value=sct)
        sct.__exit__ = MagicMock(return_value=False)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path, max_retries=1)
        result = sc.capture_with_validation("TestWindow", "test-case")

        assert result.status == "error"
        assert "Resolution out of tolerance" in (result.message or "")

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_window_not_found_still_captures(
        self, mock_mss_cls: MagicMock, tmp_path: Path
    ) -> None:
        bgra = _make_bgra_varied(num_colors=10)
        shot = _make_screenshot_mock(bgra, 1920, 1080)
        sct = MagicMock()
        sct.monitors = [{}, {"left": 0, "top": 0, "width": 1920, "height": 1080}]
        sct.grab.return_value = shot
        sct.__enter__ = MagicMock(return_value=sct)
        sct.__exit__ = MagicMock(return_value=False)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        with patch.object(sc, "_foreground_window", return_value=False):
            result = sc.capture_with_validation("MissingWindow", "test-case")

        # Should still succeed — foreground failure is non-blocking
        assert result.status == "ok"

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_no_monitor_returns_skipped(
        self, mock_mss_cls: MagicMock, tmp_path: Path
    ) -> None:
        sct = MagicMock()
        sct.monitors = [{}]
        sct.__enter__ = MagicMock(return_value=sct)
        sct.__exit__ = MagicMock(return_value=False)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        result = sc.capture_with_validation("TestWindow", "test-case")

        assert result.status == "skipped"

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_recovery_after_retry(
        self, mock_mss_cls: MagicMock, tmp_path: Path
    ) -> None:
        """First capture is solid, second has colors — should succeed."""
        bad_bgra = _make_bgra_data(color=(0, 0, 0))
        good_bgra = _make_bgra_varied(num_colors=10)

        bad_shot = _make_screenshot_mock(bad_bgra, 1920, 1080)
        good_shot = _make_screenshot_mock(good_bgra, 1920, 1080)

        sct = MagicMock()
        sct.monitors = [{}, {"left": 0, "top": 0, "width": 1920, "height": 1080}]
        sct.grab.side_effect = [bad_shot, good_shot]
        sct.__enter__ = MagicMock(return_value=sct)
        sct.__exit__ = MagicMock(return_value=False)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path, max_retries=2)
        result = sc.capture_with_validation("TestWindow", "test-case")

        assert result.status == "ok"
        assert sct.grab.call_count == 2


# ── _foreground_window (Win32) ───────────────────────────────


class TestForegroundWindow:
    @patch("sts2_autotest.evidence.capture.ctypes")
    def test_success(self, mock_ctypes: MagicMock, tmp_path: Path) -> None:
        mock_user32 = MagicMock()
        mock_user32.FindWindowW.return_value = 12345  # Non-zero HWND
        mock_ctypes.windll.user32 = mock_user32

        sc = ScreenCapture(tmp_path)
        assert sc._foreground_window("TestWindow") is True
        mock_user32.SetForegroundWindow.assert_called_once_with(12345)

    @patch("sts2_autotest.evidence.capture.ctypes")
    def test_window_not_found(self, mock_ctypes: MagicMock, tmp_path: Path) -> None:
        mock_user32 = MagicMock()
        mock_user32.FindWindowW.return_value = 0  # NULL HWND
        mock_ctypes.windll.user32 = mock_user32

        sc = ScreenCapture(tmp_path)
        assert sc._foreground_window("MissingWindow") is False

    @patch("sts2_autotest.evidence.capture.ctypes")
    def test_win32_exception(self, mock_ctypes: MagicMock, tmp_path: Path) -> None:
        mock_ctypes.windll = AttributeError("No windll")

        sc = ScreenCapture(tmp_path)
        assert sc._foreground_window("TestWindow") is False


# ── FrameworkConfig screenshot fields ────────────────────────


class TestFrameworkConfigScreenshot:
    def test_screenshot_defaults(self) -> None:
        from sts2_autotest.config.schema import FrameworkConfig

        cfg = FrameworkConfig()
        assert cfg.screenshot_rgb_threshold == 3
        assert cfg.screenshot_target_resolution == "1920x1080"
        assert cfg.screenshot_resolution_tolerance == 2
        assert cfg.screenshot_min_file_bytes == 1024
        assert cfg.screenshot_max_retries == 3

    def test_screenshot_custom(self) -> None:
        from sts2_autotest.config.schema import FrameworkConfig

        cfg = FrameworkConfig(
            screenshot_rgb_threshold=5,
            screenshot_target_resolution="1280x720",
            screenshot_resolution_tolerance=5,
            screenshot_min_file_bytes=2048,
            screenshot_max_retries=5,
        )
        assert cfg.screenshot_rgb_threshold == 5
        assert cfg.screenshot_target_resolution == "1280x720"
        assert cfg.screenshot_resolution_tolerance == 5
        assert cfg.screenshot_min_file_bytes == 2048
        assert cfg.screenshot_max_retries == 5

    def test_invalid_rgb_threshold(self) -> None:
        from pydantic import ValidationError

        from sts2_autotest.config.schema import FrameworkConfig

        with pytest.raises(ValidationError):
            FrameworkConfig(screenshot_rgb_threshold=0)

    def test_invalid_resolution_tolerance(self) -> None:
        from pydantic import ValidationError

        from sts2_autotest.config.schema import FrameworkConfig

        with pytest.raises(ValidationError):
            FrameworkConfig(screenshot_resolution_tolerance=-1)


# ── RealEvidenceHooks ────────────────────────────────────────


class TestRealEvidenceHooks:
    def test_on_case_end_captures(self) -> None:
        from sts2_autotest.core.evidence_hooks import RealEvidenceHooks
        from sts2_autotest.core.action_model import TestResult

        mock_capture = MagicMock()
        mock_capture.capture_with_validation.return_value = CaptureResult(
            status="ok", path=Path("/tmp/screenshot.png")
        )

        hooks = RealEvidenceHooks(mock_capture, window_title="TestGame")
        result = TestResult(case_id="test-1", status="pass")
        hooks.on_case_end(result)

        mock_capture.capture_with_validation.assert_called_once_with("TestGame", "test-1")

    def test_on_case_end_skipped(self) -> None:
        from sts2_autotest.core.evidence_hooks import RealEvidenceHooks
        from sts2_autotest.core.action_model import TestResult

        mock_capture = MagicMock()
        mock_capture.capture_with_validation.return_value = CaptureResult(
            status="skipped", message="Window not found"
        )

        hooks = RealEvidenceHooks(mock_capture)
        result = TestResult(case_id="test-2", status="fail")
        hooks.on_case_end(result)

        # Should not raise, just log warning

    def test_on_crash_captures(self) -> None:
        from sts2_autotest.core.evidence_hooks import RealEvidenceHooks

        mock_capture = MagicMock()
        mock_capture.capture.return_value = CaptureResult(
            status="ok", path=Path("/tmp/crash.png")
        )

        hooks = RealEvidenceHooks(mock_capture)
        hooks.on_crash("test-3", RuntimeError("Game crashed"))

        mock_capture.capture.assert_called_once()

    def test_on_session_end(self) -> None:
        from sts2_autotest.core.evidence_hooks import RealEvidenceHooks

        mock_capture = MagicMock()
        mock_packager = MagicMock()
        hooks = RealEvidenceHooks(mock_capture, packager=mock_packager)
        hooks.on_session_end({"passed": 5, "failed": 1, "crashed": 0, "skipped": 0})
        mock_packager.create_pack.assert_called_once_with(run_result="failed")

    def test_on_session_end_passed(self) -> None:
        from sts2_autotest.core.evidence_hooks import RealEvidenceHooks

        mock_capture = MagicMock()
        mock_packager = MagicMock()
        hooks = RealEvidenceHooks(mock_capture, packager=mock_packager)
        hooks.on_session_end({"passed": 5, "failed": 0, "crashed": 0, "skipped": 0})
        mock_packager.create_pack.assert_called_once_with(run_result="passed")

    def test_on_case_end_collects_logs_on_failure(self) -> None:
        from sts2_autotest.core.evidence_hooks import RealEvidenceHooks
        from sts2_autotest.core.action_model import TestResult

        mock_capture = MagicMock()
        mock_capture.capture_with_validation.return_value = CaptureResult(status="ok", path=Path("/tmp/s.png"))
        mock_log_collector = MagicMock()
        hooks = RealEvidenceHooks(mock_capture, log_collector=mock_log_collector)
        result = TestResult(case_id="fail-case", status="fail")
        hooks.on_case_end(result)
        mock_log_collector.collect_on_failure.assert_called_once_with("fail-case")

    def test_on_crash_collects_logs(self) -> None:
        from sts2_autotest.core.evidence_hooks import RealEvidenceHooks

        mock_capture = MagicMock()
        mock_capture.capture.return_value = CaptureResult(status="ok", path=Path("/tmp/c.png"))
        mock_log_collector = MagicMock()
        hooks = RealEvidenceHooks(mock_capture, log_collector=mock_log_collector)
        hooks.on_crash("crash-case", RuntimeError("boom"))
        mock_log_collector.collect_on_failure.assert_called_once_with("crash-case_crash")


# ── handlers.py capture_screenshot upgrade ───────────────────


class TestCaptureScreenshotHandler:
    def test_with_real_evidence_hooks(self) -> None:
        from sts2_autotest.dsl.handlers import capture_screenshot
        from sts2_autotest.core.orchestrator import TestOrchestrator

        mock_capture = MagicMock()
        mock_capture.capture_with_validation.return_value = CaptureResult(
            status="ok", path=Path("/tmp/handler.png")
        )

        # Create orchestrator with RealEvidenceHooks
        mock_adapter = MagicMock()
        hooks = MagicMock()
        hooks._capture = mock_capture
        orch = TestOrchestrator(adapter=mock_adapter, evidence=hooks)

        # Should not raise
        capture_screenshot(orch, "test-case")
        mock_capture.capture_with_validation.assert_called_once()

    def test_without_capture_fallback(self) -> None:
        from sts2_autotest.dsl.handlers import capture_screenshot
        from sts2_autotest.core.evidence_hooks import StubEvidenceHooks
        from sts2_autotest.core.orchestrator import TestOrchestrator

        mock_adapter = MagicMock()
        stub_hooks = StubEvidenceHooks()
        orch = TestOrchestrator(adapter=mock_adapter, evidence=stub_hooks)

        # Should not raise, just log warning
        capture_screenshot(orch, "test-case")
