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
    echo "$dir"
}

# --- 用例 1：happy path —— running + GitHub online → 合法 JSONL ---
test_begin "probe: running + online → 合法 JSONL"
FAKE="$(new_fake_runner running)"
FAKE_BIN="$(new_probe_bin)"
cat > "$FAKE_BIN/gh" <<'FAKE_GH'
#!/usr/bin/env bash
echo '{"runners":[{"name":"Chris-Mac-mini-STS2-AUTOTEST","status":"online"}]}'
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
for KEY in ts service_state runner_pids github_online proxy_local_reachable direct_github_reachable proxy_github_reachable exit_ip_direct exit_ip_proxy; do
    if json_has_key "$OUT" "$KEY"; then
        pass "字段 $KEY 存在"
    else
        fail "缺少字段 $KEY"
    fi
done

echo
echo "runner-probe 测试完成：$((TEST_COUNT)) 用例，$FAIL_COUNT 失败"
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
