"""CI baseline 脚本共享的带超时子进程执行工具（issue #18）。

为 Ruff / mypy / 单元验证三类外部检查提供独立时间边界：

- ``run_timed`` 在独立进程组中启动子进程，超时后先 SIGTERM、宽限期后 SIGKILL，
  同时把子进程输出增量追加到 ``log_path``，保证超时也保留已有输出。
- 超时时返回 ``TimedResult(timed_out=True, returncode=124)``（与 GNU ``timeout``
  的退出码一致），并向 stderr 输出 ``TIMEOUT: ...`` 行；若在 GitHub Actions
  中运行（环境变量 ``GITHUB_OUTPUT`` 存在），同时写入 ``timeout=true`` 标记，
  供 workflow 的 summary / enforce 步骤区分超时失败与普通质量失败。
- 时间上限可通过 ``<CHECK>_TIMEOUT_SECONDS`` 环境变量覆盖，用于受控超时验证。

进程组语义（Windows 与 POSIX 不同，属尽力而为；CI 主平台为 macOS）：

- POSIX：``start_new_session=True`` 建立新会话/进程组，终止走 ``killpg``，
  可连带清理未独立建组的孙子进程。
- Windows：``CREATE_NEW_PROCESS_GROUP`` 建组，终止优先发送 ``CTRL_BREAK_EVENT``，
  失败则回退 ``terminate()`` / ``kill()``。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, TextIO

TIMEOUT_EXIT_CODE = 124  # 与 GNU timeout 一致；调用方据此判定超时
POLL_INTERVAL = 0.1  # 等待子进程的轮询间隔（秒）
GRACE_PERIOD = 5.0  # SIGTERM 后等待退出的宽限期（秒）


@dataclass(frozen=True)
class TimedResult:
    """一次带超时子进程执行的结果。"""

    returncode: int
    output: str  # 子进程 stdout 全文（超时时为已产生部分）
    error: str  # 子进程 stderr 全文（超时时为已产生部分）
    timed_out: bool


def timeout_from_env(default: float, env_name: str) -> float:
    """读取环境变量时间上限，未设置时返回默认值（受控验证用）。"""
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    return float(raw)


def _capture_lines(
    stream: TextIO,
    log_path: Path,
    sink: list[str],
    echo: bool,
    echo_stream: TextIO,
) -> None:
    """读取子进程一路输出：追加日志、收集到 sink，可选回显到父进程控制台。"""
    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        for line in stream:
            log.write(line)
            log.flush()
            sink.append(line)
            if echo:
                echo_stream.write(line)
                echo_stream.flush()


def _terminate_tree(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        except (OSError, NotImplementedError):
            proc.terminate()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            proc.terminate()


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        proc.kill()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()


def run_timed(
    name: str,
    cmd: Sequence[str],
    log_path: str | Path,
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    echo: bool = False,
) -> TimedResult:
    """在新进程组中运行 ``cmd``，超时后终止整个进程组并保留已有输出。

    - 正常结束：``returncode`` 为子进程退出码，``timed_out=False``。
    - 超时：先 SIGTERM 整个进程组，宽限期后 SIGKILL；``timed_out=True``、
      ``returncode=124``；向 stderr 输出 ``TIMEOUT: ...``；若 ``GITHUB_OUTPUT``
      环境变量存在则追加 ``timeout=true``（供 GitHub Actions 步骤读取）。
    - 命令不存在（``FileNotFoundError``）直接向上抛出，由调用方按基础设施
      错误处理。
    - 输出实时追加到 ``log_path``；``echo=True`` 时同步回显到父进程控制台
      （pytest 场景保持与直接继承控制台一致的观感）。
    - ``cwd`` 指定子进程工作目录（默认继承调用方），用于对 baseline checkout
      运行检查。
    """
    log = Path(log_path)
    log.parent.mkdir(parents=True, exist_ok=True)

    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        env=None if env is None else dict(env),
        cwd=None if cwd is None else Path(cwd),
        start_new_session=(sys.platform != "win32"),
        creationflags=creationflags,
    )
    assert proc.stdout is not None and proc.stderr is not None

    with log.open("a", encoding="utf-8", errors="replace") as log_file:
        log_file.write(f"===== {name} start =====\n")

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    stdout_thread = threading.Thread(
        target=_capture_lines,
        args=(proc.stdout, log, stdout_lines, echo, sys.stdout),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_capture_lines,
        args=(proc.stderr, log, stderr_lines, echo, sys.stderr),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL)
        timed_out = proc.poll() is None
        if timed_out:
            _terminate_tree(proc)
            try:
                proc.wait(timeout=GRACE_PERIOD)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                try:
                    proc.wait(timeout=GRACE_PERIOD)
                except subprocess.TimeoutExpired:
                    pass  # 进程组内存在忽略信号的顽固进程时，至少已尽力
    except KeyboardInterrupt:
        # 交互中断也要先清理子进程，避免残留后再向上传播
        if proc.poll() is None:
            _kill_tree(proc)
        raise
    finally:
        stdout_thread.join(timeout=GRACE_PERIOD)
        stderr_thread.join(timeout=GRACE_PERIOD)

    output = "".join(stdout_lines)
    error = "".join(stderr_lines)
    returncode = proc.returncode if proc.returncode is not None else -1

    if timed_out:
        message = f"TIMEOUT: {name} exceeded {timeout:.0f}s"
        print(message, file=sys.stderr, flush=True)
        with log.open("a", encoding="utf-8", errors="replace") as log_file:
            log_file.write(f"{message}\n")
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as marker:
                marker.write("timeout=true\n")
        return TimedResult(returncode=TIMEOUT_EXIT_CODE, output=output, error=error, timed_out=True)

    return TimedResult(returncode=returncode, output=output, error=error, timed_out=False)
