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


class ScreenCaptureSettings(Protocol):
    """Protocol for screenshot configuration — decouples evidence/ from config/.

    Implemented by config.schema.FrameworkConfig so that ScreenCapture.from_config()
    can consume config without evidence/ importing config/.
    """

    screenshot_rgb_threshold: int
    screenshot_target_resolution: str
    screenshot_resolution_tolerance: int
    screenshot_min_file_bytes: int
    screenshot_max_retries: int


class LogCollectorSettings(Protocol):
    """Protocol for log collector configuration — decouples evidence/ from config/.

    Implemented by config.schema.FrameworkConfig so that LogCollector.from_config()
    can consume config without evidence/ importing config/.
    """

    log_levels: str
    log_max_entries: int
    log_custom_paths: str
    log_backup_dir: str
    log_lock_retries: int
    log_lock_base_delay: float
    log_retention_days: int
    log_retention_max_bytes: int


class EvidencePackagerSettings(Protocol):
    """Protocol for evidence packager configuration — decouples evidence/ from config/.

    Implemented by config.schema.FrameworkConfig so that EvidencePackager.from_config()
    can consume config without evidence/ importing config/.
    """

    evidence_dir: str
    evidence_retention: int


class MetricsCollectorSettings(Protocol):
    """Protocol for metrics collector configuration — decouples evidence/ from config/.

    Implemented by config.schema.FrameworkConfig so that MetricsCollector.from_config()
    can consume config without evidence/ importing config/.
    """

    evidence_dir: str
    metrics_filename: str
