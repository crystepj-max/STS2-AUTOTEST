"""Tests for Visual QA OCR analysis models and engine."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from sts2_autotest.common.visual_qa import (
    OcrTextBlock,
    ScreenshotOcrAnalysis,
    VisualQaFinding,
)
from sts2_autotest.core.visual_qa import (
    DisabledOcrProvider,
    LocalizationTextDetector,
    ScreenshotHealthDetector,
    StaticOcrProvider,
    TesseractOcrProvider,
    VisualQaEngine,
)


def test_screenshot_ocr_analysis_roundtrip() -> None:
    analysis = ScreenshotOcrAnalysis(
        status="warning",
        provider="static",
        findings=[
            VisualQaFinding(
                rule_id="localization_text.raw_key",
                severity="warning",
                message="疑似 localization key 出现在截图文本中",
                text="gawain.card.strike.name",
                confidence=0.9,
                bbox=[1, 2, 30, 40],
            )
        ],
        extracted_text=[
            OcrTextBlock(
                text="gawain.card.strike.name",
                confidence=0.9,
                bbox=[1, 2, 30, 40],
            )
        ],
        duration_ms=12.5,
    )

    data = analysis.model_dump(mode="json")
    restored = ScreenshotOcrAnalysis.model_validate(data)

    assert restored.status == "warning"
    assert restored.provider == "static"
    assert restored.findings[0].rule_id == "localization_text.raw_key"
    assert restored.extracted_text[0].text == "gawain.card.strike.name"


def test_localization_detector_flags_raw_key() -> None:
    detector = LocalizationTextDetector()
    findings = detector.analyze(
        [OcrTextBlock(text="gawain.card.strike.name", confidence=0.91)]
    )

    assert len(findings) == 1
    assert findings[0].rule_id == "localization_text.raw_key"
    assert findings[0].severity == "warning"
    assert findings[0].text == "gawain.card.strike.name"


def test_localization_detector_flags_missing_marker() -> None:
    detector = LocalizationTextDetector()
    findings = detector.analyze([OcrTextBlock(text="<missing> CARD_NAME")])

    assert len(findings) == 1
    assert findings[0].rule_id == "localization_text.missing_marker"


def test_localization_detector_flags_unresolved_token() -> None:
    detector = LocalizationTextDetector()
    findings = detector.analyze([OcrTextBlock(text="Deal {0} damage")])

    assert len(findings) == 1
    assert findings[0].rule_id == "localization_text.unresolved_token"


def test_localization_detector_passes_normal_text() -> None:
    detector = LocalizationTextDetector()
    findings = detector.analyze([OcrTextBlock(text="打击 造成 6 点伤害")])

    assert findings == []


def test_localization_detector_ignores_common_ui_numbers() -> None:
    detector = LocalizationTextDetector()
    findings = detector.analyze(
        [
            OcrTextBlock(text="HP:45/50"),
            OcrTextBlock(text="v1.2.3"),
            OcrTextBlock(text="ability:chain:strike"),
        ]
    )

    assert findings == []


def test_visual_qa_engine_returns_warning_for_raw_key(tmp_path: Path) -> None:
    image = tmp_path / "gawain-card-before.png"
    image.write_bytes(b"fake png bytes")
    engine = VisualQaEngine(
        StaticOcrProvider({"gawain-card-before.png": ["gawain.card.strike.name"]}),
        health_detector=ScreenshotHealthDetector(cv2_module=None),
    )

    analysis = engine.analyze_screenshot(image)

    assert analysis.status == "warning"
    assert analysis.provider == "static"
    assert analysis.findings[0].rule_id == "localization_text.raw_key"


def test_visual_qa_engine_returns_passed_for_normal_text(tmp_path: Path) -> None:
    image = tmp_path / "normal.png"
    image.write_bytes(b"fake png bytes")
    engine = VisualQaEngine(
        StaticOcrProvider({"normal.png": ["打击 造成 6 点伤害"]}),
        health_detector=ScreenshotHealthDetector(cv2_module=None),
    )

    analysis = engine.analyze_screenshot(image)

    assert analysis.status == "passed"
    assert analysis.findings == []


def test_visual_qa_engine_returns_skipped_when_provider_disabled(
    tmp_path: Path,
) -> None:
    image = tmp_path / "normal.png"
    image.write_bytes(b"fake png bytes")
    engine = VisualQaEngine(DisabledOcrProvider())

    analysis = engine.analyze_screenshot(image)

    assert analysis.status == "skipped"
    assert analysis.provider == "disabled"
    assert "disabled" in str(analysis.message).lower()


def test_visual_qa_engine_returns_skipped_for_missing_image(tmp_path: Path) -> None:
    engine = VisualQaEngine(StaticOcrProvider({}))

    analysis = engine.analyze_screenshot(tmp_path / "missing.png")

    assert analysis.status == "skipped"
    assert analysis.provider == "static"
    assert "screenshot not found" in str(analysis.message)


def test_screenshot_health_detector_flags_low_variance_image(tmp_path: Path) -> None:
    image = tmp_path / "black.png"
    image.write_bytes(b"png")

    class FakeImage:
        def std(self) -> float:
            return 0.2

        def mean(self) -> float:
            return 10.0

    class FakeCv2:
        IMREAD_GRAYSCALE = 0

        @staticmethod
        def imread(path: str, flags: int) -> FakeImage:
            return FakeImage()

    detector = ScreenshotHealthDetector(cv2_module=FakeCv2)

    findings = detector.analyze(image)

    assert len(findings) == 1
    assert findings[0].rule_id == "visual_health.low_variance"
    assert findings[0].severity == "warning"
    assert "low visual variance" in findings[0].message
    assert findings[0].text == image.name


def test_screenshot_health_detector_flags_dark_image(tmp_path: Path) -> None:
    image = tmp_path / "dark.png"
    image.write_bytes(b"png")

    class FakeImage:
        def std(self) -> float:
            return 10.0

        def mean(self) -> float:
            return 2.5

    class FakeCv2:
        IMREAD_GRAYSCALE = 0

        @staticmethod
        def imread(path: str, flags: int) -> FakeImage:
            return FakeImage()

    detector = ScreenshotHealthDetector(
        cv2_module=FakeCv2,
        low_variance_threshold=1.0,
        low_brightness_threshold=5.0,
    )

    findings = detector.analyze(image)

    assert len(findings) == 1
    assert findings[0].rule_id == "visual_health.too_dark"
    assert findings[0].severity == "warning"
    assert "too dark" in findings[0].message
    assert findings[0].text == image.name


def test_screenshot_health_detector_flags_bright_image(tmp_path: Path) -> None:
    image = tmp_path / "bright.png"
    image.write_bytes(b"png")

    class FakeImage:
        def std(self) -> float:
            return 10.0

        def mean(self) -> float:
            return 252.5

    class FakeCv2:
        IMREAD_GRAYSCALE = 0

        @staticmethod
        def imread(path: str, flags: int) -> FakeImage:
            return FakeImage()

    detector = ScreenshotHealthDetector(
        cv2_module=FakeCv2,
        low_variance_threshold=1.0,
        low_brightness_threshold=5.0,
        high_brightness_threshold=250.0,
    )

    findings = detector.analyze(image)

    assert len(findings) == 1
    assert findings[0].rule_id == "visual_health.too_bright"
    assert findings[0].severity == "warning"
    assert "too bright" in findings[0].message
    assert findings[0].text == image.name


def test_screenshot_health_detector_flags_unreadable_image(tmp_path: Path) -> None:
    image = tmp_path / "corrupt.png"
    image.write_bytes(b"not an image")

    class FakeCv2:
        IMREAD_GRAYSCALE = 0

        @staticmethod
        def imread(path: str, flags: int) -> None:
            return None

    detector = ScreenshotHealthDetector(cv2_module=FakeCv2)

    findings = detector.analyze(image)

    assert len(findings) == 1
    assert findings[0].rule_id == "visual_health.unreadable"
    assert findings[0].severity == "warning"
    assert "not readable" in findings[0].message
    assert findings[0].text == image.name


def test_visual_qa_engine_skips_health_detector_when_cv2_missing(
    tmp_path: Path,
) -> None:
    image = tmp_path / "normal.png"
    image.write_bytes(b"fake png bytes")
    engine = VisualQaEngine(
        StaticOcrProvider({"normal.png": ["打击"]}),
        health_detector=ScreenshotHealthDetector(cv2_module=None),
    )

    analysis = engine.analyze_screenshot(image)

    assert analysis.status == "passed"
    assert analysis.findings == []
    assert analysis.extracted_text[0].text == "打击"


def test_visual_qa_engine_keeps_ocr_when_health_detector_fails(
    tmp_path: Path,
) -> None:
    image = tmp_path / "normal.png"
    image.write_bytes(b"fake png bytes")

    class BrokenCv2:
        IMREAD_GRAYSCALE = 0

        @staticmethod
        def imread(path: str, flags: int) -> object:
            raise RuntimeError("cv2 failed")

    engine = VisualQaEngine(
        StaticOcrProvider({"normal.png": ["打击"]}),
        health_detector=ScreenshotHealthDetector(cv2_module=BrokenCv2),
    )

    analysis = engine.analyze_screenshot(image)

    assert analysis.status == "passed"
    assert analysis.findings == []
    assert analysis.extracted_text[0].text == "打击"


def test_visual_qa_engine_keeps_ocr_when_health_variance_fails(
    tmp_path: Path,
) -> None:
    image = tmp_path / "normal.png"
    image.write_bytes(b"fake png bytes")

    class BrokenImage:
        def std(self) -> float:
            raise ValueError("invalid image shape")

    class BrokenCv2:
        IMREAD_GRAYSCALE = 0

        @staticmethod
        def imread(path: str, flags: int) -> BrokenImage:
            return BrokenImage()

    engine = VisualQaEngine(
        StaticOcrProvider({"normal.png": ["gawain.card.strike.name"]}),
        health_detector=ScreenshotHealthDetector(cv2_module=BrokenCv2),
    )

    analysis = engine.analyze_screenshot(image)

    assert analysis.status == "warning"
    assert analysis.findings[0].rule_id == "localization_text.raw_key"
    assert analysis.extracted_text[0].text == "gawain.card.strike.name"


def test_screenshot_health_detector_rejects_unknown_cv2_sentinel() -> None:
    try:
        ScreenshotHealthDetector(cv2_module="disabled")
    except ValueError as exc:
        assert "cv2_module" in str(exc)
    else:
        raise AssertionError("expected invalid cv2_module to raise")


def test_tesseract_provider_extracts_tsv_blocks_with_bbox_and_confidence(
    tmp_path: Path,
) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")

    stdout = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "1\t1\t0\t0\t0\t0\t0\t0\t640\t360\t-1\t\n"
        "5\t1\t1\t1\t1\t1\t34\t56\t88\t24\t91.250000\tgawain.card.strike.name\n"
        "5\t1\t1\t1\t1\t2\t140\t56\t20\t24\t-1\t\n"
        "5\t1\t1\t1\t1\t3\t34\t92\t42\t20\t77\t消耗\n"
    )
    completed = subprocess.CompletedProcess(
        args=["tesseract"],
        returncode=0,
        stdout=stdout,
        stderr="",
    )
    with patch(
        "sts2_autotest.core.visual_qa.subprocess.run",
        return_value=completed,
    ) as run:
        blocks = TesseractOcrProvider(
            command="tesseract",
            lang="chi_sim+eng",
            timeout_seconds=3.0,
        ).extract_text(image)

    assert [block.text for block in blocks] == ["gawain.card.strike.name", "消耗"]
    assert blocks[0].confidence == 0.9125
    assert blocks[0].bbox == [34, 56, 88, 24]
    assert blocks[1].confidence == 0.77
    assert blocks[1].bbox == [34, 92, 42, 20]
    run.assert_called_once_with(
        ["tesseract", str(image), "stdout", "-l", "chi_sim+eng", "tsv"],
        capture_output=True,
        text=True,
        timeout=3.0,
        check=False,
    )


def test_tesseract_provider_skips_stdout_lines_before_tsv_header(
    tmp_path: Path,
) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")

    completed = subprocess.CompletedProcess(
        args=["tesseract"],
        returncode=0,
        stdout=(
            "Warning, could not find file: chi_sim.traineddata\n"
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t34\t56\t88\t24\t91.250000\tgawain.card.strike.name\n"
        ),
        stderr="",
    )
    with patch("sts2_autotest.core.visual_qa.subprocess.run", return_value=completed):
        blocks = TesseractOcrProvider().extract_text(image)

    assert blocks == [
        OcrTextBlock(
            text="gawain.card.strike.name",
            confidence=0.9125,
            bbox=[34, 56, 88, 24],
        )
    ]


def test_tesseract_provider_accepts_decimal_bbox_values(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")

    completed = subprocess.CompletedProcess(
        args=["tesseract"],
        returncode=0,
        stdout=(
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t34.0\t56.0\t88.0\t24.0\t91.250000\tLOCALIZE_ME\n"
        ),
        stderr="",
    )
    with patch("sts2_autotest.core.visual_qa.subprocess.run", return_value=completed):
        blocks = TesseractOcrProvider().extract_text(image)

    assert blocks[0].bbox == [34, 56, 88, 24]


def test_tesseract_provider_ignores_invalid_tsv_metadata(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")

    completed = subprocess.CompletedProcess(
        args=["tesseract"],
        returncode=0,
        stdout=(
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\tbad\t56\t88\t24\tunknown\tLOCALIZE_ME\n"
        ),
        stderr="",
    )
    with patch(
        "sts2_autotest.core.visual_qa.subprocess.run",
        return_value=completed,
    ):
        blocks = TesseractOcrProvider().extract_text(image)

    assert blocks == [
        OcrTextBlock(text="LOCALIZE_ME", confidence=None, bbox=None)
    ]


def test_tesseract_provider_missing_command_becomes_skipped(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")
    engine = VisualQaEngine(TesseractOcrProvider(command="missing-tesseract"))

    with patch(
        "sts2_autotest.core.visual_qa.subprocess.run",
        side_effect=FileNotFoundError("missing-tesseract"),
    ):
        analysis = engine.analyze_screenshot(image)

    assert analysis.status == "skipped"
    assert analysis.provider == "tesseract"
    assert "command not found" in str(analysis.message)


def test_tesseract_provider_timeout_becomes_skipped(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")
    engine = VisualQaEngine(TesseractOcrProvider(timeout_seconds=0.1))

    with patch(
        "sts2_autotest.core.visual_qa.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="tesseract", timeout=0.1),
    ):
        analysis = engine.analyze_screenshot(image)

    assert analysis.status == "skipped"
    assert "timed out" in str(analysis.message)


def test_tesseract_provider_nonzero_exit_becomes_skipped(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")
    completed = subprocess.CompletedProcess(
        args=["tesseract"],
        returncode=1,
        stdout="",
        stderr="Error opening data file",
    )
    engine = VisualQaEngine(TesseractOcrProvider())

    with patch("sts2_autotest.core.visual_qa.subprocess.run", return_value=completed):
        analysis = engine.analyze_screenshot(image)

    assert analysis.status == "skipped"
    assert "tesseract failed" in str(analysis.message)
