"""Unified error classification for STS2-AUTOTEST (PRD FR5)."""

from datetime import datetime, timezone
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
        self.timestamp = timestamp or datetime.now(timezone.utc)
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
