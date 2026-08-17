#!/usr/bin/env bash
# runner-probe.sh 行为测试（issue-24 T3）：
# 1. 输出为合法 JSONL（每行一个可解析 JSON，含必需字段）
# 2. 各探测项失败（无 gh / 无网络）时不崩溃，状态记为 unknown/false
# 3. svc.sh 不存在 → service_state=not-installed
# 4. GitHub 侧查询失败 → github_online=unknown
# 5. 支持 PROBE_OUTPUT 落盘追加
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/helpers.sh
source "$SCRIPT_DIR/lib/helpers.sh"

PROBE_SCRIPT="$SCRIPT_DIR/../runner-probe.sh"

# 解析一行 JSON 的指定字段（依赖 python3，macOS 自带）
json_field() {
    local line="$1" key="$2"
    python3 -c "import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2], ''))" "$line" "$key"
}

# 检查 JSON 中字段是否存在（值为空字符串也算存在）
json_has_key() {
    local line="$1" key="$2"
    python3 -c "import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if sys.argv[2] in d else 1)" "$line" "$key"
}

# 创建 fake 环境：fake curl（模拟网络可达 200 + fake IP），不含 gh
new_probe_bin() {
    local dir
    dir="$(mktemp -d "${TMPDIR:-/tmp}/probe-bin.XXXXXX")"
    cat > "$dir/curl" <<'FAKE_CURL'
#!/usr/bin/env bash
# fake curl：模拟 GitHub 可达（-w 输出 200）、ipify 输出 fake IP
for arg in "$@"; do
    if [[ "$arg" == *"ipify.org"* ]]; then
        echo "203.0.113.7"
        exit 0
    fi
done
printf '%s' "200"
echo
exit 0
FAKE_CURL
    chmod +x "$dir/curl"
    # fake DNS（探针独立联网探针用）：getent/nslookup 返回成功
    cat > "$dir/getent" <<'FAKE_GETENT_COMMON'
#!/usr/bin/env bash
echo "203.0.113.77"
exit 0
FAKE_GETENT_COMMON
    cat > "$dir/nslookup" <<'FAKE_NSLOOKUP_COMMON'
#!/usr/bin/env bash
echo "Server: 127.0.0.1"
exit 0
FAKE_NSLOOKUP_COMMON
    chmod +x "$dir/getent" "$dir/nslookup"
    echo "$dir"
}

# --- 用例 1：happy path —— running + GitHub online → 合法 JSONL ---
test_begin "probe: running + online → 合法 JSONL"
FAKE="$(new_fake_runner running)"
FAKE_BIN="$(new_probe_bin)"
cat > "$FAKE_BIN/gh" <<'FAKE_GH'
#!/usr/bin/env bash
# 模拟 gh api --jq @tsv：输出 "<status>\t<busy>" 行
if [[ "$*" == *"--jq"* ]]; then
    printf 'online\tfalse\n'
fi
exit 0
FAKE_GH
chmod +x "$FAKE_BIN/gh"

OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$FAKE_BIN:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "probe 应正常退出(0)"
if echo "$OUT" | python3 -c 'import json,sys; json.loads(sys.stdin.read())' 2>/dev/null; then
    pass "输出为合法 JSON"
else
    fail "输出不是合法 JSON：$OUT"
fi
assert_eq "$(json_field "$OUT" ts | wc -c | tr -d ' ')" "21" "ts 应为 ISO8601（19 字符+换行→21）"
assert_eq "$(json_field "$OUT" service_state)" "running" "service_state 应为 running"

# --- 用例 2：svc.sh 不存在 → not-installed ---
test_begin "probe: 未安装 → service_state=not-installed"
EMPTY="$(mktemp -d "${TMPDIR:-/tmp}/probe-empty.XXXXXX")"
FAKE_BIN_N="$(new_probe_bin)"
OUT="$(cd /tmp && RUNNER_DIR="$EMPTY" PATH="$FAKE_BIN_N:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "未安装时也应正常退出(0)"
assert_eq "$(json_field "$OUT" service_state)" "not-installed" "service_state 应为 not-installed"

# --- 用例 3：gh 失败 → github_online=unknown，不崩溃 ---
test_begin "probe: gh 失败 → github_online=unknown"
FAKE="$(new_fake_runner running)"
FAKE_BIN2="$(new_probe_bin)"
cat > "$FAKE_BIN2/gh" <<'FAKE_GH2'
#!/usr/bin/env bash
echo "boom" >&2
exit 1
FAKE_GH2
chmod +x "$FAKE_BIN2/gh"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$FAKE_BIN2:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "gh 失败不崩溃"
assert_eq "$(json_field "$OUT" github_online)" "unknown" "github_online 应为 unknown"

# --- 用例 4：PROBE_OUTPUT 落盘追加 ---
test_begin "probe: PROBE_OUTPUT 落盘追加"
FAKE="$(new_fake_runner stopped)"
FAKE_BIN_P="$(new_probe_bin)"
PROBE_FILE="$(mktemp "${TMPDIR:-/tmp}/probe-out.XXXXXX")"
echo '{"pre":true}' > "$PROBE_FILE"
(cd /tmp && RUNNER_DIR="$FAKE" PROBE_OUTPUT="$PROBE_FILE" PATH="$FAKE_BIN_P:/usr/bin:/bin" bash "$PROBE_SCRIPT" >/dev/null 2>&1) || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "落盘模式正常退出"
LINE_COUNT="$(wc -l < "$PROBE_FILE" | tr -d ' ')"
assert_eq "$LINE_COUNT" "2" "应为追加（原 1 行 + 新 1 行）"
assert_eq "$(json_field "$(tail -1 "$PROBE_FILE")" service_state)" "stopped" "新记录 service_state=stopped"

# --- 用例 5：必需字段齐全（四类归因所需）---
test_begin "probe: 必需字段齐全"
FAKE="$(new_fake_runner running)"
FAKE_BIN_F="$(new_probe_bin)"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$FAKE_BIN_F:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)"
for KEY in ts service_state runner_pids github_online proxy_local_reachable direct_github_reachable proxy_github_reachable exit_ip_direct exit_ip_proxy internet_reachable dns_resolvable; do
    if json_has_key "$OUT" "$KEY"; then
        pass "字段 $KEY 存在"
    else
        fail "缺少字段 $KEY"
    fi
done

# --- 用例 6（R1 反例）：存在环境代理时，直连探测必须强制绕过代理 ---
test_begin "probe: 直连探测强制绕过环境代理（--noproxy）"
FAKE="$(new_fake_runner running)"
FAKE_BIN_NP="$(mktemp -d "${TMPDIR:-/tmp}/probe-noproxy.XXXXXX")"
cat > "$FAKE_BIN_NP/curl" <<'FAKE_CURL_NOPROXY'
#!/usr/bin/env bash
# fake curl：记录调用参数；直连调用（无 -x）必须带 --noproxy，否则视为泄漏
echo "$@" >> "$NOPROXY_LOG"
if [[ "$*" != *"-x "* ]]; then
    if [[ "$*" != *"--noproxy"* ]]; then
        echo "LEAKED-THROUGH-PROXY" >&2
        exit 1
    fi
fi
if [[ "$*" == *"ipify.org"* ]]; then
    echo "203.0.113.9"
    exit 0
fi
printf '%s' "200"
echo
exit 0
FAKE_CURL_NOPROXY
chmod +x "$FAKE_BIN_NP/curl"
NOPROXY_LOG="$(mktemp "${TMPDIR:-/tmp}/noproxy-log.XXXXXX")"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" NOPROXY_LOG="$NOPROXY_LOG" \
    http_proxy="http://127.0.0.1:9" https_proxy="http://127.0.0.1:9" all_proxy="http://127.0.0.1:9" \
    PATH="$FAKE_BIN_NP:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "存在环境代理时探针应正常退出(0)"
# 对每条直连调用（不含 -x）断言都带 --noproxy
LEAKED=0
while IFS= read -r line; do
    if [[ "$line" == *"-x "* ]]; then
        continue
    fi
    if [[ "$line" != *"--noproxy"* ]]; then
        echo "  FAIL: 直连调用未强制 --noproxy：$line" >&2
        LEAKED=1
    fi
done < "$NOPROXY_LOG"
if [[ "$LEAKED" -eq 0 ]]; then
    pass "直连调用均强制 --noproxy"
else
    fail "存在泄漏到环境代理的直连调用"
fi
assert_eq "$(json_field "$OUT" direct_github_reachable)" "True" "直连可达应为 true（绕过代理实测）"

# --- 用例 7（R2）：github_busy 字段 —— gh 返回 busy 时如实记录 ---
test_begin "probe: github_busy 反映任务领取/忙闲"
FAKE="$(new_fake_runner running)"
FAKE_BIN_B="$(new_probe_bin)"
cat > "$FAKE_BIN_B/gh" <<'FAKE_GH_BUSY'
#!/usr/bin/env bash
# 模拟 gh api --jq @tsv：busy=true
if [[ "$*" == *"--jq"* ]]; then
    printf 'online\ttrue\n'
fi
exit 0
FAKE_GH_BUSY
chmod +x "$FAKE_BIN_B/gh"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$FAKE_BIN_B:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)"
assert_eq "$(json_field "$OUT" github_busy)" "True" "github_busy 应为 True（busy 领取任务中）"

# --- 用例 8（R2）：维护操作留记录 —— PROBE_OP 注入操作标记 ---
test_begin "probe: PROBE_OP 维护操作标记写入 JSON"
FAKE="$(new_fake_runner stopped)"
FAKE_BIN_O="$(new_probe_bin)"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PROBE_OP="manual-stop" PATH="$FAKE_BIN_O:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)"
assert_eq "$(json_field "$OUT" op)" "manual-stop" "op 字段应为 manual-stop"
assert_eq "$(json_field "$OUT" service_state)" "stopped" "服务状态仍如实记录"

# --- 用例 9（R2）：状态文件推导断线/恢复事件 transition ---
test_begin "probe: 状态文件推导 disconnect/recover 事件"
FAKE="$(new_fake_runner running)"
FAKE_BIN_T="$(new_probe_bin)"
cat > "$FAKE_BIN_T/gh" <<'FAKE_GH_T'
#!/usr/bin/env bash
# 模拟 gh api --jq @tsv：online
if [[ "$*" == *"--jq"* ]]; then
    printf 'online\tfalse\n'
fi
exit 0
FAKE_GH_T
chmod +x "$FAKE_BIN_T/gh"
STATE_FILE="$(mktemp "${TMPDIR:-/tmp}/probe-state.XXXXXX")"
# 上次采样：服务 running + GitHub offline（断线中）→ 本次 online = recover
echo '{"service_state":"running","github_online":"offline"}' > "$STATE_FILE"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PROBE_STATE_FILE="$STATE_FILE" PATH="$FAKE_BIN_T:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)"
assert_eq "$(json_field "$OUT" transition)" "recover" "断线→恢复应记为 recover"

# 反例：上次 online → 本次 offline = disconnect
cat > "$STATE_FILE" <<'PREV_ONLINE'
{"service_state":"running","github_online":"online"}
PREV_ONLINE
FAKE_BIN_T2="$(new_probe_bin)"
cat > "$FAKE_BIN_T2/gh" <<'FAKE_GH_OFF'
#!/usr/bin/env bash
# 模拟 gh api --jq @tsv：offline
if [[ "$*" == *"--jq"* ]]; then
    printf 'offline\tfalse\n'
fi
exit 0
FAKE_GH_OFF
chmod +x "$FAKE_BIN_T2/gh"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PROBE_STATE_FILE="$STATE_FILE" PATH="$FAKE_BIN_T2:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)"
assert_eq "$(json_field "$OUT" transition)" "disconnect" "在线→断线应记为 disconnect"

# --- 用例 9b（R2/T3 延伸）：ops.jsonl 近期维护操作 → op 字段反映（区分人工维护 vs 意外中断）---
test_begin "probe: ops.jsonl 近期维护操作 → op 字段反映"
# 匹配场景：stopped 服务（prev running）+ 近期 manual-stop → transition=service-stopped + op=manual-stop
FAKE="$(new_fake_runner stopped)"
FAKE_BIN_OPS="$(new_probe_bin)"
OPS_FILE="$(mktemp "${TMPDIR:-/tmp}/probe-ops.XXXXXX")"
STATE_FILE_B="$(mktemp "${TMPDIR:-/tmp}/probe-state-b.XXXXXX")"
echo '{"service_state":"running","github_online":"online"}' > "$STATE_FILE_B"
# 写一条 1 分钟前的 manual-stop 记录（时间窗口内）
RECENT_TS="$(date -u -v-1M +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d '1 minute ago' +"%Y-%m-%dT%H:%M:%SZ")"
printf '{"ts": "%s", "op": "manual-stop"}\n' "$RECENT_TS" > "$OPS_FILE"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PROBE_OPS_FILE="$OPS_FILE" PROBE_STATE_FILE="$STATE_FILE_B" PATH="$FAKE_BIN_OPS:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)"
assert_eq "$(json_field "$OUT" op)" "manual-stop" "近期维护操作应反映在 op 字段"

# 反例 1：2 小时前的记录超出时间窗口 → op 为空（不误报维护操作）
OLD_TS="$(date -u -v-2H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d '2 hours ago' +"%Y-%m-%dT%H:%M:%SZ")"
printf '{"ts": "%s", "op": "manual-stop"}\n' "$OLD_TS" > "$OPS_FILE"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PROBE_OPS_FILE="$OPS_FILE" PROBE_STATE_FILE="$STATE_FILE_B" PATH="$FAKE_BIN_OPS:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)"
assert_eq "$(json_field "$OUT" op)" "" "超窗维护操作不应误报"

# 反例 2：时间窗口内 manual-start 是独立维护事件（transition=steady 也记录）
printf '{"ts": "%s", "op": "manual-start"}\n' "$RECENT_TS" > "$OPS_FILE"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PROBE_OPS_FILE="$OPS_FILE" PROBE_STATE_FILE="$STATE_FILE_B" PATH="$FAKE_BIN_OPS:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)"
assert_eq "$(json_field "$OUT" op)" "manual-start" "manual-start 是独立维护事件（steady 也记录）"

# --- 用例 9c（P1 反例）：ops 标记须与 transition 匹配——manual-start 后发生
# disconnect（意外断线）不得误标为维护操作 ---
test_begin "probe: op 与 transition 不匹配时不误标（意外断线 ≠ 维护）"
FAKE="$(new_fake_runner running)"
FAKE_BIN_OPM="$(new_probe_bin)"
cat > "$FAKE_BIN_OPM/gh" <<'FAKE_GH_OFF2'
#!/usr/bin/env bash
if [[ "$*" == *"--jq"* ]]; then
    printf 'offline\tfalse\n'
fi
exit 0
FAKE_GH_OFF2
chmod +x "$FAKE_BIN_OPM/gh"
OPS_FILE_M="$(mktemp "${TMPDIR:-/tmp}/probe-ops-m.XXXXXX")"
RECENT_TS_M="$(date -u -v-1M +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d '1 minute ago' +"%Y-%m-%dT%H:%M:%SZ")"
printf '{"ts": "%s", "op": "manual-start"}\n' "$RECENT_TS_M" > "$OPS_FILE_M"
STATE_FILE_M="$(mktemp "${TMPDIR:-/tmp}/probe-state-m.XXXXXX")"
echo '{"service_state":"running","github_online":"online"}' > "$STATE_FILE_M"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PROBE_OPS_FILE="$OPS_FILE_M" PROBE_STATE_FILE="$STATE_FILE_M" PATH="$FAKE_BIN_OPM:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)"
assert_eq "$(json_field "$OUT" transition)" "disconnect" "断线应记为 disconnect"
assert_eq "$(json_field "$OUT" op)" "" "manual-start 与 disconnect 不匹配，op 应为空（不误标维护）"

# 匹配场景：manual-stop + service-stopped 才关联
# 用 stopped 服务 + 无 gh 的 fake bin（github_online=unknown，避免 disconnect 优先判定）
FAKE_STOP="$(new_fake_runner stopped)"
FAKE_BIN_NOGH="$(new_probe_bin)"
OPS_FILE_S="$(mktemp "${TMPDIR:-/tmp}/probe-ops-s.XXXXXX")"
printf '{"ts": "%s", "op": "manual-stop"}\n' "$RECENT_TS_M" > "$OPS_FILE_S"
STATE_FILE_S="$(mktemp "${TMPDIR:-/tmp}/probe-state-s.XXXXXX")"
echo '{"service_state":"running","github_online":"online"}' > "$STATE_FILE_S"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE_STOP" PROBE_OPS_FILE="$OPS_FILE_S" PROBE_STATE_FILE="$STATE_FILE_S" PATH="$FAKE_BIN_NOGH:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)"
assert_eq "$(json_field "$OUT" transition)" "service-stopped" "服务停止应记为 service-stopped"
assert_eq "$(json_field "$OUT" op)" "manual-stop" "manual-stop 与 service-stopped 匹配应关联"

# --- 用例 9d（P2 四类归因）：GitHub 不可达时，独立联网探针区分本机网络 vs 上游故障 ---
test_begin "probe: GitHub 不可达时独立联网探针仍能区分网络状态"
FAKE="$(new_fake_runner running)"
FAKE_BIN_NET="$(mktemp -d "${TMPDIR:-/tmp}/probe-net.XXXXXX")"
# fake curl：对 GitHub 返回 000（不可达），对 ipify（独立端点）返回 200 + IP
cat > "$FAKE_BIN_NET/curl" <<'FAKE_CURL_NET'
#!/usr/bin/env bash
if [[ "$*" == *"api.github.com"* ]]; then
    printf '%s' "000"; echo
    exit 0
fi
if [[ "$*" == *"ipify.org"* ]]; then
    # 探针用 -w %{http_code} 检查状态码，输出 200；IP 内容由 exit_ip 单独探测
    printf '%s' "200"; echo
    exit 0
fi
printf '%s' "200"; echo
exit 0
FAKE_CURL_NET
chmod +x "$FAKE_BIN_NET/curl"
# fake DNS：getent/nslookup 返回成功（DNS 可解析）
cat > "$FAKE_BIN_NET/getent" <<'FAKE_GETENT'
#!/usr/bin/env bash
echo "203.0.113.77"
exit 0
FAKE_GETENT
cat > "$FAKE_BIN_NET/nslookup" <<'FAKE_NSLOOKUP'
#!/usr/bin/env bash
echo "Server: 127.0.0.1"
exit 0
FAKE_NSLOOKUP
chmod +x "$FAKE_BIN_NET/getent" "$FAKE_BIN_NET/nslookup"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PATH="$FAKE_BIN_NET:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)"
# GitHub 两条路径失败（000），但独立联网探针成功 → 可判定「本机网络正常，GitHub 上游问题」
assert_eq "$(json_field "$OUT" direct_github_reachable)" "False" "GitHub 直连不可达"
if json_has_key "$OUT" internet_reachable; then
    assert_eq "$(json_field "$OUT" internet_reachable)" "True" "独立联网探针应可达（区分本机网络正常）"
else
    fail "缺少 internet_reachable 字段（四类归因需要独立联网探针）"
fi

# --- 用例 10（S2）：超时后无残留子进程 ---
test_begin "probe: gh 挂起超时后无残留子进程"
FAKE="$(new_fake_runner running)"
FAKE_BIN_H="$(new_probe_bin)"
cat > "$FAKE_BIN_H/gh" <<'FAKE_GH_HANG'
#!/usr/bin/env bash
# 挂起替身：永不返回（模拟外部调用无响应）
while true; do sleep 1; done
FAKE_GH_HANG
chmod +x "$FAKE_BIN_H/gh"
# 挂起替身启动前记录自身 pid 基线（pgrep -f 匹配脚本路径）
GH_HANG_PATTERN="$FAKE_BIN_H/gh"
PRE_PIDS="$(pgrep -f "$GH_HANG_PATTERN" 2>/dev/null | wc -l | tr -d ' ' || true)"
PRE_PIDS="${PRE_PIDS:-0}"
(cd /tmp && RUNNER_DIR="$FAKE" PROBE_GH_TIMEOUT=2 PATH="$FAKE_BIN_H:/usr/bin:/bin" bash "$PROBE_SCRIPT" >/dev/null 2>&1) || true
# 等待 kill 传播
sleep 2
POST_PIDS="$(pgrep -f "$GH_HANG_PATTERN" 2>/dev/null | wc -l | tr -d ' ' || true)"
POST_PIDS="${POST_PIDS:-0}"
if [[ "$POST_PIDS" -le "$PRE_PIDS" ]]; then
    pass "超时后无残留挂起子进程（$PRE_PIDS → $POST_PIDS）"
else
    fail "超时后仍残留挂起子进程（$PRE_PIDS → $POST_PIDS）"
fi

# --- 用例 11（S1）：探针在超时后仍能输出 JSON（不因挂起卡死）---
test_begin "probe: 外部调用挂起不阻塞探针输出"
FAKE="$(new_fake_runner running)"
FAKE_BIN_H2="$(new_probe_bin)"
cat > "$FAKE_BIN_H2/gh" <<'FAKE_GH_HANG2'
#!/usr/bin/env bash
while true; do sleep 1; done
FAKE_GH_HANG2
chmod +x "$FAKE_BIN_H2/gh"
OUT="$(cd /tmp && RUNNER_DIR="$FAKE" PROBE_GH_TIMEOUT=2 PATH="$FAKE_BIN_H2:/usr/bin:/bin" bash "$PROBE_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "挂起替身下探针仍应正常退出(0)"
assert_eq "$(json_field "$OUT" github_online)" "unknown" "挂起超时后 github_online 应为 unknown"

# --- 用例 12（S2 反例）：超时后子进程也被回收（不残留孤儿）---
test_begin "probe: 超时后子进程一并回收"
FAKE="$(new_fake_runner running)"
FAKE_BIN_C="$(new_probe_bin)"
HANG_PID_FILE="$(mktemp "${TMPDIR:-/tmp}/hang-main.XXXXXX")"
HANG_CHILD_PID_FILE="$(mktemp "${TMPDIR:-/tmp}/hang-child.XXXXXX")"
cat > "$FAKE_BIN_C/gh" <<'FAKE_GH_CHILD'
#!/usr/bin/env bash
# 挂起替身：记录自身 pid 与一个长活子进程 pid
echo $$ > "$HANG_PID_FILE"
(sleep 300) &
echo $! > "$HANG_CHILD_PID_FILE"
while true; do sleep 1; done
FAKE_GH_CHILD
chmod +x "$FAKE_BIN_C/gh"
(cd /tmp && RUNNER_DIR="$FAKE" HANG_PID_FILE="$HANG_PID_FILE" HANG_CHILD_PID_FILE="$HANG_CHILD_PID_FILE" \
    PROBE_GH_TIMEOUT=2 PATH="$FAKE_BIN_C:/usr/bin:/bin" bash "$PROBE_SCRIPT" >/dev/null 2>&1) || true
sleep 2
CHILD_PID="$(cat "$HANG_CHILD_PID_FILE" 2>/dev/null || echo '')"
MAIN_PID="$(cat "$HANG_PID_FILE" 2>/dev/null || echo '')"
RESIDUAL=""
if [[ -n "$MAIN_PID" ]] && kill -0 "$MAIN_PID" 2>/dev/null; then
    RESIDUAL="主进程 $MAIN_PID "
fi
if [[ -n "$CHILD_PID" ]] && kill -0 "$CHILD_PID" 2>/dev/null; then
    RESIDUAL="${RESIDUAL}子进程 $CHILD_PID "
fi
if [[ -n "$RESIDUAL" ]]; then
    fail "超时后仍残留进程：$RESIDUAL"
else
    pass "超时后主进程与子进程均已回收"
fi

echo
echo "runner-probe 测试完成：$((TEST_COUNT)) 用例，$FAIL_COUNT 失败"
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
