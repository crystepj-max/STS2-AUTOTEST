"""Screen capture system — mss + RGB validation + resolution check (FR20, NFR21-24)."""

from __future__ import annotations

__test__ = False

import ctypes
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
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
_MACOS_MIN_BAND_CONTENT_RATIO = 0.01
_MACOS_SRGB_PROFILE = Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc")
_MACOS_GAME_BUNDLE_ID = "com.megacrit.SlayTheSpire2"


def _ensure_macos_window_onscreen(
    window_id: int, *, timeout: float = 5.0, settle: float = 1.0
) -> bool:
    """确保目标 macOS 窗口处于可见（onscreen）状态。

    离屏窗口（被最小化、隐藏或不在当前空间）的窗口截图只会返回系统缓存的
    旧帧，会把滞后画面误当最新证据。窗口不可见时先激活所属应用并等待其
    恢复渲染；激活无效则返回 False，由调用方放弃本次截图而不是产出
    看似正常的陈旧截图。
    """
    script = r'''
import sys
import time
import Quartz
from AppKit import (
    NSApplicationActivateAllWindows,
    NSApplicationActivateIgnoringOtherApps,
    NSRunningApplication,
)

window_id = int(sys.argv[1])
bundle_id = sys.argv[2]
timeout = float(sys.argv[3])
settle = float(sys.argv[4])

def onscreen():
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID
    ) or []
    for window in windows:
        if int(window.get(Quartz.kCGWindowNumber, 0) or 0) == window_id:
            return bool(window.get(Quartz.kCGWindowIsOnscreen))
    return False

if not onscreen():
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
    if apps:
        apps[0].activateWithOptions_(
            NSApplicationActivateIgnoringOtherApps | NSApplicationActivateAllWindows
        )
    deadline = time.time() + timeout
    while time.time() < deadline and not onscreen():
        time.sleep(0.25)
if not onscreen():
    raise SystemExit(1)
time.sleep(settle)
'''
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(window_id),
                _MACOS_GAME_BUNDLE_ID,
                str(timeout),
                str(settle),
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 15.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("macOS window onscreen check failed: %s", exc)
        return False
    return result.returncode == 0


def _find_macos_window(window_title: str) -> tuple[int, tuple[int, int, int, int]] | None:
    """Find the largest visible macOS window owned by the requested app."""
    script = r'''
import re
import sys
import Quartz

def norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

target = norm(sys.argv[1])
windows = Quartz.CGWindowListCopyWindowInfo(
    Quartz.kCGWindowListOptionAll,
    Quartz.kCGNullWindowID,
) or []
candidates = []
for window in windows:
    if int(window.get(Quartz.kCGWindowLayer, 0) or 0) != 0:
        continue
    owner = norm(window.get(Quartz.kCGWindowOwnerName, ""))
    name = norm(window.get(Quartz.kCGWindowName, ""))
    if not target or not (target in owner or owner in target or target in name or name in target):
        continue
    bounds = window.get(Quartz.kCGWindowBounds, {}) or {}
    width = int(round(float(bounds.get("Width", 0) or 0)))
    height = int(round(float(bounds.get("Height", 0) or 0)))
    window_id = int(window.get(Quartz.kCGWindowNumber, 0) or 0)
    if window_id > 0 and width > 0 and height > 0:
        exact = int(target == owner or target == name)
        x = int(round(float(bounds.get("X", 0) or 0)))
        y = int(round(float(bounds.get("Y", 0) or 0)))
        candidates.append((exact, width * height, window_id, x, y, width, height))

if candidates:
    _, _, window_id, x, y, width, height = max(candidates)
    print(f"{window_id}\t{x}\t{y}\t{width}\t{height}")
'''
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, window_title],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("macOS window lookup failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.warning("macOS window lookup failed: %s", result.stderr.strip())
        return None
    fields = result.stdout.strip().split("\t")
    if len(fields) != 5:
        return None
    try:
        return int(fields[0]), (
            int(fields[1]),
            int(fields[2]),
            int(fields[3]),
            int(fields[4]),
        )
    except ValueError:
        return None


def _inspect_macos_png(path: Path) -> tuple[tuple[int, int] | None, float | None, str | None]:
    """Read physical PNG size and reject a frame with a black horizontal band."""
    script = r'''
import sys
import Quartz
from Foundation import NSURL

path = sys.argv[1]
source = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(path), None)
image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None) if source else None
if image is None:
    raise SystemExit(1)

width = int(Quartz.CGImageGetWidth(image))
height = int(Quartz.CGImageGetHeight(image))
bytes_per_row = int(Quartz.CGImageGetBytesPerRow(image))
bytes_per_pixel = max(1, int(Quartz.CGImageGetBitsPerPixel(image)) // 8)
data = bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image)))
step_x = max(1, width // 64)
step_y = max(1, height // 64)
ratios = []
for band in range(3):
    start = band * height // 3
    end = (band + 1) * height // 3
    samples = 0
    non_black = 0
    for y in range(start, end, step_y):
        row = y * bytes_per_row
        for x in range(0, width, step_x):
            offset = row + x * bytes_per_pixel
            pixel = data[offset:offset + min(bytes_per_pixel, 3)]
            samples += 1
            if pixel and max(pixel) > 8:
                non_black += 1
    ratios.append(non_black / samples if samples else 0.0)

print(f"{width}\t{height}\t{min(ratios):.6f}")
'''
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, f"PNG inspection failed: {exc}"
    if result.returncode != 0:
        return None, None, result.stderr.strip() or "PNG inspection failed"
    fields = result.stdout.strip().split("\t")
    if len(fields) != 3:
        return None, None, "PNG inspection returned malformed metadata"
    try:
        resolution = (int(fields[0]), int(fields[1]))
        min_content_ratio = float(fields[2])
    except ValueError:
        return None, None, "PNG inspection returned invalid metadata"
    if min_content_ratio < _MACOS_MIN_BAND_CONTENT_RATIO:
        return (
            resolution,
            min_content_ratio,
            "one horizontal image band is effectively black",
        )
    return resolution, min_content_ratio, None


def _normalize_macos_png(path: Path) -> str | None:
    """Rewrite screenshots to a broadly compatible image before publishing."""
    normalized = path.with_name(f"{path.stem}.normalized{path.suffix}")
    is_jpeg = path.suffix.lower() in {".jpg", ".jpeg"}
    command = [
        "/usr/bin/sips",
        "--matchTo",
        str(_MACOS_SRGB_PROFILE),
        "--setProperty",
        "dpiWidth",
        "72",
        "--setProperty",
        "dpiHeight",
        "72",
    ]
    if is_jpeg:
        command.extend(
            [
                "--resampleHeightWidth",
                "1080",
                "1920",
                "-s",
                "format",
                "jpeg",
                "-s",
                "formatOptions",
                "90",
            ]
        )
    command.extend([str(path), "--out", str(normalized)])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"PNG normalization failed: {exc}"
    if result.returncode != 0 or not normalized.is_file() or normalized.stat().st_size < 1024:
        normalized.unlink(missing_ok=True)
        return result.stderr.strip() or "PNG normalization produced no usable file"
    os.replace(normalized, path)
    return None


def _export_macos_jpeg(source: Path, target: Path) -> str | None:
    """Publish a compatible JPEG from an already window-bounded PNG source."""
    try:
        result = subprocess.run(
            [
                "/usr/bin/sips",
                "--resampleHeightWidth",
                "1080",
                "1920",
                "-s",
                "format",
                "jpeg",
                "-s",
                "formatOptions",
                "90",
                str(source),
                "--out",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=30.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"JPEG export failed: {exc}"
    if result.returncode != 0 or not target.is_file() or target.stat().st_size < 1024:
        target.unlink(missing_ok=True)
        return result.stderr.strip() or "JPEG export produced no usable file"
    return None


def _capture_macos_window_png(
    path: Path, window_title: str
) -> tuple[bool, tuple[int, int] | None]:
    """Capture only the matching macOS window, never the full desktop."""
    match = _find_macos_window(window_title)
    if match is None:
        return False, None
    window_id, bounds = match
    _x, _y, width, height = bounds
    if not _ensure_macos_window_onscreen(window_id):
        # 离屏窗口只会返回系统缓存的旧帧；宁可报告截图不可用，
        # 也不把滞后画面当作与当前状态一致的证据。
        logger.warning(
            "macOS window %s stayed offscreen; refusing stale-frame capture",
            window_id,
        )
        return False, (width, height)
    raw_path = path if path.suffix.lower() == ".png" else path.with_name(
        f"{path.stem}.raw.png"
    )
    script = r'''
import sys
import Quartz
from AppKit import NSBitmapImageRep, NSPNGFileType

path = sys.argv[1]
window_id = int(sys.argv[2])
image = Quartz.CGWindowListCreateImage(
    Quartz.CGRectNull,
    Quartz.kCGWindowListOptionIncludingWindow,
    window_id,
    Quartz.kCGWindowImageBoundsIgnoreFraming,
)
if image is None:
    raise SystemExit(1)
bitmap = NSBitmapImageRep.alloc().initWithCGImage_(image)
if bitmap is None:
    raise SystemExit(1)
png_data = bitmap.representationUsingType_properties_(NSPNGFileType, {})
if png_data is None or not png_data.writeToFile_atomically_(path, True):
    raise SystemExit(1)
'''
    try:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(raw_path),
                str(window_id),
            ],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("macOS window capture failed: %s", exc)
        return False, (width, height)
    if result.returncode != 0 or not raw_path.is_file() or raw_path.stat().st_size < 1024:
        logger.warning(
            "macOS window capture produced no usable file: %s",
            result.stderr.strip(),
        )
        return False, (width, height)
    normalization_error = _normalize_macos_png(raw_path)
    if normalization_error is not None:
        raw_path.unlink(missing_ok=True)
        logger.warning("macOS screenshot normalization failed: %s", normalization_error)
        return False, (width, height)
    if raw_path != path:
        export_error = _export_macos_jpeg(raw_path, path)
        raw_path.unlink(missing_ok=True)
        if export_error is not None:
            logger.warning("macOS screenshot JPEG export failed: %s", export_error)
            return False, (width, height)
    resolution, content_ratio, inspection_error = _inspect_macos_png(path)
    if resolution is None or inspection_error is not None:
        logger.warning(
            "macOS screenshot rejected: %s (resolution=%s, min_band_content=%s)",
            inspection_error or "unknown PNG metadata",
            resolution,
            content_ratio,
        )
        return False, resolution or (width, height)
    logger.info(
        "macOS screenshot captured from window bounds %s at physical resolution %s "
        "(minimum band content %.3f)",
        bounds,
        resolution,
        content_ratio,
    )
    return True, resolution


def _restore_window(title: str) -> bool:
    """Bring window to foreground and maximize via Win32 API.

    Calls: FindWindow → ShowWindow(SW_RESTORE) → SetForegroundWindow → ShowWindow(SW_MAXIMIZE).

    Returns True on success, False on failure (window not found or API error).
    On False, caller should log WARNING and return SKIPPED.
    """
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
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
    def from_config(cls, output_dir: Path, settings: ScreenCaptureSettings) -> ScreenCapture:
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
        if platform.system() == "Darwin":
            return self._capture_macos_with_validation(window_title, case_id)

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

    def _capture_macos_with_validation(
        self, window_title: str, case_id: str
    ) -> CaptureResult:
        """Capture and validate the actual macOS game window."""
        for attempt in range(self._max_retries):
            path = self._output_dir / f"{case_id}_{attempt}.jpg"
            ok, resolution = _capture_macos_window_png(path, window_title)
            if ok and path.stat().st_size >= self._min_file_bytes:
                return CaptureResult(
                    status="ok",
                    path=path,
                    resolution=resolution,
                )
            path.unlink(missing_ok=True)
        return CaptureResult(
            status="skipped",
            message=f"macOS game window '{window_title}' was not available for capture",
        )

    def capture(self, window_title: str, case_id: str = "unknown") -> CaptureResult:
        """Take a screenshot and save it. No validation or retry.

        Returns CaptureResult. If mss fails, returns SKIPPED.
        """
        if platform.system() == "Darwin":
            path = self._output_dir / f"{case_id}.jpg"
            ok, resolution = _capture_macos_window_png(path, window_title)
            if not ok:
                path.unlink(missing_ok=True)
                return CaptureResult(
                    status="skipped",
                    message=f"macOS game window '{window_title}' was not available for capture",
                )
            return CaptureResult(status="ok", path=path, resolution=resolution)

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

        now = datetime.now(UTC)
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
