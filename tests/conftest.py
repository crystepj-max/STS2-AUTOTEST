"""pytest 全局配置。"""

from pathlib import Path

import pytest

import sts2_autotest


def pytest_sessionstart(session: pytest.Session) -> None:
    """确保测试导入当前仓库源码，而不是旧 editable worktree。"""
    repo_root = Path(__file__).resolve().parents[1]
    expected_pkg = repo_root / "src" / "sts2_autotest"
    actual_pkg = Path(sts2_autotest.__file__).resolve().parent

    if actual_pkg != expected_pkg:
        raise RuntimeError(
            "pytest 导入了错误的 sts2_autotest 包："
            f"{actual_pkg}；期望：{expected_pkg}。"
            '请在当前仓库运行 pip install -e ".[dev]"，'
            "或临时设置 PYTHONPATH=src 后再运行测试。"
        )
