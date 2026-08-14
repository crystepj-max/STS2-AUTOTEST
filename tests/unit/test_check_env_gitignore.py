"""环境文件门禁的真实回归检查。"""

import subprocess
from pathlib import Path


def test_env_gitignore_gate_accepts_repository_configuration() -> None:
    """门禁脚本在仓库当前配置下应成功结束并报告模板文件。"""
    repo_root = Path(__file__).parents[2]
    result = subprocess.run(
        ["bash", "scripts/check-env-gitignore.sh"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "跟踪文件 .env.example" in result.stdout
