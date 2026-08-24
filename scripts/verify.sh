#!/usr/bin/env bash
# verify.sh — STS2-AUTOTEST 本地全量验证（issue-24 运行保障任务收口）
#
# 硬门槛（必须全绿）：
#   1. workflow artifact 步骤顺序（issue #61；与 CI check_workflow_artifact_order.py 同语义）
#   2. scripts/ 下 shell 脚本测试（runner-ctl / setup-mac-runner / runner-probe / check-runner-health）
#   3. 单元测试 tests/unit/
#   4. lint-imports（导入层级隔离）
#
# 增量门禁（提供 BASELINE_DIR 时执行；缺省跳过，由 CI 兜底）：
#   5. ruff 无新增债务（与 CI check_ruff_baseline.py 同语义）
#   6. mypy 无新增债务（与 CI check_mypy_baseline.py 同语义）
#   —— 仓库存在既有 Ruff/mypy 债务（归属 Issue #25），全量零错误不是当前基线
#
# 用法：./scripts/verify.sh [BASELINE_DIR=<main 分支 checkout 路径>]
# 环境变量：PYTHON 覆盖解释器（默认 .venv/bin/python）、VERIFY_STEP_TIMEOUT
#          每步超时秒数（默认：shell 测试 600 / 单元测试 1800 / lint-imports 300 /
#          workflow 顺序校验 60）
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
BASELINE_DIR="${BASELINE_DIR:-}"
FAILED=0

# 带超时执行命令：pytest 等可能长时间挂起，逐项限时；killer 不持有调用方管道
run_with_timeout() {
    local timeout="$1"
    shift
    local pid rc killer
    "$@" &
    pid=$!
    ( sleep "$timeout"; pkill -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true ) >/dev/null 2>&1 &
    killer=$!
    if wait "$pid"; then rc=0; else rc=$?; fi
    pkill -P "$killer" 2>/dev/null || true
    kill "$killer" 2>/dev/null || true
    return "$rc"
}

step() {
    local name="$1" timeout="$2"
    shift 2
    echo "===== $name ====="
    if run_with_timeout "$timeout" "$@"; then
        echo "PASS: $name"
    else
        echo "FAIL: $name"
        FAILED=1
    fi
    echo
}

cd "$REPO_ROOT"

step "workflow artifact 步骤顺序" "${VERIFY_STEP_TIMEOUT:-60}" \
    "$PYTHON" .github/scripts/check_workflow_artifact_order.py
step "shell 脚本测试" "${VERIFY_STEP_TIMEOUT:-600}" bash scripts/tests/run-all.sh
step "单元测试" "${VERIFY_STEP_TIMEOUT:-1800}" "$PYTHON" -m pytest tests/unit/ -q
step "lint-imports" "${VERIFY_STEP_TIMEOUT:-300}" "$REPO_ROOT/.venv/bin/lint-imports"

if [[ -n "$BASELINE_DIR" ]]; then
    # 基线脚本从 PATH 解析 mypy/ruff，须注入 .venv 保证基线环境一致
    # （否则 worktree 无 venv 时会解析到 homebrew mypy，错误集合失真）
    export PATH="$REPO_ROOT/.venv/bin:$PATH"
    step "ruff 无新增债务" "${VERIFY_STEP_TIMEOUT:-300}" "$PYTHON" .github/scripts/check_ruff_baseline.py \
        --baseline-dir "$BASELINE_DIR" --current-dir "$REPO_ROOT"
    step "mypy 无新增债务" "${VERIFY_STEP_TIMEOUT:-300}" "$PYTHON" .github/scripts/check_mypy_baseline.py \
        --baseline-dir "$BASELINE_DIR" --current-dir "$REPO_ROOT"
else
    echo "===== ruff / mypy 增量基线 ====="
    echo "跳过（未提供 BASELINE_DIR）；由 CI 门禁覆盖，本地可用："
    echo "  git worktree add /tmp/ci-baseline main"
    echo "  BASELINE_DIR=/tmp/ci-baseline ./scripts/verify.sh"
    echo
fi

if [[ "$FAILED" -eq 0 ]]; then
    echo "verify.sh 全部通过 ✓"
    exit 0
else
    echo "verify.sh 存在失败项 ✗"
    exit 1
fi
