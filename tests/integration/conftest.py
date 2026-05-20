"""STS2-AUTOTEST 集成测试公共 fixture。"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any

import pytest

from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.adapters.discovery import discover_sts2_cli


def _run(coro: Any) -> Any:
    """在同步 pytest 测试中执行 async adapter API。"""
    return asyncio.run(coro)


@pytest.fixture(scope="session")
def real_cli_path() -> str:
    """返回真实 sts2 CLI 路径；不存在时跳过 CLI 集成测试。"""
    cli_path = discover_sts2_cli()
    if cli_path is None:
        pytest.skip("未找到 STS2-Cli-Mod CLI，请设置 STS2_CLI_PATH 或安装 sts2.exe。")
    return cli_path


@pytest.fixture
def real_cli_adapter(real_cli_path: str) -> Generator[CliModAdapter, None, None]:
    """短超时真实 CLI adapter，用于不要求游戏运行的测试。"""
    adapter = CliModAdapter(cli_path=real_cli_path, timeout=2.0)
    yield adapter
    _run(adapter.cleanup())


@pytest.fixture
def game_adapter(real_cli_path: str) -> Generator[CliModAdapter, None, None]:
    """要求真实游戏和 Mod 可通信的 adapter。"""
    adapter = CliModAdapter(cli_path=real_cli_path, timeout=5.0)
    try:
        health = _run(adapter.health_check())
        if not health.healthy:
            pytest.skip(f"游戏或 STS2-Cli-Mod 未就绪：{health.message}")
        yield adapter
    finally:
        _run(adapter.cleanup())
