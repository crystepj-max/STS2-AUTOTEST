"""真实 STS2-Cli-Mod CLI 进程集成测试。

这些测试只要求 `sts2` CLI 可执行文件存在，不要求游戏正在运行。
"""

from __future__ import annotations

import re
import subprocess

import pytest

from sts2_autotest.adapters.base import ActionResult, HealthStatus
from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.common.errors import ErrorCategory, STS2Error

from .conftest import _run

pytestmark = pytest.mark.integration


def _run_version(real_cli_path: str) -> tuple[int, str]:
    """运行真实 CLI version 命令，避开 Windows capture_output 句柄问题。"""
    proc = subprocess.Popen(
        [real_cli_path, "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=2.0)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        stdout_bytes, stderr_bytes = proc.communicate()
        raise AssertionError(
            "sts2 --version timed out after 2.0s; "
            f"stdout={stdout_bytes.decode('utf-8', errors='replace')!r}; "
            f"stderr={stderr_bytes.decode('utf-8', errors='replace')!r}"
        ) from exc
    output = stdout_bytes.decode("utf-8", errors="replace")
    return proc.returncode, output


class TestRealCliExecutable:
    """真实 sts2 可执行文件基础契约。"""

    def test_version_exits_zero(self, real_cli_path: str) -> None:
        returncode, _ = _run_version(real_cli_path)
        assert returncode == 0

    def test_version_matches_semver(self, real_cli_path: str) -> None:
        _, version_output = _run_version(real_cli_path)
        output = version_output.strip()
        assert re.match(r"^\d+\.\d+\.\d+", output), output

    def test_adapter_accepts_real_version(self, real_cli_path: str) -> None:
        _, version_output = _run_version(real_cli_path)
        adapter = CliModAdapter(cli_path=real_cli_path, timeout=2.0, version_output=version_output)
        assert adapter._version_checked is True


class TestRealCliWithoutGame:
    """游戏未运行时的真实 CLI 降级契约。"""

    def test_health_check_returns_status(self, real_cli_adapter: CliModAdapter) -> None:
        result = _run(real_cli_adapter.health_check())
        assert isinstance(result, HealthStatus)
        assert isinstance(result.healthy, bool)

    def test_health_check_completes_under_adapter_timeout(
        self, real_cli_adapter: CliModAdapter
    ) -> None:
        import time

        started_at = time.monotonic()
        _run(real_cli_adapter.health_check())
        elapsed = time.monotonic() - started_at
        process_overhead_seconds = 1.0
        assert elapsed < real_cli_adapter.timeout + process_overhead_seconds

    def test_state_returns_or_raises_classified_adapter_error(
        self, real_cli_adapter: CliModAdapter
    ) -> None:
        try:
            state = _run(real_cli_adapter.get_state())
        except STS2Error as exc:
            assert exc.category == ErrorCategory.ADAPTER_ERROR
            assert exc.detail.get("subtype") is not None
        else:
            assert state.screen is not None

    def test_available_actions_empty_when_unhealthy(
        self, real_cli_adapter: CliModAdapter
    ) -> None:
        health = _run(real_cli_adapter.health_check())
        actions = _run(real_cli_adapter.get_available_actions())
        assert isinstance(actions, list)
        if not health.healthy:
            assert actions == []

    def test_act_returns_action_result_not_raw_exception(
        self, real_cli_adapter: CliModAdapter
    ) -> None:
        health = _run(real_cli_adapter.health_check())
        if health.healthy:
            pytest.skip("游戏正在运行，跳过可能改变游戏状态的无游戏降级测试。")

        result = _run(real_cli_adapter.act("play_card", {"card_id": "Strike"}))
        assert isinstance(result, ActionResult)
        assert result.status in {"failure", "timeout"}
        assert isinstance(result.state_changed, bool)
        assert result.detail is not None
