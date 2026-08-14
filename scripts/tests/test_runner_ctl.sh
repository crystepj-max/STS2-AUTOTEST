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

echo
echo "runner-ctl 测试完成：$((TEST_COUNT)) 用例，$FAIL_COUNT 失败"
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
