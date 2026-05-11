"""Adapter abstraction layer for STS2-AUTOTEST."""

from sts2_autotest.adapters.base import (
    ActionResult,
    GameAdapterProtocol,
    HealthStatus,
)
from sts2_autotest.adapters.cli_mod import CliModAdapter

__all__ = [
    "GameAdapterProtocol",
    "ActionResult",
    "HealthStatus",
    "CliModAdapter",
]
