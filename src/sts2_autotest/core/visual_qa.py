"""Visual QA engine for non-blocking screenshot OCR assistance."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Protocol

from sts2_autotest.common.visual_qa import (
    OcrTextBlock,
    ScreenshotOcrAnalysis,
    VisualQaFinding,
)


_RAW_KEY_PATTERN = re.compile(r"([A-Za-z0-9_]+[.:/]){2,}[A-Za-z0-9_]+")
_MISSING_MARKERS = (
    "MISSING",
    "TODO_LOCALIZE",
    "LOCALIZE_ME",
    "<missing>",
    "missing localization",
)
_TOKEN_PATTERN = re.compile(r"(\{[0-9]+\}|\{\{[^}]+\}\}|%s)")


class OcrProvider(Protocol):
    """Extract OCR text blocks from one screenshot."""

    name: str

    def extract_text(self, image_path: Path) -> list[OcrTextBlock]:
        """Return OCR text blocks for image_path."""
        ...


class DisabledOcrProvider:
    """Provider used when OCR is not configured."""

    name = "disabled"

    def extract_text(self, image_path: Path) -> list[OcrTextBlock]:
        raise RuntimeError("OCR provider is disabled")


class StaticOcrProvider:
    """Test provider returning preconfigured text by screenshot filename."""

    name = "static"

    def __init__(self, text_by_name: dict[str, list[str]] | None = None) -> None:
        self._text_by_name = text_by_name or {}

    def extract_text(self, image_path: Path) -> list[OcrTextBlock]:
        return [
            OcrTextBlock(text=text, confidence=1.0)
            for text in self._text_by_name.get(image_path.name, [])
        ]


class LocalizationTextDetector:
    """Detect localization risks in OCR text blocks."""

    def __init__(self, raw_key_pattern: str = _RAW_KEY_PATTERN.pattern) -> None:
        self._raw_key_pattern = re.compile(raw_key_pattern)

    def analyze(self, blocks: list[OcrTextBlock]) -> list[VisualQaFinding]:
        findings: list[VisualQaFinding] = []
        for block in blocks:
            text = block.text.strip()
            if not text:
                continue
            if self._raw_key_pattern.search(text):
                findings.append(
                    VisualQaFinding(
                        rule_id="localization_text.raw_key",
                        severity="warning",
                        message="疑似 localization key 出现在截图文本中",
                        text=text,
                        confidence=block.confidence,
                        bbox=block.bbox,
                    )
                )
                continue
            if any(marker.lower() in text.lower() for marker in _MISSING_MARKERS):
                findings.append(
                    VisualQaFinding(
                        rule_id="localization_text.missing_marker",
                        severity="warning",
                        message="疑似 missing localization 占位出现在截图文本中",
                        text=text,
                        confidence=block.confidence,
                        bbox=block.bbox,
                    )
                )
                continue
            if _TOKEN_PATTERN.search(text):
                findings.append(
                    VisualQaFinding(
                        rule_id="localization_text.unresolved_token",
                        severity="warning",
                        message="疑似未替换文本 token 出现在截图文本中",
                        text=text,
                        confidence=block.confidence,
                        bbox=block.bbox,
                    )
                )
        return findings


class VisualQaEngine:
    """Run OCR and localization detectors for one screenshot."""

    def __init__(
        self,
        provider: OcrProvider | None = None,
        detector: LocalizationTextDetector | None = None,
    ) -> None:
        self._provider = provider or DisabledOcrProvider()
        self._detector = detector or LocalizationTextDetector()

    def analyze_screenshot(self, image_path: Path) -> ScreenshotOcrAnalysis:
        started = time.perf_counter()
        provider_name = getattr(self._provider, "name", "unknown")

        if not image_path.is_file():
            return ScreenshotOcrAnalysis(
                status="skipped",
                provider=provider_name,
                message="screenshot not found",
                duration_ms=self._elapsed_ms(started),
            )

        try:
            blocks = self._provider.extract_text(image_path)
            findings = self._detector.analyze(blocks)
        except Exception as exc:
            return ScreenshotOcrAnalysis(
                status="skipped",
                provider=provider_name,
                message=f"{exc.__class__.__name__}: {exc}",
                duration_ms=self._elapsed_ms(started),
            )

        return ScreenshotOcrAnalysis(
            status="warning" if findings else "passed",
            provider=provider_name,
            findings=findings,
            extracted_text=blocks,
            duration_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 3)
