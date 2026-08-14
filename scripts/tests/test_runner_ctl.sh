#!/usr/bin/env bash
# runner-ctl.sh 行为测试（issue-24 T2）：
# 退出码契约：0=RUNNING 1=STOPPED 2=NOT_INSTALLED 3=USAGE
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/helpers.sh
source "$SCRIPT_DIR/lib/helpers.sh"

# --- 用例 1：running 状态 → exit 0，输出含 Started 与 state: running ---
test_begin "status: running → exit 0"
FAKE="$(new_fake_runner running)"
run_ctl "$FAKE" status
assert_eq "$CTL_RC" "0" "running 时退出码应为 0"
assert_contains "$CTL_OUT" "Started:" "输出应包含 Started:"
assert_contains "$CTL_OUT" "state: running" "输出应包含 state: running"
assert_contains "$CTL_OUT" "actions.runner.example.Chris-Mac-mini" "输出应包含检测到的 label"

# --- 用例 2：stopped 状态 → exit 1 ---
test_begin "status: stopped → exit 1"
FAKE="$(new_fake_runner stopped)"
run_ctl "$FAKE" status
assert_eq "$CTL_RC" "1" "stopped 时退出码应为 1"
assert_contains "$CTL_OUT" "Stopped" "输出应包含 Stopped"
assert_contains "$CTL_OUT" "state: stopped" "输出应包含 state: stopped"

# --- 用例 3：not-installed（fake 无 .fake-state）→ exit 2 ---
test_begin "status: not installed → exit 2"
FAKE="$(new_fake_runner not-installed)"
run_ctl "$FAKE" status
assert_eq "$CTL_RC" "2" "not installed 时退出码应为 2"
assert_contains "$CTL_OUT" "not installed" "输出应包含 not installed"
assert_contains "$CTL_OUT" "state: not-installed" "输出应包含 state: not-installed"

# --- 用例 4：RUNNER_DIR 不存在 → exit 2 + 明确错误 ---
test_begin "status: RUNNER_DIR 不存在 → exit 2"
run_ctl "/nonexistent/runner-dir" status
assert_eq "$CTL_RC" "2" "目录不存在时退出码应为 2"
assert_contains "$CTL_OUT" "not found" "应给出目录不存在的明确提示"

# --- 用例 5：RUNNER_DIR 存在但无 svc.sh → exit 2 ---
test_begin "status: 无 svc.sh → exit 2"
EMPTY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fake-runner.XXXXXX")"
run_ctl "$EMPTY_DIR" status
assert_eq "$CTL_RC" "2" "缺 svc.sh 时退出码应为 2"

# --- 用例 6：stop 后状态变为 stopped（实证 stop 生效）---
test_begin "stop: 调用 svc.sh stop 且之后 status 为 stopped"
FAKE="$(new_fake_runner running)"
run_ctl "$FAKE" stop
assert_contains "$CTL_OUT" "stopping" "stop 应透传 svc.sh stop"
run_ctl "$FAKE" status
assert_eq "$CTL_RC" "1" "stop 后 status 应为 stopped(1)"

# --- 用例 7：start 后状态变为 running（实证 start 生效）---
test_begin "start: 调用 svc.sh start 且之后 status 为 running"
FAKE="$(new_fake_runner stopped)"
run_ctl "$FAKE" start
assert_contains "$CTL_OUT" "starting" "start 应透传 svc.sh start"
run_ctl "$FAKE" status
assert_eq "$CTL_RC" "0" "start 后 status 应为 running(0)"

# --- 用例 8：未知子命令 → usage + exit 3 ---
test_begin "未知子命令 → usage + exit 3"
FAKE="$(new_fake_runner running)"
run_ctl "$FAKE" frobnicate
assert_eq "$CTL_RC" "3" "未知子命令退出码应为 3"
assert_contains "$CTL_OUT" "usage" "应打印 usage"

# --- 用例 9：无参数 → usage + exit 3 ---
test_begin "无参数 → usage + exit 3"
FAKE="$(new_fake_runner running)"
run_ctl "$FAKE"
assert_eq "$CTL_RC" "3" "无参数退出码应为 3"
assert_contains "$CTL_OUT" "usage" "应打印 usage"

# --- 用例 10（R3 反例）：svc.sh 报 Started 但 Runner.Listener 进程缺失 → 非 0 + 异常状态 ---
test_begin "status: 服务标记 started 但进程缺失 → exit 非 0 + 状态异常"
FAKE="$(new_fake_runner running)"
RUN_CTL_NO_PROCESS=1 run_ctl "$FAKE" status
assert_ne "$CTL_RC" "0" "进程缺失时退出码不应为 0（R3 反例）"
assert_contains "$CTL_OUT" "process" "应给出进程相关提示"

# --- 用例 11：svc.sh 报 Started 且进程存在 → exit 0（进程核验通过）---
test_begin "status: 服务标记 started 且进程存在 → exit 0"
FAKE="$(new_fake_runner running)"
RUN_CTL_NO_PROCESS=0 run_ctl "$FAKE" status
assert_eq "$CTL_RC" "0" "服务与进程一致时退出码应为 0"
assert_contains "$CTL_OUT" "state: running" "输出应包含 state: running"

# --- 用例 12（S1 反例）：svc.sh 挂起 → status 限时退出而非无期等待 ---
test_begin "status: svc.sh 挂起 → 限时退出"
FAKE="$(new_fake_runner running)"
cat > "$FAKE/svc.sh" <<'FAKE_SVC_HANG'
#!/usr/bin/env bash
while true; do sleep 1; done
FAKE_SVC_HANG
chmod +x "$FAKE/svc.sh"
START="$(date +%s)"
RUN_CTL_NO_PROCESS=0 SVC_TIMEOUT=2 run_ctl "$FAKE" status
ELAPSED="$(( $(date +%s) - START ))"
if [[ "$ELAPSED" -le 10 ]]; then
    pass "svc.sh 挂起时限时退出（用时 ${ELAPSED}s ≤ 10s）"
else
    fail "svc.sh 挂起时未限时（用时 ${ELAPSED}s）"
fi
assert_ne "$CTL_RC" "0" "svc.sh 挂起超时后不应报 running(0)"

# --- 用例 13（R2/T3 延伸）：stop/start 自动持久化维护操作标记（探针归因用）---
test_begin "stop: 自动写入维护操作标记（ops.jsonl）"
FAKE="$(new_fake_runner running)"
OPS_FILE="$(mktemp "${TMPDIR:-/tmp}/ops.XXXXXX")"
RUN_CTL_NO_PROCESS=0 RUN_CTL_OPS_FILE="$OPS_FILE" run_ctl "$FAKE" stop
assert_eq "$CTL_RC" "0" "stop 应正常执行"
if [[ -s "$OPS_FILE" ]]; then
    OP_JSON="$(tail -1 "$OPS_FILE")"
    if echo "$OP_JSON" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d.get("op")=="manual-stop"; assert "ts" in d' 2>/dev/null; then
        pass "ops.jsonl 含 manual-stop 标记与时间戳"
    else
        fail "ops.jsonl 内容不符合预期: $OP_JSON"
    fi
else
    fail "stop 未写入 ops.jsonl（维护操作标记缺失）"
fi

# --- 用例 14（R2/T3 延伸）：start 自动写入维护操作标记 ---
test_begin "start: 自动写入维护操作标记（ops.jsonl）"
FAKE="$(new_fake_runner stopped)"
OPS_FILE2="$(mktemp "${TMPDIR:-/tmp}/ops2.XXXXXX")"
RUN_CTL_NO_PROCESS=0 RUN_CTL_OPS_FILE="$OPS_FILE2" run_ctl "$FAKE" start
assert_eq "$CTL_RC" "0" "start 应正常执行"
if [[ -s "$OPS_FILE2" ]]; then
    OP_JSON="$(tail -1 "$OPS_FILE2")"
    if echo "$OP_JSON" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d.get("op")=="manual-start"; assert "ts" in d' 2>/dev/null; then
        pass "ops.jsonl 含 manual-start 标记与时间戳"
    else
        fail "ops.jsonl 内容不符合预期: $OP_JSON"
    fi
else
    fail "start 未写入 ops.jsonl（维护操作标记缺失）"
fi

# --- 用例 15（P2）：锁获取失败时中止写入（不覆盖/删除他人锁，不产生半行）---
test_begin "stop: 锁被占用时中止维护标记写入"
FAKE="$(new_fake_runner running)"
OPS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ops-dir.XXXXXX")"
OPS_FILE="$OPS_DIR/ops.jsonl"
printf '{"ts": "2026-08-14T00:00:00Z", "op": "manual-stop"}\n' > "$OPS_FILE"
# 预置锁目录（模拟前次中断残留）
mkdir "$OPS_DIR/.ops.lock"
LINE_BEFORE="$(wc -l < "$OPS_FILE" | tr -d ' ')"
RUN_CTL_NO_PROCESS=0 RUN_CTL_OPS_FILE="$OPS_FILE" RUN_CTL_OPS_TIMEOUT=3 run_ctl "$FAKE" stop
assert_eq "$CTL_RC" "0" "锁占用时 stop 仍应正常执行（标记写入跳过但不影响服务操作）"
LINE_AFTER="$(wc -l < "$OPS_FILE" | tr -d ' ')"
assert_eq "$LINE_AFTER" "$LINE_BEFORE" "锁占用时 ops.jsonl 不应新增行"
if [[ -d "$OPS_DIR/.ops.lock" ]]; then
    pass "他人锁未被删除"
else
    fail "锁目录被误删（应保留他人持有的锁）"
fi
rmdir "$OPS_DIR/.ops.lock" 2>/dev/null || true

# --- 用例 16（P2）：陈旧锁（持有进程已死）自动回收并正常写入 ---
test_begin "stop: 陈旧锁（死进程持有）自动回收并写入标记"
FAKE="$(new_fake_runner running)"
OPS_DIR_S="$(mktemp -d "${TMPDIR:-/tmp}/ops-dir-s.XXXXXX")"
OPS_FILE_S="$OPS_DIR_S/ops.jsonl"
printf '{"ts": "2026-08-14T00:00:00Z", "op": "manual-stop"}\n' > "$OPS_FILE_S"
# 预置陈旧锁：holder PID 999999（必然不存在）
mkdir "$OPS_DIR_S/.ops.lock"
printf '%s %s\n' "999999" "$(date +%s)" > "$OPS_DIR_S/.ops.lock/holder"
LINE_BEFORE_S="$(wc -l < "$OPS_FILE_S" | tr -d ' ')"
RUN_CTL_NO_PROCESS=0 RUN_CTL_OPS_FILE="$OPS_FILE_S" RUN_CTL_OPS_TIMEOUT=30 run_ctl "$FAKE" stop
assert_eq "$CTL_RC" "0" "陈旧锁回收后 stop 正常执行"
LINE_AFTER_S="$(wc -l < "$OPS_FILE_S" | tr -d ' ')"
assert_eq "$LINE_AFTER_S" "$((LINE_BEFORE_S + 1))" "陈旧锁回收后应新增一行标记"
if [[ ! -d "$OPS_DIR_S/.ops.lock" ]]; then
    pass "陈旧锁已被回收"
else
    fail "陈旧锁未被回收"
fi

# --- 用例 17（P2）：无 holder 文件的陈旧锁（mkdir 后写 holder 前中断）按年龄回收 ---
test_begin "stop: 无 holder 陈旧锁按目录龄回收并写入标记"
FAKE="$(new_fake_runner running)"
OPS_DIR_N="$(mktemp -d "${TMPDIR:-/tmp}/ops-dir-n.XXXXXX")"
OPS_FILE_N="$OPS_DIR_N/ops.jsonl"
printf '{"ts": "2026-08-14T00:00:00Z", "op": "manual-stop"}\n' > "$OPS_FILE_N"
# 预置无 holder 的陈旧锁：目录 mtime 设为 10 分钟前（超过 OPS_LOCK_STALE_AFTER=300）
mkdir "$OPS_DIR_N/.ops.lock"
touch -t "$(date -v-10M +%Y%m%d%H%M.%S 2>/dev/null || date -d '10 minutes ago' +%Y%m%d%H%M.%S)" "$OPS_DIR_N/.ops.lock"
LINE_BEFORE_N="$(wc -l < "$OPS_FILE_N" | tr -d ' ')"
RUN_CTL_NO_PROCESS=0 RUN_CTL_OPS_FILE="$OPS_FILE_N" RUN_CTL_OPS_TIMEOUT=30 \
    RUN_CTL_OPS_STALE=300 run_ctl "$FAKE" stop
assert_eq "$CTL_RC" "0" "无 holder 陈旧锁回收后 stop 正常执行"
LINE_AFTER_N="$(wc -l < "$OPS_FILE_N" | tr -d ' ')"
assert_eq "$LINE_AFTER_N" "$((LINE_BEFORE_N + 1))" "无 holder 陈旧锁回收后应新增一行标记"
if [[ ! -d "$OPS_DIR_N/.ops.lock" ]]; then
    pass "无 holder 陈旧锁已被回收"
else
    fail "无 holder 陈旧锁未被回收"
fi

# --- 用例 18（P2）：并发竞争陈旧锁——原子认领，两进程写入均完整不丢失 ---
test_begin "并发: 两进程竞争死进程锁，写入均完整"
OPS_DIR_C="$(mktemp -d "${TMPDIR:-/tmp}/ops-dir-c.XXXXXX")"
OPS_FILE_C="$OPS_DIR_C/ops.jsonl"
# 预置死进程陈旧锁
mkdir "$OPS_DIR_C/.ops.lock"
printf '%s %s\n' "999999" "$(date +%s)" > "$OPS_DIR_C/.ops.lock/holder"
# 并发写入：两个子 shell 各自直接调用 runner-ctl stop（触发 log_operation）
# stop 在 fake runner 上执行，两个进程同时竞争回收死进程锁并写 ops.jsonl
FAKE_C="$(new_fake_runner running)"
for OP in stop stop; do
    ( RUNNER_DIR="$FAKE_C" PROBE_OPS_FILE="$OPS_FILE_C" OPS_LOCK_TIMEOUT=50 \
        PATH="/usr/bin:/bin" bash "$SCRIPT_DIR/../runner-ctl.sh" "$OP" >/dev/null 2>&1 ) &
done
wait
LINE_COUNT_C="$(wc -l < "$OPS_FILE_C" 2>/dev/null | tr -d ' ')"
if [[ "$LINE_COUNT_C" == "2" ]]; then
    # 两行都必须是合法 JSON
    if python3 -c 'import json,sys
for line in open(sys.argv[1]): json.loads(line)
print("ok")' "$OPS_FILE_C" 2>/dev/null | grep -q ok; then
        pass "并发写入两行均合法（无覆盖无半行）"
    else
        fail "并发写入存在非法行: $(cat "$OPS_FILE_C")"
    fi
else
    fail "并发写入行数异常（期望 2 实际 $LINE_COUNT_C）: $(cat "$OPS_FILE_C" 2>/dev/null)"
fi

# --- 用例 19（P2）：活持有者的锁不回收（进程卡顿/休眠时 kill -0 仍成功，不得按年龄回收）---
test_begin "stop: 活持有者的锁不回收（即使锁龄超限）"
FAKE="$(new_fake_runner running)"
OPS_DIR_L="$(mktemp -d "${TMPDIR:-/tmp}/ops-dir-l.XXXXXX")"
OPS_FILE_L="$OPS_DIR_L/ops.jsonl"
printf '{"ts": "2026-08-14T00:00:00Z", "op": "manual-stop"}\n' > "$OPS_FILE_L"
# 预置锁：holder 是当前 shell 的 PID（活进程）+ 10 分钟前的超龄时间戳
mkdir "$OPS_DIR_L/.ops.lock"
printf '%s %s\n' "$$" "$(( $(date +%s) - 600 ))" > "$OPS_DIR_L/.ops.lock/holder"
LINE_BEFORE_L="$(wc -l < "$OPS_FILE_L" | tr -d ' ')"
RUN_CTL_NO_PROCESS=0 RUN_CTL_OPS_FILE="$OPS_FILE_L" RUN_CTL_OPS_TIMEOUT=3 \
    RUN_CTL_OPS_STALE=300 run_ctl "$FAKE" stop
assert_eq "$CTL_RC" "0" "活持有者锁存在时 stop 正常执行（等待后跳过标记写入）"
LINE_AFTER_L="$(wc -l < "$OPS_FILE_L" | tr -d ' ')"
assert_eq "$LINE_AFTER_L" "$LINE_BEFORE_L" "活持有者锁不被回收（ops 不新增行）"
if [[ -d "$OPS_DIR_L/.ops.lock" ]]; then
    pass "活持有者锁保留（未被年龄回收删除）"
else
    fail "活持有者锁被误删（应按年龄回收仅限死进程）"
fi
rmdir "$OPS_DIR_L/.ops.lock" 2>/dev/null || true

echo
echo "runner-ctl 测试完成：$((TEST_COUNT)) 用例，$FAIL_COUNT 失败"
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
