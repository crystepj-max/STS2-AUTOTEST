"""CI 共享超时执行工具（.github/scripts/runner_utils.py）的单元测试（issue #18）。"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import psutil
import pytest

# 脚本模块加载方式：spec_from_file_location 加载 .github/scripts/runner_utils.py。
# sys.modules 中该名字的常驻条目是刻意的——脚本与 src/ 命名空间隔离，仓库内
# 无同名模块；若未来新增同名模块需改为 fixture 作用域化加载。
_SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/runner_utils.py"
_SPEC = importlib.util.spec_from_file_location("runner_utils", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
runner_utils = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runner_utils
_SPEC.loader.exec_module(runner_utils)

_SLEEPER = "import time; time.sleep(300)"
_PRINT_THEN_SLEEP = "import sys, time; print('started', flush=True); time.sleep(300)"


def _write_pid_then_sleep(marker: Path) -> str:
    return f"import os; open({str(marker)!r}, 'w').write(str(os.getpid())); import time; time.sleep(300)"


def test_run_timed_returns_output_and_exit_code_for_fast_command(tmp_path: Path) -> None:
    log = tmp_path / "check.log"
    result = runner_utils.run_timed(
        "demo",
        [sys.executable, "-c", "print('hello from child')"],
        log,
        timeout=10,
    )

    assert result.timed_out is False
    assert result.returncode == 0
    assert result.output == "hello from child\n"
    assert result.error == ""
    assert "hello from child" in log.read_text(encoding="utf-8")
    assert "===== demo start =====" in log.read_text(encoding="utf-8")


def test_run_timed_marks_timeout_and_preserves_partial_output(tmp_path: Path) -> None:
    log = tmp_path / "check.log"
    result = runner_utils.run_timed(
        "demo",
        [sys.executable, "-c", _PRINT_THEN_SLEEP],
        log,
        timeout=1,
    )

    assert result.timed_out is True
    assert result.returncode == runner_utils.TIMEOUT_EXIT_CODE == 124
    assert "started" in result.output
    log_text = log.read_text(encoding="utf-8")
    assert "started" in log_text
    assert "TIMEOUT: demo exceeded 1s" in log_text


def test_run_timed_leaves_no_residual_process_after_timeout(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    log = tmp_path / "check.log"
    result = runner_utils.run_timed(
        "demo",
        [sys.executable, "-c", _write_pid_then_sleep(pid_file)],
        log,
        timeout=1,
    )

    assert result.timed_out is True
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(child_pid), f"child {child_pid} still alive after timeout"


@pytest.mark.skipif(sys.platform == "win32", reason="进程组清理行为在 POSIX 上验证")
def test_run_timed_terminates_grandchild_process_group_on_posix(tmp_path: Path) -> None:
    parent_pid = tmp_path / "parent.pid"
    child_pid = tmp_path / "child.pid"
    grandchild_code = _write_pid_then_sleep(child_pid)
    parent_code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild_code!r}]); "
        f"open({str(parent_pid)!r}, 'w').write(str(__import__('os').getpid())); "
        "import time; time.sleep(300)"
    )
    log = tmp_path / "check.log"
    result = runner_utils.run_timed(
        "demo",
        [sys.executable, "-c", parent_code],
        log,
        timeout=1,
    )

    assert result.timed_out is True
    for pid_file in (parent_pid, child_pid):
        pid = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 10
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not psutil.pid_exists(pid), f"process {pid} still alive after timeout"


def test_run_timed_raises_file_not_found_for_missing_command(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        runner_utils.run_timed(
            "demo",
            ["no-such-binary-issue18"],
            tmp_path / "check.log",
            timeout=5,
        )


def test_run_timed_separates_stdout_and_stderr(tmp_path: Path) -> None:
    code = "import sys; print('out-line', flush=True); print('err-line', file=sys.stderr, flush=True)"
    result = runner_utils.run_timed(
        "demo",
        [sys.executable, "-c", code],
        tmp_path / "check.log",
        timeout=10,
    )

    assert result.timed_out is False
    assert "out-line" in result.output
    assert "err-line" in result.error
    assert "err-line" not in result.output


def test_run_timed_writes_github_output_marker_and_stderr_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(marker))
    result = runner_utils.run_timed(
        "demo",
        [sys.executable, "-c", _PRINT_THEN_SLEEP],
        tmp_path / "check.log",
        timeout=1,
    )

    assert result.timed_out is True
    assert marker.read_text(encoding="utf-8") == "timeout=true\n"
    assert "TIMEOUT: demo exceeded 1s" in capsys.readouterr().err


def test_run_timed_echo_streams_child_output_to_console(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = "import sys; print('echo-out', flush=True); print('echo-err', file=sys.stderr, flush=True)"
    result = runner_utils.run_timed(
        "demo",
        [sys.executable, "-c", code],
        tmp_path / "check.log",
        timeout=10,
        echo=True,
    )

    assert result.timed_out is False
    captured = capsys.readouterr()
    assert "echo-out" in captured.out
    assert "echo-err" in captured.err


class _InterruptAfterPoll:
    """代理真实 ``time`` 模块，前 N 次 sleep 正常，之后抛 KeyboardInterrupt。

    用于模拟交互中断发生在子进程运行期间（子进程已写入 pid 文件之后）。
    """

    def __init__(self, after: int) -> None:
        self._remaining = after

    def __getattr__(self, name: str) -> object:
        return getattr(time, name)

    def sleep(self, seconds: float) -> None:
        if self._remaining > 0:
            self._remaining -= 1
            time.sleep(seconds)
        else:
            raise KeyboardInterrupt


def test_run_timed_cleans_up_and_rethrows_on_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "child.pid"
    log = tmp_path / "check.log"

    # spy：断言 _kill_tree 被调用，同时保持真实清理（测试不留残留进程）
    real_kill_tree = runner_utils._kill_tree
    kill_calls: list[object] = []

    def spy_kill_tree(proc: object) -> None:
        kill_calls.append(proc)
        real_kill_tree(proc)  # type: ignore[arg-type]

    monkeypatch.setattr(runner_utils, "_kill_tree", spy_kill_tree)
    # 轮询约 1s（10 次 POLL_INTERVAL）后抛 KeyboardInterrupt，此时子进程已写入
    # pid 文件仍在运行——中断路径必须先清理子进程，再向上传播异常
    monkeypatch.setattr(runner_utils, "time", _InterruptAfterPoll(10))

    with pytest.raises(KeyboardInterrupt):
        runner_utils.run_timed(
            "demo",
            [sys.executable, "-c", _write_pid_then_sleep(pid_file)],
            log,
            timeout=10,
        )

    assert len(kill_calls) == 1
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(child_pid), f"child {child_pid} still alive after interrupt"


def test_env_timeout_uses_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISSUE18_DEMO_TIMEOUT", raising=False)
    assert runner_utils.timeout_from_env(600.0, "ISSUE18_DEMO_TIMEOUT") == 600.0

    monkeypatch.setenv("ISSUE18_DEMO_TIMEOUT", "12.5")
    assert runner_utils.timeout_from_env(600.0, "ISSUE18_DEMO_TIMEOUT") == 12.5
