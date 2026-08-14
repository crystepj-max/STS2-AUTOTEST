#!/usr/bin/env bash
# setup-mac-runner.sh — 配置 / 修复 macOS 自托管 GitHub Actions Runner（issue-24 T2）
#
# 以真实安装为准（修复 F1 漂移，见 .agent-runs/issue-24-mac-runner-maintainability/evidence/）：
#   - 安装目录默认 ~/actions-runner（RUNNER_DIR 可覆盖）
#   - 服务形态为 svc.sh 管理的 launchd 服务（label: actions.runner.<owner>-<repo>.<name>）
# 幂等：已配置的安装跳过下载与注册，只确保服务状态正确。
#
# 用法：
#   ./scripts/setup-mac-runner.sh
#   RUNNER_DIR=/custom/path ./scripts/setup-mac-runner.sh   # 自定义安装目录
# 前提：仅新安装需要 gh CLI 已登录（gh auth login）
#
# 环境变量：RUNNER_DIR（默认 $HOME/actions-runner）、RUNNER_VERSION（默认 2.336.0）、
#           SETUP_CMD_TIMEOUT（外部调用超时秒数，默认 30）、
#           RUNNER_NAME（新安装必填：机器身份，默认不预设固定机器名）、
#           ALLOW_REPLACE（=1 时允许覆盖同名既有注册，默认拒绝，R4 安全门禁）、
#           PROXY_URL（写入 runner .env 的代理地址，默认 http://127.0.0.1:7890）

set -euo pipefail

REPO="crystepj-max/STS2-AUTOTEST"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
RUNNER_VERSION="${RUNNER_VERSION:-2.336.0}"
SETUP_CMD_TIMEOUT="${SETUP_CMD_TIMEOUT:-30}"
RUNNER_NAME="${RUNNER_NAME:-}"
ALLOW_REPLACE="${ALLOW_REPLACE:-0}"
PROXY_URL="${PROXY_URL:-http://127.0.0.1:7890}"

# 带超时执行命令：外部调用（gh/curl/tar/config.sh/svc.sh）可能挂起，逐项限时；
# killer 不持有调用方管道，超时后递归回收子进程
run_with_timeout() {
    local timeout="$1"
    shift
    local pid rc killer
    "$@" &
    pid=$!
    ( sleep "$timeout"; pkill -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true ) >/dev/null 2>&1 &
    killer=$!
    if wait "$pid"; then rc=0; else rc=$?; fi
    pkill -P "$killer" 2>/dev/null || true
    kill "$killer" 2>/dev/null || true
    return "$rc"
}

# 架构 → runner 包名
case "$(uname -m)" in
    arm64) RUNNER_ARCH="osx-arm64" ;;
    x86_64) RUNNER_ARCH="osx-x64" ;;
    *) echo "ERROR: 不支持的架构 $(uname -m)" >&2; exit 1 ;;
esac

SVC_SCRIPT="$RUNNER_DIR/svc.sh"
RUNNER_NAME_FILE="$RUNNER_DIR/.runner"

echo "=== STS2-AUTOTEST Mac Runner Setup（真实安装为准）==="
echo "安装目录: ${RUNNER_DIR}（架构 ${RUNNER_ARCH}）"

# 已配置安装（svc.sh 存在）→ 跳过下载/注册，幂等（不需要 gh）
if [[ -f "$SVC_SCRIPT" ]]; then
    echo "检测到已配置安装（${SVC_SCRIPT}），跳过下载与注册。"
    RUNNER_NAME=""
    if [[ -f "$RUNNER_NAME_FILE" ]]; then
        RUNNER_NAME="$(grep -o '"agentName": *"[^"]*"' "$RUNNER_NAME_FILE" | sed 's/.*: *"//;s/"//')"
    fi
    echo "Runner 名称: ${RUNNER_NAME:-（未知）}"
    echo "=== 已配置安装，跳过注册 ==="
    echo "状态/停止/启动请使用: scripts/runner-ctl.sh {status|stop|start}"
    exit 0
fi

# 检查 GitHub CLI（仅新安装需要）
if ! command -v gh &>/dev/null; then
    echo "ERROR: GitHub CLI (gh) is required. Install: brew install gh" >&2
    exit 1
fi
if ! run_with_timeout "$SETUP_CMD_TIMEOUT" gh auth status &>/dev/null; then
    echo "ERROR: gh not authenticated. Run: gh auth login" >&2
    exit 1
fi

# --- 新安装：下载 + 注册 + 安装服务 ---
echo "未检测到已配置安装（$RUNNER_DIR/svc.sh 不存在），开始新安装…"

# 机器身份必须显式传入（R4）：不再默认固定机器名，防止误覆盖其他机器身份
if [[ -z "$RUNNER_NAME" ]]; then
    echo "ERROR: RUNNER_NAME 必须显式提供（新安装的机器身份）。" >&2
    echo "示例: RUNNER_NAME=my-mac-runner ./scripts/setup-mac-runner.sh" >&2
    exit 1
fi

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

# 同名注册保护（R4）：默认拒绝覆盖既有同名 runner，需显式 ALLOW_REPLACE=1
if [[ "$ALLOW_REPLACE" != "1" ]]; then
    EXISTING="$(run_with_timeout "$SETUP_CMD_TIMEOUT" gh api "repos/$REPO/actions/runners" \
        --paginate --jq '.runners[] | select(.name == $name) | .id' --arg name "$RUNNER_NAME" 2>/dev/null || true)"
    if [[ -n "$EXISTING" ]]; then
        echo "ERROR: 已存在同名 runner '$RUNNER_NAME'（id=$EXISTING）。" >&2
        echo "默认禁止覆盖注册；确认要替换请显式: ALLOW_REPLACE=1 ..." >&2
        exit 1
    fi
fi

TARBALL="actions-runner-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
if [[ ! -f "$TARBALL" ]]; then
    echo "下载 runner ${RUNNER_VERSION}（${RUNNER_ARCH}）…"
    run_with_timeout "$SETUP_CMD_TIMEOUT" curl -o "$TARBALL" -L \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
    run_with_timeout "$SETUP_CMD_TIMEOUT" tar xzf "$TARBALL"
    rm "$TARBALL"
fi

# 获取注册 token（仅新安装）
echo "获取注册 token…"
RUNNER_TOKEN="$(run_with_timeout "$SETUP_CMD_TIMEOUT" gh api "repos/$REPO/actions/runners/registration-token" \
    --method POST --jq '.token')"

# 配置（非交互；仅 ALLOW_REPLACE=1 时带 --replace）
CONFIG_ARGS=(--url "https://github.com/$REPO" --token "$RUNNER_TOKEN" --name "$RUNNER_NAME" \
    --labels "self-hosted,macos,autotest" --work "_work" --unattended)
if [[ "$ALLOW_REPLACE" == "1" ]]; then
    CONFIG_ARGS+=(--replace)
fi
run_with_timeout "$SETUP_CMD_TIMEOUT" ./config.sh "${CONFIG_ARGS[@]}"

# 运行环境写入 runner .env（R4：手册声明代理等运行环境位于 runner .env，追加而非覆盖）
ENV_FILE="$RUNNER_DIR/.env"
if [[ -f "$ENV_FILE" ]] && grep -q '^HTTP_PROXY=' "$ENV_FILE"; then
    echo ".env 已含 HTTP_PROXY，跳过代理写入。"
else
    {
        echo "HTTP_PROXY=$PROXY_URL"
        echo "HTTPS_PROXY=$PROXY_URL"
    } >> "$ENV_FILE"
    echo "已写入运行环境（HTTP_PROXY/HTTPS_PROXY）到 $ENV_FILE"
fi

# 安装 launchd 服务（svc.sh 形态，非自定义 plist）并启动验证（R4：装后状态为真）
run_with_timeout "$SETUP_CMD_TIMEOUT" ./svc.sh install
run_with_timeout "$SETUP_CMD_TIMEOUT" ./svc.sh start
SVC_STATUS="$(run_with_timeout "$SETUP_CMD_TIMEOUT" ./svc.sh status 2>/dev/null || true)"
if [[ "$SVC_STATUS" == *"Started:"* ]]; then
    echo "=== 服务已启动（svc.sh status 确认 Started）==="
else
    echo "WARNING: 服务安装后 status 未确认 Started（可能需手动启动或检查 svc.sh 输出）" >&2
    echo "$SVC_STATUS" >&2
fi

echo "=== 安装完成。Runner 将出现在: https://github.com/$REPO/settings/actions/runners ==="
echo "后续状态/停止/启动请使用: scripts/runner-ctl.sh {status|stop|start}"
