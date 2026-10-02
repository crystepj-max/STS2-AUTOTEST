#!/usr/bin/env bash
# nightly-game-bootstrap.sh 契约测试（issue #66）
# 用 fake curl/pgrep/open/sleep 注入 PATH 模拟游戏会话状态，不依赖真实游戏。
# 注意：kill/printf 是 bash 内建，PATH 遮蔽无效；kill 行为不在本测试覆盖内。
# 这些是实现内的可重复检查，不是游戏集成测试。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/helpers.sh
source "$SCRIPT_DIR/lib/helpers.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BOOTSTRAP="$REPO_ROOT/scripts/nightly-game-bootstrap.sh"
WORKFLOW="$REPO_ROOT/.github/workflows/ci-nightly.yml"

# 建 fake 工具环境并打印目录路径（stdout）：
#   curl       FAIL_N=N 前 N 次返回 000 之后 200；NEVER_READY 恒 000；否则恒 200
#   pgrep      PGREP_EMPTY 存在 → 无进程；否则返回固定 pid 4242
#   open       记录 args 与 STS2_* env 到 state/opened.txt，并 touch PGREP_EMPTY
#   sleep      立即返回（加速轮询）
run_bootstrap() {
    local fake="$1"
    shift
    local out rc
    out="$(env HOME="$fake" STS2_GAME_DIR="$fake/game" "$@" \
        PATH="$fake/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
        bash "$BOOTSTRAP" 2>&1)" && rc=0 || rc=$?
    RUN_OUT="$out"
    RUN_RC="${rc:-0}"
}

new_fake_env() {
    local dir
    dir="$(mktemp -d "${TMPDIR:-/tmp}/bootstrap-fake.XXXXXX")"
    mkdir -p "$dir/bin" "$dir/state" "$dir/game"
    : > "$dir/state/curl.count"
    cat > "$dir/bin/curl" <<FAKE_CURL
#!/usr/bin/env bash
COUNT_FILE="$dir/state/curl.count"
n="\$(cat "\$COUNT_FILE")"
n=\$((n + 1))
printf '%s' "\$n" > "\$COUNT_FILE"
if [[ -f "$dir/state/NEVER_READY" ]]; then printf '000'; exit 0; fi
if [[ -f "$dir/state/FAIL_N" ]]; then
    if (( n <= \$(cat "$dir/state/FAIL_N") )); then printf '000'; exit 0; fi
fi
printf '200'
exit 0
FAKE_CURL
    cat > "$dir/bin/pgrep" <<FAKE_PGREP
#!/usr/bin/env bash
if [[ -f "$dir/state/PGREP_EMPTY" ]]; then exit 1; fi
echo 4242
exit 0
FAKE_PGREP
    cat > "$dir/bin/open" <<FAKE_OPEN
#!/usr/bin/env bash
{
    printf 'args: %s\n' "\$*"
    env | grep '^STS2_' | sort
} >> "$dir/state/opened.txt"
touch "$dir/state/PGREP_EMPTY"
exit 0
FAKE_OPEN
    printf '#!/bin/sh\nexit 0\n' > "$dir/bin/sleep"
    for tool in date env tr; do
        src="$(command -v "$tool")"
        [[ -x "$src" ]] && ln -sf "$src" "$dir/bin/$tool"
    done
    chmod +x "$dir/bin/"*
    printf '%s\n' "$dir"
}

test_begin "三端点已就绪 → 复用既有会话且不调用 open"
FAKE="$(new_fake_env)"
touch "$FAKE/state/PGREP_EMPTY"
run_bootstrap "$FAKE"
assert_eq "$RUN_RC" "0" "就绪会话应复用并退出 0"
assert_contains "$RUN_OUT" "复用" "应报告复用既有会话"
[[ -f "$FAKE/state/opened.txt" ]] && fail "复用路径不应调用 open" || pass "复用路径未调用 open"
rm -rf "$FAKE"

test_begin "无进程且无 bundle → 失败退出 1"
FAKE="$(new_fake_env)"
touch "$FAKE/state/PGREP_EMPTY" "$FAKE/state/NEVER_READY"
run_bootstrap "$FAKE"
assert_eq "$RUN_RC" "1" "bundle 缺失应退出 1"
assert_contains "$RUN_OUT" "未找到游戏 bundle" "应报告 bundle 缺失"
[[ -f "$FAKE/state/opened.txt" ]] && fail "bundle 缺失不应调用 open" || pass "bundle 缺失未调用 open"
rm -rf "$FAKE"

test_begin "无进程且有 bundle → open 拉起并注入调试 API env"
FAKE="$(new_fake_env)"
mkdir -p "$FAKE/game/SlayTheSpire2.app"
touch "$FAKE/state/PGREP_EMPTY"
printf '9' > "$FAKE/state/FAIL_N"
run_bootstrap "$FAKE"
assert_eq "$RUN_RC" "0" "拉起后就绪应退出 0"
assert_contains "$RUN_OUT" "bootstrap 完成" "应报告 bootstrap 完成"
assert_contains "$(cat "$FAKE/state/opened.txt")" "SlayTheSpire2.app" "应以 bundle 方式 open"
assert_contains "$(cat "$FAKE/state/opened.txt")" "STS2_API_PORT=8080" "应注入 STS2_API_PORT"
assert_contains "$(cat "$FAKE/state/opened.txt")" "STS2_ENABLE_DEBUG_ACTIONS=1" "应注入调试 API 开关"
rm -rf "$FAKE"

test_begin "stale 进程（宽限耗尽）→ 干净重启后再等就绪"
FAKE="$(new_fake_env)"
mkdir -p "$FAKE/game/SlayTheSpire2.app"
touch "$FAKE/state/NEVER_READY"
RUN_OUT=""
RUN_RC=""
run_bootstrap "$FAKE"
assert_eq "$RUN_RC" "1" "启动后仍未就绪应退出 1"
[[ -f "$FAKE/state/opened.txt" ]] && pass "stale 重启应调用 open" || fail "stale 重启未调用 open"
rm -rf "$FAKE"

test_begin "stale 进程且 ALLOW_RESTART=0 → 拒绝重启且不 open"
FAKE="$(new_fake_env)"
touch "$FAKE/state/NEVER_READY"
run_bootstrap "$FAKE" BOOTSTRAP_ALLOW_RESTART=0
assert_eq "$RUN_RC" "1" "拒绝重启应退出 1"
assert_contains "$RUN_OUT" "拒绝自动重启" "应报告拒绝自动重启"
[[ -f "$FAKE/state/opened.txt" ]] && fail "拒绝重启不应调用 open" || pass "拒绝重启未调用 open"
rm -rf "$FAKE"

test_begin "workflow 接线：bootstrap 仅在 env_check 失败后、retry 之前执行"
BOOT_LINE="$(grep -n 'id: game_bootstrap' "$WORKFLOW" | head -1 | cut -d: -f1)"
RETRY_LINE="$(grep -n 'id: env_check_retry' "$WORKFLOW" | head -1 | cut -d: -f1)"
COND_IN_RANGE=0
# bootstrap 自身的 if 条件必须落在其 id 与 env_check_retry 之间（区间内唯一的 failure 条件）
while IFS= read -r pair; do
    line="${pair%%:*}"
    cond="${pair#*:}"
    if (( line > BOOT_LINE && line < RETRY_LINE )) && [[ "$cond" == *"env_check.outcome == 'failure'"* ]]; then
        COND_IN_RANGE=1
    fi
done < <(grep -n "if: steps.env_check.outcome == 'failure'" "$WORKFLOW")
if [[ -n "$BOOT_LINE" && -n "$RETRY_LINE" && "$COND_IN_RANGE" -eq 1 ]] && (( BOOT_LINE < RETRY_LINE )); then
    pass "game_bootstrap 位于 env_check 之后、env_check_retry 之前，且条件为首次探测失败"
else
    fail "game_bootstrap 接线位置错误 (boot=$BOOT_LINE retry=$RETRY_LINE cond_in_range=$COND_IN_RANGE)"
fi

test_begin "外部 HTTP 探测必须带超时"
if grep -q -- '--max-time' "$BOOTSTRAP"; then
    pass "curl 探测带 --max-time"
else
    fail "curl 探测缺少 --max-time"
fi

echo
echo "nightly game bootstrap 测试完成：$((TEST_COUNT)) 用例，$FAIL_COUNT 失败"
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
