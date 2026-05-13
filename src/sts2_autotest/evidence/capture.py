"""Screen capture system — mss + RGB validation + resolution check (FR20, NFR21-24)."""

from __future__ import annotations

__test__ = False

import ctypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import mss
import mss.exception
import mss.tools

from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.types import CaptureResult, ScreenCaptureSettings
from sts2_autotest.core.disk_guard import check_disk_space

logger = get_logger("evidence.capture")

# Win32 constants
_SW_RESTORE = 9
_SW_MAXIMIZE = 3


def _restore_window(title: str) -> bool:
    """Bring window to foreground and maximize via Win32 API.

    Calls: FindWindow → ShowWindow(SW_RESTORE) → SetForegroundWindow → ShowWindow(SW_MAXIMIZE).

    Returns True on success, False on failure (window not found or API error).
    On False, caller should log WARNING and return SKIPPED.
    """
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, _SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        user32.ShowWindow(hwnd, _SW_MAXIMIZE)
        return True
    except Exception:
        return False


def _parse_resolution(resolution_str: str) -> tuple[int, int]:
    """Parse 'WxH' string into (width, height) tuple."""
    parts = resolution_str.lower().split("x")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid resolution format '{resolution_str}', expected 'WxH'"
        )
    return int(parts[0]), int(parts[1])


@dataclass
class _RawCapture:
    """Internal container for raw mss capture data."""

    bgra: bytes
    width: int
    height: int


class ScreenCapture:
    """Screenshot system with mss capture, RGB validation, resolution check.

    Uses atomic writes (write-to-temp + os.replace) for file safety.
    Window foreground via Win32 API before capture.
    RGB validation operates on raw BGRA pixel data from mss, not on
    compressed PNG bytes.
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        rgb_threshold: int = 3,
        target_resolution: str = "1920x1080",
        resolution_tolerance: int = 2,
        min_file_bytes: int = 1024,
        max_retries: int = 3,
    ) -> None:
        self._output_dir = output_dir
        self._rgb_threshold = rgb_threshold
        self._target_resolution = _parse_resolution(target_resolution)
        self._resolution_tolerance = resolution_tolerance
        self._min_file_bytes = min_file_bytes
        self._max_retries = max_retries

    @classmethod
    def from_config(cls, output_dir: Path, settings: ScreenCaptureSettings) -> "ScreenCapture":
        """Construct ScreenCapture from a ScreenCaptureSettings protocol instance.

        The settings protocol is implemented by FrameworkConfig, allowing
        config-driven construction without evidence/ importing config/.
        """
        return cls(
            output_dir,
            rgb_threshold=settings.screenshot_rgb_threshold,
            target_resolution=settings.screenshot_target_resolution,
            resolution_tolerance=settings.screenshot_resolution_tolerance,
            min_file_bytes=settings.screenshot_min_file_bytes,
            max_retries=settings.screenshot_max_retries,
        )

    # ── public API ──────────────────────────────────────────

    def capture_with_validation(
        self, window_title: str, case_id: str
    ) -> CaptureResult:
        """Full capture flow: foreground → capture → validate → retry.

        Returns CaptureResult with status ok/error/skipped.
        AC5: If the window is not found (not visible, minimized, crashed),
        returns SKIPPED immediately without blocking the test.
        """
        if not _restore_window(window_title):
            logger.warning(
                "Window '%s' not found or foreground failed — "
                "skipping capture (WARNING degradation)",
                window_title,
            )
            return CaptureResult(
                status="skipped",
                message=f"Window '{window_title}' not found or not visible",
            )

        for attempt in range(self._max_retries):
            try:
                raw = self._raw_capture()
            except OSError as exc:
                logger.warning("Capture failed with OSError: %s", exc)
                return CaptureResult(
                    status="skipped",
                    message=f"Capture error: {exc}",
                )

            if raw is None:
                return CaptureResult(
                    status="skipped",
                    message="Capture failed — no monitor or mss error",
                )

            bgra_data = raw.bgra
            width = raw.width
            height = raw.height
            resolution = (width, height)

            # RGB validation on raw pixel data
            rgb_ok, rgb_count = self._count_distinct_rgb(bgra_data)
            res_ok = self._check_resolution(resolution)

            # Save to file for size check and persistence
            try:
                path = self._save_screenshot(bgra_data, width, height, case_id)
            except OSError as exc:
                logger.warning("Screenshot save failed: %s", exc)
                return CaptureResult(
                    status="skipped",
                    message=f"Failed to save screenshot: {exc}",
                )

            size_ok = path.stat().st_size >= self._min_file_bytes

            if rgb_ok and res_ok and size_ok:
                return CaptureResult(
                    status="ok",
                    path=path,
                    rgb_count=rgb_count,
                    resolution=resolution,
                )

            # Build failure reasons
            reasons: list[str] = []
            if not rgb_ok:
                reasons.append(
                    f"RGB validation failed (distinct colors: {rgb_count}, "
                    f"threshold: {self._rgb_threshold})"
                )
            if not res_ok:
                reasons.append(
                    f"Resolution out of tolerance "
                    f"(got {resolution}, target {self._target_resolution}, "
                    f"tolerance ±{self._resolution_tolerance}px)"
                )
            if not size_ok:
                reasons.append("File size below minimum")

            if attempt < self._max_retries - 1:
                logger.warning(
                    "Capture validation failed (attempt %d/%d): %s — retrying",
                    attempt + 1,
                    self._max_retries,
                    "; ".join(reasons),
                )
            else:
                logger.error(
                    "Capture validation failed after %d retries: %s",
                    self._max_retries,
                    "; ".join(reasons),
                )
                return CaptureResult(
                    status="error",
                    path=path,
                    message="; ".join(reasons),
                    rgb_count=rgb_count,
                    resolution=resolution,
                )

        # Unreachable, but satisfies type checker
        return CaptureResult(status="error", message="Unexpected retry loop exit")  # pragma: no cover

    def capture(self, window_title: str, case_id: str = "unknown") -> CaptureResult:
        """Take a screenshot and save it. No validation or retry.

        Returns CaptureResult. If mss fails, returns SKIPPED.
        """
        try:
            raw = self._raw_capture()
        except OSError as exc:
            logger.warning("Capture failed with OSError: %s", exc)
            return CaptureResult(
                status="skipped",
                message=f"Capture error: {exc}",
            )

        if raw is None:
            return CaptureResult(
                status="skipped",
                message="Capture failed — no monitor or mss error",
            )

        bgra_data = raw.bgra
        width = raw.width
        height = raw.height
        try:
            path = self._save_screenshot(bgra_data, width, height, case_id)
        except OSError as exc:
            logger.warning("Screenshot save failed: %s", exc)
            return CaptureResult(
                status="skipped",
                message=f"Failed to save screenshot: {exc}",
            )
        return CaptureResult(
            status="ok",
            path=path,
            resolution=(width, height),
        )

    # ── internal: capture ───────────────────────────────────

    def _raw_capture(self) -> _RawCapture | None:
        """Grab the primary monitor and return raw capture data.

        Returns None if no monitor is available or mss raises.
        """
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if len(monitors) < 2:
                    return None
                shot = sct.grab(monitors[1])
                return _RawCapture(
                    bgra=bytes(shot.bgra), width=shot.width, height=shot.height
                )
        except mss.exception.ScreenShotError as exc:
            logger.warning("mss capture failed: %s", exc)
            return None

    # ── internal: validation ────────────────────────────────

    def _count_distinct_rgb(self, bgra_data: bytes) -> tuple[bool, int]:
        """Count distinct RGB values across ALL pixels in BGRA data.

        BGRA format: each pixel is 4 bytes (B, G, R, A).
        Traverses every pixel as required by AC2.
        Returns (is_valid, distinct_color_count).
        Early-exits once threshold is met.
        """
        rgb_values: set[int] = set()
        pixel_count = len(bgra_data) // 4

        for i in range(pixel_count):
            offset = i * 4
            b = bgra_data[offset]
            g = bgra_data[offset + 1]
            r = bgra_data[offset + 2]
            rgb_values.add((r << 16) | (g << 8) | b)
            if len(rgb_values) >= self._rgb_threshold:
                return True, len(rgb_values)

        count = len(rgb_values)
        return count >= self._rgb_threshold, count

    def _check_resolution(self, resolution: tuple[int, int]) -> bool:
        """Check screenshot resolution is within tolerance of target."""
        target_w, target_h = self._target_resolution
        actual_w, actual_h = resolution
        return (
            abs(actual_w - target_w) <= self._resolution_tolerance
            and abs(actual_h - target_h) <= self._resolution_tolerance
        )

    # ── internal: file I/O ──────────────────────────────────

    def _save_screenshot(
        self, bgra_data: bytes, width: int, height: int, case_id: str
    ) -> Path:
        """Convert BGRA to PNG and save with atomic write.

        Raises OSError if disk space is below the default threshold (100 MB).
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)

        if not check_disk_space(str(self._output_dir)):
            raise OSError(
                f"Insufficient disk space for screenshot in {self._output_dir}"
            )

        png_bytes = mss.tools.to_png(bgra_data, (width, height))
        if png_bytes is None:
            raise OSError("mss.tools.to_png returned None")

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%dT%H%M%S")
        ms = now.microsecond // 1000
        filename = f"{case_id}_{timestamp}_{ms:03d}.png"
        target = self._output_dir / filename

        # Atomic write: write to temp then os.replace
        tmp = target.with_suffix(".png.tmp")
        try:
            tmp.write_bytes(png_bytes)
            os.replace(str(tmp), str(target))
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        return target

    # ── internal: window foreground ─────────────────────────
    # (module-level _restore_window handles foreground logic)
