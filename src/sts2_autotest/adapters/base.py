"""Adapter Protocol and shared types for STS2-AUTOTEST (FR25, FR8).

Defines the GameAdapterProtocol — the single interface that all game
adapters must implement. Orchestrator depends only on this Protocol,
enabling mock substitution and adapter independence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from sts2_autotest.common.state import GameState


@dataclass(frozen=True)
class ActionResult:
    """Result of executing a game action.

    Args:
        status: success / failure / timeout
        state_changed: whether the action modified game state
        detail: optional human-readable context (error message, etc.)
    """

    status: Literal["success", "failure", "timeout"]
    state_changed: bool
    detail: str | None = None


@dataclass(frozen=True)
class HealthStatus:
    """Adapter health check result."""

    healthy: bool
    message: str | None = None


@runtime_checkable
class GameAdapterProtocol(Protocol):
    """Unified adapter interface — 6 core methods (FR25).

    All methods are async. Synchronous adapters wrap internal
    calls via asyncio.to_thread(). Protocol enables structural
    typing: any object with these 6 async methods satisfies the
    interface without explicit inheritance.
    """

    async def health_check(self) -> HealthStatus:
        """Check adapter and game connectivity."""
        ...

    async def get_state(self) -> "GameState":
        """Return current game state as a frozen GameState snapshot."""
        ...

    async def get_available_actions(self) -> list[str]:
        """Return list of currently legal action names."""
        ...

    async def act(
        self, action: str, args: dict[str, Any] | None = None
    ) -> ActionResult:
        """Execute a game action and return the result."""
        ...

    async def wait_until_actionable(self, timeout: float) -> bool:
        """Block until health_check passes and actions are available."""
        ...

    async def capture_bug_snapshot(self) -> dict[str, Any]:
        """Capture current state snapshot for debugging."""
        ...

    async def cleanup(self) -> None:
        """Release adapter resources (connections, handles, temp files).

        Called during test teardown. Must be safe to call multiple times.
        """
        ...
