"""Recovery strategy — stub for Epic 2, full implementation in Epic 4 (FR5)."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class FailureRecord:
    """Record of a single adapter/crash failure."""

    error_type: str
    message: str
    timestamp: str


class RecoveryStrategy(Protocol):
    """Decide recovery action based on failure + history.

    MVP stub: always returns TERMINATE.
    Full implementation in Epic 4 (Story 4.2).
    """

    def decide(
        self, failure: Exception, history: list[FailureRecord]
    ) -> str:
        """Return recovery action: FAST_PATH / RECREATE / TERMINATE."""
        ...


class StubRecoveryStrategy:
    """MVP implementation — always terminates on failure."""

    def decide(
        self, failure: Exception, history: list[FailureRecord]
    ) -> str:
        return "TERMINATE"
