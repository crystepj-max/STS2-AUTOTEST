"""Common shared types and utilities for STS2-AUTOTEST.

This is the ONLY shared cross-module layer. No module imports another
module except through common/. Enforced by import-linter.
"""

from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.common.errors import STS2Error
from sts2_autotest.common.types import Capabilities, CaptureResult, ScreenCaptureProtocol

__all__ = [
    "GameScreen",
    "GameState",
    "STS2Error",
    "Capabilities",
    "CaptureResult",
    "ScreenCaptureProtocol",
]
