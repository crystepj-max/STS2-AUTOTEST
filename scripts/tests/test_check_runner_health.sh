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
    # 健康检查用 ps -eo args 检测进程且限定 RUNNER_DIR/bin/ 路径（CI 环境 pgrep
    # 实测不可靠 + 多 runner 隔离），测试须隔离 ps 并输出与 RUNNER_DIR 一致的路径
    cat > "$dir/ps" <<'FAKE_PS'
#!/usr/bin/env bash
if [[ "$*" == *"-eo"* || "$*" == *"args"* ]]; then
    printf '%s\n' \
        '  1 1 /sbin/launchd' \
        "40231 80357 ${RUNNER_DIR:-/Users/chris/actions-runner}/bin/Runner.Listener run --startuptype service" \
        '40235 40231 /Users/chris/actions-runner/bin/Runner.Worker'
elif [[ "$*" == *"eww"* && "$*" == *"-p"* ]]; then
    printf '%s\n' "  PID   TT  STAT      TIME COMMAND"
    printf '%s\n' "40231   ??  S      0:00.01 ${RUNNER_DIR:-/Users/chris/actions-runner}/bin/Runner.Listener run --startuptype service"
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

# --- 用例 10：调用方 shell 有会话变量但服务侧干净 → 不误报 UNHEALTHY ---
test_begin "health: 调用方 shell 有会话变量但 plist/listener 干净 → 仍 HEALTHY"
FAKE="$(new_fake_runner running)"
BIN="$(new_health_bin 200)"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$BIN:/usr/bin:/bin" CODEBUDDY_SESSION_ID=sess-1 bash "$HEALTH_SCRIPT" --json 2>/dev/null)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "仅调用方 shell 有会话变量时不应 UNHEALTHY"
if echo "$OUT" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d.get("healthy") is True; assert d.get("safe_delete_session_env","") == ""' 2>/dev/null; then
    pass "未误报 SAFE_DELETE"
else
    fail "调用方 shell 变量导致误报：$OUT"
fi

# --- 用例 10b：Runner.Listener 进程环境含会话变量 → UNHEALTHY ---
test_begin "health: Listener 进程环境含 CODEBUDDY_SESSION_ID → exit 1 UNHEALTHY"
FAKE="$(new_fake_runner running)"
BIN="$(new_health_bin 200)"
cat > "$BIN/ps" <<'FAKE_PS_LISTENER_ENV'
#!/usr/bin/env bash
if [[ "$*" == *"-eo"* || "$*" == *"args"* ]]; then
    printf '%s\n' \
        '  1 1 /sbin/launchd' \
        "40231 80357 ${RUNNER_DIR:-/Users/chris/actions-runner}/bin/Runner.Listener run --startuptype service" \
        '40235 40231 /Users/chris/actions-runner/bin/Runner.Worker'
elif [[ "$*" == *"eww"* && "$*" == *"-p"* ]]; then
    printf '%s\n' "  PID   TT  STAT      TIME COMMAND"
    printf '%s\n' "40231   ??  S      0:00.01 ${RUNNER_DIR:-/Users/chris/actions-runner}/bin/Runner.Listener run CODEBUDDY_SESSION_ID=from-listener"
else
    /bin/ps "$@"
fi
exit 0
FAKE_PS_LISTENER_ENV
chmod +x "$BIN/ps"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$BIN:/usr/bin:/bin" env -u CODEBUDDY_SESSION_ID -u CLAUDE_SESSION_ID bash "$HEALTH_SCRIPT" --json 2>/dev/null)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "1" "Listener 进程含会话变量时应 UNHEALTHY(1)"
if echo "$OUT" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d.get("healthy") is False; assert "CODEBUDDY_SESSION_ID(listener)" in d.get("safe_delete_session_env","")' 2>/dev/null; then
    pass "JSON 含 safe_delete_session_env=CODEBUDDY_SESSION_ID(listener)"
else
    fail "未检测到 Listener 进程会话变量：$OUT"
fi

# --- 用例 11：会话变量为空字符串不触发 ---
test_begin "health: 会话变量置空 → 仍 HEALTHY"
FAKE="$(new_fake_runner running)"
BIN="$(new_health_bin 200)"
RC=0; OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$BIN:/usr/bin:/bin" CODEBUDDY_SESSION_ID= CLAUDE_SESSION_ID= bash "$HEALTH_SCRIPT" --json 2>/dev/null)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "会话变量置空时应 HEALTHY(0)"
if echo "$OUT" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d.get("healthy") is True; assert d.get("safe_delete_session_env","") == ""' 2>/dev/null; then
    pass "置空后 safe_delete_session_env 为空"
else
    fail "置空后仍误报 SAFE_DELETE：$OUT"
fi

# --- 用例 12：launchd plist EnvironmentVariables 含会话变量 → UNHEALTHY ---
test_begin "health: launchd plist 含 CODEBUDDY_SESSION_ID → exit 1 UNHEALTHY"
if [[ ! -x /usr/libexec/PlistBuddy ]]; then
    pass "跳过：非 macOS 或无 PlistBuddy"
else
    FAKE="$(new_fake_runner running)"
    BIN="$(new_health_bin 200)"
    FAKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/health-home.XXXXXX")"
    RUNNER_NAME="test-mac-runner"
    printf '{"agentName": "%s"}\n' "$RUNNER_NAME" > "$FAKE/.runner"
    REPO_SLUG="crystepj-max-STS2-AUTOTEST"
    PLIST_DIR="$FAKE_HOME/Library/LaunchAgents"
    mkdir -p "$PLIST_DIR"
    PLIST="$PLIST_DIR/actions.runner.${REPO_SLUG}.${RUNNER_NAME}.plist"
    cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CODEBUDDY_SESSION_ID</key>
    <string>from-plist</string>
  </dict>
</dict>
</plist>
PLIST_EOF
    RC=0; OUT="$(cd /tmp && HOME="$FAKE_HOME" RUNNER_DIR="$FAKE" REPO="crystepj-max/STS2-AUTOTEST" PATH="$BIN:/usr/bin:/bin" env -u CODEBUDDY_SESSION_ID -u CLAUDE_SESSION_ID bash "$HEALTH_SCRIPT" --json 2>/dev/null)" || RC=$?
    RC="${RC:-0}"
    assert_eq "$RC" "1" "plist 含会话变量时应 UNHEALTHY(1)"
    if echo "$OUT" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d.get("healthy") is False; assert "CODEBUDDY_SESSION_ID(plist)" in d.get("safe_delete_session_env","")' 2>/dev/null; then
        pass "JSON 报告 plist 中的 SAFE_DELETE 风险"
    else
        fail "未检测到 plist 会话变量：$OUT"
    fi
fi

echo
echo "check-runner-health 测试完成：$((TEST_COUNT)) 用例，$FAIL_COUNT 失败"
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
