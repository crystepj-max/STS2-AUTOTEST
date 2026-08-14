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
# 用法：bash scripts/check-env-gitignore.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=0

cd "$REPO_ROOT"

echo "===== 环境文件忽略规则检查 ====="

# 1. .env 必须被忽略
if git check-ignore -q .env; then
    echo "PASS: .env 已被 git 忽略"
else
    echo "FAIL: .env 未被 git 忽略（.gitignore 缺少 .env 条目）"
    FAILED=1
fi

# 2. .env 不得被跟踪
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
    echo "FAIL: .env 已被 git 跟踪，必须 git rm --cached .env"
    FAILED=1
else
    echo "PASS: .env 未被 git 跟踪"
fi

# 3. 已跟踪的环境文件只允许 .env.example
TRACKED_ENV_FILES="$(git ls-files | grep -E '(^|/)\.env($|\.)|^\.env' || true)"
for f in $TRACKED_ENV_FILES; do
    if [[ "$f" == ".env.example" ]]; then
        echo "PASS: 跟踪文件 $f（允许的模板）"
    else
        echo "FAIL: 意外跟踪的环境文件 $f（只允许 .env.example）"
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
