"""issue-23 治理证据对账门禁（scripts/check-issue23-evidence.sh）的测试。

背景（S4 复审要求）：正式证据曾出现与真实状态不一致——`.env` 规则写成 `/env`、
候选 SHA/run 引用旧值、Issue 正文与实时规则脱节、治理文档相对链接失效。
要求补「自动对账门禁」：交接前自动核验候选 SHA/run headSha、`.gitignore` 与 JSON、
Issue 正文与实时规则、Markdown 相对链接，并逐条核验绕过台账。

本测试以「假 gh」注入 PATH 模拟远端 API（PR head、check-runs、run、ruleset、
issue 正文），用真实仓库文件验证脚本在证据一致时通过、在证据冲突时失败退出。
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import psutil
import pytest

REPO_ROOT = Path(__file__).parents[2]
SCRIPT = "scripts/check-issue23-evidence.sh"
EVIDENCE_JSON = Path(
    ".agent-runs/issue-23-main-merge-protection/evidence/t5-final-evidence.json"
)

# 与 t5-final-evidence.json 绕过台账中补验记录一致的假 gh 固定响应
DEFAULT_RUN_JSON = json.dumps(
    {
        "conclusion": "success",
        "head_sha": "64ed09fcd1f0174ca211da7170ce61da2a1b6b50",
        "completed_at": "2026-08-14T07:47:43Z",
        "updated_at": "2026-08-14T07:47:43Z",
        "path": ".github/workflows/ci-pr.yml",
        "event": "pull_request",
    }
)
DEFAULT_PR_JSON = json.dumps(
    {
        "head": {"sha": "3a732858af66051db3962a76be4ad7379f1f2c76"},
        # 台账被绕过 PR #31 的 merge 事实（台账核验会回读比对）
        "merge_commit_sha": "750ba9768159c3e310bf906abf84a1207f292cbe",
        "merged_at": "2026-08-14T05:50:11Z",
    }
)
DEFAULT_CHECKS_JSON = json.dumps(
    {
        "check_runs": [
            {
                "name": "PR Check Summary",
                "conclusion": "success",
                "app": {"id": 15368},
                "details_url": "https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31833525853/job/94874564465",
            }
        ]
    }
)
DEFAULT_RULESET_JSON = json.dumps(
    {
        "name": "Autotest protect",
        "enforcement": "active",
        "target": "branch",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "bypass_actors": [],
        "current_user_can_bypass": "never",
        "rules": [
            {"type": "deletion", "parameters": None},
            {"type": "non_fast_forward", "parameters": None},
            {
                "type": "pull_request",
                "parameters": {
                    "required_review_thread_resolution": True,
                    "required_approving_review_count": 0,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": "PR Check Summary", "integration_id": 15368}
                    ],
                },
            },
        ],
    }
)
# 补验 run 的 jobs：含成功的 PR Check Summary 才算等价验收
DEFAULT_JOBS_JSON = json.dumps(
    {"jobs": [{"name": "PR Check Summary", "conclusion": "success"}]}
)
# 授权评论（按 ID 直接回读）：台账 authorization_comment_id 引用的预存授权记录
# （真实 Issue #23 的 5289650944，04:56:31Z，作者 crystepj-max，早于绕过操作 05:50:11Z）
DEFAULT_COMMENT_JSON = json.dumps(
    {
        "id": 5289650944,
        "issue_url": "https://api.github.com/repos/crystepj-max/STS2-AUTOTEST/issues/23",
        "user": {"login": "crystepj-max"},
        "created_at": "2026-08-14T04:56:31Z",
        "body": "紧急绕过有明确权限、原因记录和事后补验要求",
    }
)
# branch protection 层（T7 演练曾临时解除 enforce_admins，须逐层回读）
DEFAULT_BP_JSON = json.dumps(
    {
        "enforce_admins": {"enabled": True},
        "required_status_checks": {
            "strict": True,
            "contexts": ["PR Check Summary"],
            "checks": [{"context": "PR Check Summary", "app_id": 15368}],
        },
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "allow_deletions": {"enabled": False},
        "allow_force_pushes": {"enabled": False},
    }
)
# compare API：status=ahead 表示 base（被绕过 SHA）是 head（补验 run head）的祖先
DEFAULT_COMPARE_JSON = json.dumps({"status": "ahead"})
# 与待更新 Issue 正文保持一致的对账标记（含本次绕过的授权记录：PR #31 / SHA 前缀）
DEFAULT_ISSUE_BODY = (
    "紧急绕过授权记录：S4 复审演练要求（T7），合并被阻断的探针 PR #31（750ba976）\n"
    "缺失样例证据：T8（探针 PR #31）\n"
    "线程解决要求：required_review_thread_resolution=true"
)


# 假 gh 静态模板：载荷一律经环境变量 FAKE_* 传入（避免 bash 3.2 双引号内
# ${VAR:='...'} 剥离默认值引号的坑），测试统一在 _base_env 中注入。
_FAKE_GH_TEMPLATE = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail
    url=""
    while [[ $# -gt 0 ]]; do
        if [[ "$1" == "api" ]]; then
            url="$2"
            break
        fi
        shift
    done
    if [[ -n "${FAKE_GH_FAIL_FRAGMENT:-}" && "$url" == *"$FAKE_GH_FAIL_FRAGMENT"* ]]; then
        echo "fake gh: 404 (forced by FAKE_GH_FAIL_FRAGMENT)" >&2
        exit 1
    fi
    case "$url" in
        *"/actions/runs/"*"/jobs"*) echo "$FAKE_JOBS_JSON" ;;
        *"/actions/runs/"*) echo "$FAKE_RUN_JSON" ;;
        *"/commits/"*"/check-runs") echo "$FAKE_CHECKS_JSON" ;;
        *"/pulls/"*) echo "$FAKE_PR_JSON" ;;
        *"/rulesets/"*) echo "$FAKE_RULESET_JSON" ;;
        *"/branches/main/protection"*) echo "$FAKE_BP_JSON" ;;
        *"/compare/"*) echo "$FAKE_COMPARE_JSON" ;;
        *"/issues/comments/"*) echo "$FAKE_COMMENT_JSON" ;;
        *"/issues/"*) "${FAKE_GH_PYTHON:-python3}" -c "import json,os,sys; print(json.dumps({'body': sys.stdin.read(), 'created_at': os.environ.get('FAKE_ISSUE_CREATED_AT', '2026-08-14T00:00:00Z')}))" <<< "$FAKE_ISSUE_BODY" ;;
        *) echo "fake gh: 未预期的 URL: $url" >&2; exit 1 ;;
    esac
    """
)


def _fake_gh(tmp_path: Path) -> Path:
    """在 tmp_path/bin/gh 生成按 URL 路由返回固定响应的假 gh，返回 bin 目录。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(_FAKE_GH_TEMPLATE, encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    return bin_dir


def _run_script(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """运行对账脚本（60s 超时，超时后终止整棵进程树，与门禁测试清理模式一致）。"""
    proc = psutil.Popen(
        ["bash", SCRIPT],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        try:
            children = proc.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
        try:
            proc.wait(timeout=5)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired, subprocess.TimeoutExpired):
            pass
        pytest.fail("对账脚本超过 60s 未结束（可能外部调用阻塞）")

    # 脚本输出为 UTF-8 中文字节，显式按 UTF-8 解码（errors=replace 兜底非 UTF-8 字节）
    return subprocess.CompletedProcess(
        args=["bash", SCRIPT],
        returncode=proc.returncode,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
    )


def _base_env(bin_dir: Path, tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "LC_ALL"}
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["FAKE_GH_PYTHON"] = overrides.pop("FAKE_GH_PYTHON", sys.executable)
    env["FAKE_GH_FAIL_FRAGMENT"] = overrides.pop("FAKE_GH_FAIL_FRAGMENT", "")
    env["FAKE_ISSUE_CREATED_AT"] = overrides.pop("FAKE_ISSUE_CREATED_AT", "2026-08-14T00:00:00Z")
    env["FAKE_COMMENT_JSON"] = overrides.pop("FAKE_COMMENT_JSON", DEFAULT_COMMENT_JSON)
    env["FAKE_RUN_JSON"] = overrides.pop("FAKE_RUN_JSON", DEFAULT_RUN_JSON)
    env["FAKE_JOBS_JSON"] = overrides.pop("FAKE_JOBS_JSON", DEFAULT_JOBS_JSON)
    env["FAKE_CHECKS_JSON"] = overrides.pop("FAKE_CHECKS_JSON", DEFAULT_CHECKS_JSON)
    env["FAKE_PR_JSON"] = overrides.pop("FAKE_PR_JSON", DEFAULT_PR_JSON)
    env["FAKE_RULESET_JSON"] = overrides.pop("FAKE_RULESET_JSON", DEFAULT_RULESET_JSON)
    env["FAKE_BP_JSON"] = overrides.pop("FAKE_BP_JSON", DEFAULT_BP_JSON)
    env["FAKE_COMPARE_JSON"] = overrides.pop("FAKE_COMPARE_JSON", DEFAULT_COMPARE_JSON)
    env["FAKE_ISSUE_BODY"] = overrides.pop("FAKE_ISSUE_BODY", DEFAULT_ISSUE_BODY)
    env.update(overrides)
    return env


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_evidence_gate_accepts_consistent_state(tmp_path: Path) -> None:
    """证据与真实状态一致时对账门禁应通过（exit 0）。"""
    env = _base_env(_fake_gh(tmp_path), tmp_path)
    proc = _run_script(env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "全部通过" in proc.stdout


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_run_failure(tmp_path: Path) -> None:
    """补验 run 非 success 时对账门禁应失败。"""
    run_json = json.dumps(
        {
            "conclusion": "failure",
            "head_sha": "64ed09fcd1f0174ca211da7170ce61da2a1b6b50",
            "completed_at": "2026-08-14T07:47:43Z",
        }
    )
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RUN_JSON=run_json)
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "补验" in proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "completed_at,label",
    [
        ("2026-08-16T07:47:43Z", "超过 24h（操作完成 05:50:11Z 后 2 天）"),
        ("2026-08-14T04:00:00Z", "早于操作完成时间（负时长）"),
    ],
)
@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_24h_window_violation(tmp_path: Path, completed_at: str, label: str) -> None:
    """补验窗口制度固定 0~24h：超出或负时长时对账门禁应失败。"""
    run_json = json.dumps(
        {
            "conclusion": "success",
            "head_sha": "64ed09fcd1f0174ca211da7170ce61da2a1b6b50",
            "completed_at": completed_at,
        }
    )
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RUN_JSON=run_json)
    proc = _run_script(env)
    assert proc.returncode != 0, f"{label} 应使对账门禁失败"
    assert "24" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_gitignore_json_mismatch(tmp_path: Path) -> None:
    """.gitignore 与证据 JSON 的 env 条目不一致时对账门禁应失败。"""
    data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    data["env_guard"]["gitignore"] = [".env", ".env.*", "!.env.example", ".env.local"]
    broken_json = tmp_path / "t5-broken.json"
    broken_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    env = _base_env(_fake_gh(tmp_path), tmp_path)
    env["CHECK_ISSUE23_EVIDENCE"] = str(broken_json)
    proc = _run_script(env)
    assert proc.returncode != 0
    assert ".env" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_issue_body_out_of_sync(tmp_path: Path) -> None:
    """Issue 正文缺少 T8 / 线程解决要求标记时对账门禁应失败。"""
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_ISSUE_BODY="旧正文：缺失检查证据 T3b")
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "Issue" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_disabled_ruleset(tmp_path: Path) -> None:
    """ruleset enforcement 非 active 时对账门禁应失败（已停用的保护不算生效）。"""
    ruleset_json = json.loads(DEFAULT_RULESET_JSON)
    ruleset_json["enforcement"] = "disabled"
    env = _base_env(
        _fake_gh(tmp_path),
        tmp_path,
        FAKE_RULESET_JSON=json.dumps(ruleset_json),
    )
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "enforcement" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_branch_protection_weakened(tmp_path: Path) -> None:
    """branch protection 层 enforce_admins=false 时对账门禁应失败（只查 ruleset 会漏掉）。"""
    bp_json = json.loads(DEFAULT_BP_JSON)
    bp_json["enforce_admins"]["enabled"] = False
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_BP_JSON=json.dumps(bp_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "branch protection" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_ruleset_missing_required_check(tmp_path: Path) -> None:
    """ruleset 移除了必填 PR Check Summary 规则时对账门禁应失败（残留同名成功 check 不算数）。"""
    ruleset_json = json.loads(DEFAULT_RULESET_JSON)
    ruleset_json["rules"] = [
        r for r in ruleset_json["rules"] if r["type"] != "required_status_checks"
    ]
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RULESET_JSON=json.dumps(ruleset_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "required_check" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_bp_missing_required_check(tmp_path: Path) -> None:
    """branch protection 移除了必填检查 context 时对账门禁应失败。"""
    bp_json = json.loads(DEFAULT_BP_JSON)
    bp_json["required_status_checks"]["contexts"] = []
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_BP_JSON=json.dumps(bp_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "required_check" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_ruleset_not_covering_default_branch(tmp_path: Path) -> None:
    """ruleset 条件不再覆盖默认分支时对账门禁应失败（规则内容再对也只在其他分支生效）。"""
    ruleset_json = json.loads(DEFAULT_RULESET_JSON)
    ruleset_json["conditions"]["ref_name"]["include"] = ["release/*"]
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RULESET_JSON=json.dumps(ruleset_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "covers_default" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_post_verification_without_gate_job(tmp_path: Path) -> None:
    """补验 run 不含成功的 PR Check Summary job 时对账门禁应失败（轻量 run 不构成等价验收）。"""
    jobs_json = json.loads(DEFAULT_JOBS_JSON)
    jobs_json["jobs"] = [{"name": "docs-only", "conclusion": "success"}]
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_JOBS_JSON=json.dumps(jobs_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "PR Check Summary" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_ruleset_excluding_default_branch(tmp_path: Path) -> None:
    """ruleset 的 exclude 排除了默认分支时对账门禁应失败（被排除的规则不算生效）。"""
    ruleset_json = json.loads(DEFAULT_RULESET_JSON)
    ruleset_json["conditions"]["ref_name"]["exclude"] = ["~DEFAULT_BRANCH"]
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RULESET_JSON=json.dumps(ruleset_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "covers_default" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_ledger_pr_sha_mismatch(tmp_path: Path) -> None:
    """台账被绕过 SHA 与所记 PR 的 merge_commit_sha 不一致时对账门禁应失败（任意祖先 SHA 不算数）。"""
    pr_json = json.loads(DEFAULT_PR_JSON)
    pr_json["merge_commit_sha"] = "0000000000000000000000000000000000000000"
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_PR_JSON=json.dumps(pr_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "merge_commit_sha" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_ruleset_missing_deletion_rule(tmp_path: Path) -> None:
    """ruleset 移除 deletion 规则时对账门禁应失败（允许删除 main 的配置与文档声明不符）。"""
    ruleset_json = json.loads(DEFAULT_RULESET_JSON)
    ruleset_json["rules"] = [
        r for r in ruleset_json["rules"] if r["type"] != "deletion"
    ]
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RULESET_JSON=json.dumps(ruleset_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "deletion_rule" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_bp_force_push_allowed(tmp_path: Path) -> None:
    """branch protection 允许 force push 时对账门禁应失败。"""
    bp_json = json.loads(DEFAULT_BP_JSON)
    bp_json["allow_force_pushes"]["enabled"] = True
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_BP_JSON=json.dumps(bp_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "no_force_push" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_ruleset_approvals_required(tmp_path: Path) -> None:
    """ruleset 要求审批数 1 时对账门禁应失败（solo 维护者无法自审，审批要求会卡死合并）。"""
    ruleset_json = json.loads(DEFAULT_RULESET_JSON)
    ruleset_json["rules"] = [
        {**r, "parameters": {**(r.get("parameters") or {}), "required_approving_review_count": 1}}
        if r["type"] == "pull_request"
        else r
        for r in ruleset_json["rules"]
    ]
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RULESET_JSON=json.dumps(ruleset_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "approvals_zero" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_unauthorized_authorizer(tmp_path: Path) -> None:
    """台账授权人非仓库所有者时对账门禁应失败（未经授权的绕过记录不算审计闭环）。"""
    data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    data["emergency_bypass"]["ledger"][0]["authorizer"] = "unauthorized-user"
    broken_json = tmp_path / "t5-unauthorized.json"
    broken_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    env = _base_env(_fake_gh(tmp_path), tmp_path)
    env["CHECK_ISSUE23_EVIDENCE"] = str(broken_json)
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "授权人" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_ruleset_excluding_literal_main_ref(tmp_path: Path) -> None:
    """ruleset 的 exclude 用字面 ref refs/heads/main 排除默认分支时对账门禁应失败。"""
    ruleset_json = json.loads(DEFAULT_RULESET_JSON)
    ruleset_json["conditions"]["ref_name"]["exclude"] = ["refs/heads/main"]
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RULESET_JSON=json.dumps(ruleset_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "covers_default" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_check_not_bound_to_actions_app(tmp_path: Path) -> None:
    """必填检查未绑定 GitHub Actions App（app_id 被改）时对账门禁应失败。"""
    bp_json = json.loads(DEFAULT_BP_JSON)
    bp_json["required_status_checks"]["checks"] = [
        {"context": "PR Check Summary", "app_id": 99999}
    ]
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_BP_JSON=json.dumps(bp_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "required_check" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_evidence_json_state_mismatch(tmp_path: Path) -> None:
    """证据 JSON 的 branch_protection 记录与实时回读不一致时对账门禁应失败。"""
    data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    data["branch_protection"]["enforce_admins"] = False
    broken_json = tmp_path / "t5-state-broken.json"
    broken_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    env = _base_env(_fake_gh(tmp_path), tmp_path)
    env["CHECK_ISSUE23_EVIDENCE"] = str(broken_json)
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "branch_protection" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_ruleset_excluding_wildcard(tmp_path: Path) -> None:
    """ruleset 的 exclude 用通配模式 refs/heads/* 排除默认分支时对账门禁应失败。"""
    ruleset_json = json.loads(DEFAULT_RULESET_JSON)
    ruleset_json["conditions"]["ref_name"]["exclude"] = ["refs/heads/*"]
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RULESET_JSON=json.dumps(ruleset_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "covers_default" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_check_run_wrong_app(tmp_path: Path) -> None:
    """PR head 的同名 check 由其他 App 创建时对账门禁应失败（绑定 GitHub Actions App 15368）。"""
    checks_json = json.loads(DEFAULT_CHECKS_JSON)
    checks_json["check_runs"][0]["app"] = {"id": 99999}
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_CHECKS_JSON=json.dumps(checks_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "PR Check Summary" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_ledger_conclusion_mismatch(tmp_path: Path) -> None:
    """台账补验结论与 run 实际结论不一致时对账门禁应失败（正式证据不得与真实状态矛盾）。"""
    data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    data["emergency_bypass"]["ledger"][0]["post_verification"]["conclusion"] = "failure"
    broken_json = tmp_path / "t5-conclusion-broken.json"
    broken_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    env = _base_env(_fake_gh(tmp_path), tmp_path)
    env["CHECK_ISSUE23_EVIDENCE"] = str(broken_json)
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "台账补验结论" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_ruleset_excluding_question_mark(tmp_path: Path) -> None:
    """ruleset 的 exclude 用 ? 通配模式（refs/heads/ma?n）排除默认分支时对账门禁应失败。"""
    ruleset_json = json.loads(DEFAULT_RULESET_JSON)
    ruleset_json["conditions"]["ref_name"]["exclude"] = ["refs/heads/ma?n"]
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RULESET_JSON=json.dumps(ruleset_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "covers_default" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_unreadable_reason_url(tmp_path: Path) -> None:
    """台账原因链接指向不存在的 Issue 时对账门禁应失败（没有可审计原因记录的绕过不算闭环）。"""
    data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    data["emergency_bypass"]["ledger"][0]["reason_url"] = (
        "https://github.com/crystepj-max/STS2-AUTOTEST/issues/999999"
    )
    broken_json = tmp_path / "t5-reason-broken.json"
    broken_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_GH_FAIL_FRAGMENT="issues/999999")
    env["CHECK_ISSUE23_EVIDENCE"] = str(broken_json)
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "原因链接" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_ruleset_name_mismatch(tmp_path: Path) -> None:
    """证据 JSON 的 ruleset.name 与实时名称不一致时对账门禁应失败。"""
    data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    data["ruleset"]["name"] = "WRONG RULESET"
    broken_json = tmp_path / "t5-name-broken.json"
    broken_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    env = _base_env(_fake_gh(tmp_path), tmp_path)
    env["CHECK_ISSUE23_EVIDENCE"] = str(broken_json)
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "ruleset" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_post_verification_wrong_workflow(tmp_path: Path) -> None:
    """补验 run 非 ci-pr.yml 工作流时对账门禁应失败（其他工作流的同名 job 不算等价验收）。"""
    run_json = json.loads(DEFAULT_RUN_JSON)
    run_json["path"] = ".github/workflows/ci-nightly.yml"
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RUN_JSON=json.dumps(run_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "工作流" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_pr_check_wrong_workflow(tmp_path: Path) -> None:
    """PR head 的 PR Check Summary 来自非 ci-pr.yml 工作流时对账门禁应失败。"""
    run_json = json.loads(DEFAULT_RUN_JSON)
    run_json["path"] = ".github/workflows/ci-nightly.yml"
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RUN_JSON=json.dumps(run_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "非正式工作流" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_restoration_evidence_content(tmp_path: Path) -> None:
    """恢复证据内容自相矛盾（bypass 非空 / enforce_admins=false）时对账门禁应失败。"""
    tmp_ev = tmp_path / "evidence"
    tmp_ev.mkdir()
    data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    (tmp_ev / "t5-final-evidence.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 误用 during 快照：绕过者非空 + enforce_admins=false
    (tmp_ev / "t7-ruleset-after.json").write_text(
        json.dumps(
            {
                "bypass_actors": [{"actor_id": 5}],
                "current_user_can_bypass": "pull_requests_only",
            }
        ),
        encoding="utf-8",
    )
    (tmp_ev / "t7-branch-protection-after.json").write_text(
        json.dumps({"enforce_admins": {"enabled": False}}), encoding="utf-8"
    )
    (tmp_ev / "t7-post-verification.md").write_text("x", encoding="utf-8")
    env = _base_env(_fake_gh(tmp_path), tmp_path)
    env["CHECK_ISSUE23_EVIDENCE"] = str(tmp_ev / "t5-final-evidence.json")
    proc = _run_script(env)
    assert proc.returncode != 0
    # 快照内容须从已提交 blob 校验——未提交/无法从 HEAD 读取的内容不被认可
    assert "已提交 blob" in proc.stdout + proc.stderr or "未提交修改" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_reason_url_other_repo(tmp_path: Path) -> None:
    """原因链接指向其他仓库时对账门禁应失败（host/owner/repo 必须匹配当前仓库）。"""
    data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    data["emergency_bypass"]["ledger"][0]["reason_url"] = (
        "https://github.com/other-org/other-repo/issues/23"
    )
    broken_json = tmp_path / "t5-other-repo.json"
    broken_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    env = _base_env(_fake_gh(tmp_path), tmp_path)
    env["CHECK_ISSUE23_EVIDENCE"] = str(broken_json)
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "原因链接" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_reason_url_missing_authorization(tmp_path: Path) -> None:
    """原因链接资源正文缺少授权记录（紧急绕过）时对账门禁应失败。"""
    env = _base_env(
        _fake_gh(tmp_path),
        tmp_path,
        FAKE_ISSUE_BODY="无关 Issue：一些别的内容",
    )
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "授权记录" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_authorization_after_operation(tmp_path: Path) -> None:
    """授权记录时间晚于操作完成时间时对账门禁应失败（授权必须先于绕过）。"""
    env = _base_env(
        _fake_gh(tmp_path),
        tmp_path,
        FAKE_ISSUE_CREATED_AT="2026-08-14T06:00:00Z",  # 操作完成（05:50:11Z）之后
    )
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "授权记录时间" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_authorization_not_bound_to_bypass(tmp_path: Path) -> None:
    """授权记录泛泛提及紧急绕过但未绑定本次 PR/SHA 时对账门禁应失败。"""
    env = _base_env(
        _fake_gh(tmp_path),
        tmp_path,
        FAKE_ISSUE_BODY="紧急绕过授权记录：一般性条款，未提及具体 PR",
    )
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "本次绕过的授权记录" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_missing_pre_operation_authorization_comment(tmp_path: Path) -> None:
    """台账引用的授权评论晚于操作时对账门禁应失败（时间戳授权记录须先于绕过）。"""
    comment_json = json.loads(DEFAULT_COMMENT_JSON)
    comment_json["created_at"] = "2026-08-14T07:00:00Z"  # 晚于操作
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_COMMENT_JSON=json.dumps(comment_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "授权评论" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_authorization_comment_missing(tmp_path: Path) -> None:
    """台账引用的授权评论不存在（按 ID 回读 404）时对账门禁应失败。"""
    env = _base_env(
        _fake_gh(tmp_path),
        tmp_path,
        FAKE_GH_FAIL_FRAGMENT="issues/comments/5289650944",
    )
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "授权评论" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_authorization_comment_wrong_author(tmp_path: Path) -> None:
    """授权评论非仓库所有者发布时对账门禁应失败（授权人仅限 crystepj-max）。"""
    comment_json = json.loads(DEFAULT_COMMENT_JSON)
    comment_json["user"] = {"login": "unauthorized-user"}
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_COMMENT_JSON=json.dumps(comment_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "仓库所有者发布" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_authorization_comment_edited_after(tmp_path: Path) -> None:
    """授权评论在操作后被编辑（updated_at 晚于操作）时对账门禁应失败（事后编辑不算事前授权）。"""
    comment_json = json.loads(DEFAULT_COMMENT_JSON)
    comment_json["updated_at"] = "2026-08-14T08:00:00Z"  # 晚于操作 05:50:11Z
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_COMMENT_JSON=json.dumps(comment_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "未经事后编辑" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_authorization_record_not_bound(tmp_path: Path) -> None:
    """不可变授权记录文件未绑定本条 PR/SHA 时对账门禁应失败。"""
    tmp_ev = tmp_path / "evidence"
    tmp_ev.mkdir()
    data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    (tmp_ev / "t5-final-evidence.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (tmp_ev / "t7-emergency-bypass-drill.md").write_text(
        "探针 PR #99（未绑定本条绕过）", encoding="utf-8"
    )
    (tmp_ev / "t7-post-verification.md").write_text("x", encoding="utf-8")
    (tmp_ev / "t7-ruleset-after.json").write_text(
        json.dumps({"bypass_actors": [], "current_user_can_bypass": "never"}), encoding="utf-8"
    )
    (tmp_ev / "t7-branch-protection-after.json").write_text(
        json.dumps({"enforce_admins": True}), encoding="utf-8"
    )
    env = _base_env(_fake_gh(tmp_path), tmp_path)
    env["CHECK_ISSUE23_EVIDENCE"] = str(tmp_ev / "t5-final-evidence.json")
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "授权记录文件" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_authorization_comment_wrong_issue(tmp_path: Path) -> None:
    """授权评论属于其他 Issue（URL 末尾编号不匹配）时对账门禁应失败（子串会误判 #230）。"""
    comment_json = json.loads(DEFAULT_COMMENT_JSON)
    comment_json["issue_url"] = "https://api.github.com/repos/crystepj-max/STS2-AUTOTEST/issues/230"
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_COMMENT_JSON=json.dumps(comment_json))
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "属于原因链接资源" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_force_tracked_env_file(tmp_path: Path) -> None:
    """实际跟踪的环境文件与证据 JSON tracked_env_files 不一致（如 git add -f）时对账门禁应失败。"""
    data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    data["env_guard"]["tracked_env_files"] = [".env.example", ".env.local"]
    broken_json = tmp_path / "t5-env-tracked-broken.json"
    broken_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    env = _base_env(_fake_gh(tmp_path), tmp_path)
    env["CHECK_ISSUE23_EVIDENCE"] = str(broken_json)
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "跟踪的环境文件" in proc.stdout + proc.stderr


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_dirty_restoration_snapshot(tmp_path: Path) -> None:
    """已提交快照被未提交地改成「已恢复」形态时对账门禁应失败（工作树修改不算不可变证据）。"""
    snapshot = REPO_ROOT / EVIDENCE_JSON.parent / "t7-ruleset-after.json"
    original = snapshot.read_text(encoding="utf-8")
    try:
        snapshot.write_text(
            json.dumps({"bypass_actors": [], "current_user_can_bypass": "never"}),
            encoding="utf-8",
        )
        env = _base_env(_fake_gh(tmp_path), tmp_path)
        proc = _run_script(env)
        assert proc.returncode != 0
        assert "未提交修改" in proc.stdout + proc.stderr
    finally:
        snapshot.write_text(original, encoding="utf-8")


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_gitignore_negation_semantics(tmp_path: Path) -> None:
    """.gitignore 与证据 JSON 协同追加 !.env 时对账门禁应失败（.env 实际未被忽略，声明比较发现不了）。"""
    gitignore = REPO_ROOT / ".gitignore"
    original = gitignore.read_text(encoding="utf-8")
    try:
        gitignore.write_text(original + "\n!.env\n", encoding="utf-8")
        # 证据 JSON 同步追加 !.env（两侧协同，声明一致）
        data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
        data["env_guard"]["gitignore"] = [".env", ".env.*", "!.env.example", "!.env"]
        broken_json = tmp_path / "t5-negation.json"
        broken_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        env = _base_env(_fake_gh(tmp_path), tmp_path)
        env["CHECK_ISSUE23_EVIDENCE"] = str(broken_json)
        proc = _run_script(env)
        assert proc.returncode != 0
        # 否定规则扫描或语义检查任一命中即失败
        assert "否定规则" in proc.stdout + proc.stderr or "未被实际忽略" in proc.stdout + proc.stderr
    finally:
        gitignore.write_text(original, encoding="utf-8")


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_gitignore_negation_bracket_evasion(tmp_path: Path) -> None:
    """否定规则用括号通配隐藏 .env（!secrets/[.]env）时对账门禁应失败（字面子串会漏过）。"""
    gitignore = REPO_ROOT / ".gitignore"
    original = gitignore.read_text(encoding="utf-8")
    try:
        gitignore.write_text(original + "\n!secrets/[.]env\n", encoding="utf-8")
        data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
        data["env_guard"]["gitignore"] = [".env", ".env.*", "!.env.example", "!secrets/[.]env"]
        broken_json = tmp_path / "t5-bracket.json"
        broken_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        env = _base_env(_fake_gh(tmp_path), tmp_path)
        env["CHECK_ISSUE23_EVIDENCE"] = str(broken_json)
        proc = _run_script(env)
        assert proc.returncode != 0
        # 声明不一致或否定规则扫描命中均算失败（核心：!secrets/[.]env 不得放行）
        assert "否定规则" in proc.stdout + proc.stderr or "不一致" in proc.stdout + proc.stderr
    finally:
        gitignore.write_text(original, encoding="utf-8")


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_restoration_evidence_missing_snapshot_types(tmp_path: Path) -> None:
    """恢复证据缺少两类终态快照（如只列空 JSON）时对账门禁应失败。"""
    tmp_ev = tmp_path / "evidence"
    tmp_ev.mkdir()
    data = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    data["emergency_bypass"]["ledger"][0]["restoration_evidence"] = ["t7-empty.json"]
    (tmp_ev / "t5-final-evidence.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (tmp_ev / "t7-empty.json").write_text("{}", encoding="utf-8")
    (tmp_ev / "t7-post-verification.md").write_text("x", encoding="utf-8")
    env = _base_env(_fake_gh(tmp_path), tmp_path)
    env["CHECK_ISSUE23_EVIDENCE"] = str(tmp_ev / "t5-final-evidence.json")
    proc = _run_script(env)
    assert proc.returncode != 0
    assert "终态快照" in proc.stdout + proc.stderr
