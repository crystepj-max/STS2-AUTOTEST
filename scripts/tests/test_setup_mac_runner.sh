#!/usr/bin/env bash
# setup-mac-runner.sh 行为测试（issue-24 T2，F1 漂移修复）：
# 1. 默认安装目录是 ~/actions-runner（真实安装），不再引用已废弃的
#    ~/actions-runner-autotest / com.sts2.autotest-runner / run.sh
# 2. 已配置安装幂等：不重复下载、不重复注册
# 3. 未配置安装：下载 → config.sh 注册 → svc.sh install
# 4. .env 追加而非覆盖
# 5. 服务安装走 svc.sh（launchd label actions.runner.*），不写自定义 plist
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/helpers.sh
source "$SCRIPT_DIR/lib/helpers.sh"

SETUP_SCRIPT="$SCRIPT_DIR/../setup-mac-runner.sh"

# --- 用例 1：脚本不再引用已废弃路径 / 自定义 plist / run.sh（F1 漂移静态检查）---
test_begin "静态检查：无废弃引用（actions-runner-autotest / com.sts2.autotest-runner / run.sh）"
if grep -nE 'actions-runner-autotest|com\.sts2\.autotest-runner|runner-stdout\.log|runner-stderr\.log|/run\.sh' "$SETUP_SCRIPT"; then
    fail "setup-mac-runner.sh 仍引用已废弃路径/label（F1 漂移未修复）"
else
    pass "setup-mac-runner.sh 无废弃引用"
fi

# --- 用例 2：默认安装目录是 ~/actions-runner ---
test_begin "静态检查：默认 RUNNER_DIR 为 \$HOME/actions-runner"
if grep -nF 'RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"' "$SETUP_SCRIPT"; then
    pass "默认 RUNNER_DIR 指向 ~/actions-runner"
else
    fail "未找到指向 ~/actions-runner 的默认 RUNNER_DIR 定义"
fi

# --- 用例 3：已配置安装幂等 —— svc.sh 存在时不调用 config.sh 注册 ---
test_begin "已配置安装幂等：不重复注册"
FAKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-home.XXXXXX")"
FAKE_RUNNER="$FAKE_HOME/actions-runner"
mkdir -p "$FAKE_RUNNER"
# 已配置安装的形态：svc.sh 存在 + .env 存在
# fake svc.sh 镜像真实语义：install/start 成功（exit 0），其余 usage（exit 1）
cat > "$FAKE_RUNNER/svc.sh" <<'FAKE_SVC'
#!/usr/bin/env bash
case "${1:-}" in
    install|start)
        echo "fake svc.sh: $1"
        exit 0
        ;;
    *)
        echo "Usage: ./svc.sh [install, start, stop, status, uninstall]" >&2
        exit 1
        ;;
esac
FAKE_SVC
chmod +x "$FAKE_RUNNER/svc.sh"
echo "PATH=/usr/bin:/bin" > "$FAKE_RUNNER/.env"

FAKE_BIN="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-bin.XXXXXX")"
cat > "$FAKE_BIN/gh" <<'FAKE_GH'
#!/usr/bin/env bash
echo "should-not-be-called"
exit 42
FAKE_GH
cat > "$FAKE_BIN/curl" <<'FAKE_CURL'
#!/usr/bin/env bash
echo "should-not-be-called" >&2
exit 42
FAKE_CURL
cat > "$FAKE_BIN/tar" <<'FAKE_TAR'
#!/usr/bin/env bash
echo "should-not-be-called" >&2
exit 42
FAKE_TAR
chmod +x "$FAKE_BIN/gh" "$FAKE_BIN/curl" "$FAKE_BIN/tar"

OUT="$(cd /tmp && HOME="$FAKE_HOME" RUNNER_DIR="$FAKE_RUNNER" PATH="$FAKE_BIN:/usr/bin:/bin" bash "$SETUP_SCRIPT" 2>&1)" || RC=$?
RC="${RC:-0}"
assert_eq "$RC" "0" "已配置安装应正常退出(0)"
if [[ "$OUT" == *"config.sh"* ]] || [[ "$OUT" == *"Got registration token"* ]]; then
    fail "已配置安装不应触发注册流程：$OUT"
else
    pass "已配置安装未触发注册流程"
fi
if [[ -f "$FAKE_RUNNER/config.sh" ]]; then
    fail "已配置安装不应生成 config.sh"
else
    pass "已配置安装未生成 config.sh"
fi

# --- 用例 4：.env 追加而非覆盖（已有内容保留）---
test_begin ".env 追加而非覆盖"
if grep -q "PATH=/usr/bin:/bin" "$FAKE_RUNNER/.env"; then
    pass "已有 .env 内容被保留"
else
    fail ".env 被覆盖，原有 PATH 丢失"
fi

# --- 用例 5：服务安装走 svc.sh，不写 com.sts2 自定义 plist ---
test_begin "服务安装形态：不写自定义 plist"
if ls "$FAKE_HOME/Library/LaunchAgents/com.sts2.autotest-runner.plist" >/dev/null 2>&1; then
    fail "不应生成 com.sts2.autotest-runner.plist（F1 漂移残留）"
else
    pass "未生成废弃自定义 plist"
fi

# --- 用例 6：未配置安装完整链路 —— 下载 → config.sh 注册 → svc.sh install ---
test_begin "未配置安装：下载 + config.sh 注册 + svc.sh install"
FAKE_HOME2="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-home2.XXXXXX")"
FAKE_RUNNER2="$FAKE_HOME2/actions-runner"
mkdir -p "$FAKE_RUNNER2"

FAKE_BIN2="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-bin2.XXXXXX")"
cat > "$FAKE_BIN2/gh" <<'FAKE_GH2'
#!/usr/bin/env bash
echo "fake-reg-token"
exit 0
FAKE_GH2
cat > "$FAKE_BIN2/curl" <<'FAKE_CURL2'
#!/usr/bin/env bash
# 生成 fake tarball
printf 'fake-tar-content' > "$2"
exit 0
FAKE_CURL2
cat > "$FAKE_BIN2/tar" <<'FAKE_TAR2'
#!/usr/bin/env bash
# fake tar：tar xzf 解压到 cwd，生成 svc.sh / config.sh 骨架
cat > svc.sh <<'SVC'
#!/usr/bin/env bash
case "${1:-}" in
    status) echo "not installed"; exit 0 ;;
    install|start) echo "fake svc.sh: $1"; exit 0 ;;
    *) echo "Usage: ./svc.sh [install, start, stop, status, uninstall]" >&2; exit 1 ;;
esac
SVC
cat > config.sh <<'CFG'
#!/usr/bin/env bash
echo "config args: $@" >> "$CONFIG_LOG"
exit 0
CFG
chmod +x svc.sh config.sh
exit 0
FAKE_TAR2
cat > "$FAKE_BIN2/launchctl" <<'FAKE_LAUNCHCTL'
#!/usr/bin/env bash
exit 0
FAKE_LAUNCHCTL
chmod +x "$FAKE_BIN2"/*

OUT2="$(cd /tmp && CONFIG_LOG="$FAKE_HOME2/config.log" HOME="$FAKE_HOME2" RUNNER_DIR="$FAKE_RUNNER2" RUNNER_VERSION="9.9.9" PATH="$FAKE_BIN2:/usr/bin:/bin" bash "$SETUP_SCRIPT" 2>&1)" || RC2=$?
RC2="${RC2:-0}"
assert_eq "$RC2" "0" "未配置安装应正常退出(0)"
if [[ -f "$FAKE_HOME2/config.log" ]]; then
    assert_contains "$(cat "$FAKE_HOME2/config.log")" "--url https://github.com/" "config.sh 应以仓库 URL 注册"
    assert_contains "$(cat "$FAKE_HOME2/config.log")" "--token fake-reg-token" "config.sh 应携带注册 token"
    assert_contains "$(cat "$FAKE_HOME2/config.log")" "--unattended" "config.sh 应非交互"
else
    fail "config.sh 未被调用（未走注册流程）"
fi

echo
echo "setup-mac-runner 测试完成：$((TEST_COUNT)) 用例，$FAIL_COUNT 失败"
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
