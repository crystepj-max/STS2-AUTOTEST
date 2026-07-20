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


@dataclass(frozen=True)
class DebugVerification:
    """调试能力（如快速结束战斗 win_combat）的真实运行状态。

    区分"配置要求启用"与"实际探测确认可用"，避免只按配置声明能力。

    Args:
        configured: 配置是否要求启用调试能力。
        verified:   通过非破坏性探测确认调试控制台实际可用。
        reason:     不可用原因（如 NOT_CONFIGURED / GAME_CONTROL_UNAVAILABLE /
                    DEBUG_CONSOLE_UNAVAILABLE / NOT_SUPPORTED）；可用时为 None。
        checked_at: 最近一次验证的 ISO 时间戳。
    """

    configured: bool
    verified: bool
    reason: str | None = None
    checked_at: str | None = None


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

    async def verify_debug_actions(self) -> "DebugVerification":
        """非破坏性地验证调试能力是否真实可用。

        实现必须只用无副作用的探测（如调试控制台 help），严禁执行任何会改变
        游戏进度的命令（尤其是结束战斗）。返回配置/实际验证/原因/时间的组合。
        """
        ...
