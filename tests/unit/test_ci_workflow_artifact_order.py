"""check_workflow_artifact_order.py 单元测试（issue #51）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / ".github" / "scripts"
_SPEC = importlib.util.spec_from_file_location(
    "check_workflow_artifact_order_script",
    _SCRIPTS_DIR / "check_workflow_artifact_order.py",
)
assert _SPEC and _SPEC.loader
check_module = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = check_module
_SPEC.loader.exec_module(check_module)

validate_manifest = check_module.validate_manifest
main = check_module.main


def _write_workflow(tmp_path: Path, content: dict) -> Path:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir(parents=True)
    path = workflows_dir / "sample.yml"
    path.write_text(yaml.dump(content), encoding="utf-8")
    return workflows_dir


def _write_manifest(
    tmp_path: Path,
    *,
    workflow_file: str,
    job: str,
    artifact: str,
    producer_id: str,
) -> Path:
    manifest = {
        "workflows": [
            {
                "file": workflow_file,
                "job": job,
                "rules": [
                    {
                        "artifact": artifact,
                        "producers": [{"id": producer_id}],
                    }
                ],
            }
        ]
    }
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.dump(manifest), encoding="utf-8")
    return manifest_path


def test_pass_when_upload_after_producer(tmp_path: Path) -> None:
    workflows_dir = _write_workflow(
        tmp_path,
        {
            "jobs": {
                "validation": {
                    "steps": [
                        {"id": "mypy", "name": "Check no new mypy debt", "run": "echo mypy"},
                        {
                            "name": "Upload check logs",
                            "uses": "actions/upload-artifact@v4",
                            "with": {"path": "mypy-check.log"},
                        },
                    ]
                }
            }
        },
    )
    manifest_path = _write_manifest(
        tmp_path,
        workflow_file="sample.yml",
        job="validation",
        artifact="mypy-check.log",
        producer_id="mypy",
    )

    violations = validate_manifest(manifest_path, workflows_dir)
    assert violations == []


def test_fail_when_upload_before_producer(tmp_path: Path) -> None:
    workflows_dir = _write_workflow(
        tmp_path,
        {
            "jobs": {
                "validation": {
                    "steps": [
                        {
                            "name": "Upload check logs",
                            "uses": "actions/upload-artifact@v4",
                            "with": {"path": "mypy-check.log"},
                        },
                        {"id": "mypy", "name": "Check no new mypy debt", "run": "echo mypy"},
                    ]
                }
            }
        },
    )
    manifest_path = _write_manifest(
        tmp_path,
        workflow_file="sample.yml",
        job="validation",
        artifact="mypy-check.log",
        producer_id="mypy",
    )

    violations = validate_manifest(manifest_path, workflows_dir)
    assert len(violations) == 1
    assert violations[0].artifact == "mypy-check.log"
    assert violations[0].upload.index == 0
    assert violations[0].producer.index == 1


def test_multiline_upload_path_matches_artifact(tmp_path: Path) -> None:
    workflows_dir = _write_workflow(
        tmp_path,
        {
            "jobs": {
                "validation": {
                    "steps": [
                        {"id": "mypy", "run": "echo"},
                        {
                            "uses": "actions/upload-artifact@v4",
                            "with": {
                                "path": "ruff-check.log\nmypy-check.log\npytest-check.log",
                            },
                        },
                    ]
                }
            }
        },
    )
    manifest_path = _write_manifest(
        tmp_path,
        workflow_file="sample.yml",
        job="validation",
        artifact="mypy-check.log",
        producer_id="mypy",
    )

    assert validate_manifest(manifest_path, workflows_dir) == []


def test_fail_closed_when_producer_missing(tmp_path: Path) -> None:
    workflows_dir = _write_workflow(
        tmp_path,
        {
            "jobs": {
                "validation": {
                    "steps": [
                        {
                            "uses": "actions/upload-artifact@v4",
                            "with": {"path": "mypy-check.log"},
                        },
                    ]
                }
            }
        },
    )
    manifest_path = _write_manifest(
        tmp_path,
        workflow_file="sample.yml",
        job="validation",
        artifact="mypy-check.log",
        producer_id="mypy",
    )

    violations = validate_manifest(manifest_path, workflows_dir)
    assert len(violations) == 1
    assert "producer 步骤未找到" in violations[0].message


def test_real_ci_pr_yml_passes(repo_root: Path) -> None:
    manifest_path = repo_root / ".github" / "workflow-artifact-manifest.yaml"
    workflows_dir = repo_root / ".github" / "workflows"
    if not manifest_path.is_file():
        pytest.skip("manifest 尚未合入当前分支")

    violations = validate_manifest(manifest_path, workflows_dir)
    assert violations == []


def test_main_exit_code_on_violation(tmp_path: Path) -> None:
    workflows_dir = _write_workflow(
        tmp_path,
        {
            "jobs": {
                "validation": {
                    "steps": [
                        {
                            "uses": "actions/upload-artifact@v4",
                            "with": {"path": "mypy-check.log"},
                        },
                        {"id": "mypy", "run": "echo"},
                    ]
                }
            }
        },
    )
    manifest_path = _write_manifest(
        tmp_path,
        workflow_file="sample.yml",
        job="validation",
        artifact="mypy-check.log",
        producer_id="mypy",
    )

    rc = main(["--manifest", str(manifest_path), "--workflows-dir", str(workflows_dir)])
    assert rc == 1


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
