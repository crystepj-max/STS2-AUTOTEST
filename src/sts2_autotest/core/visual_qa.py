"""Visual QA engine for non-blocking screenshot OCR assistance."""

from __future__ import annotations

import re
import subprocess
import time
from csv import DictReader
from io import StringIO
from pathlib import Path
from typing import Protocol

from sts2_autotest.common.visual_qa import (
    OcrTextBlock,
    ScreenshotOcrAnalysis,
    VisualQaFinding,
)


_RAW_KEY_PATTERN = re.compile(
    r"\b(?=[A-Za-z0-9_./]*[A-Za-z_][A-Za-z0-9_./]*[./])"
    r"(?!(?:v|V)[0-9]+(?:[./][0-9]+)+\b)"
    r"(?:[A-Za-z_][A-Za-z0-9_]*[./]){2,}[A-Za-z_][A-Za-z0-9_]*\b"
)
_MISSING_MARKERS = (
    "MISSING",
    "TODO_LOCALIZE",
    "LOCALIZE_ME",
    "<missing>",
    "missing localization",
)
_TOKEN_PATTERN = re.compile(r"(\{[0-9]+\}|\{\{[^}]+\}\}|%s)")
_LOW_VARIANCE_THRESHOLD = 1.0


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


class TesseractOcrProvider:
    """OCR provider backed by the local tesseract command."""

    name = "tesseract"

    def __init__(
        self,
        command: str = "tesseract",
        lang: str = "chi_sim+eng",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._command = command
        self._lang = lang
        self._timeout_seconds = timeout_seconds

    def extract_text(self, image_path: Path) -> list[OcrTextBlock]:
        try:
            completed = subprocess.run(
                [self._command, str(image_path), "stdout", "-l", self._lang, "tsv"],
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"tesseract command not found: {self._command}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"tesseract timed out after {self._timeout_seconds} seconds"
            ) from exc
        if completed.returncode != 0:
            message = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise RuntimeError(f"tesseract failed: {message}")
        return self._parse_tsv(completed.stdout)

    @staticmethod
    def _parse_tsv(output: str) -> list[OcrTextBlock]:
        blocks: list[OcrTextBlock] = []
        reader = DictReader(StringIO(output), delimiter="\t")
        for row in reader:
            text = (row.get("text") or "").strip()
            if not text:
                continue
            blocks.append(
                OcrTextBlock(
                    text=text,
                    confidence=_parse_tesseract_confidence(row.get("conf")),
                    bbox=_parse_tesseract_bbox(row),
                )
            )
        return blocks


def _parse_tesseract_confidence(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        confidence = float(value)
    except ValueError:
        return None
    if confidence < 0:
        return None
    return round(min(confidence, 100.0) / 100.0, 4)


def _parse_tesseract_bbox(row: dict[str, str | None]) -> list[int] | None:
    values: list[int] = []
    for key in ("left", "top", "width", "height"):
        try:
            values.append(int(row.get(key) or ""))
        except ValueError:
            return None
    return values


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


class ScreenshotHealthDetector:
    """Detect screenshot-level health issues without affecting test results."""

    def __init__(
        self,
        *,
        cv2_module: object | None = "auto",
        low_variance_threshold: float = _LOW_VARIANCE_THRESHOLD,
    ) -> None:
        self._cv2_module = cv2_module
        self._low_variance_threshold = low_variance_threshold

    def analyze(self, image_path: Path) -> list[VisualQaFinding]:
        cv2_module = self._resolve_cv2()
        if cv2_module is None:
            return []

        try:
            image = cv2_module.imread(str(image_path), cv2_module.IMREAD_GRAYSCALE)
        except Exception:
            return []
        if image is None:
            return []

        variance = float(image.std())
        if variance >= self._low_variance_threshold:
            return []

        return [
            VisualQaFinding(
                rule_id="visual_health.low_variance",
                severity="warning",
                message=f"Screenshot has low visual variance ({variance:.3f})",
                text=str(image_path),
                confidence=None,
                bbox=None,
            )
        ]

    def _resolve_cv2(self) -> object | None:
        if self._cv2_module != "auto":
            return self._cv2_module
        try:
            import cv2  # type: ignore[import-not-found]
        except Exception:
            return None
        return cv2


class VisualQaEngine:
    """Run OCR and localization detectors for one screenshot."""

    def __init__(
        self,
        provider: OcrProvider | None = None,
        detector: LocalizationTextDetector | None = None,
        health_detector: ScreenshotHealthDetector | None = None,
    ) -> None:
        self._provider = provider or DisabledOcrProvider()
        self._detector = detector or LocalizationTextDetector()
        self._health_detector = health_detector or ScreenshotHealthDetector()

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
            findings = (
                self._health_detector.analyze(image_path)
                + self._detector.analyze(blocks)
            )
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
