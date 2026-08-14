"""环境文件门禁的真实回归检查。

背景（2026-08-14 复验定论）：macOS 自带 bash 3.2 在 UTF-8 locale（`C.UTF-8`，
macOS 常见默认，pytest 子进程亦继承）下存在多字节解析缺陷——`$f` 后紧跟全角
括号 `（`（UTF-8 首字节 0xEF）时，bash 3.2 会把 `f\xef` 合并解析为变量名，
`set -u` 下报 `unbound variable`，门禁脚本在第 3 项检查循环中直接失败
（旧版实测：`line 42: f�: unbound variable`）。

修复：变量插值显式写 `${f}`，界定变量名边界，消除多字节解析歧义（新版实测通过）。

本测试强制 UTF-8 locale 运行门禁脚本，确保在旧实现（`$f`）上真实失败、
在新实现（`${f}`）上通过——是能捕获缺陷的回归测试，而非仅验证当前成功输出。

脚本自身限时（S4 复审要求，2026-08-14）：门禁脚本对每个外部调用（git）自备
限时机制（macOS/Linux/Git Bash 均自带的 perl `alarm`），不依赖测试外层的整段
超时。负例测试用「卡死的假 git」验证：直接运行脚本也会在限定时间内失败退出，
且不遗留进程。
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
        # 全程容错：bash 可能在超时时已自行退出，children()/kill()/wait() 均可能
        # 抛 psutil.NoSuchProcess——逐一捕获，保证后代清理与 pytest.fail 必定执行。
        try:
            try:
                children = proc.children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
        finally:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
            try:
                proc.wait(timeout=5)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                # 回收等待超时（进程未在 5s 内退出）也吞掉——回收尽力而为，
                # 诊断失败 pytest.fail 必须照常执行，不允许清理异常掩盖根因
                pass
        pytest.fail(
            f"门禁脚本超过 {GATE_TIMEOUT_SECONDS}s 未结束（可能 git 调用阻塞）"
        )

    # Windows 上父进程代码页可能非 UTF-8，脚本输出为 UTF-8 中文字节：
    # 显式按 UTF-8 解码（errors=replace 兜底非 UTF-8 字节，不掩盖失败原因）
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    assert proc.returncode == 0, stdout + stderr
    assert "跟踪文件 .env.example" in stdout


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="门禁脚本依赖 bash，当前环境无 bash（如未安装 Git Bash 的 Windows），跳过",
)
def test_git_error_not_treated_as_untracked(tmp_path: Path) -> None:
    """git 返回 128（仓库损坏/I/O 等执行错误）时不得判定为「未跟踪」并输出 PASS。

    背景（bot 复审发现）：`git ls-files --error-unmatch .env` 的宽泛 else 曾把
    128 等错误码当成「未跟踪」放行。只允许明确的退出码 1 表示未跟踪。
    """
    repo_root = Path(__file__).parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/usr/bin/env bash\nexit 128\n")
    fake_git.chmod(0o755)

    env = {k: v for k, v in os.environ.items() if k != "LC_ALL"}
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")

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
        try:
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
        finally:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
            try:
                proc.wait(timeout=5)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                pass
        pytest.fail("门禁脚本超过 60s 未结束")

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    assert proc.returncode != 0, "git 执行错误（128）时门禁脚本应失败：" + stdout + stderr
    assert "PASS: .env 未被 git 跟踪" not in stdout, "执行错误不得判定为未跟踪：" + stdout
    assert "退出码 128" in stdout, "应输出退出码诊断：" + stdout


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="门禁脚本依赖 bash，当前环境无 bash（如未安装 Git Bash 的 Windows），跳过",
)
def test_script_self_limits_when_git_stuck(tmp_path: Path) -> None:
    """git 卡死时脚本须自行限时失败退出，且不遗留进程（不依赖测试外层的整段超时）。

    背景（S4 复审发现）：测试外层 60s 超时只保护通过测试启动的路径，
    开发手册中的直接运行（`bash scripts/check-env-gitignore.sh`）仍可能无限挂起，
    违反 AGENTS.md「所有外部调用必须有 timeout」。门禁脚本对每个外部调用（git）
    自备限时（perl alarm，macOS/Linux/Git Bash 均自带）。

    负例构造：PATH 前置「卡死的假 git」（`exec sleep 600`——exec 使进程树单节点，
    超时终止后必然无后代残留），并把脚本限时调小到 2s。期望：脚本在约 3×2s 内
    失败退出（退出码非 0），且无 sleep 进程遗留。
    """

    repo_root = Path(__file__).parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/usr/bin/env bash\nexec sleep 600\n")
    fake_git.chmod(0o755)

    env = {k: v for k, v in os.environ.items() if k != "LC_ALL"}
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["CHECK_ENV_GITIGNORE_CMD_TIMEOUT"] = "2"

    proc = psutil.Popen(
        ["bash", "scripts/check-env-gitignore.sh"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            children = proc.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
        try:
            proc.wait(timeout=5)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
            pass
        pytest.fail("门禁脚本在 git 卡死时未在限定时间内退出（脚本本体缺少限时）")

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    assert proc.returncode != 0, "git 卡死时门禁脚本应失败退出：" + stdout + stderr
    assert "TIMEOUT" in stdout + stderr, "失败输出应含 TIMEOUT 诊断信息：" + stdout + stderr

    sleepers = [
        p
        for p in psutil.process_iter(["cmdline"])
        if " ".join(p.info.get("cmdline") or []) == "sleep 600"
    ]
    assert not sleepers, f"门禁脚本超时终止后遗留进程: {sleepers}"
