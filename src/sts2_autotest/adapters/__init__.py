"""Adapter abstraction layer for STS2-AUTOTEST."""

from sts2_autotest.adapters.base import (
    ActionResult,
    GameAdapterProtocol,
    HealthStatus,
    get_adapter_capabilities,
)
from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.adapters.discovery import discover_sts2_cli

__all__ = [
    "GameAdapterProtocol",
    "ActionResult",
    "HealthStatus",
    "get_adapter_capabilities",
    "CliModAdapter",
    "discover_sts2_cli",
]
