"""Lifecycle hooks for test session events (FR55).

Hooks can be registered by users to inject custom logic at key
points during the test lifecycle.
"""

import logging
from typing import Any
from collections.abc import Callable

_logger = logging.getLogger("sts2_autotest.pytest_plugin.hooks")

HookFn = Callable[..., None]

HOOK_POINTS: tuple[str, ...] = (
    "session_start",
    "session_end",
    "case_start",
    "case_end",
    "game_start",
    "game_stop",
    "state_reset",
)


class HookRegistry:
    """Per-session lifecycle hook registry."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookFn]] = {
            hook_point: [] for hook_point in HOOK_POINTS
        }

    def register(self, hook_point: str, callback: HookFn) -> None:
        """Register a callback for a lifecycle hook point."""
        if hook_point not in self._hooks:
            raise ValueError(f"Unknown hook point: {hook_point}")
        self._hooks[hook_point].append(callback)

    def fire(self, hook_point: str, **kwargs: Any) -> None:
        """Execute all callbacks registered for a hook point."""
        for cb in self._hooks.get(hook_point, []):
            try:
                cb(**kwargs)
            except Exception as exc:
                _logger.warning("Hook callback %r failed: %s", cb, exc)

    def clear(self) -> None:
        """Remove all registered hooks."""
        for callbacks in self._hooks.values():
            callbacks.clear()


_default_registry = HookRegistry()


def register(hook_point: str, callback: HookFn) -> None:
    """Register a callback for a lifecycle hook point."""
    _default_registry.register(hook_point, callback)


def fire(hook_point: str, **kwargs: Any) -> None:
    """Execute all callbacks registered for a hook point.

    Individual callback exceptions are caught and logged to prevent
    one failing hook from blocking subsequent hooks.
    """
    _default_registry.fire(hook_point, **kwargs)


def clear() -> None:
    """Remove all registered hooks. Primarily for testing."""
    _default_registry.clear()
