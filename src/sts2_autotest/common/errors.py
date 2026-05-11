"""Unified error classification for STS2-AUTOTEST (PRD FR5)."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    """Top-level error classification (5 categories)."""

    ADAPTER_ERROR = "adapter_error"
    GAME_ERROR = "game_error"
    ASSERTION_ERROR = "assertion_error"
    CRASH_ERROR = "crash_error"
    TIMEOUT_ERROR = "timeout_error"


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
