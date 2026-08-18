"""单元测试：Ruff 增量基线门禁的工具版本固定与显式路径参数。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / ".github/scripts"
_SCRIPT = _SCRIPTS_DIR / "check_ruff_baseline.py"
_SPEC = importlib.util.spec_from_file_location("check_ruff_baseline_script", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
check_ruff_baseline = importlib.util.module_from_spec(_SPEC)
# sys.modules 常驻条目是刻意的（脚本与 src/ 命名空间隔离，仓库内无同名模块）；
# 若未来新增同名模块需改为 fixture 作用域化加载
sys.modules[_SPEC.name] = check_ruff_baseline
# 脚本内 `from runner_utils import ...` 需要 .github/scripts 在 sys.path 上
sys.path.insert(0, str(_SCRIPTS_DIR))
_SPEC.loader.exec_module(check_ruff_baseline)
runner_utils = sys.modules["runner_utils"]


def test_run_ruff_uses_explicit_bin(monkeypatch) -> None:
    """--ruff-bin 显式传入时，脚本必须使用该二进制而非 PATH。"""

    recorded: list[list[str]] = []

    def fake_run_timed(name: str, cmd: list[str], log_path, *, timeout: float, cwd):
        recorded.append(cmd)
        return runner_utils.TimedResult(returncode=0, output="[]", error="", timed_out=False)

    monkeypatch.setattr(check_ruff_baseline, "run_timed", fake_run_timed)

    check_ruff_baseline._run_ruff(Path("."), "/fixed/bin/ruff", timeout=10.0)

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


def test_relative_bin_absolute_for_baseline_cwd(monkeypatch, tmp_path) -> None:
    """CI 传入相对 bin 路径（.venv-baseline/bin/ruff）时必须转绝对，否则子进程 cwd=基线目录时解析错误。"""

    # 模拟脚本在仓库根运行、CI 传入相对路径
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)

    resolved = check_ruff_baseline._resolve_tool_bin(".venv-baseline/bin/ruff", "ruff")
    assert resolved == str((repo_root / ".venv-baseline" / "bin" / "ruff").resolve())
    assert Path(resolved).is_absolute()


def test_main_passes_separate_bins_to_baseline_and_current(monkeypatch, tmp_path) -> None:
    """--ruff-bin 与 --baseline-ruff-bin 可分别指定（基线独立 venv）。"""

    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    baseline_dir.mkdir()
    current_dir.mkdir()

    commands: list[list[str]] = []

    def fake_run_timed(name: str, cmd: list[str], log_path, *, timeout: float, cwd):
        commands.append(cmd)
        return runner_utils.TimedResult(returncode=0, output="[]", error="", timed_out=False)

    monkeypatch.setattr(check_ruff_baseline, "run_timed", fake_run_timed)

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


def _finding(filename: str, code: str, message: str, row: int = 1) -> dict:
    return {
        "filename": filename,
        "location": {"row": row, "column": 1},
        "code": code,
        "message": message,
    }


def test_main_rename_only_is_not_new(monkeypatch, tmp_path, capsys) -> None:
    """S1：只重命名文件——Ruff 基线比较 0 新增，退出码 0（issue #17）。"""

    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    (baseline_dir / "src").mkdir(parents=True)
    (current_dir / "src").mkdir(parents=True)
    (baseline_dir / "src" / "legacy_a.py").write_text("import os\n", encoding="utf-8")
    (current_dir / "src" / "modern_a.py").write_text("import os\n", encoding="utf-8")

    def fake_run_ruff(root, bin_path, timeout):
        if root == baseline_dir:
            return [_finding("src/legacy_a.py", "F401", "unused import")]
        return [_finding("src/modern_a.py", "F401", "unused import")]

    monkeypatch.setattr(check_ruff_baseline, "_run_ruff", fake_run_ruff)

    rc = check_ruff_baseline.main(
        argv=["--baseline-dir", str(baseline_dir), "--current-dir", str(current_dir)]
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "Moved with files (not new): 1" in out
    assert "New in this PR: 0" in out


def test_main_move_plus_new_blocks_ci(monkeypatch, tmp_path, capsys) -> None:
    """S5：移动 + 实质新增——只报真实新增并阻断，退出码 1（issue #17）。"""

    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    (baseline_dir / "src").mkdir(parents=True)
    (current_dir / "src").mkdir(parents=True)
    (baseline_dir / "src" / "legacy_a.py").write_text("import os\n", encoding="utf-8")
    (current_dir / "src" / "modern_a.py").write_text("import os\n", encoding="utf-8")
    (current_dir / "src" / "new_file.py").write_text("print(missing)\n", encoding="utf-8")

    def fake_run_ruff(root, bin_path, timeout):
        if root == baseline_dir:
            return [_finding("src/legacy_a.py", "F401", "unused import")]
        return [
            _finding("src/modern_a.py", "F401", "unused import"),
            _finding("src/new_file.py", "F821", "undefined name"),
        ]

    monkeypatch.setattr(check_ruff_baseline, "_run_ruff", fake_run_ruff)

    rc = check_ruff_baseline.main(
        argv=["--baseline-dir", str(baseline_dir), "--current-dir", str(current_dir)]
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert "Moved with files (not new): 1" in out
    assert "src/new_file.py" in out
    assert "modern_a.py" not in out
    assert "CI failed: new Ruff debt is not allowed." in out
