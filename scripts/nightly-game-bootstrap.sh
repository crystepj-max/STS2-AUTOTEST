#!/usr/bin/env bash
# nightly-game-bootstrap.sh — 夜间回归游戏会话保障（issue #66）。
#
# 背景：ci-nightly.yml Phase 0 只探测不拉起（scripts/nightly-env-check.sh），
# 2026-08-28 起夜间机器上无运行中的游戏，nightly 连续 10 晚 BLOCKED。
# 本脚本在 env_check 首次失败后、重试探针之前执行，幂等建立可用的
# STS2-Agent 游戏会话；拉起失败仍由 env_check_retry 判 BLOCKED，分类语义不变。
#
# 就绪契约（对齐 sts2-start-game 技能）：/health、/state、/actions/available
# 三个 GET 端点全部成功才算就绪；sts2 ping 仅诊断，不作成功依据。
# 启动方式（对齐 core/lifecycle.py）：macOS 经 `steam://run/<APP_ID>` 由 Steam 亲自
# 拉起；绝不 Popen 内层二进制，也不直开 .app —— `open` 不向应用传递环境变量（注入
# STS2_API_PORT 之类的写法是静默失效的），且 Steam 客户端已在运行时直开 .app 会撞上
# "Cannot create IPC pipe to Steam client process" 弹窗退出。
#
# 用法：scripts/nightly-game-bootstrap.sh
#
# 环境变量：
#   STS2_GAME_DIR            游戏安装目录（默认 Steam macOS 常见路径）
#   STS2_STEAM_APP_ID        Steam 应用 ID（默认 2868840，用于 steam://run）
#   STS2_AGENT_BASE_URL      Agent 基地址（默认 http://127.0.0.1:8080）
#   BOOTSTRAP_TIMEOUT        启动后等待三端点就绪的总预算秒数（默认 180）
#   BOOTSTRAP_READY_GRACE    进程已存在但未就绪时的宽限等待秒数（默认 30）
#   BOOTSTRAP_ALLOW_RESTART  1=stale 进程先干净关闭再重启（默认）；
#                            0=不自动重启，直接失败（人工排查场景）
#   BOOTSTRAP_KILL_WAIT      TERM 后等待进程退出的秒数（默认 15）
#   STS2_GAME_LOGS_DIR       游戏引擎日志目录（默认 ~/Library/Application
#                            Support/SlayTheSpire2/logs），超时取证用
#
# 退出码：0=三端点就绪 1=未就绪（原因见输出）
# 超时失败按三类输出（issue #99），便于区分机器侧问题：
#   进程从未出现（open 返回成功但 LaunchServices 未拉起）/
#   进程存在但端点未就绪（游戏卡在启动早期）/
#   进程出现后消失（启动即退出或崩溃）；
# 并自动落盘取证：pgrep 快照、近期崩溃报告、Steam 与游戏引擎日志尾部。
set -uo pipefail

GAME_DIR="${STS2_GAME_DIR:-$HOME/Library/Application Support/Steam/steamapps/common/Slay the Spire 2}"
BUNDLE="$GAME_DIR/SlayTheSpire2.app"
APP_ID="${STS2_STEAM_APP_ID:-2868840}"
GAME_LOGS_DIR="${STS2_GAME_LOGS_DIR:-$HOME/Library/Application Support/SlayTheSpire2/logs}"
BASE_URL="${STS2_AGENT_BASE_URL:-http://127.0.0.1:8080}"
TOTAL_TIMEOUT="${BOOTSTRAP_TIMEOUT:-180}"
READY_GRACE="${BOOTSTRAP_READY_GRACE:-30}"
ALLOW_RESTART="${BOOTSTRAP_ALLOW_RESTART:-1}"
KILL_WAIT="${BOOTSTRAP_KILL_WAIT:-15}"
PROBE_HTTP_TIMEOUT=3
POLL_INTERVAL=3

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# 三端点全部 2xx 才算就绪；任何单项失败只记警告，不提前退出。
probe_ready() {
    local path status
    for path in /health /state /actions/available; do
        status="$(curl -s -o /dev/null -w '%{http_code}' \
            --max-time "$PROBE_HTTP_TIMEOUT" "${BASE_URL}${path}" 2>/dev/null || true)"
        if [[ ! "$status" =~ ^2 ]]; then
            log "⚠️ 端点未就绪 ${path} → ${status:-unreachable}"
            return 1
        fi
    done
    log "✅ Agent 三端点就绪 (${BASE_URL})"
    return 0
}

# 精确匹配游戏二进制完整路径，避免误杀同名无关进程。
find_game_pids() {
    pgrep -f "SlayTheSpire2.app/Contents/MacOS/Slay the Spire 2" 2>/dev/null || true
}

stop_game() {
    local pids pid waited=0
    pids="$(find_game_pids)"
    [[ -z "$pids" ]] && return 0
    log "🛑 干净关闭 stale 游戏进程: $(echo "$pids" | tr '\n' ' ')"
    for pid in $pids; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    while (( waited < KILL_WAIT )); do
        [[ -z "$(find_game_pids)" ]] && break
        sleep 1
        waited=$((waited + 1))
    done
    for pid in $(find_game_pids); do
        log "⚠️ TERM 未退出，升级 KILL: pid=$pid"
        kill -KILL "$pid" 2>/dev/null || true
    done
    sleep "$POLL_INTERVAL"
}

launch_game() {
    # 每次尝试都记录 open 退出码：#99 的「进程从未出现」归因依赖这一行。
    local open_rc=0
    # 首选让 Steam 亲自拉起：此时游戏的 Steamworks 握手必然成立。
    open -b com.valvesoftware.steam "steam://run/${APP_ID}" 2>/dev/null || open_rc=$?
    log "open 退出码: $open_rc (steam://run via com.valvesoftware.steam)"
    if [[ "$open_rc" == "0" ]]; then
        log "🚀 经 Steam 启动游戏: steam://run/${APP_ID}"
        return 0
    fi
    log "⚠️ Steam bundle URL 启动失败，改用通用 steam:// URL"
    open_rc=0
    open "steam://run/${APP_ID}" 2>/dev/null || open_rc=$?
    log "open 退出码: $open_rc (steam://run via URL handler)"
    if [[ "$open_rc" == "0" ]]; then
        log "🚀 经 Steam 启动游戏（URL 处理器）: steam://run/${APP_ID}"
        return 0
    fi
    if [[ ! -d "$BUNDLE" ]]; then
        log "❌ 未找到游戏 bundle: ${BUNDLE}（可用 STS2_GAME_DIR 覆盖）"
        return 1
    fi
    # 兜底：只有 Steam 压根没起来时才可能握手成功（游戏会自行带起 Steam）。
    # 不在此注入 STS2_API_PORT 等变量：`open` 经 Launch Services 启动应用时不传递
    # 环境变量，写了也是静默失效。
    log "⚠️ Steam URL 全部失败，兜底直开 bundle（Steam 未运行时才可用）: $BUNDLE"
    open_rc=0
    open "$BUNDLE" || open_rc=$?
    log "open 退出码: $open_rc (bundle fallback)"
    return "$open_rc"
}

# 启动后轮询三端点直到总预算耗尽；同步观察游戏进程（issue #99）。
# 返回：0=就绪 1=进程存在但端点未就绪 2=进程出现后消失 3=进程从未出现。
wait_ready() {
    local waited=0 seen=0 pids
    while (( waited < TOTAL_TIMEOUT )); do
        pids="$(find_game_pids)"
        if [[ -z "$pids" && "$seen" == "1" ]]; then
            log "⚠️ 游戏进程启动后消失 (elapsed=${waited}s)"
            return 2
        fi
        if [[ -n "$pids" && "$seen" == "0" ]]; then
            seen=1
            log "ℹ️ 游戏进程已出现 (elapsed=${waited}s, pids=$(echo "$pids" | tr '\n' ' '))"
        fi
        if probe_ready; then
            return 0
        fi
        sleep "$POLL_INTERVAL"
        waited=$((waited + POLL_INTERVAL))
    done
    if [[ "$seen" == "0" ]]; then
        return 3
    fi
    return 1
}

# 超时/失败后的取证落盘（写入 bootstrap.log，随证据包上传，issue #99）。
diagnose_failure() {
    local class="$1" pids_now crashes steam_log
    log "❌ 三端点未就绪，失败类别："
    case "$class" in
        2) log "   进程出现后消失（启动即退出或崩溃）" ;;
        3) log "   进程从未出现（open 返回成功但 LaunchServices 未拉起）" ;;
        *) log "   进程存在但端点未就绪（游戏卡在启动早期）" ;;
    esac
    log "---- 超时取证 ----"
    pids_now="$(find_game_pids)"
    log "pgrep 快照: ${pids_now:-<无进程>}"
    if [[ -d "${HOME}/Library/Logs/DiagnosticReports" ]]; then
        crashes="$(find "${HOME}/Library/Logs/DiagnosticReports" -maxdepth 1 \
            -name '*.ips' -mmin -40 2>/dev/null | head -5)"
        log "最近 40 分钟崩溃报告: ${crashes:-<无>}"
    fi
    steam_log="${HOME}/Library/Application Support/Steam/logs/gameprocess_log.txt"
    if [[ -f "$steam_log" ]]; then
        log "Steam gameprocess_log 最近 5 行:"
        tail -5 "$steam_log" 2>/dev/null | sed 's/^/    /'
    fi
    if [[ -d "$GAME_LOGS_DIR" ]]; then
        log "游戏引擎日志目录最近文件:"
        ls -t "$GAME_LOGS_DIR" 2>/dev/null | head -3 | sed 's/^/    /'
    fi
    log "排查看 Steam 日志："
    log "   ~/Library/Application Support/Steam/logs/gameprocess_log.txt"
}

log "== nightly game bootstrap start (base=$BASE_URL)"
pids="$(find_game_pids)"

if probe_ready; then
    log "✅ 既有游戏会话已就绪，复用（不重启）"
    exit 0
fi

if [[ -n "$pids" ]]; then
    log "⚠️ 游戏进程存在但未就绪，宽限 ${READY_GRACE}s: $(echo "$pids" | tr '\n' ' ')"
    waited=0
    while (( waited < READY_GRACE )); do
        if probe_ready; then
            log "✅ 宽限期内就绪，复用"
            exit 0
        fi
        sleep "$POLL_INTERVAL"
        waited=$((waited + POLL_INTERVAL))
    done
    if [[ "$ALLOW_RESTART" != "1" ]]; then
        log "❌ stale 会话且 BOOTSTRAP_ALLOW_RESTART=0，拒绝自动重启（人工排查）"
        exit 1
    fi
    stop_game
fi

if ! launch_game; then
    exit 1
fi

wait_ready
class=$?
if [[ "$class" == "0" ]]; then
    log "✅ bootstrap 完成：游戏会话可用"
    exit 0
fi
diagnose_failure "$class"
exit 1
