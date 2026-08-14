"""环境文件门禁的真实回归检查。

背景（2026-08-14 复验定论）：macOS 自带 bash 3.2 在 UTF-8 locale（`C.UTF-8`，
macOS 常见默认，pytest 子进程亦继承）下存在多字节解析缺陷——`$f` 后紧跟全角
括号 `（`（UTF-8 首字节 0xEF）时，bash 3.2 会把 `f\xef` 合并解析为变量名，
`set -u` 下报 `unbound variable`，门禁脚本在第 3 项检查循环中直接失败
（旧版实测：`line 42: f�: unbound variable`）。

修复：变量插值显式写 `${f}`，界定变量名边界，消除多字节解析歧义（新版实测通过）。

本测试强制 UTF-8 locale 运行门禁脚本，确保在旧实现（`$f`）上真实失败、
在新实现（`${f}`）上通过——是能捕获缺陷的回归测试，而非仅验证当前成功输出。
"""

import os
import shutil
import subprocess
from pathlib import Path

import psutil
import pytest

# 门禁脚本每个外部调用（git）的限时：阻塞时应产生可诊断失败而非无限挂起
GATE_TIMEOUT_SECONDS = 60


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="门禁脚本依赖 bash，当前环境无 bash（如未安装 Git Bash 的 Windows），跳过",
)
def test_env_gitignore_gate_accepts_repository_configuration() -> None:
    """门禁脚本在仓库当前配置下应成功结束并报告模板文件（强制 UTF-8 locale）。"""
    repo_root = Path(__file__).parents[2]
    # 强制 UTF-8 locale：bash 3.2 在该 locale 下对 `$f（` 的多字节解析有缺陷，
    # 旧实现（`$f`）在此环境必然失败，确保回归测试能捕获缺陷回归。
    # LC_ALL 优先级最高，必须一并移除，否则会覆盖 LC_CTYPE/LANG。
    env = {k: v for k, v in os.environ.items() if k != "LC_ALL"}
    env["LC_CTYPE"] = "C.UTF-8"
    env["LANG"] = "C.UTF-8"

    proc = psutil.Popen(
        ["bash", "scripts/check-env-gitignore.sh"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=GATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        # 终止整棵子进程树：timeout 只杀 bash，其后代 git 仍可能持有管道，
        # 须一并清理避免超时清理路径继续等待与遗留进程（AGENTS.md 防僵尸约束）。
        # 注意：psutil.Popen.communicate 委托 subprocess，抛的是
        # subprocess.TimeoutExpired（实测确认），不是 psutil.TimeoutExpired。
        # 逐进程容错：后代可能在 children() 快照后自行退出，kill 抛
        # NoSuchProcess 时忽略；finally 保证父进程终止与回收必定执行。
        try:
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
        finally:
            proc.kill()
            proc.wait(timeout=5)
        pytest.fail(
            f"门禁脚本超过 {GATE_TIMEOUT_SECONDS}s 未结束（可能 git 调用阻塞）"
        )

    # Windows 上父进程代码页可能非 UTF-8，脚本输出为 UTF-8 中文字节：
    # 显式按 UTF-8 解码（errors=replace 兜底非 UTF-8 字节，不掩盖失败原因）
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    assert proc.returncode == 0, stdout + stderr
    assert "跟踪文件 .env.example" in stdout
