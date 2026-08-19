#!/usr/bin/env python3
"""静态校验 GitHub Actions workflow 中 upload-artifact 与 producer 步骤顺序（issue #51）。

规则：manifest 声明的 artifact 路径，凡被 upload-artifact 引用，其上传步骤在
同一 job 的 steps 数组中必须出现在所有 producer 步骤之后。
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_MANIFEST = Path(".github/workflow-artifact-manifest.yaml")
DEFAULT_WORKFLOWS_DIR = Path(".github/workflows")


@dataclass(frozen=True)
class StepRef:
    """Job 内步骤引用。"""

    index: int
    step_id: str | None
    name: str | None

    @property
    def label(self) -> str:
        if self.step_id:
            return f"id={self.step_id!r}"
        if self.name:
            return f"name={self.name!r}"
        return f"index={self.index}"


@dataclass(frozen=True)
class Violation:
    workflow_file: str
    job_id: str
    artifact: str
    upload: StepRef
    producer: StepRef
    message: str


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _step_ref(index: int, step: dict[str, Any]) -> StepRef:
    raw_id = step.get("id")
    raw_name = step.get("name")
    step_id = raw_id if isinstance(raw_id, str) and raw_id else None
    name = raw_name if isinstance(raw_name, str) and raw_name else None
    return StepRef(index=index, step_id=step_id, name=name)


def _is_upload_artifact_step(step: dict[str, Any]) -> bool:
    uses = step.get("uses")
    return isinstance(uses, str) and "upload-artifact" in uses


def _upload_paths(step: dict[str, Any]) -> list[str]:
    with_block = step.get("with")
    if not isinstance(with_block, dict):
        return []
    path_value = with_block.get("path")
    if not isinstance(path_value, str):
        return []
    return [line.strip() for line in path_value.splitlines() if line.strip()]


def _path_matches(upload_pattern: str, artifact: str) -> bool:
    if upload_pattern == artifact:
        return True
    if any(ch in upload_pattern for ch in "*?[]"):
        return fnmatch.fnmatch(artifact, upload_pattern)
    return False


def _upload_references_artifact(step: dict[str, Any], artifact: str) -> bool:
    return any(_path_matches(path, artifact) for path in _upload_paths(step))


def _find_producer_indices(
    steps: list[dict[str, Any]],
    producers: list[dict[str, str]],
) -> list[StepRef]:
    found: list[StepRef] = []
    for index, step in enumerate(steps):
        ref = _step_ref(index, step)
        for producer in producers:
            pid = producer.get("id")
            pname = producer.get("name")
            if pid and ref.step_id == pid:
                found.append(ref)
                break
            if pname and ref.name == pname:
                found.append(ref)
                break
    return found


def _find_upload_indices(steps: list[dict[str, Any]], artifact: str) -> list[StepRef]:
    uploads: list[StepRef] = []
    for index, step in enumerate(steps):
        if _is_upload_artifact_step(step) and _upload_references_artifact(step, artifact):
            uploads.append(_step_ref(index, step))
    return uploads


def _validate_job_rules(
    workflow_file: str,
    job_id: str,
    steps: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> list[Violation]:
    violations: list[Violation] = []

    for rule in rules:
        artifact = rule.get("artifact")
        producers_cfg = rule.get("producers")
        if not isinstance(artifact, str) or not isinstance(producers_cfg, list):
            continue

        producer_refs = _find_producer_indices(steps, producers_cfg)
        if not producer_refs:
            producer_desc = ", ".join(
                f"id={p.get('id')!r}" if p.get("id") else f"name={p.get('name')!r}"
                for p in producers_cfg
                if isinstance(p, dict)
            )
            violations.append(
                Violation(
                    workflow_file=workflow_file,
                    job_id=job_id,
                    artifact=artifact,
                    upload=StepRef(index=-1, step_id=None, name="(none)"),
                    producer=StepRef(index=-1, step_id=None, name="(missing)"),
                    message=(
                        f"producer 步骤未找到（{producer_desc}），"
                        f"无法校验 {artifact!r} 的上传顺序"
                    ),
                )
            )
            continue

        upload_refs = _find_upload_indices(steps, artifact)
        if not upload_refs:
            continue

        latest_producer_index = max(ref.index for ref in producer_refs)
        latest_producer = next(ref for ref in producer_refs if ref.index == latest_producer_index)

        for upload in upload_refs:
            if upload.index <= latest_producer_index:
                violations.append(
                    Violation(
                        workflow_file=workflow_file,
                        job_id=job_id,
                        artifact=artifact,
                        upload=upload,
                        producer=latest_producer,
                        message=(
                            f"upload 步骤 ({upload.label}, index={upload.index}) "
                            f"必须晚于 producer 步骤 ({latest_producer.label}, "
                            f"index={latest_producer_index})"
                        ),
                    )
                )

    return violations


def validate_manifest(
    manifest_path: Path,
    workflows_dir: Path,
) -> list[Violation]:
    manifest = _load_yaml(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest 根节点必须是 mapping：{manifest_path}")

    workflow_rules = manifest.get("workflows")
    if not isinstance(workflow_rules, list):
        raise ValueError(f"manifest 缺少 workflows 列表：{manifest_path}")

    all_violations: list[Violation] = []

    for entry in workflow_rules:
        if not isinstance(entry, dict):
            continue
        file_name = entry.get("file")
        job_filter = entry.get("job")
        rules = entry.get("rules")
        if not isinstance(file_name, str) or not isinstance(rules, list):
            continue

        workflow_path = workflows_dir / file_name
        if not workflow_path.is_file():
            all_violations.append(
                Violation(
                    workflow_file=file_name,
                    job_id=str(job_filter or "*"),
                    artifact="*",
                    upload=StepRef(index=-1, step_id=None, name="(none)"),
                    producer=StepRef(index=-1, step_id=None, name="(missing)"),
                    message=f"workflow 文件不存在：{workflow_path}",
                )
            )
            continue

        workflow = _load_yaml(workflow_path)
        if not isinstance(workflow, dict):
            continue
        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict):
            continue

        for job_id, job in jobs.items():
            if job_filter is not None and job_id != job_filter:
                continue
            if not isinstance(job, dict):
                continue
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            step_dicts = [step for step in steps if isinstance(step, dict)]
            all_violations.extend(
                _validate_job_rules(file_name, job_id, step_dicts, rules)
            )

    return all_violations


def _print_violations(violations: list[Violation]) -> None:
    for item in violations:
        print(
            f"::error file=.github/workflows/{item.workflow_file}"
            f"::[{item.job_id}] {item.artifact}: {item.message}",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="校验 workflow 中 upload-artifact 步骤必须晚于 producer 步骤",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="artifact↔producer 映射 manifest（默认 .github/workflow-artifact-manifest.yaml）",
    )
    parser.add_argument(
        "--workflows-dir",
        type=Path,
        default=DEFAULT_WORKFLOWS_DIR,
        help="workflow YAML 目录（默认 .github/workflows）",
    )
    args = parser.parse_args(argv)

    manifest_path = args.manifest.resolve()
    workflows_dir = args.workflows_dir.resolve()

    if not manifest_path.is_file():
        print(f"::error::manifest 不存在：{manifest_path}", file=sys.stderr)
        return 2

    try:
        violations = validate_manifest(manifest_path, workflows_dir)
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2

    if violations:
        _print_violations(violations)
        print(
            f"workflow artifact order check FAILED: {len(violations)} violation(s)",
            file=sys.stderr,
        )
        return 1

    print("workflow artifact order check PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
