"""Lifecycle hooks for test session events (FR55).

Hooks can be registered by users to inject custom logic at key
points during the test lifecycle.
"""

import logging
from typing import Any, Callable

_logger = logging.getLogger("sts2_autotest.pytest_plugin.hooks")

HookFn = Callable[..., None]

_lifecycle_hooks: dict[str, list[HookFn]] = {
    "session_start": [],
    "session_end": [],
    "case_start": [],
    "case_end": [],
    "game_start": [],
    "game_stop": [],
    "state_reset": [],
}


def register(hook_point: str, callback: HookFn) -> None:
    """Register a callback for a lifecycle hook point."""
    if hook_point not in _lifecycle_hooks:
        raise ValueError(f"Unknown hook point: {hook_point}")
    _lifecycle_hooks[hook_point].append(callback)


def fire(hook_point: str, **kwargs: Any) -> None:
    """Execute all callbacks registered for a hook point.

    Individual callback exceptions are caught and logged to prevent
    one failing hook from blocking subsequent hooks.
    """
    for cb in _lifecycle_hooks.get(hook_point, []):
        try:
            cb(**kwargs)
        except Exception as exc:
            _logger.warning("Hook callback %r failed: %s", cb, exc)


def clear() -> None:
    """Remove all registered hooks. Primarily for testing."""
    for key in _lifecycle_hooks:
        _lifecycle_hooks[key].clear()
