#!/usr/bin/env bash
# 夜间回归 Phase 0 环境就绪探针（issue #15 / #65）。
# 退出码：0=就绪 1=未就绪。
# 禁止在失败前写入 runner_ready=true，避免分类脚本把环境失败误当成就绪。
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
else
    echo "✅ pip available"
fi

if command -v sts2 >/dev/null 2>&1; then
    echo "✅ sts2 CLI: $(sts2 --version 2>&1 || echo 'version unknown')"
else
    echo "⚠️ sts2 CLI not in PATH (game tests will be skipped)"
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
