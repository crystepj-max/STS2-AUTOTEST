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

# --- 用例 6：未配置安装完整链路 —— 下载 → config.sh 注册 → svc.sh install → 启动验证 ---
test_begin "未配置安装：下载 + config.sh 注册 + svc.sh install/start + 装后验证"
FAKE_HOME2="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-home2.XXXXXX")"
FAKE_RUNNER2="$FAKE_HOME2/actions-runner"
mkdir -p "$FAKE_RUNNER2"

FAKE_BIN2="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-bin2.XXXXXX")"
cat > "$FAKE_BIN2/gh" <<'FAKE_GH2'
#!/usr/bin/env bash
# 模拟 gh api：registration-token 返回 token；同名检查 --jq select 无匹配 → 无输出
if [[ "$*" == *"registration-token"* ]]; then
    echo "fake-reg-token"
fi
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
# fake svc.sh：install/start 后 status 报告 Started（R4 装后验证）
STATE="$PWD/.fake-state"
case "${1:-}" in
    status)
        if [[ -f "$STATE" && "$(cat "$STATE")" == "started" ]]; then
            echo "Started:"
        else
            echo "not installed"
        fi
        exit 0 ;;
    install) echo "installed" > "$STATE"; exit 0 ;;
    start) echo "started" > "$STATE"; exit 0 ;;
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

OUT2="$(cd /tmp && CONFIG_LOG="$FAKE_HOME2/config.log" HOME="$FAKE_HOME2" RUNNER_DIR="$FAKE_RUNNER2" \
    RUNNER_VERSION="9.9.9" RUNNER_NAME="my-runner" PATH="$FAKE_BIN2:/usr/bin:/bin" \
    bash "$SETUP_SCRIPT" 2>&1)" || RC2=$?
RC2="${RC2:-0}"
assert_eq "$RC2" "0" "未配置安装应正常退出(0)"
if [[ -f "$FAKE_HOME2/config.log" ]]; then
    assert_contains "$(cat "$FAKE_HOME2/config.log")" "--url https://github.com/" "config.sh 应以仓库 URL 注册"
    assert_contains "$(cat "$FAKE_HOME2/config.log")" "--token fake-reg-token" "config.sh 应携带注册 token"
    assert_contains "$(cat "$FAKE_HOME2/config.log")" "--unattended" "config.sh 应非交互"
    assert_contains "$(cat "$FAKE_HOME2/config.log")" "--name my-runner" "config.sh 应使用显式机器身份"
else
    fail "config.sh 未被调用（未走注册流程）"
fi
# R4 装后验证：运行环境写入 .env + 服务启动确认
if [[ -f "$FAKE_RUNNER2/.env" ]] && grep -q '^HTTP_PROXY=' "$FAKE_RUNNER2/.env"; then
    pass "运行环境（HTTP_PROXY）已写入 runner .env"
else
    fail ".env 未写入 HTTP_PROXY（R4 运行环境缺失）"
fi
assert_contains "$OUT2" "Started" "安装后应确认服务已启动（svc.sh status Started）"

# --- 用例 7（S1 反例）：外部调用挂起 → 限时退出而非无期等待 ---
test_begin "外部调用挂起：setup 限时退出"
FAKE_HOME3="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-home3.XXXXXX")"
FAKE_RUNNER3="$FAKE_HOME3/actions-runner"
mkdir -p "$FAKE_RUNNER3"
FAKE_BIN3="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-bin3.XXXXXX")"
cat > "$FAKE_BIN3/gh" <<'FAKE_GH_HANG'
#!/usr/bin/env bash
# 挂起替身：永不返回（模拟 gh api 无响应）
while true; do sleep 1; done
FAKE_GH_HANG
chmod +x "$FAKE_BIN3/gh"
cat > "$FAKE_BIN3/curl" <<'FAKE_CURL3'
#!/usr/bin/env bash
printf 'fake-tar-content' > "$2"
exit 0
FAKE_CURL3
cat > "$FAKE_BIN3/tar" <<'FAKE_TAR3'
#!/usr/bin/env bash
cat > svc.sh <<'SVC'
#!/usr/bin/env bash
exit 0
SVC
cat > config.sh <<'CFG'
#!/usr/bin/env bash
echo "config args: $@" >> "$CONFIG_LOG"
exit 0
CFG
chmod +x svc.sh config.sh
exit 0
FAKE_TAR3
chmod +x "$FAKE_BIN3"/*
START="$(date +%s)"
OUT3="$(cd /tmp && CONFIG_LOG="$FAKE_HOME3/config.log" HOME="$FAKE_HOME3" RUNNER_DIR="$FAKE_RUNNER3" \
    RUNNER_VERSION="9.9.9" SETUP_CMD_TIMEOUT=2 PATH="$FAKE_BIN3:/usr/bin:/bin" \
    bash "$SETUP_SCRIPT" 2>&1)" || RC3=$?
RC3="${RC3:-0}"
ELAPSED="$(( $(date +%s) - START ))"
if [[ "$ELAPSED" -le 10 ]]; then
    pass "外部调用挂起时限时退出（用时 ${ELAPSED}s ≤ 10s）"
else
    fail "外部调用挂起时未限时（用时 ${ELAPSED}s）"
fi

# --- 用例 8（R4 反例）：机器身份必须显式传入，未提供时拒绝新安装 ---
test_begin "机器身份未显式传入：拒绝新安装（不再固定机器名）"
FAKE_HOME4="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-home4.XXXXXX")"
FAKE_RUNNER4="$FAKE_HOME4/actions-runner"
mkdir -p "$FAKE_RUNNER4"
FAKE_BIN4="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-bin4.XXXXXX")"
cat > "$FAKE_BIN4/gh" <<'FAKE_GH4'
#!/usr/bin/env bash
echo "fake-reg-token"
exit 0
FAKE_GH4
cat > "$FAKE_BIN4/curl" <<'FAKE_CURL4'
#!/usr/bin/env bash
printf 'fake-tar-content' > "$2"
exit 0
FAKE_CURL4
cat > "$FAKE_BIN4/tar" <<'FAKE_TAR4'
#!/usr/bin/env bash
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
FAKE_TAR4
chmod +x "$FAKE_BIN4"/*
# CI 环境会注入 RUNNER_NAME（GitHub Actions runner 环境变量）——本用例必须显式清空，
# 否则脚本继承 CI 的 RUNNER_NAME 不会走「未提供机器身份」拒绝分支
OUT4="$(cd /tmp && CONFIG_LOG="$FAKE_HOME4/config.log" HOME="$FAKE_HOME4" RUNNER_DIR="$FAKE_RUNNER4" \
    RUNNER_VERSION="9.9.9" RUNNER_NAME="" PATH="$FAKE_BIN4:/usr/bin:/bin" bash "$SETUP_SCRIPT" 2>&1)" || RC4=$?
RC4="${RC4:-0}"
assert_ne "$RC4" "0" "未显式提供机器身份时新安装应拒绝（非 0 退出）"
if [[ "$OUT4" == *"RUNNER_NAME"* ]]; then
    pass "应提示必须显式提供机器身份（RUNNER_NAME）"
else
    fail "应提示必须显式提供机器身份（RUNNER_NAME）——实际输出: $(echo "$OUT4" | head -3 | tr '\n' ' | ')"
fi

# --- 用例 9（R4）：显式提供 RUNNER_NAME 后正常安装 ---
test_begin "显式 RUNNER_NAME：新安装正常完成"
FAKE_HOME5="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-home5.XXXXXX")"
FAKE_RUNNER5="$FAKE_HOME5/actions-runner"
mkdir -p "$FAKE_RUNNER5"
FAKE_BIN5="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-bin5.XXXXXX")"
cat > "$FAKE_BIN5/gh" <<'FAKE_GH5'
#!/usr/bin/env bash
# 模拟 gh api：registration-token 返回 token；同名检查 --jq select 无匹配 → 无输出
if [[ "$*" == *"registration-token"* ]]; then
    echo "fake-reg-token"
fi
exit 0
FAKE_GH5
cat > "$FAKE_BIN5/curl" <<'FAKE_CURL5'
#!/usr/bin/env bash
printf 'fake-tar-content' > "$2"
exit 0
FAKE_CURL5
cat > "$FAKE_BIN5/tar" <<'FAKE_TAR5'
#!/usr/bin/env bash
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
FAKE_TAR5
chmod +x "$FAKE_BIN5"/*
OUT5="$(cd /tmp && CONFIG_LOG="$FAKE_HOME5/config.log" HOME="$FAKE_HOME5" RUNNER_DIR="$FAKE_RUNNER5" \
    RUNNER_VERSION="9.9.9" RUNNER_NAME="my-explicit-runner" PATH="$FAKE_BIN5:/usr/bin:/bin" \
    bash "$SETUP_SCRIPT" 2>&1)" || RC5=$?
RC5="${RC5:-0}"
assert_eq "$RC5" "0" "显式机器身份时新安装应正常退出(0)"
if [[ -f "$FAKE_HOME5/config.log" ]]; then
    assert_contains "$(cat "$FAKE_HOME5/config.log")" "--name my-explicit-runner" "config.sh 应使用显式传入的机器身份"
else
    fail "config.sh 未被调用"
fi

# --- 用例 10（R4/S3 反例）：同名已注册 → 默认拒绝覆盖注册 ---
test_begin "同名已注册：默认拒绝覆盖（需显式 ALLOW_REPLACE）"
FAKE_HOME6="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-home6.XXXXXX")"
FAKE_RUNNER6="$FAKE_HOME6/actions-runner"
mkdir -p "$FAKE_RUNNER6"
FAKE_BIN6="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-bin6.XXXXXX")"
cat > "$FAKE_BIN6/gh" <<'FAKE_GH6'
#!/usr/bin/env bash
# 查询既有 runner：返回同名已存在
if [[ "$*" == *"registration-token"* ]]; then
    echo "fake-reg-token"
else
    echo '{"runners":[{"name":"dup-runner","id":21}]}'
fi
exit 0
FAKE_GH6
cat > "$FAKE_BIN6/curl" <<'FAKE_CURL6'
#!/usr/bin/env bash
printf 'fake-tar-content' > "$2"
exit 0
FAKE_CURL6
cat > "$FAKE_BIN6/tar" <<'FAKE_TAR6'
#!/usr/bin/env bash
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
FAKE_TAR6
chmod +x "$FAKE_BIN6"/*
OUT6="$(cd /tmp && CONFIG_LOG="$FAKE_HOME6/config.log" HOME="$FAKE_HOME6" RUNNER_DIR="$FAKE_RUNNER6" \
    RUNNER_VERSION="9.9.9" RUNNER_NAME="dup-runner" PATH="$FAKE_BIN6:/usr/bin:/bin" \
    bash "$SETUP_SCRIPT" 2>&1)" || RC6=$?
RC6="${RC6:-0}"
assert_ne "$RC6" "0" "同名已注册时默认应拒绝（非 0 退出）"
assert_contains "$OUT6" "ALLOW_REPLACE" "应提示需显式 ALLOW_REPLACE=1 才允许覆盖"

# --- 用例 11（R4/S3）：显式 ALLOW_REPLACE=1 后允许覆盖注册 ---
test_begin "显式 ALLOW_REPLACE=1：允许覆盖注册"
FAKE_HOME7="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-home7.XXXXXX")"
FAKE_RUNNER7="$FAKE_HOME7/actions-runner"
mkdir -p "$FAKE_RUNNER7"
FAKE_BIN7="$(mktemp -d "${TMPDIR:-/tmp}/setup-runner-bin7.XXXXXX")"
cat > "$FAKE_BIN7/gh" <<'FAKE_GH7'
#!/usr/bin/env bash
if [[ "$*" == *"registration-token"* ]]; then
    echo "fake-reg-token"
else
    echo '{"runners":[{"name":"dup-runner","id":21}]}'
fi
exit 0
FAKE_GH7
cat > "$FAKE_BIN7/curl" <<'FAKE_CURL7'
#!/usr/bin/env bash
printf 'fake-tar-content' > "$2"
exit 0
FAKE_CURL7
cat > "$FAKE_BIN7/tar" <<'FAKE_TAR7'
#!/usr/bin/env bash
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
FAKE_TAR7
chmod +x "$FAKE_BIN7"/*
OUT7="$(cd /tmp && CONFIG_LOG="$FAKE_HOME7/config.log" HOME="$FAKE_HOME7" RUNNER_DIR="$FAKE_RUNNER7" \
    RUNNER_VERSION="9.9.9" RUNNER_NAME="dup-runner" ALLOW_REPLACE=1 PATH="$FAKE_BIN7:/usr/bin:/bin" \
    bash "$SETUP_SCRIPT" 2>&1)" || RC7=$?
RC7="${RC7:-0}"
assert_eq "$RC7" "0" "显式 ALLOW_REPLACE=1 时允许覆盖注册(0)"
if [[ -f "$FAKE_HOME7/config.log" ]]; then
    assert_contains "$(cat "$FAKE_HOME7/config.log")" "--replace" "覆盖注册时应带 --replace"
else
    fail "config.sh 未被调用"
fi

echo
echo "setup-mac-runner 测试完成：$((TEST_COUNT)) 用例，$FAIL_COUNT 失败"
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
