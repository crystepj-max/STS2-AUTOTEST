"""STS2-AUTOTEST: End-to-end automated testing framework for Slay the Spire 2 mods."""

__version__ = "0.1.0"

from sts2_autotest.common import Capabilities, GameScreen, GameState, STS2Error

__all__ = [
    "Capabilities",
    "GameScreen",
    "GameState",
    "STS2Error",
    "__version__",
]
