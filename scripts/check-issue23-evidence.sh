#!/usr/bin/env bash
# check-issue23-evidence.sh — issue-23 治理证据自动对账门禁（S4 复审要求）
#
# 交接前核验「正式证据与真实状态一致」，任一不满足 → 退出码 1：
#   1. .gitignore 的 env 条目 ↔ t5-final-evidence.json 的 env_guard.gitignore；
#   2. PR head ↔ 该 head 的 PR Check Summary 结论（必须 success）；
#   3. Issue 正文 ↔ 实时规则（正文含 T8 缺失样例引用与线程解决要求标记；
#      ruleset 回读 enforcement=active + required_review_thread_resolution=true 且无绕过者；
#      branch protection 回读 enforce_admins=true 且 strict=true——紧急演练时两层都被临时改过，
#      只查 ruleset 会漏掉 branch protection 层仍被削弱的情况）；
#   4. 治理文档 Markdown 相对链接可解析；
#   5. 绕过台账逐条核验：授权/原因链接、被绕过 SHA 可解析（远端 compare API）、
#      恢复证据列表非空且文件存在、补验 run success 且 head 一致、
#      制度固定 0~24 小时内完成（不被台账字段放宽）、补验证据文件存在、
#      补验 head 祖先链包含被绕过合并（compare API，与本地克隆深度无关）。
#
# 依赖：gh（远端 API）、bash、python3（JSON 解析）。
# 所有外部调用（gh）自带限时（AGENTS.md 硬规则）。
#
# 可选环境变量：CHECK_ISSUE23_PR（默认 33）、CHECK_ISSUE23_RULESET（默认 19962718）、
#               CHECK_ISSUE23_EVIDENCE（证据 JSON 路径，测试可指定）、
#               CHECK_ISSUE23_CMD_TIMEOUT（秒，默认 15，测试可调小）
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="crystepj-max/STS2-AUTOTEST"
PR_NUMBER="${CHECK_ISSUE23_PR:-33}"
RULESET_ID="${CHECK_ISSUE23_RULESET:-19962718}"
EVIDENCE_JSON="${CHECK_ISSUE23_EVIDENCE:-$REPO_ROOT/.agent-runs/issue-23-main-merge-protection/evidence/t5-final-evidence.json}"
EVIDENCE_DIR="$(dirname "$EVIDENCE_JSON")"
GOV_DOC="$REPO_ROOT/docs/process/main-merge-protection.md"
CMD_TIMEOUT="${CHECK_ISSUE23_CMD_TIMEOUT:-15}"
# 项目 Python 解释器（run_timeout 与内嵌 python 统一使用）：优先项目 venv
# （psutil 依赖的安装位置），Windows venv（.venv/Scripts/python.exe）与
# Unix venv（.venv/bin/python3）均识别；可用 CHECK_ISSUE23_PYTHON 显式指定；
# psutil 缺失时明确失败而非静默挂起。
GATE_PYTHON="${CHECK_ISSUE23_PYTHON:-}"
if [[ -z "$GATE_PYTHON" ]]; then
    if [[ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]]; then
        GATE_PYTHON="$REPO_ROOT/.venv/Scripts/python.exe"
    elif [[ -x "$REPO_ROOT/.venv/bin/python3" ]]; then
        GATE_PYTHON="$REPO_ROOT/.venv/bin/python3"
    else
        GATE_PYTHON="python3"
    fi
fi
FAILED=0

# 外部命令限时：python3 + psutil 封装（项目依赖，macOS/Linux/Windows 一致）。
# 超时 → 终止整棵进程树（AGENTS.md 防僵尸/防遗留）并返回 142（128+SIGTERM 惯例）；
# 正常结束 → 返回命令退出码。
run_timeout() {
    "$GATE_PYTHON" - "$CMD_TIMEOUT" "$@" <<'PY'
import os, signal

try:
    import psutil, subprocess, sys
except ModuleNotFoundError:
    print("run_timeout 需要 psutil（项目依赖）；请使用项目 venv（.venv/bin/python3）或设置 CHECK_ISSUE23_PYTHON", file=sys.stderr)
    sys.exit(1)

timeout = float(sys.argv[1])
# 独立进程组：POSIX 用 start_new_session + killpg；Windows 用 CREATE_NEW_PROCESS_GROUP
# + CTRL_BREAK_EVENT（可送达整组、不可被控制台程序轻易忽略）回收后代
if os.name == "nt":
    popen_kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


    def _kill_group(pgid, sig):  # type: ignore[no-redef]
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


proc = psutil.Popen(sys.argv[2:], **popen_kwargs)
import time

# Windows Python 无 SIGKILL：按平台选择信号（防止构造元组时 AttributeError 崩溃）
GROUP_SIGNALS = [signal.SIGTERM] + ([signal.SIGKILL] if hasattr(signal, "SIGKILL") else [])

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

# 提取 JSON 字段：json_field '<json>' '<python 表达式（d 为解析结果）>'
json_field() {
    printf '%s' "$1" | "$GATE_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print($2)"
}

fail() {
    echo "FAIL: $1"
    FAILED=1
}

pass() {
    echo "PASS: $1"
}

if ! command -v gh >/dev/null 2>&1; then
    echo "FAIL: 需要 gh CLI（远端 API 对账），未找到" >&2
    exit 1
fi
if ! "$GATE_PYTHON" -c "import psutil" 2>/dev/null; then
    echo "FAIL: 需要带 psutil 的 Python（项目依赖）；请使用项目 venv（.venv/bin/python3 或 .venv/Scripts/python.exe）或设置 CHECK_ISSUE23_PYTHON" >&2
    exit 1
fi

echo "===== issue-23 治理证据对账（PR #${PR_NUMBER}） ====="

# --- 1. .gitignore ↔ 证据 JSON env_guard.gitignore ---
if [[ ! -f "$EVIDENCE_JSON" ]]; then
    fail "证据 JSON 不存在：$EVIDENCE_JSON"
else
    expected="$(json_field "$(cat "$EVIDENCE_JSON")" '"\n".join(d["env_guard"]["gitignore"])' | sort)"
    actual="$(grep -E '^[!/]?\.env' "$REPO_ROOT/.gitignore" | sort)"
    if [[ "$expected" == "$actual" ]]; then
        pass ".gitignore env 条目与证据 JSON 一致：$(echo "$expected" | tr '\n' ' ')"
    else
        fail ".gitignore 与证据 JSON 不一致：JSON=[$(echo "$expected" | tr '\n' ' ')] 实际=[$(echo "$actual" | tr '\n' ' ')]"
    fi
fi

# --- 1b. 实际跟踪的环境文件 ↔ 证据 JSON tracked_env_files ---
# （git add -f 强制跟踪的 .env 不会被 .gitignore 规则变化暴露，须直接对账跟踪列表）
if [[ -f "$EVIDENCE_JSON" ]]; then
    ls_output="$(run_timeout git ls-files)"
    ls_rc=$?
    if [[ $ls_rc -ne 0 ]]; then
        fail "无法读取 git 跟踪文件列表（git 退出码 $ls_rc）"
    else
        actual_env="$(printf '%s\n' "$ls_output" | grep -E '(^|/)\.env($|\.)|^\.env' | sort)"
        expected_env="$(json_field "$(cat "$EVIDENCE_JSON")" '"\\n".join(sorted(d["env_guard"]["tracked_env_files"]))' | sort)"
        if [[ "$actual_env" != "$expected_env" ]]; then
            fail "实际跟踪的环境文件与证据 JSON 不一致：实际=[$(echo "$actual_env" | tr '\n' ' ')] 证据=[$(echo "$expected_env" | tr '\n' ' ')]"
        elif [[ "$actual_env" != ".env.example" ]]; then
            # 允许列表约束（与 check-env-gitignore.sh 一致）：即使两侧协调一致，
            # 强制跟踪 .env 等非模板文件仍必须失败
            fail "实际跟踪的环境文件含非模板条目（只允许 .env.example）：$(echo "$actual_env" | tr '\n' ' ')"
        else
            pass "实际跟踪的环境文件与证据 JSON 一致且仅含模板：$(echo "$actual_env" | tr '\n' ' ')"
        fi
    fi
fi

# --- 2. PR head ↔ 该 head 的 PR Check Summary ---
if pr_json="$(run_timeout gh api "repos/$REPO/pulls/$PR_NUMBER")"; then
    head_sha="$(json_field "$pr_json" 'd["head"]["sha"]')"
    if checks_json="$(run_timeout gh api "repos/$REPO/commits/$head_sha/check-runs")"; then
        # 检查结果须绑定 GitHub Actions App（app.id=15368）——其他 App 创建的同名
        # 成功 check 不满足保护要求，也不得算作 PR 门禁结果
        summary="$(json_field "$checks_json" 'next((c["conclusion"] for c in d.get("check_runs", []) if c.get("name") == "PR Check Summary" and c.get("app", {}).get("id") == 15368), "missing")')"
        if [[ "$summary" == "success" ]]; then
            # 绑定正式工作流：解析 details_url 的 run id 并回读 path/event——
            # 其他工作流的同名成功 job（同为 Actions App）不算正式验收结果
            run_url="$(json_field "$checks_json" 'next((c.get("details_url", "") for c in d.get("check_runs", []) if c.get("name") == "PR Check Summary" and c.get("app", {}).get("id") == 15368), "")')"
            run_id="$(printf '%s' "$run_url" | sed -E 's#.*/actions/runs/([0-9]+)/.*#\1#')"
            if [[ -n "$run_id" ]] && run_meta="$(run_timeout gh api "repos/$REPO/actions/runs/$run_id")"; then
                wf_path="$(json_field "$run_meta" 'd.get("path", "")')"
                wf_event="$(json_field "$run_meta" 'd.get("event", "")')"
                if [[ "$wf_path" == ".github/workflows/ci-pr.yml" && "$wf_event" == "pull_request" ]]; then
                    pass "PR #$PR_NUMBER head $head_sha 的 PR Check Summary=success（run ${run_id}，ci-pr.yml/pull_request）"
                else
                    fail "PR #$PR_NUMBER 的 PR Check Summary 非正式工作流（path=${wf_path}、event=${wf_event}）"
                fi
            else
                fail "PR #$PR_NUMBER 的 PR Check Summary 无法绑定工作流（run 回读失败）"
            fi
        else
            fail "PR #$PR_NUMBER head $head_sha 的 PR Check Summary=${summary}（应 success）"
        fi
    else
        fail "拉取 PR #$PR_NUMBER head 的 check-runs 失败"
    fi
else
    fail "拉取 PR #$PR_NUMBER 失败（gh 不可用或网络错误）"
fi

# --- 3. Issue 正文 ↔ 实时规则 ---
if issue_json="$(run_timeout gh api "repos/$REPO/issues/23")"; then
    body="$(json_field "$issue_json" 'd["body"]')"
    if [[ "$body" == *"T8"* && "$body" == *"required_review_thread_resolution=true"* ]]; then
        pass "Issue #23 正文含 T8 缺失样例引用与 required_review_thread_resolution=true 标记"
    else
        fail "Issue #23 正文缺少对账标记（需含 T8 与 required_review_thread_resolution=true）"
    fi
else
    fail "拉取 Issue #23 失败"
fi

if ruleset_json="$(run_timeout gh api "repos/$REPO/rulesets/$RULESET_ID")"; then
    enforcement="$(json_field "$ruleset_json" 'd.get("enforcement", "missing")')"
    thread="$(json_field "$ruleset_json" 'str([r for r in d.get("rules", []) if r.get("type") == "pull_request"][0]["parameters"].get("required_review_thread_resolution")).lower()')"
    bypass_ok="$(json_field "$ruleset_json" 'str(d.get("bypass_actors", []) == [] and d.get("current_user_can_bypass") == "never").lower()')"
    # ruleset 的必填检查是独立 rule 类型 required_status_checks（strict 策略 + context 列表；
    # 须绑定 GitHub Actions App（integration_id 15368），防止同名检查由错误来源满足）
    rsc_ok="$(json_field "$ruleset_json" 'str(any(
        r.get("type") == "required_status_checks"
        and r.get("parameters", {}).get("strict_required_status_checks_policy") is True
        and all(c.get("integration_id") == 15368 for c in r.get("parameters", {}).get("required_status_checks", []))
        and "PR Check Summary" in [c.get("context") for c in r.get("parameters", {}).get("required_status_checks", [])]
        for r in d.get("rules", [])
    )).lower()')"
    # ruleset 必须实际覆盖默认分支（target=branch 且 include 含 ~DEFAULT_BRANCH、
    # exclude 不得匹配默认分支——含通配模式 refs/heads/* 等，按 ruleset ref pattern 语义匹配）
    target="$(json_field "$ruleset_json" 'str(d.get("target") == "branch").lower()')"
    covers_default="$("$GATE_PYTHON" -c "$(cat <<'PY'
import fnmatch, json, sys

d = json.loads(sys.argv[1])


def pat_matches_main(pat):
    if pat in ("~DEFAULT_BRANCH", "refs/heads/main", "main"):
        return True
    # ruleset ref pattern 语义：*、?、[] 通配（fnmatch 完整 glob 语义）
    return fnmatch.fnmatchcase("refs/heads/main", pat)


cond = d.get("conditions", {}).get("ref_name", {})
print(
    str(
        "~DEFAULT_BRANCH" in cond.get("include", [])
        and not any(pat_matches_main(p) for p in cond.get("exclude", []))
    ).lower()
)
PY
)" "$ruleset_json")"
    # 禁止删除分支 / 非快进推送（治理文档声明；与 branch protection 的 allow_* 配套核验）
    del_rule_ok="$(json_field "$ruleset_json" 'str(any(r.get("type") == "deletion" for r in d.get("rules", []))).lower()')"
    nff_rule_ok="$(json_field "$ruleset_json" 'str(any(r.get("type") == "non_fast_forward" for r in d.get("rules", []))).lower()')"
    # 审批数必须为 0（治理文档：solo 维护者无法自审，PR 形态 + 必填检查已构成门禁）
    approvals_zero="$(json_field "$ruleset_json" 'str(
        [r for r in d.get("rules", []) if r.get("type") == "pull_request"][0]["parameters"].get("required_approving_review_count") == 0
    ).lower()')"
    if [[ "$enforcement" == "active" && "$target" == "true" && "$covers_default" == "true" && "$thread" == "true" && "$bypass_ok" == "true" && "$rsc_ok" == "true" && "$del_rule_ok" == "true" && "$nff_rule_ok" == "true" && "$approvals_zero" == "true" ]]; then
        pass "ruleset $RULESET_ID 实时回读：enforcement=${enforcement}、覆盖默认分支、必填 PR Check Summary、线程=${thread}、无绕过者、禁删除/禁非快进、审批 0"
    else
        fail "ruleset $RULESET_ID 实时回读异常：enforcement=${enforcement}、target=${target}、covers_default=${covers_default}、required_check_ok=${rsc_ok}、thread=${thread}、bypass_actors_ok=${bypass_ok}、deletion_rule=${del_rule_ok}、non_fast_forward_rule=${nff_rule_ok}、approvals_zero=${approvals_zero}"
    fi
else
    fail "拉取 ruleset $RULESET_ID 失败"
fi

# branch protection 层回读（T7 演练曾临时解除 enforce_admins，只查 ruleset 会漏掉该层仍被削弱；
# 两层都必须实际要求 PR Check Summary——PR head 残留同名成功 check 不代表保护仍要求它）
if bp_json="$(run_timeout gh api "repos/$REPO/branches/main/protection")"; then
    enforce_admins="$(json_field "$bp_json" 'str(d.get("enforce_admins", {}).get("enabled")).lower()')"
    strict="$(json_field "$bp_json" 'str(d.get("required_status_checks", {}).get("strict")).lower()')"
    rsc_ok="$(json_field "$bp_json" 'str(
        "PR Check Summary" in d.get("required_status_checks", {}).get("contexts", [])
        and any(
            c.get("context") == "PR Check Summary" and c.get("app_id") == 15368
            for c in d.get("required_status_checks", {}).get("checks", [])
        )
    ).lower()')"
    # 禁止删除 / 禁止 force push（治理文档声明，须与 ruleset 的 deletion/non_fast_forward 规则配套）
    no_del="$(json_field "$bp_json" 'str(not d.get("allow_deletions", {}).get("enabled", False)).lower()')"
    no_ff="$(json_field "$bp_json" 'str(not d.get("allow_force_pushes", {}).get("enabled", False)).lower()')"
    # 审批要求必须未启用（治理文档：solo 维护者无法自审）。
    # 注意：未启用审批时 GitHub 返回 required_pull_request_reviews=null，须先或 {} 再取字段
    approvals_zero="$(json_field "$bp_json" 'str(not (d.get("required_pull_request_reviews") or {}).get("required_approving_review_count", 0)).lower()')"
    if [[ "$enforce_admins" == "true" && "$strict" == "true" && "$rsc_ok" == "true" && "$no_del" == "true" && "$no_ff" == "true" && "$approvals_zero" == "true" ]]; then
        pass "branch protection 实时回读：enforce_admins=${enforce_admins}、strict=${strict}、必填 PR Check Summary、禁删除/禁 force push、无审批要求"
    else
        fail "branch protection 实时回读异常：enforce_admins=${enforce_admins}、strict=${strict}、required_check_ok=${rsc_ok}、no_deletions=${no_del}、no_force_push=${no_ff}、approvals_zero=${approvals_zero}"
    fi
else
    fail "拉取 branch protection 失败"
fi

# --- 3b. 证据 JSON 的 branch_protection/ruleset 记录 ↔ 实时回读对账 ---
# （同 env_guard.gitignore 的双向对账模式：正式证据不得与真实治理状态矛盾）
if [[ -f "$EVIDENCE_JSON" ]] && [[ -n "${bp_json:-}" ]] && [[ -n "${ruleset_json:-}" ]]; then
    bp_record="$(json_field "$(cat "$EVIDENCE_JSON")" 'json.dumps({
        "checks": sorted(d.get("branch_protection", {}).get("required_status_checks", [])),
        "strict": d.get("branch_protection", {}).get("strict"),
        "enforce_admins": d.get("branch_protection", {}).get("enforce_admins"),
        "approvals": d.get("branch_protection", {}).get("required_approving_review_count"),
    }, sort_keys=True)')"
    bp_live="$(json_field "$bp_json" 'json.dumps({
        "checks": sorted(d.get("required_status_checks", {}).get("contexts", [])),
        "strict": d.get("required_status_checks", {}).get("strict"),
        "enforce_admins": d.get("enforce_admins", {}).get("enabled"),
        "approvals": (d.get("required_pull_request_reviews") or {}).get("required_approving_review_count", 0),
    }, sort_keys=True)')"
    if [[ "$bp_record" == "$bp_live" ]]; then
        pass "证据 JSON branch_protection 与实时回读一致"
    else
        fail "证据 JSON branch_protection 与实时回读不一致：JSON=[${bp_record}] 实时=[${bp_live}]"
    fi

    rs_record="$(json_field "$(cat "$EVIDENCE_JSON")" 'json.dumps({
        "name": d.get("ruleset", {}).get("name"),
        "checks": sorted(d.get("ruleset", {}).get("required_status_checks", [])),
        "strict": d.get("ruleset", {}).get("strict"),
        "thread": d.get("ruleset", {}).get("required_review_thread_resolution"),
        "bypass": d.get("ruleset", {}).get("bypass_actors"),
        "can_bypass": d.get("ruleset", {}).get("current_user_can_bypass"),
    }, sort_keys=True)')"
    rs_live="$(json_field "$ruleset_json" 'json.dumps({
        "name": d.get("name"),
        "checks": sorted(c.get("context") for c in next(
            r.get("parameters", {}).get("required_status_checks", []) for r in d.get("rules", [])
            if r.get("type") == "required_status_checks"
        )),
        "strict": next(
            r.get("parameters", {}).get("strict_required_status_checks_policy") for r in d.get("rules", [])
            if r.get("type") == "required_status_checks"
        ),
        "thread": next(
            r.get("parameters", {}).get("required_review_thread_resolution") for r in d.get("rules", [])
            if r.get("type") == "pull_request"
        ),
        "bypass": d.get("bypass_actors"),
        "can_bypass": d.get("current_user_can_bypass"),
    }, sort_keys=True)')"
    if [[ "$rs_record" == "$rs_live" ]]; then
        pass "证据 JSON ruleset 与实时回读一致"
    else
        fail "证据 JSON ruleset 与实时回读不一致：JSON=[${rs_record}] 实时=[${rs_live}]"
    fi
else
    fail "证据 JSON 缺失或规则回读未完成，无法对账 branch_protection/ruleset 记录"
fi

# --- 4. 治理文档 Markdown 相对链接 ---
broken_links="$("$GATE_PYTHON" - "$GOV_DOC" <<'PY'
import re, sys, pathlib
doc = pathlib.Path(sys.argv[1])
base = doc.parent
text = doc.read_text(encoding="utf-8")
broken = []
for m in re.finditer(r"\]\(([^)]+)\)", text):
    link = m.group(1)
    if "://" in link or link.startswith(("#", "mailto:")):
        continue
    if not (base / link).resolve().exists():
        broken.append(link)
print("\n".join(broken))
PY
)"
if [[ -z "$broken_links" ]]; then
    pass "治理文档 Markdown 相对链接全部可解析"
else
    fail "治理文档存在失效相对链接：$(echo "$broken_links" | tr '\n' ' ')"
fi

# --- 5. 绕过台账逐条核验（授权/原因/SHA/恢复终态/24h 补验） ---
# 注：非交互 bash 中后台任务（run_timeout 的 &）stdin 被重定向为 /dev/null，
# 故 Python 代码经 $(cat <<'PY') 取出后以 -c 传入，不能靠 stdin 传递脚本。
if [[ -f "$EVIDENCE_JSON" ]]; then
    ledger_code="$(cat <<'PY'
import json, os, pathlib, psutil, re, signal, subprocess, sys, time, datetime

evidence_json, evidence_dir, evidence_rel, repo, timeout = sys.argv[1:6]
timeout = float(timeout)
data = json.load(open(evidence_json, encoding="utf-8"))
ledger = data.get("emergency_bypass", {}).get("ledger", [])
failed = 0


def check(ok: bool, msg: str) -> None:
    global failed
    print(("PASS" if ok else "FAIL") + ": " + msg)
    if not ok:
        failed = 1


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    """执行外部调用并整树超时回收（独立进程组 + psutil 树清理，防遗留后代进程）。

    显式 UTF-8 解码：gh API 输出可能含中文标题/正文，Windows 默认代码页解码会抛错。
    """
    if os.name == "nt":
        popen_kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        popen_kwargs = {"start_new_session": True}
    proc = psutil.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # 父进程已退出但后代被重新托管后 children() 为空——按进程组回收，
        # TERM → 宽限 → KILL 升级（防忽略 TERM 的后代持有管道）
        group_signals = [signal.SIGTERM] + ([signal.SIGKILL] if hasattr(signal, "SIGKILL") else [])
        for sig in group_signals:
            try:
                if os.name == "nt":
                    os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(proc.pid, sig)
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
            except (psutil.NoSuchProcess, subprocess.TimeoutExpired):
                pass
        raise
    return subprocess.CompletedProcess(
        cmd,
        proc.returncode,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


if not ledger:
    check(False, "绕过台账为空（emergency_bypass.ledger 无记录）")

for i, e in enumerate(ledger):
    tag = f"台账[{i}]"
    # 授权人仅限仓库所有者 crystepj-max（治理文档「执行步骤（授权人：仅限仓库所有者）」）
    check(
        e.get("authorizer") == "crystepj-max",
        f"{tag} 授权人为仓库所有者（{e.get('authorizer', '')}）",
    )
    # 原因链接须绑定当前仓库且可回读：host/owner/repo 必须匹配，解析 issues/pull 数字
    # 并请求确认资源存在（其他仓库的链接或不存在 Issue 均不算可审计原因）
    m = re.match(
        rf"^https://github\.com/{re.escape(repo)}/(?:issues|pull)/(\d+)/?$",
        e.get("reason_url", ""),
    )
    if not m:
        check(False, f"{tag} 原因链接格式不正确（应为 issues/<数字> 或 pull/<数字>）：{e.get('reason_url', '')}")
    else:
        r = run(["gh", "api", f"repos/{repo}/issues/{m.group(1)}"])
        if r.returncode != 0:
            check(False, f"{tag} 原因链接资源可回读（issues/{m.group(1)}）")
        else:
            issue_data = json.loads(r.stdout)
            body = issue_data.get("body", "")
            # 授权记录须绑定本次绕过：正文含紧急绕过条款 + 本条的 PR 号与被绕过 SHA
            # 前缀（仅泛泛提及「紧急绕过」会放过无关既存 Issue/事后补写）；资源创建
            # 时间须早于操作完成时间
            check(
                "紧急绕过" in body
                and f"PR #{e.get('bypassed_pr')}" in body
                and e.get("bypassed_sha", "")[:7] in body,
                f"{tag} 原因链接资源正文含本次绕过的授权记录（PR #{e.get('bypassed_pr')} / {e.get('bypassed_sha', '')[:7]}）",
            )
            check(
                issue_data.get("created_at", "") <= e.get("completed_at", ""),
                f"{tag} 授权记录时间早于操作（created_at={issue_data.get('created_at')}）",
            )
            # 时间戳授权记录：台账须引用具体评论 ID（不可变记录），按 ID 直接回读
            # （无分页问题），核验归属、发布者（仓库所有者）、时间早于操作、正文含
            # 「紧急绕过」条款——Issue 正文事后补写会被较早的 created_at 掩盖，
            # 评论 ID + 时间戳可验证且不可抵赖
            auth_cid = e.get("authorization_comment_id")
            # 不可变专用授权记录：台账须引用 git 提交的授权记录文件（内容不可变），
            # 文件须绑定本条 bypassed_pr 与 bypassed_sha（正文可编辑的 Issue 不算）
            auth_file = e.get("authorization_record_file")
            if not auth_file:
                check(False, f"{tag} 缺少 authorization_record_file（须引用不可变授权记录文件）")
            else:
                af = pathlib.Path(f"{evidence_dir}/{auth_file}")
                if not af.exists():
                    check(False, f"{tag} 授权记录文件存在（{auth_file}）")
                else:
                    # 文件必须已被 git 跟踪，且从已提交 blob 读取内容核验绑定——
                    # 工作树未提交修改（事后补入 PR/SHA）不算不可变记录
                    tracked = run(["git", "ls-files", "--error-unmatch", str(af)])
                    check(tracked.returncode == 0, f"{tag} 授权记录文件 {auth_file} 已被 git 跟踪")
                    blob = run(["git", "show", f"HEAD:{evidence_rel}/{auth_file}"])
                    if blob.returncode != 0:
                        check(False, f"{tag} 授权记录文件 {auth_file} 无法从已提交 blob 读取")
                    else:
                        af_body = blob.stdout
                        check(
                            f"PR #{e.get('bypassed_pr')}" in af_body
                            and e.get("bypassed_sha", "")[:7] in af_body,
                            f"{tag} 授权记录文件 {auth_file} 已提交内容绑定本次绕过（PR #{e.get('bypassed_pr')} / {e.get('bypassed_sha', '')[:7]}）",
                        )
            if not auth_cid:
                check(False, f"{tag} 缺少 authorization_comment_id（须引用具体授权评论）")
            else:
                cmt = run(["gh", "api", f"repos/{repo}/issues/comments/{auth_cid}"])
                if cmt.returncode != 0:
                    check(False, f"{tag} 授权评论 {auth_cid} 不存在")
                else:
                    auth_comment = json.loads(cmt.stdout)
                    # 精确匹配 URL 末尾的 Issue 编号（子串匹配会误判 issues/230 含 issues/23）
                    issue_m = re.search(r"/issues/(\d+)/?$", auth_comment.get("issue_url", ""))
                    check(
                        issue_m is not None and issue_m.group(1) == m.group(1),
                        f"{tag} 授权评论 {auth_cid} 属于原因链接资源（issue #{m.group(1)}）",
                    )
                    check(
                        auth_comment.get("user", {}).get("login") == "crystepj-max",
                        f"{tag} 授权评论 {auth_cid} 由仓库所有者发布（{auth_comment.get('user', {}).get('login')}）",
                    )
                    check(
                        auth_comment.get("created_at", "") <= e.get("completed_at", ""),
                        f"{tag} 授权评论 {auth_cid} 早于操作（created_at={auth_comment.get('created_at')}）",
                    )
                    # 评论正文可编辑（updated_at 反映）——事后编辑加入授权文字不算事前授权
                    check(
                        auth_comment.get("updated_at", "") <= e.get("completed_at", ""),
                        f"{tag} 授权评论 {auth_cid} 未经事后编辑（updated_at={auth_comment.get('updated_at')}）",
                    )
                    check(
                        "紧急绕过" in auth_comment.get("body", ""),
                        f"{tag} 授权评论 {auth_cid} 正文含紧急绕过条款",
                    )
    check(bool(e.get("authorization_note")), f"{tag} 授权说明已记录")
    check(bool(e.get("bypassed_sha")), f"{tag} 被绕过 SHA 已记录（{e.get('bypassed_sha', '')}）")
    check(bool(e.get("completed_at")), f"{tag} 操作完成时间已记录（{e.get('completed_at', '')}）")
    # 被绕过 SHA 必须绑定台账中的 PR：回读 PR 的 merge_commit_sha / merged_at 与台账一致
    # （任意祖先 SHA 都能通过 compare，但只有真实 merge commit 才算审计闭环）
    pr_num = e.get("bypassed_pr")
    if pr_num:
        pr = run(["gh", "api", f"repos/{repo}/pulls/{pr_num}"])
        if pr.returncode != 0:
            check(False, f"{tag} 拉取被绕过 PR #{pr_num} 失败")
        else:
            pr_data = json.loads(pr.stdout)
            check(
                pr_data.get("merge_commit_sha") == e.get("bypassed_sha"),
                f"{tag} 被绕过 SHA 与 PR #{pr_num} 的 merge_commit_sha 一致",
            )
            check(
                pr_data.get("merged_at") == e.get("completed_at"),
                f"{tag} 台账完成时间与 PR #{pr_num} 的 merged_at 一致",
            )
    else:
        check(False, f"{tag} 缺少 bypassed_pr（无法绑定被绕过 SHA）")
    check(e.get("restored") is True, f"{tag} 恢复终态 restored=true")
    check(bool(e.get("restoration_evidence")), f"{tag} 恢复证据列表非空")
    saw_ruleset_snapshot = False
    saw_bp_snapshot = False
    for ev in e.get("restoration_evidence", []):
        p = pathlib.Path(f"{evidence_dir}/{ev}")
        if not p.exists():
            check(False, f"{tag} 恢复证据文件存在（{ev}）")
            continue
        check(True, f"{tag} 恢复证据文件存在（{ev}）")
        # 内容核验（存在性不够）：ruleset 回读须无绕过者；branch protection 回读须
        # enforce_admins=true——恢复证据自相矛盾（如误用 during 快照）必须失败
        try:
            snap = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            check(False, f"{tag} 恢复证据文件 {ev} 无法解析为 JSON")
            continue
        if "bypass_actors" in snap:
            saw_ruleset_snapshot = True
            check(
                snap.get("bypass_actors") == [] and snap.get("current_user_can_bypass") == "never",
                f"{tag} 恢复证据 {ev} 无绕过者（ruleset 终态）",
            )
        if "enforce_admins" in snap:
            saw_bp_snapshot = True
            # API 形态兼容：旧快照为布尔（true/false），现行 API 为 {"enabled": bool}
            ea = snap.get("enforce_admins")
            ea_ok = ea.get("enabled") is True if isinstance(ea, dict) else ea is True
            check(ea_ok, f"{tag} 恢复证据 {ev} enforce_admins=true（branch protection 终态）")
        if "bypass_actors" in snap or "enforce_admins" in snap:
            # 快照须绑定到本次绕过操作之后：git 提交时间可验证地晚于操作完成时间，
            # 防止把演练前已处于安全状态的旧快照（如 t1 回读）当作恢复证据
            gt = run(["git", "log", "-1", "--format=%cI", "--", str(p)])
            if gt.returncode != 0 or not gt.stdout.strip():
                check(False, f"{tag} 恢复证据 {ev} 无法核验提交时间（须晚于操作完成时间）")
            else:
                try:
                    committed = datetime.datetime.fromisoformat(
                        gt.stdout.strip().replace("Z", "+00:00")
                    ).astimezone(datetime.timezone.utc)
                    operated = datetime.datetime.fromisoformat(
                        e["completed_at"].replace("Z", "+00:00")
                    ).astimezone(datetime.timezone.utc)
                    check(
                        committed >= operated,
                        f"{tag} 恢复证据 {ev} 提交时间（{gt.stdout.strip()}）晚于操作完成时间",
                    )
                except ValueError as err:
                    check(False, f"{tag} 恢复证据 {ev} 提交时间解析失败：{err}")
    # 恢复证据集合必须包含并验证两类终态快照（任意可解析 JSON 偶然入列不算数）
    check(saw_ruleset_snapshot, f"{tag} 恢复证据含 ruleset 绕过终态快照")
    check(saw_bp_snapshot, f"{tag} 恢复证据含 branch protection enforce_admins 终态快照")

    pv = e.get("post_verification", {})
    run_id = pv.get("run_id")
    if run_id:
        r = run(["gh", "api", f"repos/{repo}/actions/runs/{run_id}"])
        if r.returncode != 0:
            check(False, f"{tag} 拉取补验 run {run_id} 失败（gh）")
        else:
            run_data = json.loads(r.stdout)
            check(run_data.get("conclusion") == "success", f"{tag} 补验 run {run_id} conclusion={run_data.get('conclusion')}（应 success）")
            check(
                run_data.get("head_sha") == pv.get("head_sha"),
                f"{tag} 补验 run head {run_data.get('head_sha')} 与台账一致",
            )
            # 台账记录的补验结论/完成时间须与 run 双向一致（正式证据不得与真实状态矛盾）
            check(
                pv.get("conclusion") == run_data.get("conclusion"),
                f"{tag} 台账补验结论（{pv.get('conclusion')}）与 run 一致",
            )
            check(
                pv.get("completed_at") == (run_data.get("completed_at") or run_data.get("updated_at")),
                f"{tag} 台账补验完成时间（{pv.get('completed_at')}）与 run 一致",
            )
            # 补验 run 必须绑定正式验收工作流 ci-pr.yml（pull_request 事件）——
            # 其他工作流的同名轻量 job 不构成等价验收
            check(
                run_data.get("path") == ".github/workflows/ci-pr.yml"
                and run_data.get("event") == "pull_request",
                f"{tag} 补验 run 绑定 ci-pr.yml 工作流（path={run_data.get('path')}、event={run_data.get('event')}）",
            )
            # 补验 run 必须真实执行等价门禁：含成功的 PR Check Summary job
            # （仅 conclusion=success 的任意轻量 run 不构成等价验收）
            jobs = run(["gh", "api", f"repos/{repo}/actions/runs/{run_id}/jobs"])
            if jobs.returncode != 0:
                check(False, f"{tag} 拉取补验 run {run_id} 的 jobs 失败")
            else:
                job_list = json.loads(jobs.stdout).get("jobs", [])
                summary_job = next(
                    (j for j in job_list if j.get("name") == "PR Check Summary"), None
                )
                check(
                    summary_job is not None and summary_job.get("conclusion") == "success",
                    f"{tag} 补验 run 含成功 PR Check Summary job（{summary_job.get('conclusion') if summary_job else '缺失'}）",
                )
            try:
                # 部分 run 的 completed_at 为空，回退 updated_at（实测 31774866515 用 updated_at）
                done_at = run_data.get("completed_at") or run_data.get("updated_at") or ""
                done = datetime.datetime.fromisoformat(done_at.replace("Z", "+00:00"))
                base = datetime.datetime.fromisoformat(e["completed_at"].replace("Z", "+00:00"))
                hours = (done - base).total_seconds() / 3600
                # 制度固定窗口：0 ≤ 补验耗时 ≤ 24h（不被台账 within_hours 放宽；
                # hours<0 表示补验时间早于操作完成时间，同样不通过）
                within = 0 <= hours <= 24
                check(within, f"{tag} 补验在操作后 {hours:.1f}h 内完成（制度固定 0~24h）")
            except (KeyError, ValueError) as err:
                check(False, f"{tag} 补验时间解析失败：{err}")
            check(
                pv.get("covers_bypassed_merge") is True,
                f"{tag} 台账声明补验 head 覆盖被绕过合并",
            )
            if e.get("bypassed_sha") and pv.get("head_sha"):
                # compare API 核验祖先（与本地克隆深度无关）：status=ahead/identical
                # 表示 base（被绕过 SHA）是 head（补验 run head）的祖先；base 不存在 → 404
                cmp = run(["gh", "api", f"repos/{repo}/compare/{e['bypassed_sha']}...{pv['head_sha']}"])
                if cmp.returncode != 0:
                    check(False, f"{tag} 被绕过 SHA 在远端不可解析（compare API 失败）")
                else:
                    status = json.loads(cmp.stdout).get("status", "")
                    check(
                        status in ("ahead", "identical"),
                        f"{tag} 补验 head 祖先链包含被绕过合并（compare status={status}）",
                    )
    else:
        check(False, f"{tag} 缺少补验 run_id")

    check(bool(pv.get("evidence_file")), f"{tag} 补验证据文件已指定")
    ev_file = pv.get("evidence_file")
    if ev_file:
        check(pathlib.Path(f"{evidence_dir}/{ev_file}").exists(), f"{tag} 补验证据文件存在（{ev_file}）")

sys.exit(failed)
PY
)"
    # 台账核验本身不设总限时：内层每次 gh/git 调用已各自限时（run() 内 timeout），
    # 外层再套 run_timeout 会把「多次调用累计耗时」误判为超时（网络稍慢即误报）
    # git show HEAD:<path> 需要仓库相对路径
    evidence_rel="${EVIDENCE_DIR#"$REPO_ROOT"/}"
    ledger_out="$("$GATE_PYTHON" -c "$ledger_code" "$EVIDENCE_JSON" "$EVIDENCE_DIR" "$evidence_rel" "$REPO" "$CMD_TIMEOUT" 2>&1)"
    if [[ $? -eq 0 ]]; then
        pass "绕过台账核验全部通过"
    else
        fail "绕过台账核验存在失败项"
    fi
    echo "$ledger_out"
fi

echo
if [[ "$FAILED" -eq 0 ]]; then
    echo "check-issue23-evidence.sh 全部通过 ✓"
    exit 0
else
    echo "check-issue23-evidence.sh 存在失败项 ✗"
    exit 1
fi
