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
import textwrap
from pathlib import Path

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
    }
)
DEFAULT_PR_JSON = json.dumps(
    {"head": {"sha": "3a732858af66051db3962a76be4ad7379f1f2c76"}}
)
DEFAULT_CHECKS_JSON = json.dumps(
    {"check_runs": [{"name": "PR Check Summary", "conclusion": "success"}]}
)
DEFAULT_RULESET_JSON = json.dumps(
    {
        "bypass_actors": [],
        "current_user_can_bypass": "never",
        "rules": [
            {
                "type": "pull_request",
                "parameters": {"required_review_thread_resolution": True},
            }
        ],
    }
)
# compare API：status=ahead 表示 base（被绕过 SHA）是 head（补验 run head）的祖先
DEFAULT_COMPARE_JSON = json.dumps({"status": "ahead"})
# 与待更新 Issue 正文保持一致的对账标记
DEFAULT_ISSUE_BODY = (
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
    case "$url" in
        *"/actions/runs/"*) echo "$FAKE_RUN_JSON" ;;
        *"/commits/"*"/check-runs") echo "$FAKE_CHECKS_JSON" ;;
        *"/pulls/"*) echo "$FAKE_PR_JSON" ;;
        *"/rulesets/"*) echo "$FAKE_RULESET_JSON" ;;
        *"/compare/"*) echo "$FAKE_COMPARE_JSON" ;;
        *"/issues/"*) python3 -c "import json,sys; print(json.dumps({'body': sys.stdin.read()}))" <<< "$FAKE_ISSUE_BODY" ;;
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
    # 脚本输出为 UTF-8 中文字节，显式按 UTF-8 解码（errors=replace 兜底非 UTF-8 字节）
    return subprocess.run(
        ["bash", SCRIPT],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
        check=False,  # 退出码由调用方断言（ruff PLW1510 要求显式声明）
    )


def _base_env(bin_dir: Path, tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "LC_ALL"}
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["FAKE_RUN_JSON"] = overrides.pop("FAKE_RUN_JSON", DEFAULT_RUN_JSON)
    env["FAKE_CHECKS_JSON"] = overrides.pop("FAKE_CHECKS_JSON", DEFAULT_CHECKS_JSON)
    env["FAKE_PR_JSON"] = overrides.pop("FAKE_PR_JSON", DEFAULT_PR_JSON)
    env["FAKE_RULESET_JSON"] = overrides.pop("FAKE_RULESET_JSON", DEFAULT_RULESET_JSON)
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


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="对账脚本依赖 bash，当前环境无 bash，跳过",
)
def test_gate_detects_24h_window_violation(tmp_path: Path) -> None:
    """补验 run 完成时间超过操作后 24h 时对账门禁应失败。"""
    run_json = json.dumps(
        {
            "conclusion": "success",
            "head_sha": "64ed09fcd1f0174ca211da7170ce61da2a1b6b50",
            "completed_at": "2026-08-16T07:47:43Z",  # 操作完成（05:50:11Z）后 2 天
        }
    )
    env = _base_env(_fake_gh(tmp_path), tmp_path, FAKE_RUN_JSON=run_json)
    proc = _run_script(env)
    assert proc.returncode != 0
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
