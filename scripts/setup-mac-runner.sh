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
# 环境变量：RUNNER_DIR（默认 $HOME/actions-runner）、RUNNER_VERSION（默认 2.336.0）

set -euo pipefail

REPO="crystepj-max/STS2-AUTOTEST"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
RUNNER_VERSION="${RUNNER_VERSION:-2.336.0}"

# 架构 → runner 包名
case "$(uname -m)" in
    arm64) RUNNER_ARCH="osx-arm64" ;;
    x86_64) RUNNER_ARCH="osx-x64" ;;
    *) echo "ERROR: 不支持的架构 $(uname -m)" >&2; exit 1 ;;
esac

SVC_SCRIPT="$RUNNER_DIR/svc.sh"
RUNNER_NAME_FILE="$RUNNER_DIR/.runner"

echo "=== STS2-AUTOTEST Mac Runner Setup（真实安装为准）==="
echo "安装目录: $RUNNER_DIR（架构 $RUNNER_ARCH）"

# 已配置安装（svc.sh 存在）→ 跳过下载/注册，幂等（不需要 gh）
if [[ -f "$SVC_SCRIPT" ]]; then
    echo "检测到已配置安装（$SVC_SCRIPT），跳过下载与注册。"
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
if ! gh auth status &>/dev/null; then
    echo "ERROR: gh not authenticated. Run: gh auth login" >&2
    exit 1
fi

# --- 新安装：下载 + 注册 + 安装服务 ---
echo "未检测到已配置安装（$RUNNER_DIR/svc.sh 不存在），开始新安装…"

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

TARBALL="actions-runner-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
if [[ ! -f "$TARBALL" ]]; then
    echo "下载 runner $RUNNER_VERSION（$RUNNER_ARCH）…"
    curl -o "$TARBALL" -L \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
    tar xzf "$TARBALL"
    rm "$TARBALL"
fi

# 获取注册 token（仅新安装）
echo "获取注册 token…"
RUNNER_TOKEN="$(gh api "repos/$REPO/actions/runners/registration-token" \
    --method POST --jq '.token')"

# 配置（非交互，replace 保证幂等）
./config.sh \
    --url "https://github.com/$REPO" \
    --token "$RUNNER_TOKEN" \
    --name "Chris-Mac-mini-STS2-AUTOTEST" \
    --labels "self-hosted,macos,autotest" \
    --work "_work" \
    --unattended \
    --replace

# 安装 launchd 服务（svc.sh 形态，非自定义 plist）
./svc.sh install

echo "=== 安装完成。Runner 将出现在: https://github.com/$REPO/settings/actions/runners ==="
echo "后续状态/停止/启动请使用: scripts/runner-ctl.sh {status|stop|start}"
