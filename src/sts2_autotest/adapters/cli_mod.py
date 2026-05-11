"""CliModAdapter — MVP adapter for STS2-Cli-Mod (FR8, FR9, FR26, FR50).

Wraps synchronous CLI calls with asyncio.to_thread() to satisfy the
async GameAdapterProtocol. MVP implementation returns stub data;
real game integration happens in Story 1.5.
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from sts2_autotest.adapters.base import ActionResult, HealthStatus
from sts2_autotest.common.errors import AdapterErrorSubType, ErrorCategory, STS2Error
from sts2_autotest.common.state import GameScreen, GameState


class CliModAdapter:
    """STS2-Cli-Mod adapter implementing the GameAdapterProtocol.

    MVP: all methods return stub/skeleton data. Real subprocess calls
    to the sts2 CLI will be added in Story 1.5 (Steam & Game Process
    Controller) once game process management is available.
    """

    SUPPORTED_MAJOR_VERSION = 1

    def __init__(
        self,
        cli_path: str = "sts2",
        timeout: float = 30.0,
        version_output: str | None = None,
    ) -> None:
        self.cli_path = cli_path
        self.timeout = timeout
        self._cache_stale = True
        self._cached_state: GameState | None = None
        self._version_checked = False
        if version_output is not None:
            self._check_version(version_output)

    # ── public async interface ──────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Check adapter health. MVP returns healthy unconditionally."""
        return await asyncio.to_thread(self._health_check_sync)

    async def get_state(self) -> GameState:
        """Read current game state. Cached when not stale."""
        return await asyncio.to_thread(self._get_state_sync)

    async def get_available_actions(self) -> list[str]:
        """List currently legal actions. MVP returns empty list."""
        return await asyncio.to_thread(self._get_available_actions_sync)

    async def act(
        self, action: str, args: dict[str, Any] | None = None
    ) -> ActionResult:
        """Execute a game action. Marks cache stale after execution."""
        return await asyncio.to_thread(self._act_sync, action, args)

    async def wait_until_actionable(self, timeout: float) -> bool:
        """Wait until the game is actionable. MVP returns True immediately."""
        return await asyncio.to_thread(self._wait_until_actionable_sync, timeout)

    async def capture_bug_snapshot(self) -> dict[str, Any]:
        """Capture a debugging snapshot of current state."""
        return await asyncio.to_thread(self._capture_bug_snapshot_sync)

    async def cleanup(self) -> None:
        """Release resources. MVP: clear cache, mark stale."""
        self._cached_state = None
        self._cache_stale = True

    # ── synchronous internals (wrapped by asyncio.to_thread) ──

    def _health_check_sync(self) -> HealthStatus:
        return HealthStatus(healthy=True, message="MVP stub — no real game")

    def _get_state_sync(self) -> GameState:
        if not self._cache_stale and self._cached_state is not None:
            return self._cached_state
        self._cached_state = GameState(screen=GameScreen.MAIN_MENU)
        self._cache_stale = False
        return self._cached_state

    def _get_available_actions_sync(self) -> list[str]:
        return []

    def _act_sync(
        self, action: str, args: dict[str, Any] | None = None
    ) -> ActionResult:
        self._cache_stale = True
        return ActionResult(status="success", state_changed=True)

    def _wait_until_actionable_sync(self, timeout: float) -> bool:
        return True

    def _capture_bug_snapshot_sync(self) -> dict[str, Any]:
        return {
            "game_state": self._get_state_sync(),
            "available_actions": self._get_available_actions_sync(),
            "timestamp": datetime.now(timezone.utc),
        }

    # ── version handshake ────────────────────────────────────

    def _check_version(self, version_output: str) -> None:
        """Parse 'MAJOR.MINOR.PATCH' and verify major version (FR50).

        Raises STS2Error(ADAPTER_ERROR) on parse failure or major mismatch.
        """
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version_output.strip())
        if not match:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=f"Cannot parse version from: {version_output!r}",
                detail={
                    "subtype": AdapterErrorSubType.JSON_PARSE_FAILURE,
                    "command": "sts2 --version",
                    "raw_output": version_output,
                },
            )
        major = int(match.group(1))
        if major != self.SUPPORTED_MAJOR_VERSION:
            raise STS2Error(
                category=ErrorCategory.ADAPTER_ERROR,
                message=(
                    f"Adapter major version {major} is incompatible "
                    f"(supported: {self.SUPPORTED_MAJOR_VERSION}). "
                    f"Please upgrade STS2-Cli-Mod."
                ),
                detail={
                    "subtype": AdapterErrorSubType.VERSION_MISMATCH,
                    "command": "sts2 --version",
                    "raw_output": version_output,
                },
            )
        self._version_checked = True
