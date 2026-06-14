"""Visual QA data models for screenshot OCR analysis."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OcrTextBlock(BaseModel):
    """A single OCR text block extracted from a screenshot."""

    model_config = ConfigDict(frozen=True)

    text: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    bbox: list[int] | None = None


class VisualQaFinding(BaseModel):
    """A non-blocking OCR finding shown as report assistance."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    severity: Literal["warning", "info"]
    message: str
    text: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    bbox: list[int] | None = None


class ScreenshotOcrAnalysis(BaseModel):
    """OCR analysis result for one screenshot.

    Status values are intentionally non-failing. A warning is displayed in
    reports but must not change the underlying test result.
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["passed", "warning", "skipped"]
    provider: str
    findings: list[VisualQaFinding] = []
    extracted_text: list[OcrTextBlock] = []
    message: str | None = None
    duration_ms: float = Field(default=0.0, ge=0.0)
