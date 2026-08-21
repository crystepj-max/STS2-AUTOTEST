#!/usr/bin/env python3
"""静态校验夜间回归工作流契约（issue #15 / #64 / #65）。

保护规则：
- 分类必须使用 step outcome，不能用 continue-on-error 后的 conclusion
- 结论时间不能是未展开占位
- checkout / 环境准备失败仍有早期证据上传
- 环境步骤最多一次受控重试；功能步骤不得重试
- 证据上传失败必须可见（if-no-files-found: error）
- 不得改动 PR #54 已落地的整体时长上限
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_WORKFLOW = Path(".github/workflows/ci-nightly.yml")
DEFAULT_CLASSIFIER = Path(".github/scripts/classify_nightly.py")
REQUIRED_TIMEOUT_MINUTES = 360
ENV_RETRY_IDS = {
    "checkout": "checkout_retry",
    "env_check": "env_check_retry",
    "setup_python": "setup_python_retry",
    "install": "install_retry",
}
FORBIDDEN_FUNCTIONAL_RETRY_IDS = {
    "lint_retry",
    "typecheck_retry",
    "import_check_retry",
    "unit_tests_retry",
    "cli_tests_retry",
    "game_tests_retry",
}
REQUIRED_STEP_IDS = {
    "checkout",
    "checkout_retry",
    "early_diagnosis",
    "upload_early",
    "env_check",
    "env_check_retry",
    "setup_python",
    "setup_python_retry",
    "install",
    "install_retry",
    "env_gate",
    "classify",
    "upload_evidence",
    "record_upload",
}


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _steps_by_id(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return {}
    found: dict[str, dict[str, Any]] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = step.get("id")
        if isinstance(step_id, str) and step_id:
            found[step_id] = step
    return found


def _run_text(step: dict[str, Any]) -> str:
    raw = step.get("run")
    return raw if isinstance(raw, str) else ""


def validate_workflow(workflow_path: Path, classifier_path: Path) -> list[str]:
    violations: list[str] = []
    text = workflow_path.read_text(encoding="utf-8")
    data = _load_yaml(workflow_path)
    if not isinstance(data, dict):
        return [f"{workflow_path} 不是 mapping"]

    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or "nightly" not in jobs:
        return [f"{workflow_path} 缺少 jobs.nightly"]
    job = jobs["nightly"]
    if not isinstance(job, dict):
        return ["jobs.nightly 不是 mapping"]

    timeout = job.get("timeout-minutes")
    if timeout != REQUIRED_TIMEOUT_MINUTES:
        violations.append(
            f"整体时长上限必须保持 timeout-minutes: {REQUIRED_TIMEOUT_MINUTES}，实际={timeout!r}"
        )

    steps = _steps_by_id(job)
    for step_id in REQUIRED_STEP_IDS:
        if step_id not in steps:
            violations.append(f"缺少步骤 id={step_id}")

    for forbidden in FORBIDDEN_FUNCTIONAL_RETRY_IDS:
        if forbidden in steps:
            violations.append(f"功能步骤不得重试：发现 id={forbidden}")

    for primary, retry in ENV_RETRY_IDS.items():
        retry_step = steps.get(retry)
        if not isinstance(retry_step, dict):
            continue
        condition = str(retry_step.get("if") or "")
        if f"steps.{primary}.outcome" not in condition or "failure" not in condition:
            violations.append(
                f"{retry} 必须仅在 {primary} outcome==failure 时运行，实际 if={condition!r}"
            )

    game = steps.get("game_tests")
    if isinstance(game, dict):
        if game.get("continue-on-error") is not True:
            violations.append("game_tests 必须 continue-on-error: true，以便失败后仍收集证据")
        game_if = str(game.get("if") or "")
        if "game_tests" in game_if and "retry" in game_if:
            violations.append("game_tests 不得带环境重试条件")

    classify = steps.get("classify")
    if isinstance(classify, dict):
        classify_if = str(classify.get("if") or "")
        if "always()" not in classify_if:
            violations.append("classify 必须 if: always()")
        classify_run = _run_text(classify)
        if "classify_nightly.py" not in classify_run:
            violations.append("classify 必须调用 classify_nightly.py")
        if "NIGHTLY_STEP_GAME_TESTS" not in classify_run and "NIGHTLY_STEP_GAME_TESTS" not in text:
            violations.append("必须把 steps.game_tests.outcome 传入分类器")
        env_block = classify.get("env")
        game_outcome_passed = False
        if isinstance(env_block, dict):
            for key, value in env_block.items():
                if str(key) == "NIGHTLY_STEP_GAME_TESTS" and "steps.game_tests.outcome" in str(value):
                    game_outcome_passed = True
        if "NIGHTLY_STEP_GAME_TESTS: ${{ steps.game_tests.outcome }}" in text:
            game_outcome_passed = True
        if not game_outcome_passed:
            violations.append("分类必须读取 steps.game_tests.outcome 而非 conclusion")

    if "steps.game_tests.conclusion" in text:
        violations.append("禁止使用 steps.game_tests.conclusion 做分类（continue-on-error 会误报通过）")
    if "steps.${" in text and ".conclusion" in text:
        # 仍允许 stages 诊断记录 conclusion，但分类输入不得用 conclusion
        pass
    if "<< 'CLASS_EOF'" in text and "$(date" in text:
        violations.append("禁止用单引号 heredoc 写 $(date) 占位时间戳")

    early = steps.get("early_diagnosis")
    if isinstance(early, dict) and "always()" not in str(early.get("if") or ""):
        violations.append("early_diagnosis 必须 if: always()")

    upload_early = steps.get("upload_early")
    if isinstance(upload_early, dict):
        with_block = upload_early.get("with")
        if not isinstance(with_block, dict) or with_block.get("if-no-files-found") != "error":
            violations.append("upload_early 必须 if-no-files-found: error，避免空证据被当成成功")
        condition = str(upload_early.get("if") or "")
        if "always()" not in condition:
            violations.append("upload_early 必须在 always() 下按 checkout 失败触发")

    upload_evidence = steps.get("upload_evidence")
    if isinstance(upload_evidence, dict):
        with_block = upload_evidence.get("with")
        if not isinstance(with_block, dict) or with_block.get("if-no-files-found") != "error":
            violations.append("upload_evidence 必须 if-no-files-found: error")
        if "always()" not in str(upload_evidence.get("if") or ""):
            violations.append("upload_evidence 必须 if: always()")

    record_upload = steps.get("record_upload")
    if isinstance(record_upload, dict):
        if "always()" not in str(record_upload.get("if") or ""):
            violations.append("record_upload 必须 if: always()")
        if "upload_evidence.outcome" not in str(record_upload.get("run") or "") and \
                "steps.upload_evidence.outcome" not in text:
            violations.append("record_upload 必须记录 upload_evidence.outcome")

    for marker in ("Phase 0", "Phase 6", "Phase 9"):
        if marker not in text:
            violations.append(f"不得拆除 PR #54 阶段划分：缺少 {marker} 标记")

    if not classifier_path.is_file():
        violations.append(f"分类器不存在：{classifier_path}")

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 ci-nightly.yml 的分类/证据/重试契约")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--classifier", type=Path, default=DEFAULT_CLASSIFIER)
    parser.add_argument("--skip-self-check", action="store_true")
    args = parser.parse_args(argv)

    if not args.workflow.is_file():
        print(f"::error::workflow 不存在：{args.workflow}", file=sys.stderr)
        return 2

    violations = validate_workflow(args.workflow, args.classifier)
    if violations:
        for item in violations:
            print(f"::error file={args.workflow}::{item}", file=sys.stderr)
        print(f"nightly regression contract FAILED: {len(violations)} violation(s)", file=sys.stderr)
        return 1

    if not args.skip_self_check:
        result = subprocess.run(
            [sys.executable, str(args.classifier), "--self-check"],
            check=False,
        )
        if result.returncode != 0:
            print("nightly classifier self-check FAILED", file=sys.stderr)
            return result.returncode

    print("nightly regression contract PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
