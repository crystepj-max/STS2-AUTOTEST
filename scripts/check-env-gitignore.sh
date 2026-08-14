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

# 外部命令限时执行：python3 + psutil 封装（项目依赖，macOS/Linux/Windows 一致）。
# 超时 → 终止整棵进程树（AGENTS.md 防僵尸/防遗留）并返回 142（128+SIGTERM 惯例）；
# 正常结束 → 返回命令退出码。超时/执行错误与「预期失败结果」必须区分——
# 只有命令明确返回预期的「未命中/未跟踪」状态码才算 PASS。
GATE_CMD_TIMEOUT="${CHECK_ENV_GITIGNORE_CMD_TIMEOUT:-10}"

# run_timeout 使用的 Python 解释器：优先项目 venv（psutil 依赖的安装位置），
# 可用 CHECK_ENV_GITIGNORE_PYTHON 显式指定；psutil 缺失时明确失败而非静默挂起。
GATE_PYTHON="${CHECK_ENV_GITIGNORE_PYTHON:-}"
if [[ -z "$GATE_PYTHON" ]]; then
    if [[ -x "$REPO_ROOT/.venv/bin/python3" ]]; then
        GATE_PYTHON="$REPO_ROOT/.venv/bin/python3"
    else
        GATE_PYTHON="python3"
    fi
fi

run_timeout() {
    "$GATE_PYTHON" - "$GATE_CMD_TIMEOUT" "$@" <<'PY'
try:
    import psutil, subprocess, sys
except ModuleNotFoundError:
    print("run_timeout 需要 psutil（项目依赖）；请使用项目 venv（.venv/bin/python3）或设置 CHECK_ENV_GITIGNORE_PYTHON", file=sys.stderr)
    sys.exit(1)

timeout = float(sys.argv[1])
proc = psutil.Popen(sys.argv[2:])
try:
    rc = proc.wait(timeout=timeout)
except (psutil.TimeoutExpired, subprocess.TimeoutExpired):
    # psutil.Popen.wait 抛 psutil.TimeoutExpired（与 subprocess.TimeoutExpired 为
    # 兄弟类，均继承 TimeoutError）——两者都要捕获；超时后终止整棵进程树防遗留
    try:
        for child in proc.children(recursive=True):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
    finally:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
        try:
            proc.wait(timeout=5)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired, subprocess.TimeoutExpired):
            pass
    print(f"TIMEOUT: 命令 {' '.join(sys.argv[2:])} 超过 {timeout:.0f}s 未完成，已终止整棵进程树", file=sys.stderr)
    sys.exit(142)
sys.exit(rc)
PY
}

cd "$REPO_ROOT"

echo "===== 环境文件忽略规则检查 ====="

# 1. .env 必须被忽略（git check-ignore 退出码：0=命中忽略，1=未命中；
#    其他非零码=仓库损坏/I/O 等执行错误，一律判失败，只有 1 才是「未忽略」）
run_timeout git check-ignore -q .env
rc=$?
if [[ $rc -eq 0 ]]; then
    echo "PASS: .env 已被 git 忽略"
elif [[ $rc -eq 142 ]]; then
    echo "FAIL: 无法确认 .env 是否被忽略（git 超时，已终止）"
    FAILED=1
elif [[ $rc -eq 1 ]]; then
    echo "FAIL: .env 未被 git 忽略（.gitignore 缺少 .env 条目）"
    FAILED=1
else
    echo "FAIL: 无法确认 .env 是否被忽略（git 退出码 ${rc}）"
    FAILED=1
fi

# 2. .env 不得被跟踪（git ls-files --error-unmatch 退出码：0=已跟踪，1=未跟踪；
#    128 等=仓库损坏/I/O 执行错误，不得视为「未跟踪」）
run_timeout git ls-files --error-unmatch .env >/dev/null 2>&1
rc=$?
if [[ $rc -eq 0 ]]; then
    echo "FAIL: .env 已被 git 跟踪，必须 git rm --cached .env"
    FAILED=1
elif [[ $rc -eq 142 ]]; then
    echo "FAIL: 无法确认 .env 是否被跟踪（git 超时，已终止）"
    FAILED=1
elif [[ $rc -eq 1 ]]; then
    echo "PASS: .env 未被 git 跟踪"
else
    echo "FAIL: 无法确认 .env 是否被跟踪（git 退出码 ${rc}）"
    FAILED=1
fi

# 3. 已跟踪的环境文件只允许 .env.example（先单独取列表，超时/执行错误直接判失败，
#    不让 grep/管道掩盖 git 的错误）
ls_output="$(run_timeout git ls-files)"
ls_rc=$?
if [[ $ls_rc -eq 142 ]]; then
    echo "FAIL: 无法读取 git 跟踪文件列表（git 超时，已终止）"
    FAILED=1
elif [[ $ls_rc -ne 0 ]]; then
    echo "FAIL: 无法读取 git 跟踪文件列表（git 退出码 ${ls_rc}）"
    FAILED=1
fi
TRACKED_ENV_FILES="$(printf '%s\n' "$ls_output" | grep -E '(^|/)\.env($|\.)|^\.env' || true)"
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
