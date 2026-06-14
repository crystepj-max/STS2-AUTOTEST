"""Tests for Visual QA OCR analysis models and engine."""

from __future__ import annotations

from pathlib import Path

from sts2_autotest.common.visual_qa import (
    OcrTextBlock,
    ScreenshotOcrAnalysis,
    VisualQaFinding,
)
from sts2_autotest.core.visual_qa import (
    DisabledOcrProvider,
    LocalizationTextDetector,
    StaticOcrProvider,
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


def test_visual_qa_engine_returns_warning_for_raw_key(tmp_path: Path) -> None:
    image = tmp_path / "gawain-card-before.png"
    image.write_bytes(b"fake png bytes")
    engine = VisualQaEngine(
        StaticOcrProvider({"gawain-card-before.png": ["gawain.card.strike.name"]})
    )

    analysis = engine.analyze_screenshot(image)

    assert analysis.status == "warning"
    assert analysis.provider == "static"
    assert analysis.findings[0].rule_id == "localization_text.raw_key"


def test_visual_qa_engine_returns_passed_for_normal_text(tmp_path: Path) -> None:
    image = tmp_path / "normal.png"
    image.write_bytes(b"fake png bytes")
    engine = VisualQaEngine(StaticOcrProvider({"normal.png": ["打击 造成 6 点伤害"]}))

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
