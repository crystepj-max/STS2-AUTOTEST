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
FAILED=0

# 外部命令限时：python3 + psutil 封装（项目依赖，macOS/Linux/Windows 一致）。
# 超时 → 终止整棵进程树（AGENTS.md 防僵尸/防遗留）并返回 142（128+SIGTERM 惯例）；
# 正常结束 → 返回命令退出码。
run_timeout() {
    python3 - "$CMD_TIMEOUT" "$@" <<'PY'
import psutil, subprocess, sys

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

# 提取 JSON 字段：json_field '<json>' '<python 表达式（d 为解析结果）>'
json_field() {
    printf '%s' "$1" | python3 -c "import json,sys; d=json.load(sys.stdin); print($2)"
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
if ! command -v python3 >/dev/null 2>&1; then
    echo "FAIL: 需要 python3（JSON 解析），未找到" >&2
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

# --- 2. PR head ↔ 该 head 的 PR Check Summary ---
if pr_json="$(run_timeout gh api "repos/$REPO/pulls/$PR_NUMBER")"; then
    head_sha="$(json_field "$pr_json" 'd["head"]["sha"]')"
    if checks_json="$(run_timeout gh api "repos/$REPO/commits/$head_sha/check-runs")"; then
        summary="$(json_field "$checks_json" 'next((c["conclusion"] for c in d.get("check_runs", []) if c.get("name") == "PR Check Summary"), "missing")')"
        if [[ "$summary" == "success" ]]; then
            pass "PR #$PR_NUMBER head $head_sha 的 PR Check Summary=success"
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
    if [[ "$enforcement" == "active" && "$thread" == "true" && "$bypass_ok" == "true" ]]; then
        pass "ruleset $RULESET_ID 实时回读：enforcement=${enforcement}、线程解决要求=${thread}、无绕过者"
    else
        fail "ruleset $RULESET_ID 实时回读异常：enforcement=${enforcement}、required_review_thread_resolution=${thread}、bypass_actors_ok=${bypass_ok}"
    fi
else
    fail "拉取 ruleset $RULESET_ID 失败"
fi

# branch protection 层回读（T7 演练曾临时解除 enforce_admins，只查 ruleset 会漏掉该层仍被削弱）
if bp_json="$(run_timeout gh api "repos/$REPO/branches/main/protection")"; then
    enforce_admins="$(json_field "$bp_json" 'str(d.get("enforce_admins", {}).get("enabled")).lower()')"
    strict="$(json_field "$bp_json" 'str(d.get("required_status_checks", {}).get("strict")).lower()')"
    if [[ "$enforce_admins" == "true" && "$strict" == "true" ]]; then
        pass "branch protection 实时回读：enforce_admins=${enforce_admins}、strict=${strict}"
    else
        fail "branch protection 实时回读异常：enforce_admins=${enforce_admins}、strict=${strict}"
    fi
else
    fail "拉取 branch protection 失败"
fi

# --- 4. 治理文档 Markdown 相对链接 ---
broken_links="$(python3 - "$GOV_DOC" <<'PY'
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
import json, pathlib, subprocess, sys, datetime

evidence_json, evidence_dir, repo, timeout = sys.argv[1:5]
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
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


if not ledger:
    check(False, "绕过台账为空（emergency_bypass.ledger 无记录）")

for i, e in enumerate(ledger):
    tag = f"台账[{i}]"
    check(bool(e.get("authorizer")), f"{tag} 授权人已记录（{e.get('authorizer', '')}）")
    check(
        "crystepj-max/STS2-AUTOTEST/issues/" in e.get("reason_url", ""),
        f"{tag} 原因链接指向 issue（{e.get('reason_url', '')}）",
    )
    check(bool(e.get("authorization_note")), f"{tag} 授权说明已记录")
    check(bool(e.get("bypassed_sha")), f"{tag} 被绕过 SHA 已记录（{e.get('bypassed_sha', '')}）")
    check(bool(e.get("completed_at")), f"{tag} 操作完成时间已记录（{e.get('completed_at', '')}）")
    check(e.get("restored") is True, f"{tag} 恢复终态 restored=true")
    check(bool(e.get("restoration_evidence")), f"{tag} 恢复证据列表非空")
    for ev in e.get("restoration_evidence", []):
        p = f"{evidence_dir}/{ev}"
        check(pathlib.Path(p).exists(), f"{tag} 恢复证据文件存在（{ev}）")

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
    ledger_out="$(run_timeout python3 -c "$ledger_code" "$EVIDENCE_JSON" "$EVIDENCE_DIR" "$REPO" "$CMD_TIMEOUT" 2>&1)"
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
