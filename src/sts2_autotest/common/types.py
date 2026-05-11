"""Public type aliases and adapter capability definitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Capabilities:
    """Dynamic adapter capability discovery flags.

    Used to query what features an adapter supports at runtime.
    """

    supports_multiplayer: bool = False
    supports_metadata: bool = False
    supports_debug_actions: bool = False


@dataclass
class CaptureResult:
    """Screenshot capture result — OK/ERROR/SKIPPED three-state.

    Shared across evidence, core, and dsl packages via common/.
    """

    __test__ = False

    status: str  # "ok" | "error" | "skipped"
    path: Path | None = None
    message: str | None = None
    rgb_count: int | None = None
    resolution: tuple[int, int] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class ScreenCaptureProtocol(Protocol):
    """Protocol for screenshot capture — implemented by evidence.capture.ScreenCapture."""

    def capture_with_validation(
        self, window_title: str, case_id: str
    ) -> CaptureResult: ...

    def capture(
        self, window_title: str, case_id: str = "unknown"
    ) -> CaptureResult: ...
