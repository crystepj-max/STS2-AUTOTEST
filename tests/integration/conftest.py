"""Integration test configuration for STS2-AUTOTEST.

中文说明：该说明保留英文术语，并补充中文语境。

中文说明：该说明保留英文术语，并补充中文语境。"""

import asyncio
from typing import Any

import pytest

from sts2_autotest.adapters.discovery import discover_sts2_cli

# Skip all integration tests if CLI is not available
cli_path = discover_sts2_cli()


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Apply integration marker metadata.

    中文说明：该说明保留英文术语，并补充中文语境。"""
    if item.get_closest_marker("integration"):
        if cli_path is None:
            pytest.skip("STS2-Cli-Mod CLI not found")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)
