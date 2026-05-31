"""Public type aliases and adapter capability definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
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


class RecoverySettings(Protocol):
    """Protocol for recovery strategy configuration — decouples core/ from config/.

    Implemented by config.schema.ExecutionConfig so that DefaultRecoveryStrategy
    can consume config without core/ importing config/.
    """

    max_consecutive_failures: int
    game_startup_timeout: float


class SessionStatus(StrEnum):
    """Watchdog-tracked session status. Referenced by watchdog, orchestrator, cli."""

    RUNNING = "RUNNING"
    ZOMBIE = "ZOMBIE"
    TERMINATED = "TERMINATED"


class WatchdogSettings(Protocol):
    """Protocol for watchdog configuration — decouples core/ from config/.

    Implemented by config.schema.ExecutionConfig.
    """

    heartbeat_timeout: float


class ProgressSettings(Protocol):
    """Protocol for progress persistence configuration — decouples core/ from config/.

    Implemented by config.schema.FrameworkConfig so that progress module
    can consume config without core/ importing config/.
    """

    progress_dir: str
    progress_filename: str


class LockManagerSettings(Protocol):
    """Protocol for lock manager configuration — decouples core/ from config/.

    Implemented by config.schema.FrameworkConfig so that LockManager
    can consume config without core/ importing config/.
    """

    lock_file: str


class SessionQueueSettings(Protocol):
    """Protocol for session queue configuration — decouples core/ from config/.

    Implemented by config.schema.ExecutionConfig so that SessionQueue
    can consume config without core/ importing config/.
    """

    session_queue_timeout: float
    session_queue_max_depth: int


class DataValidationSettings(Protocol):
    """Protocol for data validation configuration — decouples core/ from config/.

    Implemented by config.schema.FrameworkConfig so that data validation code
    can consume config without core/ importing config/.
    """

    strict_validation: bool


class PrecheckSettings(Protocol):
    """Protocol for pre-check configuration — decouples core/ from config/.

    Fields are satisfied at PrecheckRunner construction time by extracting
    values from FrameworkConfig, ExecutionConfig, and AdapterConfig.
    """

    disk_threshold_mb: int
    lock_file: str
    screenshot_dir: str
    evidence_dir: str
    adapter_cli_path: str
    adapter_timeout: float


class DesktopNotifier(Protocol):
    """Protocol for desktop notification — implemented by platform backends.

    level values: "info" | "warning". Platform implementations map
    these to native notification severity levels.
    """

    def notify(self, title: str, message: str, level: str) -> None: ...
