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
#   PROBE_OP        维护操作标记（如 manual-stop/manual-start，写入 op 字段；
#                   手动运行时可用；定时采集时由 PROBE_OPS_FILE 自动识别）
#   PROBE_OPS_FILE  runner-ctl stop/start 写入的维护操作日志（默认
#                   ~/.sts2-runner-probe/ops.jsonl；最近 PROBE_OP_WINDOW 秒内
#                   的操作自动标记到 op 字段，区分人工维护 vs 意外中断）
#   PROBE_STATE_FILE 上次采样状态文件（默认 ~/.sts2-runner-probe/.probe-state.json，
#                   用于推导 disconnect/recover/service-stopped/service-started 事件）
set -euo pipefail

REPO="${REPO:-crystepj-max/STS2-AUTOTEST}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
PROXY_URL="${PROXY_URL:-http://127.0.0.1:7890}"
PROBE_OUTPUT="${PROBE_OUTPUT:-}"
GH_TIMEOUT="${PROBE_GH_TIMEOUT:-8}"
PROBE_OP="${PROBE_OP:-}"
PROBE_OPS_FILE="${PROBE_OPS_FILE:-$HOME/.sts2-runner-probe/ops.jsonl}"
PROBE_OP_WINDOW="${PROBE_OP_WINDOW:-900}"
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
# 收集进程树全部 PID（含后代；父进程退出前保存完整集合，供后续统一 TERM/KILL）
collect_tree() {
    local pid="$1"
    local child
    echo "$pid"
    for child in $(pgrep -P "$pid" 2>/dev/null || true); do
        collect_tree "$child"
    done
}

kill_tree() {
    local pid="$1" sig="${2:-TERM}"
    local pids
    # 父进程退出前保存完整 PID 集合：子进程被 reparent 后 pgrep -P 找不到，
    # 必须在首次遍历时全部收集（第二次升级 SIGKILL 时仍能命中）。
    pids="$(collect_tree "$pid")"
    local p
    for p in $pids; do
        kill "-$sig" "$p" 2>/dev/null || true
    done
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
    # 后台杀手：超时后 SIGTERM 递归终止进程树，宽限期后 SIGKILL 升级
    # （拒绝 SIGTERM 的进程也被强制终止，保证探针按时输出）；
    # killer 重定向 stdout/stderr：否则其 sleep 期间持有调用方管道，命令替换会无谓等待
    ( sleep "$timeout"; kill_tree "$pid" TERM; sleep 1; kill_tree "$pid" KILL ) >/dev/null 2>&1 &
    killer=$!
    if wait "$pid"; then rc=0; else rc=$?; fi
    kill_tree "$killer" KILL
    out="$(cat "$tmp")"
    if [[ "$rc" -eq 0 ]]; then printf '%s' "$out"; fi
    return "$rc"
}

# --- 服务状态（svc.sh status 解析，带超时：svc.sh 挂起时不阻塞探针输出）---
service_state="not-installed"
if [[ -f "$RUNNER_DIR/svc.sh" ]]; then
    svc_out="$(cd "$RUNNER_DIR" && run_with_timeout "$GH_TIMEOUT" ./svc.sh status 2>/dev/null || true)"
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
    # gh 默认继承 HTTP_PROXY 环境变量；代理抖动时查询必失败。
    # 显式清空代理变量强制 gh 直连（直连由 --noproxy 实证可达，R1 同源）。
    gh_row="$(run_with_timeout "$GH_TIMEOUT" env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy gh api "repos/$REPO/actions/runners" --paginate --jq "$jq_expr" 2>/dev/null | head -1 || true)"
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
    # 服务启停优先于网络转换（P1）：stop/start 后 GitHub 侧状态 60-90s 才同步，
    # 下次采样会同时看到 running→stopped 与 online→offline；若先判 disconnect，
    # 维护操作会被误归网络事件。service 变化是本机确定性事件，优先判定。
    if [[ "$prev_state" == "running" && "$service_state" == "stopped" ]]; then
        transition="service-stopped"
    elif [[ "$prev_state" == "stopped" && "$service_state" == "running" ]]; then
        transition="service-started"
    elif [[ "$prev_online" == "online" && "$github_online" == "offline" ]]; then
        transition="disconnect"
    elif [[ "$prev_online" == "offline" && "$github_online" == "online" ]]; then
        transition="recover"
    else
        transition="steady"
    fi
fi
# 写回本次采样（临时文件 + mv 原子替换，避免中断留下半写状态）
# 临时文件必须在目标同目录：跨文件系统 mv 会失败（TMPDIR 可能与目标不同卷）
mkdir -p "$(dirname "$PROBE_STATE_FILE")" 2>/dev/null || true
state_tmp="$(mktemp "$(dirname "$PROBE_STATE_FILE")/.probe-state.XXXXXX")"
PROBE_TMP_FILES+=("$state_tmp")
python3 - "$state_tmp" "$service_state" "$github_online" <<'PY'
import json, sys
path, state, online = sys.argv[1:]
json.dump({"service_state": state, "github_online": online}, open(path, "w"))
PY
mv -f "$state_tmp" "$PROBE_STATE_FILE" 2>/dev/null || true

# --- 维护操作识别（R2/T3）：runner-ctl stop/start 写入 ops.jsonl。
# 游标模式：读取自上次采样以来新增的全部操作（cursor 记录已消费行数），
# 间隔内多次操作（如 stop→start）都识别，不依赖单条与 transition 匹配。
# 显式 PROBE_OP 优先；否则用游标消费新操作。
if [[ -z "$PROBE_OP" && -f "$PROBE_OPS_FILE" ]]; then
    # 游标文件（与 ops 同目录）：记录已消费行数
    OPS_CURSOR="${PROBE_OPS_FILE}.cursor"
    cursor="$(cat "$OPS_CURSOR" 2>/dev/null || echo 0)"
    # 同快照读取：一次读入全部内容并按行数推进游标——避免 tail 读取与 wc -l
    # 之间新操作写入导致游标跳过未读行（P2 游标一致性）。
    # cat 带硬超时（ops 文件系统 I/O 卡顿时探针仍按时输出 JSON）
    OPS_SNAPSHOT="$(run_with_timeout "$GH_TIMEOUT" cat "$PROBE_OPS_FILE" 2>/dev/null || true)"
    OPS_LINES="$(printf '%s\n' "$OPS_SNAPSHOT" | wc -l | tr -d ' ')"
    # 从快照提取自游标之后的新操作（行号 > cursor）
    OPS_TAIL="$(printf '%s\n' "$OPS_SNAPSHOT" | tail -n +"$((cursor + 1))" 2>/dev/null || true)"
    if [[ -n "$OPS_TAIL" ]]; then
        # 解析新增操作（时间窗口内 + 与 transition 匹配的才填入 op）
        NEW_OPS="$(printf '%s' "$OPS_TAIL" | python3 -c 'import json,sys,datetime
lines=[l for l in sys.stdin.read().splitlines() if l.strip()]
ops=[]
for l in lines:
    try:
        d=json.loads(l)
        ts=d.get("ts",""); op=d.get("op","")
        if ts and op:
            t=datetime.datetime.fromisoformat(ts.replace("Z","+00:00"))
            ops.append((int(t.timestamp()), op))
    except Exception:
        pass
for ts,op in ops:
    print(f"{ts} {op}")' 2>/dev/null || true)"
        # 时间窗口内且与当前 transition 匹配的操作
        NOW_EPOCH="$(date +%s)"
        while IFS= read -r op_line; do
            [[ -z "$op_line" ]] && continue
            OP_TS_EPOCH="${op_line%% *}"
            OP_NAME="${op_line##* }"
            if [[ "$(( NOW_EPOCH - OP_TS_EPOCH ))" -ge 0 ]] && [[ "$(( NOW_EPOCH - OP_TS_EPOCH ))" -le "$PROBE_OP_WINDOW" ]]; then
                case "$OP_NAME:$transition" in
                    manual-stop:service-stopped|manual-start:service-started)
                        PROBE_OP="$OP_NAME"
                        ;;
                esac
            fi
        done <<< "$NEW_OPS"
    fi
    # 更新游标（用同快照的行数——已消费位置与实际读取绑定，不跳过新写入）
    printf '%s\n' "${OPS_LINES:-0}" > "$OPS_CURSOR" 2>/dev/null || true
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

# --- 独立联网探针（P2 四类归因）：与 GitHub 无关的端点 + DNS 解析 ---
# GitHub 两条路径同时失败时，用非 GitHub 端点区分「本机网络故障」与
# 「GitHub 上游故障」：联网探针可达 → 本机网络正常，问题在 GitHub 上游；
# 联网探针也不可达 → 本机/出口网络故障。DNS 解析失败可进一步定位。
internet_reachable=false
dns_resolvable=false
code="$(curl -s --max-time 5 --noproxy '*' -o /dev/null -w '%{http_code}' https://api.ipify.org 2>/dev/null || true)"
if [[ "$code" == "200" ]]; then
    internet_reachable=true
fi
# DNS 探测也带硬超时（DNS 卡住时探针仍按时输出 JSON，降级 dns_resolvable=false）
if run_with_timeout "$GH_TIMEOUT" getent hosts api.ipify.org >/dev/null 2>&1 \
    || run_with_timeout "$GH_TIMEOUT" nslookup api.ipify.org >/dev/null 2>&1; then
    dns_resolvable=true
fi

# --- 输出 JSONL ---
line="$(python3 - "$TS" "$service_state" "$runner_pids" "$github_online" "$github_busy" "$transition" "$PROBE_OP" "$proxy_local_reachable" "$direct_github_reachable" "$proxy_github_reachable" "$exit_ip_direct" "$exit_ip_proxy" "$internet_reachable" "$dns_resolvable" <<'PY'
import json, sys
ts, state, pids, gh, busy, trans, op, proxy_local, direct, proxy_gh, ip_direct, ip_proxy, internet, dns = sys.argv[1:]
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
    "internet_reachable": internet == "true",
    "dns_resolvable": dns == "true",
}))
PY
)"
echo "$line"
if [[ -n "$PROBE_OUTPUT" ]]; then
    echo "$line" >> "$PROBE_OUTPUT"
fi
