#!/usr/bin/env bash
# 共享测试辅助：fake runner 目录、断言、计数（issue-24 T2/T3/T4 脚本测试用）
set -euo pipefail

TEST_COUNT=0
FAIL_COUNT=0
CURRENT_TEST=""

# 断言失败则记录并继续；结束后 run-all.sh 汇总退出码
fail() {
    echo "  FAIL: $*" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

pass() {
    echo "  ok: $*"
}

assert_eq() {
    local actual="$1" expected="$2" msg="${3:-}"
    if [[ "$actual" == "$expected" ]]; then
        pass "$msg [${actual}]"
    else
        fail "$msg 期望=[${expected}] 实际=[${actual}]"
    fi
}

assert_contains() {
    local haystack="$1" needle="$2" msg="${3:-}"
    if [[ "$haystack" == *"$needle"* ]]; then
        pass "$msg"
    else
        fail "$msg 未包含 [${needle}]"
    fi
}

assert_ne() {
    local actual="$1" unexpected="$2" msg="${3:-}"
    if [[ "$actual" != "$unexpected" ]]; then
        pass "$msg [${actual}]"
    else
        fail "$msg 不应等于 [${unexpected}]"
    fi
}

# 记录用例开始
test_begin() {
    CURRENT_TEST="$1"
    echo "TEST: $1"
    TEST_COUNT=$((TEST_COUNT + 1))
}

# 创建 fake runner 目录，返回目录路径（stdout）
# 用法：FAKE_DIR="$(new_fake_runner <state>)"  state: running|stopped|not-installed
new_fake_runner() {
    local state="$1"
    local dir
    dir="$(mktemp -d "${TMPDIR:-/tmp}/fake-runner.XXXXXX")"
    cat > "$dir/svc.sh" <<'FAKE_SVC'
#!/usr/bin/env bash
# fake svc.sh — 镜像真实 svc.sh 的解析语义（exit 恒 0，输出格式一致）
LABEL="actions.runner.example.Chris-Mac-mini"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
STATE_FILE="$PWD/.fake-state"
CMD="${1:-}"

if [[ "$CMD" == "status" ]]; then
    if [[ ! -f "$STATE_FILE" ]]; then
        echo "status $LABEL:"
        echo
        echo "not installed"
        echo
        exit 0
    fi
    echo "status $LABEL:"
    echo
    echo "$PLIST"
    echo
    if [[ "$(cat "$STATE_FILE")" == "started" ]]; then
        echo "Started:"
        echo "40231 0 $LABEL"
        echo
    else
        echo "Stopped"
        echo
    fi
    exit 0
elif [[ "$CMD" == "stop" ]]; then
    echo "stopping $LABEL"
    echo "stopped" > "$STATE_FILE"
    "$0" status
    exit 0
elif [[ "$CMD" == "start" ]]; then
    echo "starting $LABEL"
    echo "started" > "$STATE_FILE"
    "$0" status
    exit 0
else
    echo "Usage: ./svc.sh [install, start, stop, status, uninstall]" >&2
    exit 1
fi
FAKE_SVC
    chmod +x "$dir/svc.sh"
    if [[ "$state" == "running" ]]; then
        echo "started" > "$dir/.fake-state"
    elif [[ "$state" == "stopped" ]]; then
        echo "stopped" > "$dir/.fake-state"
    fi
    echo "$dir"
}

# 运行 runner-ctl.sh，输出到 stdout，退出码返回
# 用法：run_ctl <fake_dir> <args...>   ；stdout 存到变量 CTL_OUT，退出码存 CTL_RC
# 隔离：默认注入 fake pgrep（Runner.Listener 存在，避免读到真实机器进程）；
# RUN_CTL_NO_PROCESS=1 时注入"无进程"的 fake pgrep（R3 反例用）。
CTL_OUT=""
CTL_RC=0
run_ctl() {
    local dir="$1"
    shift
    local out rc fake_bin pgrep_body
    fake_bin="$(mktemp -d "${TMPDIR:-/tmp}/ctl-bin.XXXXXX")"
    if [[ "${RUN_CTL_NO_PROCESS:-0}" == "1" ]]; then
        ps_body='exit 1'
    else
        # 输出与 runner-ctl 新检测匹配：ps -eo args 且命令行含 $dir/bin/Runner.Listener
        ps_body="printf '%s\\\\n' '  1 1 /sbin/launchd' '40231 80357 $dir/bin/Runner.Listener run --startuptype service'"
    fi
    cat > "$fake_bin/ps" <<FAKE_PS
#!/usr/bin/env bash
$ps_body
FAKE_PS
    chmod +x "$fake_bin/ps"
    out="$(cd /tmp && RUNNER_DIR="$dir" PATH="$fake_bin:/usr/bin:/bin" bash "$SCRIPT_DIR/../runner-ctl.sh" "$@" 2>&1)" || rc=$?
    rc="${rc:-0}"
    CTL_OUT="$out"
    CTL_RC="$rc"
    rm -rf "$fake_bin"
}
