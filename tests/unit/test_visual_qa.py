"""Tests for Visual QA OCR analysis models and engine."""

from __future__ import annotations

from sts2_autotest.common.visual_qa import (
    OcrTextBlock,
    ScreenshotOcrAnalysis,
    VisualQaFinding,
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
