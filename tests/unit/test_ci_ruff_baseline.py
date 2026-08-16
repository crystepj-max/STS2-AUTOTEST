"""单元测试：Ruff 增量基线门禁的工具版本固定与显式路径参数。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/check_ruff_baseline.py"
_SPEC = importlib.util.spec_from_file_location("check_ruff_baseline_script", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
check_ruff_baseline = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = check_ruff_baseline
_SPEC.loader.exec_module(check_ruff_baseline)


def test_run_ruff_uses_explicit_bin(monkeypatch) -> None:
    """--ruff-bin 显式传入时，脚本必须使用该二进制而非 PATH。"""

    recorded: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        recorded.append(command)
        return type("Result", (), {"stderr": "", "stdout": json.dumps([])})()

    monkeypatch.setattr(check_ruff_baseline.subprocess, "run", fake_run)

    check_ruff_baseline._run_ruff(Path("."), "/fixed/bin/ruff")

    assert recorded[0][0] == "/fixed/bin/ruff"
    assert recorded[0][1:4] == ["check", "src", "tests"]


def test_resolve_tool_bin_prefers_explicit() -> None:
    assert check_ruff_baseline._resolve_tool_bin("/explicit/ruff", "ruff") == "/explicit/ruff"


def test_resolve_tool_bin_falls_back_to_python_dir(monkeypatch, tmp_path) -> None:
    """未显式传入时从当前 Python 同目录推导（不 resolve 符号链接）。"""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "ruff"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)

    monkeypatch.setattr(check_ruff_baseline.sys, "executable", str(bin_dir / "python"))

    assert check_ruff_baseline._resolve_tool_bin(None, "ruff") == str(tool)


def test_resolve_tool_bin_falls_back_to_path(monkeypatch, tmp_path) -> None:
    """Python 同目录无工具时回退到 PATH 名称。"""

    empty_dir = tmp_path / "bin"
    empty_dir.mkdir()
    monkeypatch.setattr(check_ruff_baseline.sys, "executable", str(empty_dir / "python"))

    assert check_ruff_baseline._resolve_tool_bin(None, "ruff") == "ruff"


def test_main_passes_separate_bins_to_baseline_and_current(monkeypatch, tmp_path) -> None:
    """--ruff-bin 与 --baseline-ruff-bin 可分别指定（基线独立 venv）。"""

    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    baseline_dir.mkdir()
    current_dir.mkdir()

    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        commands.append(command)
        return type("Result", (), {"stderr": "", "stdout": json.dumps([])})()

    monkeypatch.setattr(check_ruff_baseline.subprocess, "run", fake_run)

    rc = check_ruff_baseline.main(
        argv=[
            "--baseline-dir",
            str(baseline_dir),
            "--current-dir",
            str(current_dir),
            "--ruff-bin",
            "/venv-current/bin/ruff",
            "--baseline-ruff-bin",
            "/venv-baseline/bin/ruff",
        ]
    )

    assert rc == 0
    # 基线用 baseline venv 的 ruff，当前用 current venv 的 ruff
    assert commands[0][0] == "/venv-baseline/bin/ruff"
    assert commands[1][0] == "/venv-current/bin/ruff"
