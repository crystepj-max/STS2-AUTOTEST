"""Optional integration tests for the local Tesseract OCR provider."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sts2_autotest.core.visual_qa import TesseractOcrProvider, VisualQaEngine


def test_tesseract_provider_reads_game_screenshot_fixture() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract command is not installed")

    fixture = Path("tests/fixtures/visual_qa/gawain-card-before.png")
    assert fixture.is_file()

    analysis = VisualQaEngine(TesseractOcrProvider()).analyze_screenshot(fixture)

    assert analysis.provider == "tesseract"
    assert analysis.status in {"passed", "warning"}
    assert analysis.extracted_text
    assert any(block.bbox is not None for block in analysis.extracted_text)
    assert any(
        block.confidence is not None and block.confidence >= 0.9
        for block in analysis.extracted_text
    )
    assert any("消耗" in block.text for block in analysis.extracted_text)


def test_tesseract_provider_accepts_user_game_screenshot_fixture() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract command is not installed")

    fixture = Path("tests/fixtures/visual_qa/gawain-card-user-screenshot.jpg")
    assert fixture.is_file()

    analysis = VisualQaEngine(TesseractOcrProvider()).analyze_screenshot(fixture)

    assert analysis.provider == "tesseract"
    assert analysis.status == "passed"
    assert analysis.extracted_text
    assert any(block.bbox is not None for block in analysis.extracted_text)
    assert any(
        block.confidence is not None and block.confidence >= 0.9
        for block in analysis.extracted_text
    )
    assert any("消耗" in block.text for block in analysis.extracted_text)
