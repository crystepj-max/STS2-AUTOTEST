"""Visual QA engine for non-blocking screenshot OCR assistance."""

from __future__ import annotations

import re
import subprocess
import time
from csv import DictReader
from io import StringIO
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from sts2_autotest.common.visual_qa import (
    DEFAULT_HIGH_BRIGHTNESS_THRESHOLD as _DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
)
from sts2_autotest.common.visual_qa import (
    DEFAULT_LOW_BRIGHTNESS_THRESHOLD as _DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
)
from sts2_autotest.common.visual_qa import (
    DEFAULT_LOW_VARIANCE_THRESHOLD as _DEFAULT_LOW_VARIANCE_THRESHOLD,
)
from sts2_autotest.common.visual_qa import (
    OcrTextBlock,
    ScreenshotOcrAnalysis,
    VisualQaFinding,
)

DEFAULT_LOW_VARIANCE_THRESHOLD = _DEFAULT_LOW_VARIANCE_THRESHOLD

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
_TESSERACT_TSV_HEADER_PREFIX = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"


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
        reader = DictReader(StringIO(_tesseract_tsv_payload(output)), delimiter="\t")
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


def _tesseract_tsv_payload(output: str) -> str:
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(_TESSERACT_TSV_HEADER_PREFIX):
            return "\n".join(lines[index:]) + "\n"
    return ""


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
            values.append(int(float(row.get(key) or "")))
        except ValueError:
            return None
    return values


class Cv2Image(Protocol):
    def std(self) -> float:
        ...

    def mean(self) -> float:
        ...


class Cv2Module(Protocol):
    IMREAD_GRAYSCALE: int

    def imread(self, path: str, flags: int) -> Cv2Image | None:
        ...


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
        cv2_module: Cv2Module | Literal["auto"] | None = "auto",
        low_variance_threshold: float = DEFAULT_LOW_VARIANCE_THRESHOLD,
        low_brightness_threshold: float = _DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
        high_brightness_threshold: float = _DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
    ) -> None:
        if isinstance(cv2_module, str) and cv2_module != "auto":
            raise ValueError("cv2_module must be 'auto', a cv2-like module, or None")
        self._cv2_module = cv2_module
        self._low_variance_threshold = low_variance_threshold
        self._low_brightness_threshold = low_brightness_threshold
        self._high_brightness_threshold = high_brightness_threshold

    def analyze(self, image_path: Path) -> list[VisualQaFinding]:
        cv2_module = self._resolve_cv2()
        if cv2_module is None:
            return []

        try:
            image = cv2_module.imread(str(image_path), cv2_module.IMREAD_GRAYSCALE)
            if image is None:
                return [
                    VisualQaFinding(
                        rule_id="visual_health.unreadable",
                        severity="warning",
                        message="Screenshot is not readable by OpenCV",
                        text=image_path.name,
                        confidence=None,
                        bbox=None,
                    )
                ]
            variance = float(image.std())
            mean = float(image.mean())
        except Exception:
            return []

        findings: list[VisualQaFinding] = []
        if variance < self._low_variance_threshold:
            findings.append(
                VisualQaFinding(
                    rule_id="visual_health.low_variance",
                    severity="warning",
                    message=f"Screenshot has low visual variance ({variance:.3f})",
                    text=image_path.name,
                    confidence=None,
                    bbox=None,
                )
            )

        if mean < self._low_brightness_threshold:
            findings.append(
                VisualQaFinding(
                    rule_id="visual_health.too_dark",
                    severity="warning",
                    message=f"Screenshot appears too dark (mean={mean:.3f})",
                    text=image_path.name,
                    confidence=None,
                    bbox=None,
                )
            )

        if mean > self._high_brightness_threshold:
            findings.append(
                VisualQaFinding(
                    rule_id="visual_health.too_bright",
                    severity="warning",
                    message=f"Screenshot appears too bright (mean={mean:.3f})",
                    text=image_path.name,
                    confidence=None,
                    bbox=None,
                )
            )

        return findings

    def _resolve_cv2(self) -> Cv2Module | None:
        if self._cv2_module != "auto":
            return self._cv2_module
        try:
            import cv2
        except Exception:
            return None
        return cast(Cv2Module, cv2)


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


def build_visual_qa_payload(
    *,
    test_run_id: str,
    analyses_by_path: dict[str, ScreenshotOcrAnalysis | dict[str, Any]],
) -> dict[str, Any]:
    screenshots: dict[str, Any] = {}
    summary: dict[str, Any] = {
        "total": 0,
        "passed": 0,
        "warning": 0,
        "skipped": 0,
        "findings_total": 0,
        "screenshots_with_findings": 0,
        "providers": {},
        "status_by_provider": {},
        "findings": {},
        "findings_by_severity": {},
    }

    for screenshot_path in sorted(analyses_by_path):
        payload = _analysis_payload(analyses_by_path[screenshot_path])
        if payload is None:
            continue

        screenshots[screenshot_path] = payload
        summary["total"] += 1

        status = str(payload.get("status", "skipped"))
        if status in ("passed", "warning", "skipped"):
            summary[status] += 1

        provider = str(payload.get("provider", "unknown"))
        providers = cast(dict[str, int], summary["providers"])
        providers[provider] = int(providers.get(provider, 0)) + 1

        status_by_provider = cast(dict[str, dict[str, int]], summary["status_by_provider"])
        provider_status = status_by_provider.setdefault(
            provider,
            {"passed": 0, "warning": 0, "skipped": 0},
        )
        if status in provider_status:
            provider_status[status] += 1

        findings = payload.get("findings", [])
        if not isinstance(findings, list):
            continue
        if findings:
            summary["screenshots_with_findings"] += 1

        summary["findings_total"] += len(findings)
        rule_counts = cast(dict[str, int], summary["findings"])
        severity_counts = cast(dict[str, int], summary["findings_by_severity"])
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            rule_id = str(finding.get("rule_id", "unknown"))
            rule_counts[rule_id] = int(rule_counts.get(rule_id, 0)) + 1

            severity = str(finding.get("severity", "warning"))
            severity_counts[severity] = int(severity_counts.get(severity, 0)) + 1

    return {
        "test_run_id": test_run_id,
        "summary": summary,
        "screenshots": screenshots,
    }


def _analysis_payload(
    analysis: ScreenshotOcrAnalysis | dict[str, Any],
) -> dict[str, Any] | None:
    if isinstance(analysis, ScreenshotOcrAnalysis):
        return analysis.model_dump(mode="json")
    if isinstance(analysis, dict):
        return analysis
    return None
