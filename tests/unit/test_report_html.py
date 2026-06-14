"""Unit tests for HTML report rendering."""

from __future__ import annotations

import base64

from sts2_autotest.report_html import build_report_html


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlH0wAAAABJRU5ErkJggg=="
)


def test_build_report_html_counts_blocked_failed_and_skipped(tmp_path):
    """Summary cards should count blocked and skipped separately from failures."""
    config = {
        "test_run_id": "demo-run",
        "test_cases": [
            {"id": "PASS", "name": "Pass", "result": "通过", "steps": []},
            {"id": "FAIL", "name": "Fail", "result": "失败", "steps": []},
            {"id": "BLOCK", "name": "Block", "result": "阻塞", "steps": []},
            {"id": "SKIP", "name": "Skip", "result": "跳过", "steps": []},
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert '<div class="summary-card fail"><div class="num">1</div>失败</div>' in html
    assert '<div class="summary-card block"><div class="num">1</div>阻塞</div>' in html
    assert '<div class="summary-card skip"><div class="num">1</div>跳过</div>' in html


def test_build_report_html_embeds_card_screenshots(tmp_path):
    """Card evidence should embed screenshot images into the rendered report."""
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    before = screenshots / "card-before.png"
    before.write_bytes(_PNG_1X1)

    config = {
        "test_run_id": "demo-run",
        "test_cases": [
            {
                "id": "Card Smoke Test",
                "name": "Card Smoke Test",
                "result": "通过",
                "steps": [],
                "card_results": [
                    {
                        "card_id": "GAWAINMOD-STRIKE_GAWAIN",
                        "name": "打击",
                        "result": "跳过",
                        "exp": {"伤害": 6},
                        "screenshot_before": "screenshots/card-before.png",
                        "screenshot_after": "",
                    }
                ],
            }
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert "data:image/png;base64," in html
    assert "GAWAINMOD-STRIKE_GAWAIN" in html
    assert "打出前" in html


def test_build_report_html_renders_ocr_warning_block(tmp_path):
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    before = screenshots / "card-before.png"
    before.write_bytes(_PNG_1X1)

    config = {
        "test_run_id": "demo-run",
        "test_cases": [
            {
                "id": "Card Smoke Test",
                "name": "Card Smoke Test",
                "result": "通过",
                "steps": [],
                "card_results": [
                    {
                        "card_id": "GAWAINMOD-STRIKE_GAWAIN",
                        "name": "打击",
                        "result": "通过",
                        "exp": {"伤害": 6},
                        "screenshot_before": "screenshots/card-before.png",
                        "screenshot_before_ocr": {
                            "status": "warning",
                            "provider": "static",
                            "findings": [
                                {
                                    "rule_id": "localization_text.raw_key",
                                    "severity": "warning",
                                    "message": "疑似 localization key 出现在截图文本中",
                                    "text": "gawain.card.strike.name",
                                    "confidence": 0.9,
                                }
                            ],
                        },
                        "screenshot_after": "",
                    }
                ],
            }
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert "OCR 辅助分析：发现 1 条可疑文案" in html
    assert "gawain.card.strike.name" in html
    assert "Provider: static" in html
    assert '<span class="badge badge-pass">通过</span>' in html


def test_build_report_html_renders_ocr_passed_block(tmp_path):
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    image = screenshots / "card-before.png"
    image.write_bytes(_PNG_1X1)

    config = {
        "test_run_id": "demo-run",
        "test_cases": [
            {
                "id": "Card Smoke Test",
                "name": "Card Smoke Test",
                "result": "通过",
                "steps": [],
                "card_results": [
                    {
                        "card_id": "CARD",
                        "name": "打击",
                        "result": "通过",
                        "screenshot_before": "screenshots/card-before.png",
                        "screenshot_before_ocr": {
                            "status": "passed",
                            "provider": "static",
                            "findings": [],
                        },
                    }
                ],
            }
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert "OCR 辅助分析：未发现 localization 风险" in html


def test_build_report_html_renders_ocr_skipped_block(tmp_path):
    config = {
        "test_run_id": "demo-run",
        "test_cases": [
            {
                "id": "Card Smoke Test",
                "name": "Card Smoke Test",
                "result": "通过",
                "steps": [],
                "card_results": [
                    {
                        "card_id": "CARD",
                        "name": "打击",
                        "result": "通过",
                        "screenshot_before": "",
                        "screenshot_before_ocr": {
                            "status": "skipped",
                            "provider": "disabled",
                            "message": "RuntimeError: OCR provider is disabled",
                        },
                    }
                ],
            }
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert "OCR 辅助分析：未执行 - RuntimeError: OCR provider is disabled" in html


def test_build_report_html_omits_ocr_block_when_field_missing(tmp_path):
    config = {
        "test_run_id": "demo-run",
        "test_cases": [
            {
                "id": "Card Smoke Test",
                "name": "Card Smoke Test",
                "result": "通过",
                "steps": [],
                "card_results": [
                    {
                        "card_id": "CARD",
                        "name": "打击",
                        "result": "通过",
                        "screenshot_before": "",
                    }
                ],
            }
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert "OCR 辅助分析" not in html
    assert "CARD" in html
