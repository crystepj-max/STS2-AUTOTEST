#!/usr/bin/env bash
# check-runner-health.sh 行为测试（issue-24 T4）：
# 退出码契约：0=HEALTHY（可接收任务） 1=UNHEALTHY（不可用） 2=检查本身错误
# 判定：服务状态 running + 至少一条 GitHub 网络链路可达
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/helpers.sh
source "$SCRIPT_DIR/lib/helpers.sh"

HEALTH_SCRIPT="$SCRIPT_DIR/../check-runner-health.sh"

# fake curl：返回指定的 http_code
new_health_bin() {
    local code="$1"
    local dir
    dir="$(mktemp -d "${TMPDIR:-/tmp}/health-bin.XXXXXX")"
    cat > "$dir/curl" <<FAKE_CURL
#!/usr/bin/env bash
# fake curl：-\$http_code 输出 $code
for arg in "\$@"; do
    if [[ "\$arg" == *"ipify.org"* ]]; then
        echo "203.0.113.7"
        exit 0
    fi
done
printf '%s' "$code"
echo
exit 0
FAKE_CURL
    chmod +x "$dir/curl"
    # fake ps：默认存在 Runner.Listener 进程（R3 真实进程检查）
    # 健康检查用 ps -eo args 检测进程（CI 环境 pgrep 实测不可靠），测试须隔离 ps
    cat > "$dir/ps" <<'FAKE_PS'
#!/usr/bin/env bash
if [[ "$*" == *"-eo"* || "$*" == *"args"* ]]; then
    printf '%s\n' \
        '  1 1 /sbin/launchd' \
        '40231 80357 /Users/chris/actions-runner/bin/Runner.Listener run --startuptype service' \
        '40235 40231 /Users/chris/actions-runner/bin/Runner.Worker'
else
    /bin/ps "$@"
fi
exit 0
FAKE_PS
    chmod +x "$dir/ps"
    # fake gh：默认 GitHub 侧 online（R3 GitHub 侧状态检查）
    cat > "$dir/gh" <<'FAKE_GH'
#!/usr/bin/env bash
if [[ "$*" == *"--jq"* ]]; then
    printf 'online\n'
fi
exit 0
FAKE_GH
    chmod +x "$dir/gh"
    echo "$dir"
}

# --- 用例 1：running + 网络可达 → HEALTHY (0) ---
test_begin "health: running + 可达 → exit 0 HEALTHY"
FAKE="$(new_fake_runner running)"
BIN="$(new_health_bin 200)"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$BIN:/usr/bin:/bin" bash "$HEALTH_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "健康时退出码应为 0"
assert_contains "$OUT" "HEALTHY" "输出应包含 HEALTHY"

# --- 用例 2：stopped → UNHEALTHY (1) ---
test_begin "health: stopped → exit 1 UNHEALTHY"
FAKE="$(new_fake_runner stopped)"
BIN="$(new_health_bin 200)"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$BIN:/usr/bin:/bin" bash "$HEALTH_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "1" "服务停止时退出码应为 1"
assert_contains "$OUT" "UNHEALTHY" "输出应包含 UNHEALTHY"
assert_contains "$OUT" "service" "应给出 service 相关原因"

# --- 用例 3：running 但网络全不可达 → UNHEALTHY (1) ---
test_begin "health: running + 网络不可达 → exit 1 UNHEALTHY"
FAKE="$(new_fake_runner running)"
BIN="$(new_health_bin 000)"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$BIN:/usr/bin:/bin" bash "$HEALTH_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "1" "网络不可达时退出码应为 1"
assert_contains "$OUT" "UNHEALTHY" "输出应包含 UNHEALTHY"
assert_contains "$OUT" "network" "应给出 network 相关原因"

# --- 用例 4：not-installed → UNHEALTHY (1) ---
test_begin "health: 未安装 → exit 1 UNHEALTHY"
EMPTY="$(mktemp -d "${TMPDIR:-/tmp}/health-empty.XXXXXX")"
BIN="$(new_health_bin 200)"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$EMPTY" PATH="$BIN:/usr/bin:/bin" bash "$HEALTH_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "1" "未安装时退出码应为 1"
assert_contains "$OUT" "UNHEALTHY" "输出应包含 UNHEALTHY"

# --- 用例 5：--json 输出合法 JSON（含 healthy 布尔）---
test_begin "health: --json 输出合法 JSON"
FAKE="$(new_fake_runner running)"
BIN="$(new_health_bin 200)"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$BIN:/usr/bin:/bin" bash "$HEALTH_SCRIPT" --json 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "json 模式退出码应为 0"
if echo "$OUT" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d.get("healthy") is True' 2>/dev/null; then
    pass "JSON 合法且 healthy=true"
else
    fail "JSON 不合法或 healthy 不为 true：$OUT"
fi

# --- 用例 6（R3 反例）：服务标记 started 但真实进程缺失 → UNHEALTHY ---
test_begin "health: 服务 started 但 Runner.Listener 进程缺失 → exit 1 UNHEALTHY"
FAKE="$(new_fake_runner running)"
BIN="$(new_health_bin 200)"
cat > "$BIN/ps" <<'FAKE_PS_NONE'
#!/usr/bin/env bash
# 无 Runner.Listener 进程（ps 输出不含 Listener）
if [[ "$*" == *"-eo"* || "$*" == *"args"* ]]; then
    printf '%s\n' '  1 1 /sbin/launchd'
else
    /bin/ps "$@"
fi
exit 0
FAKE_PS_NONE
chmod +x "$BIN/ps"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$BIN:/usr/bin:/bin" bash "$HEALTH_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "1" "服务标记启动但进程缺失时退出码应为 1"
assert_contains "$OUT" "UNHEALTHY" "输出应包含 UNHEALTHY"
assert_contains "$OUT" "process" "应给出 process 相关原因"

# --- 用例 7（R3 反例）：服务与进程都正常但 GitHub 侧 offline → UNHEALTHY ---
test_begin "health: 服务+进程正常但 GitHub 侧 offline → exit 1 UNHEALTHY"
FAKE="$(new_fake_runner running)"
BIN="$(new_health_bin 200)"
cat > "$BIN/gh" <<'FAKE_GH_OFFLINE'
#!/usr/bin/env bash
if [[ "$*" == *"--jq"* ]]; then
    printf 'offline\n'
fi
exit 0
FAKE_GH_OFFLINE
chmod +x "$BIN/gh"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$BIN:/usr/bin:/bin" bash "$HEALTH_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "1" "GitHub 侧 offline 时退出码应为 1"
assert_contains "$OUT" "UNHEALTHY" "输出应包含 UNHEALTHY"
assert_contains "$OUT" "github" "应给出 github 相关原因"

# --- 用例 8（CI 实证修正）：gh 查询失败（unknown）不判死，服务+进程+网络正常仍 HEALTHY ---
test_begin "health: gh 查询失败(unknown)降级不判死 → exit 0 HEALTHY"
FAKE="$(new_fake_runner running)"
BIN="$(new_health_bin 200)"
cat > "$BIN/gh" <<'FAKE_GH_UNKNOWN'
#!/usr/bin/env bash
# gh 查询失败（如 token 刷新走代理超时）→ 无输出、非 0
exit 1
FAKE_GH_UNKNOWN
chmod +x "$BIN/gh"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$BIN:/usr/bin:/bin" bash "$HEALTH_SCRIPT" 2>/dev/null)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "gh 查询失败降级：服务+进程+网络正常应 HEALTHY(0)"
assert_contains "$OUT" "HEALTHY" "输出应包含 HEALTHY"

# --- 用例 9（S1 反例）：svc.sh status 挂起 → 限时退出而非无期等待 ---
test_begin "health: svc.sh status 挂起 → 限时退出"
FAKE="$(new_fake_runner running)"
BIN="$(new_health_bin 200)"
# fake svc.sh：status 挂起（永不返回）
cat > "$FAKE/svc.sh" <<'FAKE_SVC_HANG'
#!/usr/bin/env bash
while true; do sleep 1; done
FAKE_SVC_HANG
chmod +x "$FAKE/svc.sh"
START="$(date +%s)"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" HEALTH_CMD_TIMEOUT=2 PATH="$BIN:/usr/bin:/bin" bash "$HEALTH_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
ELAPSED="$(( $(date +%s) - START ))"
if [[ "$ELAPSED" -le 12 ]]; then
    pass "svc.sh 挂起时限时退出（用时 ${ELAPSED}s ≤ 12s）"
else
    fail "svc.sh 挂起时未限时（用时 ${ELAPSED}s）"
fi
assert_eq "$RC" "1" "svc.sh 挂起超时后应判定 UNHEALTHY(1)"

# --- 用例 9（S1 反例）：gh 挂起 → 限时退出 ---
test_begin "health: gh 挂起 → 限时退出"
FAKE="$(new_fake_runner running)"
BIN="$(new_health_bin 200)"
cat > "$BIN/gh" <<'FAKE_GH_HANG'
#!/usr/bin/env bash
while true; do sleep 1; done
FAKE_GH_HANG
chmod +x "$BIN/gh"
START="$(date +%s)"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" HEALTH_CMD_TIMEOUT=2 PATH="$BIN:/usr/bin:/bin" bash "$HEALTH_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
ELAPSED="$(( $(date +%s) - START ))"
if [[ "$ELAPSED" -le 12 ]]; then
    pass "gh 挂起时限时退出（用时 ${ELAPSED}s ≤ 12s）"
else
    fail "gh 挂起时未限时（用时 ${ELAPSED}s）"
fi

echo
echo "check-runner-health 测试完成：$((TEST_COUNT)) 用例，$FAIL_COUNT 失败"
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
