#!/usr/bin/env python3
"""游戏控制面就绪探针（nightly 回归 Phase 0 用，issue #15 / #65）。

独立脚本：Phase 0 阶段尚未 pip install，故不依赖已安装的包——直接把仓库
src/ 加入 sys.path 后复用 adapters.discovery 的 CLI 三级解析，消除历史上
在 bash 里手抄解析顺序造成的双源漂移。

探测顺序（与历史行为一致）：
  1. discover_sts2_cli() 定位 sts2 可执行文件 → ``sts2 ping``
  2. ping 失败时回退 STS2-Agent HTTP /health（STS2_AGENT_HEALTH_URL 可覆盖）

退出码：0 = 游戏控制面就绪；1 = 不可用（环境必须 BLOCKED）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def resolve_sts2_cli() -> str | None:
    """单源复用 adapters.discovery 的解析顺序（env → PATH → 常见安装路径）。"""
    from sts2_autotest.adapters.discovery import discover_sts2_cli

    return discover_sts2_cli()


def try_sts2_ping(sts2_bin: str, timeout: float) -> bool:
    try:
        completed = subprocess.run(
            [sts2_bin, "ping"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"⚠️ sts2 ping 不可用: {exc}")
        return False
    out = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode == 0 and "CONNECTION_ERROR" not in out.upper():
        print("✅ sts2 ping 成功 (rc=0)")
        return True
    print(f"⚠️ sts2 ping rc={completed.returncode}: {out.strip()[:200]}")
    return False


def try_http_health(timeout: float) -> bool:
    url = os.environ.get("STS2_AGENT_HEALTH_URL", "http://127.0.0.1:8080/health")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read(200).decode("utf-8", errors="replace")
            if 200 <= resp.status < 300:
                print(f"✅ Agent health {url} → {resp.status} {body[:80]!r}")
                return True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"⚠️ Agent health 不可达 ({url}): {exc}")
    return False


def main() -> int:
    timeout = float(os.environ.get("NIGHTLY_ENV_PROBE_TIMEOUT_SECONDS", "10"))

    sts2_bin = resolve_sts2_cli()
    if sts2_bin is None:
        print(
            "❌ 未找到 sts2 CLI（STS2_CLI_PATH / PATH / 常见路径均失败）"
            "— 游戏控制不可用，环境必须 BLOCKED"
        )
        return 1
    try:
        version = subprocess.run(
            [sts2_bin, "--version"], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        version_text = (version.stdout or version.stderr or "").strip() or "version unknown"
    except (OSError, subprocess.TimeoutExpired):
        version_text = "version unknown"
    print(f"✅ sts2 CLI: {sts2_bin} ({version_text})")

    if try_sts2_ping(sts2_bin, timeout):
        return 0
    if try_http_health(timeout):
        return 0
    print("❌ 游戏控制面不可用（sts2 ping 与 Agent /health 均失败）— 环境必须 BLOCKED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
