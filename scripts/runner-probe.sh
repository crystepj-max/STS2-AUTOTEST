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
#   PROBE_OP        维护操作标记（如 manual-stop/manual-start，写入 op 字段）
#   PROBE_STATE_FILE 上次采样状态文件（默认 ~/.sts2-runner-probe/.probe-state.json，
#                   用于推导 disconnect/recover/service-stopped/service-started 事件）
set -euo pipefail

REPO="${REPO:-crystepj-max/STS2-AUTOTEST}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
PROXY_URL="${PROXY_URL:-http://127.0.0.1:7890}"
PROBE_OUTPUT="${PROBE_OUTPUT:-}"
GH_TIMEOUT="${PROBE_GH_TIMEOUT:-8}"
PROBE_OP="${PROBE_OP:-}"
PROBE_STATE_FILE="${PROBE_STATE_FILE:-$HOME/.sts2-runner-probe/.probe-state.json}"

TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# 临时文件登记 + EXIT trap 清理（中断/SIGINT 也不残留）
PROBE_TMP_FILES=()
cleanup_tmp() {
    local f
    for f in "${PROBE_TMP_FILES[@]:-}"; do
        rm -f "$f" 2>/dev/null || true
    done
}
trap cleanup_tmp EXIT

# 递归终止进程树（含子进程，防止超时后残留孤儿）
kill_tree() {
    local pid="$1" child
    for child in $(pgrep -P "$pid" 2>/dev/null || true); do
        kill_tree "$child"
    done
    kill "$pid" 2>/dev/null || true
}

# 带超时执行命令：成功时输出其 stdout；超时/失败返回非 0
run_with_timeout() {
    local timeout="$1"
    shift
    local tmp pid rc out killer
    tmp="$(mktemp)"
    PROBE_TMP_FILES+=("$tmp")
    "$@" >"$tmp" 2>&1 &
    pid=$!
    # 后台杀手：超时后递归终止整个进程树（主进程 + 子进程）
    # killer 重定向 stdout/stderr：否则其 sleep 期间持有调用方管道，命令替换会无谓等待
    ( sleep "$timeout"; kill_tree "$pid" ) >/dev/null 2>&1 &
    killer=$!
    if wait "$pid"; then rc=0; else rc=$?; fi
    kill_tree "$killer"
    out="$(cat "$tmp")"
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
# 同时记录 online/offline 与 busy（任务领取/忙闲），供四类归因（issue-24 R2）。
github_online="unknown"
github_busy="unknown"
if command -v gh &>/dev/null; then
    runner_name=""
    if [[ -f "$RUNNER_DIR/.runner" ]]; then
        runner_name="$(grep -o '"agentName": *"[^"]*"' "$RUNNER_DIR/.runner" | sed 's/.*: *"//;s/"//' || true)"
    fi
    if [[ -n "$runner_name" ]]; then
        jq_expr=".runners[] | select(.name==\"$runner_name\") | [.status, (.busy|tostring)] | @tsv"
    else
        jq_expr=".runners[] | [.status, (.busy|tostring)] | @tsv"
    fi
    gh_row="$(run_with_timeout "$GH_TIMEOUT" gh api "repos/$REPO/actions/runners" --paginate --jq "$jq_expr" 2>/dev/null | head -1 || true)"
    if [[ -n "$gh_row" ]]; then
        github_online="$(printf '%s' "$gh_row" | cut -f1)"
        github_busy="$(printf '%s' "$gh_row" | cut -f2)"
    fi
fi

# --- 事件推导：与上次采样比较（断线/恢复/启停，issue-24 R2）---
transition="init"
if [[ -f "$PROBE_STATE_FILE" ]]; then
    prev_state="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("service_state",""))' "$PROBE_STATE_FILE" 2>/dev/null || true)"
    prev_online="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("github_online",""))' "$PROBE_STATE_FILE" 2>/dev/null || true)"
    if [[ "$prev_online" == "online" && "$github_online" == "offline" ]]; then
        transition="disconnect"
    elif [[ "$prev_online" == "offline" && "$github_online" == "online" ]]; then
        transition="recover"
    elif [[ "$prev_state" == "running" && "$service_state" == "stopped" ]]; then
        transition="service-stopped"
    elif [[ "$prev_state" == "stopped" && "$service_state" == "running" ]]; then
        transition="service-started"
    else
        transition="steady"
    fi
fi
# 写回本次采样（临时文件 + mv 原子替换，避免中断留下半写状态）
mkdir -p "$(dirname "$PROBE_STATE_FILE")" 2>/dev/null || true
state_tmp="$(mktemp "${TMPDIR:-/tmp}/probe-state.XXXXXX")"
PROBE_TMP_FILES+=("$state_tmp")
python3 - "$state_tmp" "$service_state" "$github_online" <<'PY'
import json, sys
path, state, online = sys.argv[1:]
json.dump({"service_state": state, "github_online": online}, open(path, "w"))
PY
mv -f "$state_tmp" "$PROBE_STATE_FILE" 2>/dev/null || true

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
# 直连探测必须显式绕过环境代理（--noproxy '*'）：若继承 http_proxy 等，
# 「直连」与「经代理」会实际走同一条路径，7 天归因数据将失真（issue-24 R1）。
direct_github_reachable=false
proxy_github_reachable=false
exit_ip_direct=""
exit_ip_proxy=""

code="$(curl -s --max-time 5 --noproxy '*' -o /dev/null -w '%{http_code}' https://api.github.com/zen 2>/dev/null || true)"
if [[ "$code" == "200" ]]; then
    direct_github_reachable=true
    exit_ip_direct="$(curl -s --max-time 5 --noproxy '*' https://api.ipify.org 2>/dev/null || true)"
fi

code="$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' -x "$PROXY_URL" https://api.github.com/zen 2>/dev/null || true)"
if [[ "$code" == "200" ]]; then
    proxy_github_reachable=true
    exit_ip_proxy="$(curl -s --max-time 5 -x "$PROXY_URL" https://api.ipify.org 2>/dev/null || true)"
fi

# --- 输出 JSONL ---
line="$(python3 - "$TS" "$service_state" "$runner_pids" "$github_online" "$github_busy" "$transition" "$PROBE_OP" "$proxy_local_reachable" "$direct_github_reachable" "$proxy_github_reachable" "$exit_ip_direct" "$exit_ip_proxy" <<'PY'
import json, sys
ts, state, pids, gh, busy, trans, op, proxy_local, direct, proxy_gh, ip_direct, ip_proxy = sys.argv[1:]
# busy 为 "true"/"false" 字符串 → JSON 布尔；unknown 保持 null
busy_json = {"true": True, "false": False}.get(busy)
print(json.dumps({
    "ts": ts,
    "service_state": state,
    "runner_pids": pids,
    "github_online": gh,
    "github_busy": busy_json,
    "transition": trans,
    "op": op,
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
