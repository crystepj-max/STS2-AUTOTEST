#!/usr/bin/env bash
# 夜间回归 Phase 0 环境就绪探针（issue #15 / #65）。
# 退出码：0=就绪 1=未就绪。
# 禁止在失败前写入 runner_ready=true，避免分类脚本把环境失败误当成就绪。
# 缺少游戏控制 CLI / 控制面时必须 FAIL（不得仅警告后继续显示“环境就绪”）。
#
# CLI 解析顺序与 adapters.discovery.discover_sts2_cli 对齐（本阶段尚未 pip install，
# 不能 import 包，故在此复刻轻量逻辑）：
#   1) STS2_CLI_PATH  2) PATH 中的 sts2  3) 常见安装路径
set -euo pipefail

PROBE_TIMEOUT_SECONDS="${NIGHTLY_ENV_PROBE_TIMEOUT_SECONDS:-10}"

resolve_sts2_cli() {
    local candidate
    if [[ -n "${STS2_CLI_PATH:-}" ]]; then
        if [[ -f "$STS2_CLI_PATH" && -x "$STS2_CLI_PATH" ]]; then
            printf '%s\n' "$STS2_CLI_PATH"
            return 0
        fi
        echo "⚠️ STS2_CLI_PATH 已设置但不可执行: $STS2_CLI_PATH" >&2
    fi
    if command -v sts2 >/dev/null 2>&1; then
        command -v sts2
        return 0
    fi
    for candidate in \
        "${HOME}/.local/bin/sts2" \
        "${HOME}/.sts2-cli-mod/sts2" \
        "${HOME}/Library/Application Support/Steam/steamapps/common/Slay the Spire 2/sts2" \
        "/usr/local/bin/sts2"
    do
        if [[ -f "$candidate" && -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

echo "::group::Environment readiness probe"
FAIL=0

if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ python3 not found in PATH"
    FAIL=1
else
    echo "✅ Python: $(python3 --version 2>&1)"
fi

if ! command -v pip >/dev/null 2>&1 && ! command -v pip3 >/dev/null 2>&1; then
    echo "❌ pip not found"
    FAIL=1
else
    echo "✅ pip available"
fi

STS2_BIN=""
if STS2_BIN="$(resolve_sts2_cli)"; then
    echo "✅ sts2 CLI: $STS2_BIN ($("$STS2_BIN" --version 2>&1 || echo 'version unknown'))"
    export STS2_BIN
    # 带超时探测游戏控制面：sts2 ping 优先，失败再试 Agent HTTP /health。
    if ! PROBE_TIMEOUT_SECONDS="$PROBE_TIMEOUT_SECONDS" STS2_BIN="$STS2_BIN" python3 - <<'PY'
import os
import subprocess
import sys
import urllib.error
import urllib.request

timeout = float(os.environ.get("PROBE_TIMEOUT_SECONDS", "10"))
sts2_bin = os.environ["STS2_BIN"]


def ok(msg: str) -> None:
    print(f"✅ {msg}")
    raise SystemExit(0)


def try_sts2_ping() -> bool:
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
        ok(f"sts2 ping 成功 (rc=0)")
    print(f"⚠️ sts2 ping rc={completed.returncode}: {out.strip()[:200]}")
    return False


def try_http_health() -> bool:
    url = os.environ.get("STS2_AGENT_HEALTH_URL", "http://127.0.0.1:8080/health")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read(200).decode("utf-8", errors="replace")
            if 200 <= resp.status < 300:
                ok(f"Agent health {url} → {resp.status} {body[:80]!r}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"⚠️ Agent health 不可达 ({url}): {exc}")
    return False


try_sts2_ping()
try_http_health()
print("❌ 游戏控制面不可用（sts2 ping 与 Agent /health 均失败）— 环境必须 BLOCKED")
raise SystemExit(1)
PY
    then
        FAIL=1
    fi
else
    echo "❌ 未找到 sts2 CLI（STS2_CLI_PATH / PATH / 常见路径均失败）— 游戏控制不可用，环境必须 BLOCKED"
    FAIL=1
fi

FREE_GB="$(df -g . 2>/dev/null | awk 'NR==2{print $4}' || df -h . | awk 'NR==2{print $4}' | sed 's/G//')"
echo "💾 Disk free: ${FREE_GB}"

echo "::endgroup::"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    if [[ "$FAIL" -eq 1 ]]; then
        echo "runner_ready=false" >> "$GITHUB_OUTPUT"
        echo "blocked_reason=environment_not_ready" >> "$GITHUB_OUTPUT"
    else
        echo "runner_ready=true" >> "$GITHUB_OUTPUT"
    fi
fi

if [[ "$FAIL" -eq 1 ]]; then
    echo "::error::Environment readiness check FAILED — marking run as BLOCKED"
    exit 1
fi

echo "✅ Environment readiness check passed"
exit 0
