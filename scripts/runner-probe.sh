#!/usr/bin/env bash
# runner-probe.sh — 自托管 GitHub Actions Runner 状态探针（issue-24 T3）
#
# 每次执行输出一行 JSON（JSONL），用于 ≥7 天连续采集与四类归因：
#   本机网络 / 代理出口 / GitHub 上游 / 维护操作
# 设计原则：所有外部探测失败都降级为 unknown/false 并正常退出，绝不崩溃；
# 每个网络探测有超时，可安全高频调用（建议每 5–15 分钟一次）。
#
# 用法：
#   scripts/runner-probe.sh                 # 输出一行 JSON 到 stdout
#   PROBE_OUTPUT=/path/run.log scripts/runner-probe.sh   # 追加落盘
#
# 环境变量：
#   RUNNER_DIR      runner 安装目录（默认 $HOME/actions-runner）
#   REPO            GitHub 仓库（默认 crystepj-max/STS2-AUTOTEST）
#   PROXY_URL       代理地址（默认 http://127.0.0.1:7890，ClashX）
#   PROBE_OUTPUT    追加写入的文件（默认空 = 仅 stdout）
#   PROBE_GH_TIMEOUT gh 查询超时秒数（默认 8）
set -euo pipefail

REPO="${REPO:-crystepj-max/STS2-AUTOTEST}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
PROXY_URL="${PROXY_URL:-http://127.0.0.1:7890}"
PROBE_OUTPUT="${PROBE_OUTPUT:-}"
GH_TIMEOUT="${PROBE_GH_TIMEOUT:-8}"

TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# 带超时执行命令：成功时输出其 stdout；超时/失败返回非 0
run_with_timeout() {
    local timeout="$1"
    shift
    local tmp pid rc out
    tmp="$(mktemp)"
    "$@" >"$tmp" 2>&1 &
    pid=$!
    ( sleep "$timeout"; kill "$pid" 2>/dev/null || true ) &
    local killer=$!
    if wait "$pid"; then rc=0; else rc=$?; fi
    kill "$killer" 2>/dev/null || true
    out="$(cat "$tmp")"
    rm -f "$tmp"
    if [[ "$rc" -eq 0 ]]; then printf '%s' "$out"; fi
    return "$rc"
}

# --- 服务状态（svc.sh status 解析）---
service_state="not-installed"
if [[ -f "$RUNNER_DIR/svc.sh" ]]; then
    svc_out="$(cd "$RUNNER_DIR" && ./svc.sh status 2>/dev/null || true)"
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

# --- runner 进程（Runner.Listener）---
runner_pids="$(pgrep -f "Runner.Listener" 2>/dev/null | tr '\n' ' ' | sed 's/ $//' || true)"

# --- GitHub 侧状态（gh api，超时失败 → unknown）---
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
    gh_status="$(run_with_timeout "$GH_TIMEOUT" gh api "repos/$REPO/actions/runners" --paginate --jq "$jq_expr" 2>/dev/null || true)"
    if [[ -n "$gh_status" ]]; then
        github_online="$gh_status"
    fi
fi

# --- 代理本地端口可达（/dev/tcp，非阻塞即时失败）---
proxy_local_reachable=false
proxy_host="${PROXY_URL#http://}"
proxy_port="${proxy_host##*:}"
proxy_host="${proxy_host%%:*}"
if (exec 3<>"/dev/tcp/$proxy_host/$proxy_port") 2>/dev/null; then
    proxy_local_reachable=true
    exec 3>&- 2>/dev/null || true
fi

# --- 网络可达性（直连 / 经代理访问 GitHub，超时 5s）---
direct_github_reachable=false
proxy_github_reachable=false
exit_ip_direct=""
exit_ip_proxy=""

code="$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' https://api.github.com/zen 2>/dev/null || true)"
if [[ "$code" == "200" ]]; then
    direct_github_reachable=true
    exit_ip_direct="$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || true)"
fi

code="$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' -x "$PROXY_URL" https://api.github.com/zen 2>/dev/null || true)"
if [[ "$code" == "200" ]]; then
    proxy_github_reachable=true
    exit_ip_proxy="$(curl -s --max-time 5 -x "$PROXY_URL" https://api.ipify.org 2>/dev/null || true)"
fi

# --- 输出 JSONL ---
line="$(python3 - "$TS" "$service_state" "$runner_pids" "$github_online" "$proxy_local_reachable" "$direct_github_reachable" "$proxy_github_reachable" "$exit_ip_direct" "$exit_ip_proxy" <<'PY'
import json, sys
ts, state, pids, gh, proxy_local, direct, proxy_gh, ip_direct, ip_proxy = sys.argv[1:]
print(json.dumps({
    "ts": ts,
    "service_state": state,
    "runner_pids": pids,
    "github_online": gh,
    "proxy_local_reachable": proxy_local == "true",
    "direct_github_reachable": direct == "true",
    "proxy_github_reachable": proxy_gh == "true",
    "exit_ip_direct": ip_direct,
    "exit_ip_proxy": ip_proxy,
}))
PY
)"
echo "$line"
if [[ -n "$PROBE_OUTPUT" ]]; then
    echo "$line" >> "$PROBE_OUTPUT"
fi
