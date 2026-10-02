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
# 启动方式（对齐 core/lifecycle.py）：macOS 用 `open <SlayTheSpire2.app>` 并注入
# STS2_API_PORT / STS2_ENABLE_DEBUG_ACTIONS；绝不 Popen 内层二进制。
#
# 用法：scripts/nightly-game-bootstrap.sh
#
# 环境变量：
#   STS2_GAME_DIR            游戏安装目录（默认 Steam macOS 常见路径）
#   STS2_AGENT_BASE_URL      Agent 基地址（默认 http://127.0.0.1:8080）
#   BOOTSTRAP_TIMEOUT        启动后等待三端点就绪的总预算秒数（默认 180）
#   BOOTSTRAP_READY_GRACE    进程已存在但未就绪时的宽限等待秒数（默认 30）
#   BOOTSTRAP_ALLOW_RESTART  1=stale 进程先干净关闭再重启（默认）；
#                            0=不自动重启，直接失败（人工排查场景）
#   BOOTSTRAP_KILL_WAIT      TERM 后等待进程退出的秒数（默认 15）
#
# 退出码：0=三端点就绪 1=未就绪（原因见输出）
set -uo pipefail

GAME_DIR="${STS2_GAME_DIR:-$HOME/Library/Application Support/Steam/steamapps/common/Slay the Spire 2}"
BUNDLE="$GAME_DIR/SlayTheSpire2.app"
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
    if [[ ! -d "$BUNDLE" ]]; then
        log "❌ 未找到游戏 bundle: ${BUNDLE}（可用 STS2_GAME_DIR 覆盖）"
        return 1
    fi
    local port="${BASE_URL##*:}"
    port="${port%%/*}"
    log "🚀 open 启动游戏: $BUNDLE (STS2_API_PORT=$port STS2_ENABLE_DEBUG_ACTIONS=1)"
    env STS2_API_PORT="$port" STS2_ENABLE_DEBUG_ACTIONS=1 STS2_GAME_DIR="$GAME_DIR" \
        open "$BUNDLE"
}

# 启动后轮询三端点直到总预算耗尽。
wait_ready() {
    local waited=0
    while (( waited < TOTAL_TIMEOUT )); do
        if probe_ready; then
            return 0
        fi
        sleep "$POLL_INTERVAL"
        waited=$((waited + POLL_INTERVAL))
    done
    log "❌ ${TOTAL_TIMEOUT}s 内三端点未就绪；排查看 Steam 日志："
    log "   ~/Library/Application Support/Steam/logs/gameprocess_log.txt"
    return 1
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

if wait_ready; then
    log "✅ bootstrap 完成：游戏会话可用"
    exit 0
fi
exit 1
