#!/usr/bin/env bash
# setup-mac-runner.sh — 配置 macOS 自托管 GitHub Actions Runner
#
# 用法：./scripts/setup-mac-runner.sh
# 前提：已安装 gh CLI 并登录 (gh auth login)
#
# 做什么：
# 1. 下载并配置 GitHub Actions runner（标签：self-hosted,macos,autotest）
# 2. 写入 STS2-WORKSPACE 环境变量到 runner 的 .env
# 3. 安装 launchd plist 实现开机自启

set -euo pipefail

REPO="crystepj-max/STS2-AUTOTEST"
RUNNER_DIR="$HOME/actions-runner-autotest"
WORKSPACE_ROOT="$HOME/STS2-WORKSPACE"

echo "=== STS2-AUTOTEST Mac Runner Setup ==="

# --- Step 1: 检查 GitHub CLI ---
if ! command -v gh &>/dev/null; then
    echo "ERROR: GitHub CLI (gh) is required. Install: brew install gh"
    exit 1
fi

if ! gh auth status &>/dev/null; then
    echo "ERROR: gh not authenticated. Run: gh auth login"
    exit 1
fi

# --- Step 2: 下载 runner ---
if [[ ! -d "$RUNNER_DIR" ]]; then
    mkdir -p "$RUNNER_DIR"
    cd "$RUNNER_DIR"
    echo "Downloading actions runner..."
    curl -o actions-runner-osx-arm64-2.322.0.tar.gz \
        -L https://github.com/actions/runner/releases/download/v2.322.0/actions-runner-osx-arm64-2.322.0.tar.gz
    tar xzf actions-runner-osx-arm64-2.322.0.tar.gz
    rm actions-runner-osx-arm64-2.322.0.tar.gz
fi

# --- Step 3: 配置 runner ---
cd "$RUNNER_DIR"

# 获取注册 token
RUNNER_TOKEN=$(gh api "repos/$REPO/actions/runners/registration-token" \
    --method POST --jq '.token')
echo "Got registration token"

# 配置 (非交互模式)
./config.sh \
    --url "https://github.com/$REPO" \
    --token "$RUNNER_TOKEN" \
    --name "mac-autotest-$(hostname -s)" \
    --labels "self-hosted,macos,autotest" \
    --work "_work" \
    --unattended \
    --replace

# --- Step 4: 写入环境变量 ---
cat > "$RUNNER_DIR/.env" << 'EOF'
STS2_WORKSPACE=$HOME/STS2-WORKSPACE
STS2_GAME_DIR=$HOME/Library/Application Support/Steam/steamapps/common/SlayTheSpire2
STS2_MODS_DIR=$STS2_GAME_DIR/Mods
GODOT_PATH=/Applications/Godot.app
EOF

# --- Step 5: 安装 launchd 服务 ---
PLIST="$HOME/Library/LaunchAgents/com.sts2.autotest-runner.plist"
cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.sts2.autotest-runner</string>
    <key>ProgramArguments</key>
    <array>
        <string>$RUNNER_DIR/run.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>$RUNNER_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>STS2_WORKSPACE</key>
        <string>$HOME/STS2-WORKSPACE</string>
        <key>STS2_MODS_DIR</key>
        <string>$HOME/Library/Application Support/Steam/steamapps/common/SlayTheSpire2/Mods</string>
    </dict>
    <key>StandardOutPath</key>
    <string>$RUNNER_DIR/runner-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$RUNNER_DIR/runner-stderr.log</string>
</dict>
</plist>
EOF

launchctl load "$PLIST"
echo "=== Runner installed. Starting... ==="
launchctl start com.sts2.autotest-runner

echo "=== Done! Runner should appear in: https://github.com/$REPO/settings/actions/runners ==="
