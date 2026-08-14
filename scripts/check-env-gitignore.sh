#!/usr/bin/env bash
# check-env-gitignore.sh — 环境文件忽略规则门禁（issue-23 复审修复）
#
# 规则（与 docs/process/main-merge-protection.md「本地配置防护」一致）：
#   1. .env 必须被 git 忽略（git check-ignore 命中）；
#   2. .env 不得被 git 跟踪；
#   3. 仓库跟踪的环境文件只允许 .env.example。
#
# 任一条不满足 → 退出码 1（阻止依赖此脚本的流程继续）；
# 全部满足 → 退出码 0。
#
# 限时（S4 复审要求）：每个外部调用（git）自备限时——直接运行本脚本时，
# 任一命令卡住也会在限定时间内失败退出，不依赖测试外层的整段超时
# （AGENTS.md 硬规则：所有外部调用必须有 timeout）。
#
# 用法：bash scripts/check-env-gitignore.sh
# 可选环境变量：CHECK_ENV_GITIGNORE_CMD_TIMEOUT（秒，默认 10，测试可调小）
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=0

# 外部命令限时执行：后台运行 + 截止时间轮询 + kill（bash 原生机制，
# macOS / Linux / Git Bash（Windows）行为一致，无 perl/第三方依赖）。
# 命令超时 → 发送 TERM 并回收，返回 142（128+SIGTERM 惯例）。
GATE_CMD_TIMEOUT="${CHECK_ENV_GITIGNORE_CMD_TIMEOUT:-10}"

run_timeout() {
    local deadline pid rc
    deadline=$(( $(date +%s) + GATE_CMD_TIMEOUT ))
    "$@" &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        if [[ "$(date +%s)" -ge "$deadline" ]]; then
            echo "TIMEOUT: 命令 '$*' 超过 ${GATE_CMD_TIMEOUT}s 未完成，已终止" >&2
            kill "$pid" 2>/dev/null
            wait "$pid" 2>/dev/null
            return 142
        fi
        sleep 0.2
    done
    wait "$pid"
    rc=$?
    return "$rc"
}

cd "$REPO_ROOT"

echo "===== 环境文件忽略规则检查 ====="

# 1. .env 必须被忽略
if run_timeout git check-ignore -q .env; then
    echo "PASS: .env 已被 git 忽略"
else
    echo "FAIL: .env 未被 git 忽略（.gitignore 缺少 .env 条目）"
    FAILED=1
fi

# 2. .env 不得被跟踪
if run_timeout git ls-files --error-unmatch .env >/dev/null 2>&1; then
    echo "FAIL: .env 已被 git 跟踪，必须 git rm --cached .env"
    FAILED=1
else
    echo "PASS: .env 未被 git 跟踪"
fi

# 3. 已跟踪的环境文件只允许 .env.example
TRACKED_ENV_FILES="$(run_timeout git ls-files | grep -E '(^|/)\.env($|\.)|^\.env' || true)"
for f in $TRACKED_ENV_FILES; do
    if [[ "$f" == ".env.example" ]]; then
        echo "PASS: 跟踪文件 ${f}（允许的模板）"
    else
        echo "FAIL: 意外跟踪的环境文件 ${f}（只允许 .env.example）"
        FAILED=1
    fi
done

echo
if [[ "$FAILED" -eq 0 ]]; then
    echo "check-env-gitignore.sh 全部通过 ✓"
    exit 0
else
    echo "check-env-gitignore.sh 存在失败项 ✗"
    exit 1
fi
