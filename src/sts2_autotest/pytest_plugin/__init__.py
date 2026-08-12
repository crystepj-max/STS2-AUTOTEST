"""pytest plugin integration for STS2-AUTOTEST."""

from sts2_autotest.pytest_plugin.fixtures import (
    SessionInitError,
    UserError,
    autotest,
    game_state,
)
from sts2_autotest.pytest_plugin.hooks import HookFn, clear, fire, register

__all__ = [
    "HookFn",
    "SessionInitError",
    "UserError",
    "autotest",
    "clear",
    "fire",
    "game_state",
    "register",
]
