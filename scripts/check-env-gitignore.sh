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
# Windows venv（.venv/Scripts/python.exe）与 Unix venv（.venv/bin/python3）均识别；
# 可用 CHECK_ENV_GITIGNORE_PYTHON 显式指定；psutil 缺失时明确失败而非静默挂起。
GATE_PYTHON="${CHECK_ENV_GITIGNORE_PYTHON:-}"
if [[ -z "$GATE_PYTHON" ]]; then
    if [[ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]]; then
        GATE_PYTHON="$REPO_ROOT/.venv/Scripts/python.exe"
    elif [[ -x "$REPO_ROOT/.venv/bin/python3" ]]; then
        GATE_PYTHON="$REPO_ROOT/.venv/bin/python3"
    else
        GATE_PYTHON="python3"
    fi
fi

run_timeout() {
    "$GATE_PYTHON" - "$GATE_CMD_TIMEOUT" "$@" <<'PY'
import os, signal

try:
    import psutil, subprocess, sys
except ModuleNotFoundError:
    print("run_timeout 需要 psutil（项目依赖）；请使用项目 venv（.venv/bin/python3）或设置 CHECK_ENV_GITIGNORE_PYTHON", file=sys.stderr)
    sys.exit(1)

timeout = float(sys.argv[1])
# 独立进程组：POSIX 用 start_new_session + killpg；Windows 用 CREATE_NEW_PROCESS_GROUP
# + CTRL_BREAK_EVENT（可送达整组、不可被控制台程序轻易忽略）回收后代
if os.name == "nt":
    popen_kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


    def _kill_group(pgid, sig):  # type: ignore[no-redef]
        if sig == "TASKKILL":
            # 强制终止整棵进程树（Windows 内置 taskkill /F /T，无需 pywin32 Job Object）
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pgid)],
                    capture_output=True,
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                pass  # taskkill 卡住不阻塞清理，继续兜底
        else:
            os.kill(pgid, signal.CTRL_BREAK_EVENT)


    def _group_alive(pgid):  # type: ignore[no-redef]
        try:
            os.kill(pgid, signal.CTRL_BREAK_EVENT)
            return True
        except OSError:
            return False

else:
    popen_kwargs = {"start_new_session": True}


    def _kill_group(pgid, sig):  # type: ignore[no-redef]
        os.killpg(pgid, sig)


    def _group_alive(pgid):  # type: ignore[no-redef]
        try:
            os.killpg(pgid, 0)
            return True
        except (ProcessLookupError, OSError):
            return False


import time

proc = psutil.Popen(sys.argv[2:], **popen_kwargs)

# Windows Python 无 SIGKILL：POSIX 用 SIGTERM→SIGKILL 升级；
# Windows 用 CTRL_BREAK_EVENT→taskkill /F /T 升级（TASKKILL 哨兵）
if os.name == "nt":
    GROUP_SIGNALS = [signal.SIGTERM, "TASKKILL"]
else:
    GROUP_SIGNALS = [signal.SIGTERM, signal.SIGKILL]

try:
    rc = proc.wait(timeout=timeout)
except (psutil.TimeoutExpired, subprocess.TimeoutExpired):
    # psutil.Popen.wait 抛 psutil.TimeoutExpired（与 subprocess.TimeoutExpired 为
    # 兄弟类，均继承 TimeoutError）——两者都要捕获；超时后按组 TERM → 宽限 → KILL
    # 升级（防忽略 TERM 的后代持有管道），进程树清理兜底
    for sig in GROUP_SIGNALS:
        try:
            _kill_group(proc.pid, sig)
        except OSError:
            break  # 组已不存在
        if sig == signal.SIGTERM:
            time.sleep(0.5)
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
# 父进程已退出但进程组仍有成员（后台子进程持有输出管道会阻塞外层命令替换）：
# TERM → 宽限 → KILL 升级回收整组，避免 $(...) 无限等待（平台对应的组探测）
if _group_alive(proc.pid):
    for sig in GROUP_SIGNALS:
        try:
            _kill_group(proc.pid, sig)
        except OSError:
            break  # 组已不存在
        if sig == signal.SIGTERM:
            time.sleep(0.5)
sys.exit(rc)
PY
}

cd "$REPO_ROOT"

echo "===== 环境文件忽略规则检查 ====="

# 0. 禁止放行 .env 的否定规则（嵌套路径如 !secrets/.env 或通配隐藏 !secrets/[.]env、
#    子目录 .gitignore 里的 !.env 都会重新暴露本地凭据；唯一允许的是 !.env.example）
#    扫描所有被跟踪的 .gitignore（-z NUL 分隔，空格路径不被拆分）；
#    括号表达式保留内容归一化（[.]→.，![.]env→!.env 才能被识别）
neg_rules=""
while IFS= read -r -d '' gi; do
    r="$(grep -E '^!' "$REPO_ROOT/$gi" | sed -E 's/\[([^]]*)\]/\1/g' | grep -E '\.env' | grep -v '^!\.env\.example$' || true)"
    if [[ -n "$r" ]]; then
        neg_rules="${neg_rules}${gi}: $(echo "$r" | tr '\n' ' ')"
    fi
done < <(run_timeout git ls-files -z | grep -z '\.gitignore$' || true)
if [[ -n "$neg_rules" ]]; then
    echo "FAIL: 存在放行 .env 的否定规则（只允许 !.env.example）：$neg_rules"
    FAILED=1
fi

# 0b. 语义探针核验：按否定规则所在目录探测 .env 候选路径（git 自身语义匹配）——
#     宽泛通配（!secrets/*、!secrets/?env、!secrets/.e?? 等）不含字面 .env，
#     用候选探针 + git check-ignore 判定是否仍被忽略
neg_violations=""
while IFS= read -r -d '' gi2; do
    while IFS= read -r pat; do
        [[ "$pat" == "!.env.example" ]] && continue
        base="${pat#!}"
        if [[ "$base" == */* ]]; then
            pdir="${base%/*}"
            # 目录部分通配实例化为具体目录名（!secret*/?env → 探针 secretx/.env），
            # git 自身语义会按模式匹配该路径
            if [[ "$pdir" == *'*'* || "$pdir" == *'?'* || "$pdir" == *'['* ]]; then
                pdir="$(printf '%s' "$pdir" | sed -E 's/\[([^]]*)\]/\1/g; s/[*?]+/x/g')"
            fi
        else
            pdir="."
        fi
        gi_dir="$(dirname "$gi2")"
        for cand in .env .env.local .env.prod .env.staging; do
            if [[ "$gi_dir" == "." ]]; then
                probe_path="${pdir}/${cand}"
            else
                probe_path="${gi_dir}/${pdir}/${cand}"
            fi
            probe_path="${probe_path#./}"
            run_timeout git check-ignore -q "$probe_path"
            if [[ $? -ne 0 ]]; then
                neg_violations="${neg_violations}${gi2}: ${pat}（探针 ${probe_path} 未被忽略） "
                break
            fi
        done
    done < <(grep -E '^!' "$REPO_ROOT/$gi2" || true)
done < <(run_timeout git ls-files -z | grep -z '\.gitignore$' || true)
if [[ -n "$neg_violations" ]]; then
    echo "FAIL: 否定规则会放行 .env（探针未忽略）：$neg_violations"
    FAILED=1
fi

# 0b. 语义核验：未跟踪文件列表不得出现环境文件（否定规则重新暴露会使其出现在
#     git status 中，即使文件尚未提交）；-z NUL 分隔解析（空格路径不拆、不引号化）。
#     注意：bash 命令替换会吞掉 NUL 字节，必须直接管道给 python 解析；
#     git 退出码经 PIPESTATUS 捕获（失败不得被 || true 掩盖）
untracked_env="$(run_timeout git status --porcelain -z --untracked-files=all | "$GATE_PYTHON" -c '
import sys
out = []
for entry in sys.stdin.buffer.read().split(b"\0"):
    if len(entry) >= 4 and entry[:2] == b"??":
        path = entry[3:].decode("utf-8", "replace")
        base = path.rsplit("/", 1)[-1]
        if base == ".env" or base.startswith(".env."):
            out.append(path)
print("\n".join(out))
' | grep -v '^\.env\.example$' || true)"
status_rc=${PIPESTATUS[0]}
if [[ $status_rc -ne 0 ]]; then
    echo "FAIL: git status 失败（退出码 ${status_rc}），无法核验未跟踪环境文件"
    FAILED=1
elif [[ -n "$untracked_env" ]]; then
    echo "FAIL: 存在未被忽略的环境文件：$(echo "$untracked_env" | tr '\n' ' ')"
    FAILED=1
fi

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
