"""Tests for TestAgentRunner Visual QA HTML report config."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from sts2_autotest.common.visual_qa import ScreenshotOcrAnalysis, VisualQaFinding
from sts2_autotest.config.schema import FrameworkConfig
from sts2_autotest.core.test_agent_runner import TestAgentRunner
from sts2_autotest.core.visual_qa import (
    DisabledOcrProvider,
    ScreenshotHealthDetector,
    StaticOcrProvider,
    TesseractOcrProvider,
    VisualQaEngine,
)


def _make_runner(tmp_path: Path) -> TestAgentRunner:
    mod = tmp_path / "mod"
    infra = tmp_path / "infra"
    mod.mkdir()
    infra.mkdir()
    runner = TestAgentRunner(
        mod_project=str(mod),
        task_id="visual-qa-demo",
        infra_path=str(infra),
    )
    runner._artifact_dir = tmp_path
    runner._card_results = [
        {
            "card_id": "GAWAINMOD-STRIKE_GAWAIN",
            "name": "打击",
            "status": "OK",
            "screenshot_before": "screenshots/card-before.png",
            "screenshot_after": "screenshots/card-after.png",
            "expected_damage": 6,
        }
    ]
    return runner


def test_build_html_report_card_results_includes_ocr_without_changing_result(
    tmp_path: Path,
) -> None:
    runner = _make_runner(tmp_path)
    runner._screenshot_ocr = {
        "screenshots/card-before.png": ScreenshotOcrAnalysis(
            status="warning",
            provider="static",
            findings=[
                VisualQaFinding(
                    rule_id="localization_text.raw_key",
                    severity="warning",
                    message="疑似 localization key 出现在截图文本中",
                    text="gawain.card.strike.name",
                    confidence=1.0,
                )
            ],
        )
    }

    cards = runner._build_html_report_card_results()

    assert cards[0]["result"] == "通过"
    assert cards[0]["screenshot_before_ocr"]["status"] == "warning"
    assert (
        cards[0]["screenshot_before_ocr"]["findings"][0]["text"]
        == "gawain.card.strike.name"
    )
    assert "screenshot_after_ocr" not in cards[0]


def test_analyze_html_report_screenshots_populates_cache(tmp_path: Path) -> None:
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    before = screenshots / "card-before.png"
    before.write_bytes(b"fake png bytes")
    runner = _make_runner(tmp_path)
    runner._visual_qa_engine = VisualQaEngine(
        StaticOcrProvider({"card-before.png": ["gawain.card.strike.name"]})
    )

    runner._analyze_html_report_screenshots()

    analysis = runner._screenshot_ocr["screenshots/card-before.png"]
    assert analysis.status == "warning"
    assert analysis.findings[0].text == "gawain.card.strike.name"


def test_build_visual_qa_report_payload_summarizes_cached_analysis(
    tmp_path: Path,
) -> None:
    runner = _make_runner(tmp_path)
    runner._screenshot_ocr = {
        "screenshots/card-before.png": ScreenshotOcrAnalysis(
            status="warning",
            provider="tesseract",
            findings=[
                VisualQaFinding(
                    rule_id="localization_text.raw_key",
                    severity="warning",
                    message="疑似 localization key 出现在截图文本中",
                    text="gawain.card.strike.name",
                    confidence=0.91,
                    bbox=[12, 34, 56, 78],
                )
            ],
        ),
        "screenshots/card-after.png": ScreenshotOcrAnalysis(
            status="passed",
            provider="tesseract",
        ),
    }

    payload = runner._build_visual_qa_report_payload()

    assert payload["summary"] == {
        "total": 2,
        "passed": 1,
        "warning": 1,
        "skipped": 0,
        "providers": {"tesseract": 2},
        "findings": {"localization_text.raw_key": 1},
    }
    assert payload["screenshots"]["screenshots/card-before.png"]["status"] == "warning"
    assert (
        payload["screenshots"]["screenshots/card-before.png"]["findings"][0]["bbox"]
        == [12, 34, 56, 78]
    )
    assert payload["screenshots"]["screenshots/card-after.png"]["status"] == "passed"


def test_generate_html_report_also_writes_visual_qa_json(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    runner._screenshot_ocr = {
        "screenshots/card-before.png": ScreenshotOcrAnalysis(
            status="warning",
            provider="static",
            findings=[
                VisualQaFinding(
                    rule_id="localization_text.raw_key",
                    severity="warning",
                    message="疑似 localization key 出现在截图文本中",
                    text="gawain.card.strike.name",
                )
            ],
        )
    }

    with patch("sts2_autotest.core.test_agent_runner.write_html_report") as mock_write_html:
        runner._generate_html_report()

    mock_write_html.assert_called_once()
    visual_qa_path = tmp_path / "visual-qa.json"
    assert visual_qa_path.is_file()
    payload = json.loads(visual_qa_path.read_text(encoding="utf-8"))
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["warning"] == 1
    assert payload["summary"]["skipped"] == 1
    assert (
        payload["screenshots"]["screenshots/card-before.png"]["findings"][0]["rule_id"]
        == "localization_text.raw_key"
    )
    assert payload["screenshots"]["screenshots/card-after.png"]["status"] == "skipped"


def test_get_visual_qa_engine_defaults_to_disabled_provider(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)

    engine = runner._get_visual_qa_engine()

    assert isinstance(engine._provider, DisabledOcrProvider)


def test_get_visual_qa_engine_uses_tesseract_provider_from_config(
    tmp_path: Path,
) -> None:
    runner = _make_runner(tmp_path)
    runner._framework_config = FrameworkConfig(
        visual_qa_ocr_provider="tesseract",
        visual_qa_tesseract_cmd="custom-tesseract",
        visual_qa_tesseract_lang="eng",
        visual_qa_timeout_seconds=2.0,
    )

    engine = runner._get_visual_qa_engine()

    assert isinstance(engine._provider, TesseractOcrProvider)


def test_get_visual_qa_engine_configures_health_detector(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    runner._framework_config = FrameworkConfig(
        visual_qa_health_enabled=True,
        visual_qa_health_provider="opencv",
        visual_qa_low_variance_threshold=2.5,
    )

    engine = runner._get_visual_qa_engine()

    assert isinstance(engine._health_detector, ScreenshotHealthDetector)
    assert engine._health_detector._cv2_module == "auto"
    assert engine._health_detector._low_variance_threshold == 2.5


def test_get_visual_qa_engine_can_disable_health_detector(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    runner._framework_config = FrameworkConfig(
        visual_qa_health_enabled=False,
        visual_qa_health_provider="opencv",
    )

    engine = runner._get_visual_qa_engine()

    assert isinstance(engine._health_detector, ScreenshotHealthDetector)
    assert engine._health_detector._cv2_module is None
