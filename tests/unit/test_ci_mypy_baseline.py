"""单元测试：mypy 增量基线门禁的配置文件与显式路径参数。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / ".github/scripts"
_SCRIPT = _SCRIPTS_DIR / "check_mypy_baseline.py"
_SPEC = importlib.util.spec_from_file_location("check_mypy_baseline_script", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
check_mypy_baseline = importlib.util.module_from_spec(_SPEC)
# sys.modules 常驻条目是刻意的（脚本与 src/ 命名空间隔离，仓库内无同名模块）；
# 若未来新增同名模块需改为 fixture 作用域化加载
sys.modules[_SPEC.name] = check_mypy_baseline
# 脚本内 `from runner_utils import ...` 需要 .github/scripts 在 sys.path 上
sys.path.insert(0, str(_SCRIPTS_DIR))
_SPEC.loader.exec_module(check_mypy_baseline)
runner_utils = sys.modules["runner_utils"]


def test_run_mypy_uses_config_file_and_explicit_bin(monkeypatch) -> None:
    """--config-file 与 mypy 二进制必须透传到子进程命令。"""

    recorded: list[list[str]] = []

    def fake_run_timed(name: str, cmd: list[str], log_path, *, timeout: float, cwd):
        recorded.append(cmd)
        return runner_utils.TimedResult(returncode=0, output="", error="", timed_out=False)

    monkeypatch.setattr(check_mypy_baseline, "run_timed", fake_run_timed)

    check_mypy_baseline._run_mypy(Path("."), "/fixed/bin/mypy", Path("/cfg/mypy-policy.ini"), timeout=10.0)

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

    def fake_run_timed(name: str, cmd: list[str], log_path, *, timeout: float, cwd):
        commands.append(cmd)
        return runner_utils.TimedResult(returncode=0, output="", error="", timed_out=False)

    monkeypatch.setattr(check_mypy_baseline, "run_timed", fake_run_timed)

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


def _run_main_with_findings(
    monkeypatch,
    tmp_path,
    capsys,
    baseline_findings: list[tuple[str, int, int, str, str]],
    current_findings: list[tuple[str, int, int, str, str]],
) -> tuple[int, str]:
    """用伪造 mypy 错误集合跑 main()，返回 (退出码, 输出全文)。"""

    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    baseline_dir.mkdir(exist_ok=True)
    current_dir.mkdir(exist_ok=True)
    config_file = tmp_path / "mypy-policy.ini"
    config_file.write_text("[mypy]\nstrict = True\n", encoding="utf-8")

    def fake_run_mypy(root, bin_path, config, timeout):
        if root == baseline_dir:
            return baseline_findings
        return current_findings

    monkeypatch.setattr(check_mypy_baseline, "_run_mypy", fake_run_mypy)

    rc = check_mypy_baseline.main(
        argv=[
            "--baseline-dir",
            str(baseline_dir),
            "--current-dir",
            str(current_dir),
            "--config-file",
            str(config_file),
        ]
    )
    return rc, capsys.readouterr().out


def test_main_rename_only_is_not_new(monkeypatch, tmp_path, capsys) -> None:
    """S1：只重命名文件——mypy 基线比较 0 新增，退出码 0（issue #17）。"""

    (tmp_path / "baseline" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "current" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "baseline" / "src" / "legacy_a.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "current" / "src" / "modern_a.py").write_text("import os\n", encoding="utf-8")

    rc, out = _run_main_with_findings(
        monkeypatch,
        tmp_path,
        capsys,
        baseline_findings=[("src/legacy_a.py", 1, 0, "unused-import", "Module os is imported but unused")],
        current_findings=[("src/modern_a.py", 1, 0, "unused-import", "Module os is imported but unused")],
    )

    assert rc == 0
    assert "Moved with files (not new): 1" in out
    assert "New in this PR: 0" in out


def test_main_move_plus_new_blocks_ci(monkeypatch, tmp_path, capsys) -> None:
    """S5：移动 + 实质新增——只报真实新增并阻断，退出码 1（issue #17）。"""

    (tmp_path / "baseline" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "current" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "baseline" / "src" / "legacy_a.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "current" / "src" / "modern_a.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "current" / "src" / "new_file.py").write_text("x: int = 's'\n", encoding="utf-8")

    rc, out = _run_main_with_findings(
        monkeypatch,
        tmp_path,
        capsys,
        baseline_findings=[("src/legacy_a.py", 1, 0, "unused-import", "Module os is imported but unused")],
        current_findings=[
            ("src/modern_a.py", 1, 0, "unused-import", "Module os is imported but unused"),
            ("src/new_file.py", 1, 0, "assignment", "Incompatible types in assignment"),
        ],
    )

    assert rc == 1
    assert "Moved with files (not new): 1" in out
    assert "src/new_file.py" in out
    assert "modern_a.py" not in out
    assert "CI failed: new mypy debt is not allowed." in out
