#!/usr/bin/env bash
# 夜间回归 Phase 0 环境就绪探针（issue #15 / #65）。
# 退出码：0=就绪 1=未就绪。
# 禁止在失败前写入 runner_ready=true，避免分类脚本把环境失败误当成就绪。
# 缺少游戏控制 CLI / 控制面时必须 FAIL（不得仅警告后继续显示"环境就绪"）。
#
# CLI 解析与游戏控制面探测统一收敛到 scripts/probe_game_control.py——它以
# sys.path 直指 src/ 的方式单源复用 adapters.discovery.discover_sts2_cli
# （Phase 0 尚未 pip install，但包源码已在 checkout 内，轻依赖可直接 import），
# 历史上在本文件中手抄解析顺序的双源实现已删除。
set -euo pipefail

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
fi

if [[ "$FAIL" -eq 0 ]]; then
    if ! python3 "$(dirname "$0")/probe_game_control.py"; then
        FAIL=1
    fi
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
