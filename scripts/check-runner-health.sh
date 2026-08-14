#!/usr/bin/env bash
# check-runner-health.sh — 自托管 GitHub Actions Runner 健康检查（issue-24 T4）
#
# 在业务验收开始前判定 runner 是否可用（不依赖游戏环境）。
# 判定（issue-24 R3）：真实状态三路一致才报 HEALTHY——
#   1. 服务状态 running（svc.sh Started）
#   2. 真实进程存在（Runner.Listener）
#   3. GitHub 侧 runner 状态 online（gh api runners）
# 外加至少一条 GitHub 网络链路可达（直连或经 ClashX 代理）。
# 反例：服务标记 started 但进程缺失 / GitHub 侧 offline → UNHEALTHY，
# 避免“服务假启动或连接失效仍误报可接任务”。
# GitHub 侧 gh 缺失/查询失败计为 unknown（不计为不可用），避免 gh 缺失误伤判定。
#
# 用法：
#   scripts/check-runner-health.sh            # 人类可读输出
#   scripts/check-runner-health.sh --json     # JSON 输出（workflow 前置 step 用）
#
# 退出码：0=HEALTHY（可接收任务） 1=UNHEALTHY（不可用） 2=检查本身错误
#
# 环境变量：RUNNER_DIR（默认 $HOME/actions-runner）、PROXY_URL（默认 http://127.0.0.1:7890）、
#           REPO（默认 crystepj-max/STS2-AUTOTEST，GitHub 侧查询用）
set -euo pipefail

RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
PROXY_URL="${PROXY_URL:-http://127.0.0.1:7890}"
REPO="${REPO:-crystepj-max/STS2-AUTOTEST}"
HEALTH_CMD_TIMEOUT="${HEALTH_CMD_TIMEOUT:-10}"
MODE="${1:-}"

# 带超时执行命令（svc.sh/gh 可能挂起，逐项限时；超时后递归回收子进程）
run_with_timeout() {
    local timeout="$1"
    shift
    local tmp pid rc out killer
    tmp="$(mktemp)"
    "$@" >"$tmp" 2>&1 &
    pid=$!
    # killer 重定向 stdout/stderr：否则其 sleep 期间持有调用方管道，命令替换会无谓等待
    ( sleep "$timeout"; pkill -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true ) >/dev/null 2>&1 &
    killer=$!
    if wait "$pid"; then rc=0; else rc=$?; fi
    pkill -P "$killer" 2>/dev/null || true
    kill "$killer" 2>/dev/null || true
    out="$(cat "$tmp")"
    rm -f "$tmp"
    if [[ "$rc" -eq 0 ]]; then printf '%s' "$out"; fi
    return "$rc"
}

# --- 服务状态 ---
service_state="not-installed"
if [[ ! -d "$RUNNER_DIR" ]]; then
    service_state="not-installed"
elif [[ -f "$RUNNER_DIR/svc.sh" ]]; then
    svc_out="$(cd "$RUNNER_DIR" && run_with_timeout "$HEALTH_CMD_TIMEOUT" ./svc.sh status 2>/dev/null || true)"
    if [[ "$svc_out" == *"Started:"* ]]; then
        service_state="running"
    elif [[ "$svc_out" == *"Stopped"* ]]; then
        service_state="stopped"
    elif [[ "$svc_out" == *"not installed"* ]]; then
        service_state="not-installed"
    else
        service_state="unknown"
    fi
fi

# --- 真实进程（Runner.Listener，issue-24 R3）---
process_present=false
if command -v pgrep &>/dev/null; then
    if pgrep -f "Runner.Listener" >/dev/null 2>&1; then
        process_present=true
    fi
else
    # pgrep 缺失时无法核验进程：按 R3 一致性要求报 UNHEALTHY（github 侧由 gh 兜底）
    process_present=false
fi
# 诊断（仅失败时输出，不污染正常路径）
if [[ "$process_present" == "false" ]]; then
    echo "diag: pgrep='$(command -v pgrep 2>/dev/null || echo MISSING)'" >&2
    echo "diag: pgrep -f 'Runner.Listener' → $(pgrep -f 'Runner.Listener' 2>&1 | head -3 | tr '\n' ' ')" >&2
    echo "diag: ps runner 进程树 → $(ps -eo pid,ppid,args 2>/dev/null | grep -iE 'Runner|actions-runner' | grep -v grep | head -5 | tr '\n' ' | ')" >&2
    echo "diag: HOME=$HOME RUNNER_DIR=$RUNNER_DIR" >&2
fi

# --- GitHub 侧状态（gh api runners，失败 → unknown，issue-24 R3）---
github_online="unknown"
if command -v gh &>/dev/null; then
    runner_name=""
    if [[ -f "$RUNNER_DIR/.runner" ]]; then
        runner_name="$(grep -o '"agentName": *"[^"]*"' "$RUNNER_DIR/.runner" | sed 's/.*: *"//;s/"//' || true)"
    fi
    if [[ -n "$runner_name" ]]; then
        jq_expr=".runners[] | select(.name==\"$runner_name\") | .status"
    else
        jq_expr=".runners[] | .status"
    fi
    gh_status="$(run_with_timeout "$HEALTH_CMD_TIMEOUT" gh api "repos/$REPO/actions/runners" --paginate --jq "$jq_expr" 2>/dev/null | head -1 || true)"
    if [[ -n "$gh_status" ]]; then
        github_online="$gh_status"
    fi
fi

# --- 网络链路（超时 5s，失败 → false）---
# 直连必须显式绕过环境代理（--noproxy '*'）：CI job 环境注入 HTTP_PROXY 时，
# 不加 --noproxy 的「直连」实际会走代理，与代理路径无法区分（issue-24 R1 同源）
direct_reachable=false
proxy_reachable=false
code="$(curl -s --max-time 5 --noproxy '*' -o /dev/null -w '%{http_code}' https://api.github.com/zen 2>/dev/null || true)"
[[ "$code" == "200" ]] && direct_reachable=true
code="$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' -x "$PROXY_URL" https://api.github.com/zen 2>/dev/null || true)"
[[ "$code" == "200" ]] && proxy_reachable=true

# --- 判定（三路一致 + 网络可达才 HEALTHY）---
reasons=""
[[ "$service_state" == "running" ]] || reasons="service_state=${service_state}"
[[ "$process_present" == "true" ]] || reasons="${reasons:+$reasons; }process=missing(Runner.Listener)"
[[ "$github_online" == "online" ]] || reasons="${reasons:+$reasons; }github=${github_online}"
if [[ "$direct_reachable" == "false" && "$proxy_reachable" == "false" ]]; then
    reasons="${reasons:+$reasons; }network=unreachable(direct=${direct_reachable},proxy=${proxy_reachable})"
fi
if [[ -z "$reasons" ]]; then
    healthy=true
    exit_code=0
    summary="HEALTHY: runner 可接收任务（service=running, process=present, github=online, direct=${direct_reachable}, proxy=${proxy_reachable}）"
else
    healthy=false
    exit_code=1
    summary="UNHEALTHY: $reasons"
fi

if [[ "$MODE" == "--json" ]]; then
    python3 - "$healthy" "$service_state" "$process_present" "$github_online" "$direct_reachable" "$proxy_reachable" "$reasons" <<'PY'
import json, sys
healthy, state, process, gh, direct, proxy, reasons = sys.argv[1:]
print(json.dumps({
    "healthy": healthy == "true",
    "service_state": state,
    "process_present": process == "true",
    "github_online": gh,
    "direct_github_reachable": direct == "true",
    "proxy_github_reachable": proxy == "true",
    "reasons": reasons,
}))
PY
else
    echo "$summary"
fi

exit "$exit_code"
