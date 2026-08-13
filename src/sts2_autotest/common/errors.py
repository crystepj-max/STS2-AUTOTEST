"""Unified error classification for STS2-AUTOTEST (PRD FR5)."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class FailureClassification(StrEnum):
    """Classification of test failure root cause (协议层 B20).

    Used by RepairAdvisor and autofix workflow to determine which
    repository or component needs repair.
    """
    MOD = "mod"
    AUTOTEST = "autotest"
    TEST_CASE = "test_case"
    ENVIRONMENT = "environment"
    UNKNOWN = "unknown"


class ErrorCategory(StrEnum):
    """Top-level error classification (6 categories)."""

    ADAPTER_ERROR = "adapter_error"
    GAME_ERROR = "game_error"
    ASSERTION_ERROR = "assertion_error"
    CRASH_ERROR = "crash_error"
    TIMEOUT_ERROR = "timeout_error"
    SESSION_ERROR = "session_error"


class AdapterErrorSubType(StrEnum):
    """Adapter error subtypes."""

    TIMEOUT = "timeout"
    JSON_PARSE_FAILURE = "json_parse_failure"
    PROCESS_EXIT = "process_exit"
    NONZERO_EXIT_CODE = "nonzero_exit_code"
    VERSION_MISMATCH = "version_mismatch"


# Canonical terminal run status string for environment blockage. Kept as a
# module constant so CLI / MCP / run_service share one spelling (previously
# duplicated as a bare string literal in several places).
BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"


class EnvironmentBlockReason(StrEnum):
    """Why a run could not start (or continue) due to the local environment.

    All of these classify a run as ``BLOCKED_ENVIRONMENT`` — never as a
    product/platform failure. A refused connection to the game control API
    (8080) is an environment block, not an "unknown" failure.
    """

    GAME_CONTROL_UNAVAILABLE = "GAME_CONTROL_UNAVAILABLE"
    GAME_START_FAILED = "GAME_START_FAILED"
    GAME_PROCESS_STALE = "GAME_PROCESS_STALE"
    GAME_READINESS_TIMEOUT = "GAME_READINESS_TIMEOUT"
    GUI_SESSION_UNAVAILABLE = "GUI_SESSION_UNAVAILABLE"


class EnvironmentIncidentReason(StrEnum):
    """Why a *running* task was stopped by the environment watchdog.

    Raised only after the environment was observed healthy and then degraded
    (e.g. macOS WindowServer crash) — used to stop cleanly instead of looping
    restarts.
    """

    WINDOWSERVER_UNHEALTHY = "WINDOWSERVER_UNHEALTHY"
    GUI_SESSION_UNAVAILABLE = "GUI_SESSION_UNAVAILABLE"
    GAME_CONTROL_LOST = "GAME_CONTROL_LOST"


class CancelFailureReason(StrEnum):
    """Why cancellation cleanup did not reach a clean CANCELLED state.

    A cancel request that cannot be cleaned up must NOT be reported as a
    normal ``CANCELLED`` — it maps to FAILED_PLATFORM / BLOCKED_ENVIRONMENT.
    """

    CANCEL_CLEANUP_FAILED = "CANCEL_CLEANUP_FAILED"
    CANCEL_EVIDENCE_FAILED = "CANCEL_EVIDENCE_FAILED"
    GAME_CONTROL_UNAVAILABLE = "GAME_CONTROL_UNAVAILABLE"


class STS2Error(Exception):
    """Base exception for all STS2-AUTOTEST errors.

    Error response structure: {type, message, detail, timestamp}
    All framework exceptions inherit from this class.
    """

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        detail: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        self.category = category
        self.message = message
        self.detail = detail if detail is not None else {}
        self.timestamp = timestamp or datetime.now(UTC)
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert to structured error response."""
        return {
            "type": self.category,
            "message": self.message,
            "detail": self.detail,
            "timestamp": self.timestamp.isoformat(),
        }


class SessionQueueError(STS2Error):
    """Session-level error for queue/lock conflicts (FR65).

    detail includes 'reason' (timeout/queue_full/lock_conflict)
    and optionally 'queue_position'.
    """

    def __init__(
        self,
        message: str,
        reason: str = "lock_conflict",
        queue_position: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        merged_detail = dict(detail or {})
        merged_detail["reason"] = reason
        if queue_position is not None:
            merged_detail["queue_position"] = queue_position
        super().__init__(
            category=ErrorCategory.SESSION_ERROR,
            message=message,
            detail=merged_detail,
        )
