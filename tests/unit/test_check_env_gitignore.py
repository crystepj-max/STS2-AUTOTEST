"""环境文件门禁的真实回归检查。

背景（2026-08-14 复验定论）：macOS 自带 bash 3.2 在 `LC_CTYPE=C.UTF-8`（macOS 常见默认，
pytest 子进程亦继承）下存在多字节解析缺陷——`$f` 后紧跟全角括号 `（`（UTF-8 首字节 0xEF）
时，bash 3.2 会把 `f\xef` 合并解析为变量名，`set -u` 下报 `unbound variable`，
门禁脚本在第 3 项检查循环中直接失败（旧版实测：`line 42: f�: unbound variable`）。

修复：变量插值显式写 `${f}`，界定变量名边界，消除多字节解析歧义（新版实测通过）。

本测试强制 `LC_CTYPE=C.UTF-8` 运行门禁脚本，确保在旧实现（`$f`）上真实失败、
在新实现（`${f}`）上通过——是能捕获缺陷的回归测试，而非仅验证当前成功输出。
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# 门禁脚本每个外部调用（git）的限时：阻塞时应产生可诊断失败而非无限挂起
GATE_TIMEOUT_SECONDS = 60


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="门禁脚本依赖 bash，当前环境无 bash（如未安装 Git Bash 的 Windows），跳过",
)
def test_env_gitignore_gate_accepts_repository_configuration() -> None:
    """门禁脚本在仓库当前配置下应成功结束并报告模板文件（C.UTF-8 locale）。"""
    repo_root = Path(__file__).parents[2]
    # 强制 macOS 常见 locale：bash 3.2 在该 locale 下对 `$f（` 的多字节解析有缺陷，
    # 旧实现（`$f`）在此环境必然失败，确保回归测试能捕获缺陷回归
    env = {**os.environ, "LC_CTYPE": "C.UTF-8", "LANG": "C.UTF-8"}
    try:
        result = subprocess.run(
            ["bash", "scripts/check-env-gitignore.sh"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=GATE_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"门禁脚本超过 {GATE_TIMEOUT_SECONDS}s 未结束（可能 git 调用阻塞），"
            f"stdout={exc.stdout!r} stderr={exc.stderr!r}"
        )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "跟踪文件 .env.example" in result.stdout
