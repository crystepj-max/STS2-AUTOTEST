#!/usr/bin/env bash
# 运行 scripts/ 下全部 shell 测试（issue-24 T2/T3/T4）
# 用法：bash scripts/tests/run-all.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAILED=0

for test_file in "$SCRIPT_DIR"/test_*.sh; do
    echo "===== $(basename "$test_file") ====="
    if bash "$test_file"; then
        echo "PASS: $(basename "$test_file")"
    else
        echo "FAIL: $(basename "$test_file")"
        FAILED=1
    fi
    echo
done

if [[ "$FAILED" -eq 0 ]]; then
    echo "全部脚本测试通过"
    exit 0
else
    echo "存在失败的脚本测试"
    exit 1
fi
