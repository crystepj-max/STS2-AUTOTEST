"""Tests for evidence/capture.py — ScreenCapture, CaptureResult, RGB validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sts2_autotest.evidence.capture as capture_module
from sts2_autotest.common.types import CaptureResult
from sts2_autotest.evidence.capture import (
    ScreenCapture,
    _export_macos_jpeg,
    _inspect_macos_png,
    _normalize_macos_png,
    _parse_resolution,
    _restore_window,
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


def _make_bgra_small_region(
    width: int = 1920, height: int = 1080
) -> bytes:
    """Create BGRA data that is mostly black but has a small colored region.

    This tests that full-pixel traversal catches small colored areas
    that sparse sampling would miss.
    """
    # Start with all black
    pixels = bytearray(_make_bgra_data(width, height, color=(0, 0, 0)))
    # Paint a 5x5 region with distinct colors in the middle
    for dy in range(5):
        for dx in range(5):
            row = (height // 2) + dy
            col = (width // 2) + dx
            offset = (row * width + col) * 4
            # Assign distinct colors: (r*50, g*30, b*20)
            pixels[offset] = (dx * 20) % 256       # B
            pixels[offset + 1] = (dy * 30) % 256   # G
            pixels[offset + 2] = ((dx + dy) * 50) % 256  # R
            pixels[offset + 3] = 255                # A
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


def _make_mss_mock(
    bgra_data: bytes, width: int, height: int
) -> MagicMock:
    """Create a complete mock mss context manager."""
    shot = _make_screenshot_mock(bgra_data, width, height)
    sct = MagicMock()
    sct.monitors = [{}, {"left": 0, "top": 0, "width": width, "height": height}]
    sct.grab.return_value = shot
    sct.__enter__ = MagicMock(return_value=sct)
    sct.__exit__ = MagicMock(return_value=False)
    return sct


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


# ── ScreenCapture.from_config ────────────────────────────────


class TestScreenCaptureFromConfig:
    def test_from_config_with_framework_config(self, tmp_path: Path) -> None:
        from sts2_autotest.config.schema import FrameworkConfig

        cfg = FrameworkConfig(
            screenshot_rgb_threshold=7,
            screenshot_target_resolution="1280x720",
            screenshot_resolution_tolerance=5,
            screenshot_min_file_bytes=2048,
            screenshot_max_retries=1,
        )
        sc = ScreenCapture.from_config(tmp_path, cfg)
        assert sc._rgb_threshold == 7
        assert sc._target_resolution == (1280, 720)
        assert sc._resolution_tolerance == 5
        assert sc._min_file_bytes == 2048
        assert sc._max_retries == 1

    def test_from_config_defaults(self, tmp_path: Path) -> None:
        from sts2_autotest.config.schema import FrameworkConfig

        cfg = FrameworkConfig()
        sc = ScreenCapture.from_config(tmp_path, cfg)
        assert sc._rgb_threshold == 3
        assert sc._target_resolution == (1920, 1080)
        assert sc._resolution_tolerance == 2
        assert sc._min_file_bytes == 1024
        assert sc._max_retries == 3

    def test_custom_config_affects_validation(self, tmp_path: Path) -> None:
        """Prove custom FrameworkConfig values affect validation behavior."""
        from sts2_autotest.config.schema import FrameworkConfig

        # Use a high RGB threshold — 3-color image should fail
        cfg_strict = FrameworkConfig(screenshot_rgb_threshold=10)
        sc = ScreenCapture.from_config(tmp_path, cfg_strict)
        bgra = _make_bgra_varied(num_colors=3)
        ok, _ = sc._count_distinct_rgb(bgra)
        assert not ok  # 3 distinct colors < threshold 10

        # Use default threshold — same image should pass
        cfg_default = FrameworkConfig()
        sc2 = ScreenCapture.from_config(tmp_path, cfg_default)
        ok2, _ = sc2._count_distinct_rgb(bgra)
        assert ok2  # 3 distinct colors >= threshold 3

    def test_custom_resolution_affects_check(self, tmp_path: Path) -> None:
        """Prove custom resolution config affects resolution check."""
        from sts2_autotest.config.schema import FrameworkConfig

        # 1280x720 target — 1920x1080 should fail
        cfg = FrameworkConfig(screenshot_target_resolution="1280x720")
        sc = ScreenCapture.from_config(tmp_path, cfg)
        assert not sc._check_resolution((1920, 1080))
        assert sc._check_resolution((1280, 720))


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

    def test_full_traversal_catches_small_region(self, tmp_path: Path) -> None:
        """Full-pixel traversal must detect a small colored region in
        an otherwise all-black image. This would fail under sparse sampling."""
        sc = ScreenCapture(tmp_path, rgb_threshold=3)
        bgra = _make_bgra_small_region(width=1920, height=1080)
        ok, count = sc._count_distinct_rgb(bgra)
        # The small region adds multiple distinct colors — should be detected
        assert ok
        assert count >= 3


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
    @pytest.fixture(autouse=True)
    def _force_legacy_monitor_capture(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """这些旧用例验证 mss 分支；macOS 窗口分支单独验证。"""
        monkeypatch.setattr(capture_module.platform, "system", lambda: "Windows")

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_success(self, mock_mss_cls: MagicMock, tmp_path: Path) -> None:
        sct = _make_mss_mock(_make_bgra_varied(), 1920, 1080)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        result = sc.capture("TestWindow", "test-case")

        assert result.status == "ok"
        assert result.path is not None
        assert result.resolution == (1920, 1080)

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_no_monitor(self, mock_mss_cls: MagicMock, tmp_path: Path) -> None:
        sct = MagicMock()
        sct.monitors = [{}]
        sct.__enter__ = MagicMock(return_value=sct)
        sct.__exit__ = MagicMock(return_value=False)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        result = sc.capture("TestWindow", "test-case")

        assert result.status == "skipped"

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_mss_exception(self, mock_mss_cls: MagicMock, tmp_path: Path) -> None:
        from mss.exception import ScreenShotError
        mock_mss_cls.side_effect = ScreenShotError("mss init failed")

        sc = ScreenCapture(tmp_path)
        result = sc.capture("TestWindow", "test-case")

        assert result.status == "skipped"

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_save_oserror_returns_skipped(
        self, mock_mss_cls: MagicMock, tmp_path: Path
    ) -> None:
        """Non-blocking OSError from save returns SKIPPED (AC5/NB1)."""
        sct = _make_mss_mock(_make_bgra_varied(), 1920, 1080)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        with patch.object(sc, "_save_screenshot", side_effect=OSError("disk full")):
            result = sc.capture("TestWindow", "test-case")

        assert result.status == "skipped"
        assert "disk full" in (result.message or "")


class TestMacOSCapture:
    def test_png_normalization_replaces_source_with_standard_output(self, tmp_path: Path) -> None:
        path = tmp_path / "event.png"
        path.write_bytes(b"source")

        def fake_sips(command: list[str], **_kwargs: object) -> MagicMock:
            Path(command[-1]).write_bytes(b"normalized" + b"\x00" * 1024)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("sts2_autotest.evidence.capture.subprocess.run", side_effect=fake_sips):
            assert _normalize_macos_png(path) is None
        assert path.read_bytes().startswith(b"normalized")
        assert not (tmp_path / "event.normalized.png").exists()

    def test_jpeg_normalization_requests_compatible_output(self, tmp_path: Path) -> None:
        path = tmp_path / "event.jpg"
        path.write_bytes(b"source")
        command: list[str] = []

        def fake_sips(args: list[str], **_kwargs: object) -> MagicMock:
            command.extend(args)
            Path(args[-1]).write_bytes(b"normalized" + b"\x00" * 1024)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("sts2_autotest.evidence.capture.subprocess.run", side_effect=fake_sips):
            assert _normalize_macos_png(path) is None
        assert "--resampleHeightWidth" in command
        assert "jpeg" in command
        assert path.read_bytes().startswith(b"normalized")

    def test_jpeg_export_uses_window_bounded_source(self, tmp_path: Path) -> None:
        source = tmp_path / "event.png"
        target = tmp_path / "event.jpg"
        source.write_bytes(b"source")

        def fake_sips(args: list[str], **_kwargs: object) -> MagicMock:
            Path(args[-1]).write_bytes(b"jpeg" + b"\x00" * 1024)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("sts2_autotest.evidence.capture.subprocess.run", side_effect=fake_sips):
            assert _export_macos_jpeg(source, target) is None
        assert target.read_bytes().startswith(b"jpeg")

    def test_png_normalization_reports_converter_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "event.png"
        path.write_bytes(b"source")
        completed = MagicMock(returncode=1, stdout="", stderr="conversion failed")
        with patch("sts2_autotest.evidence.capture.subprocess.run", return_value=completed):
            assert _normalize_macos_png(path) == "conversion failed"

    def test_png_inspection_returns_physical_resolution_and_content(self, tmp_path: Path) -> None:
        path = tmp_path / "event.png"
        path.write_bytes(b"png")
        completed = MagicMock(returncode=0, stdout="3008\t1692\t0.427000\n", stderr="")
        with patch("sts2_autotest.evidence.capture.subprocess.run", return_value=completed):
            assert _inspect_macos_png(path) == ((3008, 1692), 0.427, None)

    def test_png_inspection_rejects_black_band(self, tmp_path: Path) -> None:
        path = tmp_path / "black-band.png"
        path.write_bytes(b"png")
        completed = MagicMock(returncode=0, stdout="3008\t1692\t0.000000\n", stderr="")
        with patch("sts2_autotest.evidence.capture.subprocess.run", return_value=completed):
            resolution, ratio, error = _inspect_macos_png(path)
        assert resolution == (3008, 1692)
        assert ratio == 0.0
        assert error == "one horizontal image band is effectively black"

    def test_validation_reports_physical_resolution(self, tmp_path: Path) -> None:
        def fake_capture(path: Path, _window_title: str) -> tuple[bool, tuple[int, int]]:
            path.write_bytes(b"usable screenshot" + b"\x00" * 1024)
            return True, (3008, 1692)

        sc = ScreenCapture(tmp_path)
        with patch.object(capture_module.platform, "system", return_value="Darwin"), \
             patch("sts2_autotest.evidence.capture._capture_macos_window_png", side_effect=fake_capture):
            result = sc.capture_with_validation("Slay the Spire 2", "event")
        assert result.status == "ok"
        assert result.resolution == (3008, 1692)
        assert result.path is not None
        assert result.path.suffix == ".jpg"


# ── capture_with_validation (full flow) ──────────────────────


class TestCaptureWithValidation:
    @pytest.fixture(autouse=True)
    def _force_legacy_monitor_capture(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """这些旧用例验证 mss 分支；macOS 窗口分支单独验证。"""
        monkeypatch.setattr(capture_module.platform, "system", lambda: "Windows")

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_valid_capture(self, mock_mss_cls: MagicMock, tmp_path: Path) -> None:
        sct = _make_mss_mock(_make_bgra_varied(num_colors=10), 1920, 1080)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        with patch("sts2_autotest.evidence.capture._restore_window", return_value=True):
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
        sct = _make_mss_mock(bgra, 1920, 1080)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path, max_retries=3)
        with patch("sts2_autotest.evidence.capture._restore_window", return_value=True):
            result = sc.capture_with_validation("TestWindow", "test-case")

        assert result.status == "error"
        assert "RGB validation failed" in (result.message or "")
        assert sct.grab.call_count == 3

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_bad_resolution_marks_error(
        self, mock_mss_cls: MagicMock, tmp_path: Path
    ) -> None:
        bgra = _make_bgra_varied(num_colors=10)
        sct = _make_mss_mock(bgra, 800, 600)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path, max_retries=1)
        with patch("sts2_autotest.evidence.capture._restore_window", return_value=True):
            result = sc.capture_with_validation("TestWindow", "test-case")

        assert result.status == "error"
        assert "Resolution out of tolerance" in (result.message or "")

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_window_not_found_returns_skipped(
        self, mock_mss_cls: MagicMock, tmp_path: Path
    ) -> None:
        """AC5: Window not found → SKIPPED, not ok."""
        bgra = _make_bgra_varied(num_colors=10)
        sct = _make_mss_mock(bgra, 1920, 1080)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        with patch("sts2_autotest.evidence.capture._restore_window", return_value=False):
            result = sc.capture_with_validation("MissingWindow", "test-case")

        assert result.status == "skipped"
        assert "not found" in (result.message or "").lower()
        # Must NOT attempt capture
        sct.grab.assert_not_called()

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
        with patch("sts2_autotest.evidence.capture._restore_window", return_value=True):
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
        with patch("sts2_autotest.evidence.capture._restore_window", return_value=True):
            result = sc.capture_with_validation("TestWindow", "test-case")

        assert result.status == "ok"
        assert sct.grab.call_count == 2

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_save_oserror_returns_skipped(
        self, mock_mss_cls: MagicMock, tmp_path: Path
    ) -> None:
        """Non-blocking OSError from save in validation flow (NB1)."""
        sct = _make_mss_mock(_make_bgra_varied(), 1920, 1080)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        with patch("sts2_autotest.evidence.capture._restore_window", return_value=True), \
             patch.object(sc, "_save_screenshot", side_effect=OSError("no space")):
            result = sc.capture_with_validation("TestWindow", "test-case")

        assert result.status == "skipped"
        assert "no space" in (result.message or "")

    @patch("sts2_autotest.evidence.capture.mss.mss")
    def test_low_disk_space_skips_write(
        self, mock_mss_cls: MagicMock, tmp_path: Path
    ) -> None:
        """AC3: low disk space makes _save_screenshot raise OSError → SKIPPED."""
        sct = _make_mss_mock(_make_bgra_varied(), 1920, 1080)
        mock_mss_cls.return_value = sct

        sc = ScreenCapture(tmp_path)
        with patch("sts2_autotest.evidence.capture._restore_window", return_value=True), \
             patch("sts2_autotest.evidence.capture.check_disk_space", return_value=False):
            result = sc.capture_with_validation("TestWindow", "test-case")

        assert result.status == "skipped"
        assert "Insufficient disk space" in (result.message or "")


# ── _restore_window (AC1) ────────────────────────────────────


class TestRestoreWindow:
    @patch("sts2_autotest.evidence.capture.ctypes")
    def test_success(self, mock_ctypes: MagicMock) -> None:
        mock_user32 = MagicMock()
        mock_user32.FindWindowW.return_value = 12345  # Non-zero HWND
        mock_ctypes.windll.user32 = mock_user32

        result = _restore_window("TestWindow")
        assert result is True
        mock_user32.SetForegroundWindow.assert_called_once_with(12345)

    @patch("sts2_autotest.evidence.capture.ctypes")
    def test_window_not_found(self, mock_ctypes: MagicMock) -> None:
        mock_user32 = MagicMock()
        mock_user32.FindWindowW.return_value = 0  # NULL HWND
        mock_ctypes.windll.user32 = mock_user32

        result = _restore_window("MissingWindow")
        assert result is False

    @patch("sts2_autotest.evidence.capture.ctypes")
    def test_exception_returns_false(self, mock_ctypes: MagicMock) -> None:
        mock_ctypes.windll = AttributeError("No windll")

        result = _restore_window("TestWindow")
        assert result is False


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

    def test_implements_settings_protocol(self) -> None:
        """FrameworkConfig satisfies ScreenCaptureSettings protocol."""
        from sts2_autotest.common.types import ScreenCaptureSettings
        from sts2_autotest.config.schema import FrameworkConfig

        cfg = FrameworkConfig()
        # Structural subtyping — this should work without explicit inheritance
        settings: ScreenCaptureSettings = cfg
        assert settings.screenshot_rgb_threshold == 3


# ── RealEvidenceHooks ────────────────────────────────────────


class TestRealEvidenceHooks:
    def test_on_case_end_captures(self) -> None:
        from sts2_autotest.core.action_model import TestResult
        from sts2_autotest.core.evidence_hooks import RealEvidenceHooks

        mock_capture = MagicMock()
        mock_capture.capture_with_validation.return_value = CaptureResult(
            status="ok", path=Path("/tmp/screenshot.png")
        )

        hooks = RealEvidenceHooks(mock_capture, window_title="TestGame")
        result = TestResult(case_id="test-1", status="pass")
        hooks.on_case_end(result)

        mock_capture.capture_with_validation.assert_called_once_with("TestGame", "test-1")

    def test_on_case_end_skipped(self) -> None:
        from sts2_autotest.core.action_model import TestResult
        from sts2_autotest.core.evidence_hooks import RealEvidenceHooks

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
        mock_packager.create_pack.return_value = Path("/tmp/evidence/run_001")
        hooks = RealEvidenceHooks(mock_capture, packager=mock_packager)
        hooks.on_session_end({"passed": 5, "failed": 1, "crashed": 0, "skipped": 0})
        mock_packager.create_pack.assert_called_once_with(
            run_result="failed", duration_ms=0,
        )
        mock_packager.export_artifact.assert_called_once_with(
            "run_001", result="failed",
        )

    def test_on_session_end_passed(self) -> None:
        from sts2_autotest.core.evidence_hooks import RealEvidenceHooks

        mock_capture = MagicMock()
        mock_packager = MagicMock()
        mock_packager.create_pack.return_value = Path("/tmp/evidence/run_002")
        hooks = RealEvidenceHooks(mock_capture, packager=mock_packager)
        hooks.on_session_end({"passed": 5, "failed": 0, "crashed": 0, "skipped": 0})
        mock_packager.create_pack.assert_called_once_with(
            run_result="passed", duration_ms=0,
        )
        mock_packager.export_artifact.assert_called_once_with(
            "run_002", result="passed",
        )

    def test_on_case_end_collects_logs_on_failure(self) -> None:
        from sts2_autotest.core.action_model import TestResult
        from sts2_autotest.core.evidence_hooks import RealEvidenceHooks

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
        from sts2_autotest.core.orchestrator import TestOrchestrator
        from sts2_autotest.dsl.handlers import capture_screenshot

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
        from sts2_autotest.core.evidence_hooks import StubEvidenceHooks
        from sts2_autotest.core.orchestrator import TestOrchestrator
        from sts2_autotest.dsl.handlers import capture_screenshot

        mock_adapter = MagicMock()
        stub_hooks = StubEvidenceHooks()
        orch = TestOrchestrator(adapter=mock_adapter, evidence=stub_hooks)

        # Should not raise, just log warning
        capture_screenshot(orch, "test-case")


# ── macOS 离屏窗口陈旧帧防护 ──


class TestMacOSOffscreenGuard:
    def test_onscreen_check_passes_window_id_and_bundle_id(self) -> None:
        from sts2_autotest.evidence.capture import _ensure_macos_window_onscreen

        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("sts2_autotest.evidence.capture.subprocess.run", return_value=completed) as run:
            assert _ensure_macos_window_onscreen(1231) is True
        argv = run.call_args.args[0]
        assert "1231" in argv
        assert "com.megacrit.SlayTheSpire2" in argv

    def test_onscreen_check_fails_when_window_stays_offscreen(self) -> None:
        from sts2_autotest.evidence.capture import _ensure_macos_window_onscreen

        completed = MagicMock(returncode=1, stdout="", stderr="")
        with patch("sts2_autotest.evidence.capture.subprocess.run", return_value=completed):
            assert _ensure_macos_window_onscreen(1231) is False

    def test_capture_refuses_stale_frame_for_offscreen_window(self, tmp_path: Path) -> None:
        from sts2_autotest.evidence import capture

        target = tmp_path / "event.jpg"
        with patch.object(capture, "_find_macos_window", return_value=(1231, (0, 0, 1504, 846))), \
             patch.object(capture, "_ensure_macos_window_onscreen", return_value=False):
            ok, resolution = capture._capture_macos_window_png(target, "Slay the Spire 2")
        # 离屏窗口宁可截图不可用，也不产出与当前状态不一致的陈旧画面
        assert ok is False
        assert resolution == (1504, 846)
        assert not target.exists()
