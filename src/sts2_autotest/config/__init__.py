"""Configuration system for STS2-AUTOTEST."""

from sts2_autotest.config.errors import ConfigValidationError
from sts2_autotest.config.loader import load_config
from sts2_autotest.config.schema import STS2Config

__all__ = [
    "STS2Config",
    "load_config",
    "ConfigValidationError",
]
