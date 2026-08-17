"""单元测试：mypy 增量基线门禁的配置文件与显式路径参数。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/check_mypy_baseline.py"
_SPEC = importlib.util.spec_from_file_location("check_mypy_baseline_script", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
check_mypy_baseline = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = check_mypy_baseline
_SPEC.loader.exec_module(check_mypy_baseline)


def test_run_mypy_uses_config_file_and_explicit_bin(monkeypatch) -> None:
    """--config-file 与 mypy 二进制必须透传到子进程命令。"""

    recorded: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        recorded.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(check_mypy_baseline.subprocess, "run", fake_run)

    check_mypy_baseline._run_mypy(Path("."), "/fixed/bin/mypy", Path("/cfg/mypy-policy.ini"))

    assert recorded[0][0] == "/fixed/bin/mypy"
    assert recorded[0][1:3] == ["--config-file", "/cfg/mypy-policy.ini"]
    assert recorded[0][3] == "src/sts2_autotest"


def test_main_passes_config_and_separate_bins(monkeypatch, tmp_path) -> None:
    """main() 把 --config-file / --mypy-bin / --baseline-mypy-bin 分别传给基线与当前。"""

    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    baseline_dir.mkdir()
    current_dir.mkdir()
    config_file = tmp_path / "mypy-policy.ini"
    config_file.write_text("[mypy]\nstrict = True\n", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        commands.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(check_mypy_baseline.subprocess, "run", fake_run)

    rc = check_mypy_baseline.main(
        argv=[
            "--baseline-dir",
            str(baseline_dir),
            "--current-dir",
            str(current_dir),
            "--config-file",
            str(config_file),
            "--mypy-bin",
            "/venv-current/bin/mypy",
            "--baseline-mypy-bin",
            "/venv-baseline/bin/mypy",
        ]
    )

    assert rc == 0
    # 基线用 baseline venv 的 mypy + 同一 policy 配置；当前用 current venv 的 mypy
    assert commands[0][0] == "/venv-baseline/bin/mypy"
    assert commands[0][1:3] == ["--config-file", str(config_file)]
    assert commands[1][0] == "/venv-current/bin/mypy"
    assert commands[1][1:3] == ["--config-file", str(config_file)]


def test_resolve_tool_bin_prefers_explicit() -> None:
    assert check_mypy_baseline._resolve_tool_bin("/explicit/mypy", "mypy") == "/explicit/mypy"
