"""Optional integration tests for the local Tesseract OCR provider."""

from __future__ import annotations

import json
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


def test_visual_qa_cli_analyzes_user_screenshot_with_ocr_and_opencv(
    tmp_path: Path,
) -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract command is not installed")

    cv2 = pytest.importorskip("cv2")
    assert cv2 is not None

    fixture = Path("tests/fixtures/visual_qa/gawain-card-user-screenshot.jpg")
    assert fixture.is_file()
    output = tmp_path / "visual-qa.json"

    from sts2_autotest.cli.main import _create_parser, visual_qa_cmd

    args = _create_parser().parse_args(
        [
            "visual-qa",
            "--image",
            str(fixture),
            "--ocr-provider",
            "tesseract",
            "--health-provider",
            "opencv",
            "--output",
            str(output),
        ]
    )

    assert visual_qa_cmd(args) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))

    screenshot = payload["screenshots"][str(fixture)]
    assert screenshot["provider"] == "tesseract"
    assert screenshot["status"] == "passed"
    assert screenshot["extracted_text"]
    assert any(block["bbox"] is not None for block in screenshot["extracted_text"])
    assert payload["summary"]["passed"] == 1
    assert payload["summary"]["findings_total"] == 0
