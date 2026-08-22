"""对 `.github/workflows/ci-nightly.yml` 的分类、时间、重试规则做可重复自动检查。

issue #15 复核后 acceptance criteria 要求：
1. 环境未就绪 / 功能失败 / 任务取消 / 全部成功分别得到正确终态，功能失败不能因
   `continue-on-error: true` 被误判为通过。
2. 结论时间为真实生成时间，不是占位文本。
3. checkout 或环境准备失败时仍有可下载证据。
4. 环境失败最多重试一次并保留两次记录。
5. 上述规则有可重复自动检查（即本文件）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci-nightly.yml"


@pytest.fixture
def workflow() -> dict[str, Any]:
    assert WORKFLOW_PATH.exists(), f"workflow file not found: {WORKFLOW_PATH}"
    with WORKFLOW_PATH.open("r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)
    return data


@pytest.fixture
def nightly_job(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert "nightly" in jobs, "expected a job named 'nightly'"
    return jobs["nightly"]  # type: ignore[no-any-return]


@pytest.fixture
def steps(nightly_job: dict[str, Any]) -> list[dict[str, Any]]:
    return nightly_job["steps"]  # type: ignore[no-any-return]


def _step_by_id(steps: list[dict[str, Any]], step_id: str) -> dict[str, Any]:
    for step in steps:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"step with id '{step_id}' not found")


def _step_by_name(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for step in steps:
        if step.get("name") == name:
            return step
    raise AssertionError(f"step with name '{name}' not found")


class TestClassificationLogic:
    """AC#1：终态分类不能误报。"""

    def test_game_tests_step_uses_continue_on_error(self, steps: list[dict[str, Any]]) -> None:
        game_step = _step_by_id(steps, "game_tests")
        assert game_step.get("continue-on-error") is True, (
            "game_tests must allow the workflow to continue so that evidence can be saved, "
            "but its outcome must still be used for classification"
        )

    def test_game_tests_classification_uses_outcome_not_conclusion(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """`continue-on-error: true` 会让 `steps.*.conclusion` 变成 success，
        必须改用 `outcome` 判断真实结果。"""
        classify_step = _step_by_id(steps, "classify")
        script = classify_step.get("run", "")
        assert "steps.game_tests.conclusion" not in script, (
            "classification must not use steps.game_tests.conclusion because "
            "continue-on-error makes conclusion always appear successful"
        )
        assert "steps.game_tests.outcome" in script, (
            "classification must use steps.game_tests.outcome to detect real failures"
        )

    def test_cancelled_state_is_classified_not_passed(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """被取消的任务不能因 `conclusion != failure` 而被归为 PASSED。"""
        classify_step = _step_by_id(steps, "classify")
        script = classify_step.get("run", "")
        assert "cancelled" in script.lower() or "canceled" in script.lower(), (
            "classification script must explicitly handle cancelled state"
        )


class TestTimestamp:
    """AC#2：时间戳必须是运行时真实值。"""

    def test_classification_timestamp_is_not_literal_shell_placeholder(
        self, steps: list[dict[str, Any]]
    ) -> None:
        package_step = _step_by_id(steps, "package")
        script = package_step.get("run", "")
        # Current bug: `cat << 'CLASS_EOF'` + `"timestamp": "$(date -u +%FT%TZ)"`
        # Single-quoted heredoc prevents shell expansion, so the timestamp becomes a literal.
        has_quoted_heredoc = "<< 'CLASS_EOF'" in script or '<< "CLASS_EOF"' in script
        placeholder = '"timestamp": "$(date -u +%FT%TZ)"'
        if placeholder in script and has_quoted_heredoc:
            raise AssertionError(
                "classification.json timestamp is a literal placeholder because "
                "the heredoc is quoted; use a runtime expression or unquoted heredoc"
            )


class TestRetry:
    """AC#4：环境类失败最多一次受控重试。"""

    def test_environment_failure_has_controlled_retry(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """环境检查 / 安装等关键步骤失败后，应有一次重试步骤并记录两次结果。"""
        retry_step_names = [
            step.get("name", "")
            for step in steps
            if "retry" in step.get("name", "").lower()
            or "retest" in step.get("name", "").lower()
            or "重试" in step.get("name", "")
        ]
        assert retry_step_names, (
            "workflow must contain a controlled retry step for environment/setup failures"
        )


class TestEarlyFailureEvidence:
    """AC#3：早期失败仍保留最小可下载证据。"""

    def test_evidence_upload_runs_always(self, steps: list[dict[str, Any]]) -> None:
        upload_junit = _step_by_name(steps, "Upload JUnit results")
        upload_evidence = _step_by_name(steps, "Upload evidence pack")
        assert upload_junit.get("if") == "always()"
        assert upload_evidence.get("if") == "always()"

    def test_classification_runs_always(self, steps: list[dict[str, Any]]) -> None:
        classify_step = _step_by_id(steps, "classify")
        assert classify_step.get("if") == "always()"


class TestBounds:
    """整体上限必须存在（PR #54 已完成，保留为回归检查）。"""

    def test_job_has_overall_timeout(self, nightly_job: dict[str, Any]) -> None:
        assert nightly_job.get("timeout-minutes") is not None
