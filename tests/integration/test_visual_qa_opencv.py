"""Optional integration tests for the OpenCV screenshot health detector."""

from __future__ import annotations

import pytest

from sts2_autotest.core.visual_qa import ScreenshotHealthDetector


def test_opencv_health_detector_flags_solid_black_png(tmp_path) -> None:
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")

    image_path = tmp_path / "black.png"
    image = numpy.zeros((32, 32, 3), dtype=numpy.uint8)
    assert cv2.imwrite(str(image_path), image)

    findings = ScreenshotHealthDetector(
        cv2_module=cv2,
        low_variance_threshold=1.0,
    ).analyze(image_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "visual_health.low_variance"


def test_opencv_health_detector_flags_too_dark_png(tmp_path) -> None:
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")

    image_path = tmp_path / "dark.png"
    image = numpy.zeros((32, 32, 3), dtype=numpy.uint8)
    image[:, :16] = 2
    image[:, 16:] = 4
    assert cv2.imwrite(str(image_path), image)

    findings = ScreenshotHealthDetector(
        cv2_module=cv2,
        low_variance_threshold=0.1,
        low_brightness_threshold=5.0,
    ).analyze(image_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "visual_health.too_dark"
