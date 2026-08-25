"""CI 假绿防护契约：Nightly 分类与 SAFE_DELETE 环境剥离。

覆盖两类曾导致「workflow 绿但实际未验收」的回归：
1. game_tests 使用 continue-on-error 时，分类必须读 outcome 而非 conclusion
2. 四个 workflow 必须剥离 IDE 会话变量，避免 SAFE_DELETE 拦截 checkout
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PYPROJECT = ROOT / "pyproject.toml"


def _load_workflow(name: str) -> dict:
    raw = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def test_nightly_classifies_game_tests_by_outcome_not_conclusion() -> None:
    """continue-on-error 下 conclusion 可能仍为 success；必须用 outcome。"""
    text = (WORKFLOWS / "ci-nightly.yml").read_text(encoding="utf-8")
    assert "steps.game_tests.conclusion" not in text
    assert "steps.game_tests.outcome" in text
    assert "NIGHTLY_STEP_GAME_TESTS: ${{ steps.game_tests.outcome }}" in text
    assert "classify_nightly.py" in text

    workflow = _load_workflow("ci-nightly.yml")
    steps = workflow["jobs"]["nightly"]["steps"]
    game = next(step for step in steps if step.get("id") == "game_tests")
    assert game.get("continue-on-error") is True
    assert "--timeout=" in str(game.get("run", ""))


def test_all_workflows_clear_safe_delete_session_env() -> None:
    required = {"CODEBUDDY_SESSION_ID", "CLAUDE_SESSION_ID"}
    for name in ("ci-pr.yml", "ci-main.yml", "ci-nightly.yml", "ci-game.yml"):
        workflow = _load_workflow(name)
        env = workflow.get("env")
        assert isinstance(env, dict), f"{name} 缺少 workflow-level env"
        missing = required - set(env)
        assert not missing, f"{name} 未剥离会话变量：{sorted(missing)}"
        for key in required:
            assert env[key] == "", f"{name} 的 {key} 应为空字符串"


def test_dev_extra_includes_pytest_timeout() -> None:
    """Nightly/game workflow 传 --timeout=，dev extra 必须安装插件。"""
    text = PYPROJECT.read_text(encoding="utf-8")
    assert "pytest-timeout" in text


def test_nightly_enforces_failed_classification_after_evidence_upload() -> None:
    """continue-on-error 后必须有最终 enforce，否则 job 仍会假绿。"""
    text = (WORKFLOWS / "ci-nightly.yml").read_text(encoding="utf-8")
    assert "Enforce classification" in text

    workflow = _load_workflow("ci-nightly.yml")
    steps = workflow["jobs"]["nightly"]["steps"]
    names = [step.get("name", "") for step in steps]
    enforce_idx = names.index("Enforce classification")
    upload_idx = names.index("Upload evidence pack")
    assert enforce_idx > upload_idx, "enforce 必须在证据上传之后"
    assert steps[enforce_idx].get("if") == "always()"
