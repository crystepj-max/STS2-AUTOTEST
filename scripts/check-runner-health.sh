#!/usr/bin/env bash
# check-runner-health.sh — 自托管 GitHub Actions Runner 健康检查（issue-24 T4）
#
# 在业务验收开始前判定 runner 是否可用（不依赖游戏环境）。
# 判定：服务状态为 running（svc.sh Started）+ 至少一条 GitHub 网络链路可达
# （直连或经 ClashX 代理）。GitHub 侧 gh 查询失败不计为不可用——
# 能领取 job 本身已证明在线，避免 gh 缺失误伤判定。
#
# 用法：
#   scripts/check-runner-health.sh            # 人类可读输出
#   scripts/check-runner-health.sh --json     # JSON 输出（workflow 前置 step 用）
#
# 退出码：0=HEALTHY（可接收任务） 1=UNHEALTHY（不可用） 2=检查本身错误
#
# 环境变量：RUNNER_DIR（默认 $HOME/actions-runner）、PROXY_URL（默认 http://127.0.0.1:7890）
set -euo pipefail

RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
PROXY_URL="${PROXY_URL:-http://127.0.0.1:7890}"
MODE="${1:-}"

# --- 服务状态 ---
service_state="not-installed"
if [[ ! -d "$RUNNER_DIR" ]]; then
    service_state="not-installed"
elif [[ -f "$RUNNER_DIR/svc.sh" ]]; then
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

# --- 网络链路（超时 5s，失败 → false）---
direct_reachable=false
proxy_reachable=false
code="$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' https://api.github.com/zen 2>/dev/null || true)"
[[ "$code" == "200" ]] && direct_reachable=true
code="$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' -x "$PROXY_URL" https://api.github.com/zen 2>/dev/null || true)"
[[ "$code" == "200" ]] && proxy_reachable=true

# --- 判定 ---
reasons=""
[[ "$service_state" == "running" ]] || reasons="service_state=${service_state}"
if [[ "$direct_reachable" == "false" && "$proxy_reachable" == "false" ]]; then
    reasons="${reasons:+$reasons; }network=unreachable(direct=${direct_reachable},proxy=${proxy_reachable})"
fi
if [[ -z "$reasons" ]]; then
    healthy=true
    exit_code=0
    summary="HEALTHY: runner 可接收任务（service=running, direct=${direct_reachable}, proxy=${proxy_reachable}）"
else
    healthy=false
    exit_code=1
    summary="UNHEALTHY: $reasons"
fi

if [[ "$MODE" == "--json" ]]; then
    python3 - "$healthy" "$service_state" "$direct_reachable" "$proxy_reachable" "$reasons" <<'PY'
import json, sys
healthy, state, direct, proxy, reasons = sys.argv[1:]
print(json.dumps({
    "healthy": healthy == "true",
    "service_state": state,
    "direct_github_reachable": direct == "true",
    "proxy_github_reachable": proxy == "true",
    "reasons": reasons,
}))
PY
else
    echo "$summary"
fi

exit "$exit_code"
