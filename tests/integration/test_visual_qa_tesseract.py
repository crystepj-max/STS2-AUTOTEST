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
