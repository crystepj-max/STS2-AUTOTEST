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

echo
echo "check-runner-health 测试完成：$((TEST_COUNT)) 用例，$FAIL_COUNT 失败"
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
