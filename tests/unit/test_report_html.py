"""Unit tests for HTML report rendering."""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

from sts2_autotest.report_html import _b64img, build_report_html, write_html_report

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlH0wAAAABJRU5ErkJggg=="
)


def test_b64img_uses_jpeg_mime_type(tmp_path: Path) -> None:
    image = tmp_path / "event.jpg"
    image.write_bytes(b"jpeg")
    assert _b64img(image).startswith("data:image/jpeg;base64,")


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
                                    "bbox": [34, 56, 88, 24],
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

    assert "视觉辅助分析：发现 1 条可疑项" in html
    assert "gawain.card.strike.name" in html
    assert "confidence=0.9" in html
    assert "bbox=[34, 56, 88, 24]" in html
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

    assert "视觉辅助分析：未发现风险" in html


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

    assert "视觉辅助分析：未执行 - RuntimeError: OCR provider is disabled" in html


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

    assert "视觉辅助分析" not in html
    assert "CARD" in html


def test_build_report_html_with_user_game_screenshot_fixture(tmp_path):
    fixture = Path("tests/fixtures/visual_qa/gawain-card-before.png")
    assert fixture.is_file()
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    target = screenshots / "gawain-card-before.png"
    shutil.copy2(fixture, target)

    config = {
        "test_run_id": "fixture-run",
        "test_cases": [
            {
                "id": "Card Smoke Test",
                "name": "Card Smoke Test",
                "result": "通过",
                "steps": [],
                "card_results": [
                    {
                        "card_id": "GAWAINMOD-PORTABLE_MAGIC_TERMINAL",
                        "name": "便携魔导终端",
                        "result": "通过",
                        "exp": {"储能": 1},
                        "screenshot_before": "screenshots/gawain-card-before.png",
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

    assert "data:image/png;base64," in html
    assert "视觉辅助分析：未发现风险" in html
    assert '<span class="badge badge-pass">通过</span>' in html


def test_build_report_html_escapes_user_controlled_text(tmp_path):
    malicious = '</code></ul></div><script>alert(1)</script>'
    config = {
        "test_run_id": malicious,
        "metadata": {"navigation_path": malicious},
        "test_cases": [
            {
                "id": malicious,
                "name": malicious,
                "scenario": malicious,
                "assertions": [malicious],
                "actual": malicious,
                "result": "通过",
                "steps": [
                    {
                        "name": malicious,
                        "result": "通过",
                        "detail": malicious,
                    }
                ],
                "card_results": [
                    {
                        "card_id": malicious,
                        "name": malicious,
                        "result": "通过",
                        "exp": {malicious: malicious},
                        "screenshot_before": "",
                        "screenshot_before_ocr": {
                            "status": "warning",
                            "provider": malicious,
                            "findings": [
                                {
                                    "severity": malicious,
                                    "message": malicious,
                                    "text": malicious,
                                }
                            ],
                        },
                    }
                ],
            }
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert malicious not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_write_html_report_writes_temp_file_before_replace(
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "test-results.json"
    output_path = tmp_path / "test-report.html"
    config_path.write_text(
        json.dumps({"test_run_id": "atomic", "test_cases": [], "card_results": []}),
        encoding="utf-8",
    )
    original_write_text = Path.write_text
    written_paths: list[Path] = []

    def recording_write_text(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        written_paths.append(self)
        return original_write_text(
            self,
            data,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "write_text", recording_write_text)

    write_html_report(config_path, output_path)

    assert written_paths
    assert written_paths[0] != output_path
    assert output_path.is_file()


def test_report_renders_restart_and_relaunch_counts(tmp_path):
    """阶段 C：报告暴露重启/重拉计数，可对比改造前后（0 也显示）。"""
    config = {
        "test_run_id": "perf-run",
        "test_cases": [],
        "card_results": [],
        "restart_count": 2,
        "relaunch_count": 0,
        "_config_dir": str(tmp_path),
    }
    html = build_report_html(config)

    assert '<div class="num">2</div>重启次数' in html
    assert '<div class="num">0</div>重拉次数' in html


def test_report_omits_counter_cards_when_not_provided(tmp_path):
    """未提供计数（既有报告格式）→ 不渲染计数卡，避免破坏旧报告。"""
    config = {
        "test_run_id": "demo-run",
        "test_cases": [],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }
    html = build_report_html(config)

    assert "重启次数" not in html
    assert "重拉次数" not in html
