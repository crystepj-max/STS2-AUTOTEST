#!/usr/bin/env bash
# 夜间回归分类 / 早期证据 / 受控重试契约（issue #15 / #64 / #65）
# 这些是实现内的可重复检查，不是游戏集成测试。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/helpers.sh
source "$SCRIPT_DIR/lib/helpers.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKFLOW="$REPO_ROOT/.github/workflows/ci-nightly.yml"
CLASSIFIER="$REPO_ROOT/.github/scripts/classify_nightly.py"
CHECKER="$REPO_ROOT/.github/scripts/check_nightly_regression.py"
ENV_CHECK="$REPO_ROOT/scripts/nightly-env-check.sh"

pick_python() {
    local candidate
    for candidate in \
        "${PYTHON:-}" \
        "$REPO_ROOT/.venv/bin/python" \
        "$REPO_ROOT/../../.venv/bin/python" \
        "$(command -v python3 || true)"
    do
        if [[ -n "$candidate" && -x "$candidate" ]]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(pick_python)" || PYTHON_BIN=""

test_begin "分类器 self-check 覆盖 BLOCKED/FAILED/CANCELLED/PASSED"
if [[ -z "$PYTHON_BIN" ]]; then
    fail "找不到 python3，无法运行 classify_nightly.py --self-check"
else
    RC=0
    OUT="$("$PYTHON_BIN" "$CLASSIFIER" --self-check 2>&1)" || RC=$?
    RC="${RC:-0}"
    assert_eq "$RC" "0" "self-check 退出码应为 0"
    assert_contains "$OUT" "PASSED" "self-check 应报告 PASSED"
fi

test_begin "分类不得使用 steps.game_tests.conclusion"
if grep -nF 'steps.game_tests.conclusion' "$WORKFLOW"; then
    fail "ci-nightly.yml 仍用 conclusion 分类 game_tests"
else
    pass "未使用 steps.game_tests.conclusion"
fi

test_begin "分类读取 steps.game_tests.outcome"
if grep -nF 'NIGHTLY_STEP_GAME_TESTS: ${{ steps.game_tests.outcome }}' "$WORKFLOW"; then
    pass "game_tests 以 outcome 传入分类器"
else
    fail "未把 steps.game_tests.outcome 传给分类器"
fi

test_begin "结论时间不得是单引号 heredoc 占位"
if grep -nE "<< 'CLASS_EOF'|\\$\\(date -u \\+%FT%TZ\\)" "$WORKFLOW"; then
    fail "仍存在未展开的时间占位写法"
else
    pass "无 CLASS_EOF / %FT%TZ 占位"
fi

test_begin "整体时长上限保持 360 分钟"
if grep -nE 'timeout-minutes: 360' "$WORKFLOW"; then
    pass "timeout-minutes: 360 仍在"
else
    fail "不得改动 PR #54 的整体时长上限"
fi

test_begin "环境步骤存在一次受控重试"
for step_id in checkout_retry env_check_retry setup_python_retry install_retry; do
    if grep -nE "^[[:space:]]+id: ${step_id}$" "$WORKFLOW"; then
        pass "存在 $step_id"
    else
        fail "缺少环境重试步骤 $step_id"
    fi
done

test_begin "功能步骤不得重试"
if grep -nE '^[[:space:]]+id: (game_tests_retry|unit_tests_retry|lint_retry)$' "$WORKFLOW"; then
    fail "发现功能步骤重试 id"
else
    pass "无功能步骤重试"
fi

test_begin "早期失败证据与上传失败可见"
if grep -nE '^[[:space:]]+id: early_diagnosis$' "$WORKFLOW" \
    && grep -nE '^[[:space:]]+id: upload_early$' "$WORKFLOW" \
    && grep -nF 'if-no-files-found: error' "$WORKFLOW"; then
    pass "早期诊断与 if-no-files-found: error 已配置"
else
    fail "缺少早期证据或上传失败门禁"
fi

test_begin "env 探针不得在失败前写 runner_ready=true"
if grep -n 'runner_ready=true' "$ENV_CHECK" | grep -v 'FAIL'; then
    # 允许在 FAIL!=1 的分支写入；禁止在 FAIL 判定之前无条件写入
    :
fi
if grep -B8 'runner_ready=true' "$ENV_CHECK" | grep -q 'FAIL'; then
    pass "runner_ready=true 受 FAIL 判定保护"
else
    fail "runner_ready=true 可能在失败前被写入"
fi

test_begin "工作流契约检查器"
if [[ -z "$PYTHON_BIN" ]]; then
    fail "找不到 python，无法运行 check_nightly_regression.py"
else
    RC=0
    OUT="$("$PYTHON_BIN" "$CHECKER" --workflow "$WORKFLOW" --classifier "$CLASSIFIER" 2>&1)" || RC=$?
    RC="${RC:-0}"
    assert_eq "$RC" "0" "check_nightly_regression.py 退出码应为 0"
    assert_contains "$OUT" "PASSED" "契约检查应 PASSED"
fi

echo
echo "nightly regression 测试完成：$((TEST_COUNT)) 用例，$FAIL_COUNT 失败"
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
