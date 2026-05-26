"""Tests for CLI spec pipeline commands (review, compile, run --all)."""
from __future__ import annotations

import pytest
from pathlib import Path
from argparse import Namespace
from sts2_autotest.cli.main import (
    review_cmd,
    compile_cmd,
    _create_parser,
    _ensure_output_dir_writable,
)


class TestCLIParser:
    def test_create_parser_has_review(self) -> None:
        parser = _create_parser()
        args = parser.parse_args(["review", "--spec-dir", "tests/cases"])
        assert args.command == "review"
        assert args.spec_dir == "tests/cases"

    def test_create_parser_review_with_output_dir(self) -> None:
        parser = _create_parser()
        args = parser.parse_args(["review", "--spec-dir", "tests/cases", "--output-dir", "artifacts"])
        assert args.command == "review"
        assert args.output_dir == "artifacts"

    def test_create_parser_has_compile(self) -> None:
        parser = _create_parser()
        args = parser.parse_args(["compile", "--spec-dir", "tests/cases", "--output-dir", "tests/generated"])
        assert args.command == "compile"
        assert args.spec_dir == "tests/cases"
        assert args.output_dir == "tests/generated"

    def test_create_parser_compile_use_revised(self) -> None:
        parser = _create_parser()
        args = parser.parse_args(["compile", "--spec-dir", "tests/cases", "--use-revised"])
        assert args.command == "compile"
        assert args.use_revised is True

    def test_create_parser_review_with_project(self) -> None:
        parser = _create_parser()
        args = parser.parse_args(["review", "--project", "my-mod"])
        assert args.command == "review"
        assert args.project == "my-mod"

    def test_create_parser_compile_with_project(self) -> None:
        parser = _create_parser()
        args = parser.parse_args(["compile", "--project", "my-mod"])
        assert args.command == "compile"
        assert args.project == "my-mod"

    def test_create_parser_run_with_spec_dir(self) -> None:
        parser = _create_parser()
        args = parser.parse_args(["run", "--all", "--spec-dir", "tests/cases"])
        assert args.command == "run"
        assert args.all is True
        assert args.spec_dir == "tests/cases"

    def test_create_parser_run_with_project(self) -> None:
        parser = _create_parser()
        args = parser.parse_args(["run", "--all", "--project", "my-mod"])
        assert args.command == "run"
        assert args.project == "my-mod"


class TestReviewCmd:
    def test_review_no_spec_dir_no_project(self, capsys) -> None:
        args = Namespace(command="review", spec_dir=None, project=None, output=None)
        rc = review_cmd(args)
        # Falls back to default docs/process/specs when it exists
        assert rc == 0

    def test_review_nonexistent_dir(self, capsys) -> None:
        args = Namespace(command="review", spec_dir="/nonexistent", project=None, output=None)
        rc = review_cmd(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out or "not found" in captured.err

    def test_review_empty_dir(self, tmp_path, capsys) -> None:
        d = tmp_path / "empty_specs"
        d.mkdir()
        args = Namespace(command="review", spec_dir=str(d), project=None, output=None, output_dir=None)
        rc = review_cmd(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No spec files" in captured.out

    def test_review_writes_report_and_revised_drafts(self, tmp_path) -> None:
        specs = tmp_path / "specs"
        specs.mkdir()
        (specs / "TC-DRAFT.md").write_text(
            "# TC-DRAFT Draft\n\n"
            "## Metadata\n- id: TC-DRAFT\n- level: case\n\n"
            "## When\n1. 适当选择角色\n",
            encoding="utf-8",
        )
        out = tmp_path / "artifacts"
        args = Namespace(
            command="review",
            spec_dir=str(specs),
            project=None,
            output=None,
            output_dir=str(out),
        )
        rc = review_cmd(args)
        assert rc == 1
        assert (out / "review-report.md").exists()
        assert (out / "revised" / "TC-DRAFT.md").exists()


class TestCompileCmd:
    def test_compile_no_spec_dir_no_project(self, capsys) -> None:
        args = Namespace(command="compile", spec_dir=None, output_dir=None, project=None)
        rc = compile_cmd(args)
        # Falls back to default docs/process/specs when it exists
        assert rc == 0

    def test_compile_nonexistent_dir(self, capsys) -> None:
        args = Namespace(command="compile", spec_dir="/nonexistent", output_dir="/tmp/out", project=None)
        rc = compile_cmd(args)
        assert rc == 1

    def test_compile_empty_dir(self, tmp_path, capsys) -> None:
        d = tmp_path / "empty_specs"
        d.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        args = Namespace(command="compile", spec_dir=str(d), output_dir=str(out), project=None)
        rc = compile_cmd(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No spec files" in captured.out

    def test_compile_root_specs_dir_generates_cases_and_suite(self, tmp_path, capsys) -> None:
        root = tmp_path / "specs"
        cases_dir = root / "cases"
        suites_dir = root / "suites"
        cases_dir.mkdir(parents=True)
        suites_dir.mkdir(parents=True)
        (cases_dir / "TC-001.md").write_text(
            "# TC-001 One\n\n## Metadata\n- id: TC-001\n- level: case\n\n## When\n1. 启动游戏\n",
            encoding="utf-8",
        )
        (suites_dir / "SUITE-001.md").write_text(
            "# SUITE-001 Smoke\n\n## Metadata\n- id: SUITE-001\n- level: suite\n\n## Goal\n- 验证链路\n\n## Mode\n- execution: sequential_shared_session\n\n## Includes\n1. TC-001\n",
            encoding="utf-8",
        )
        out = tmp_path / "generated"
        args = Namespace(command="compile", spec_dir=str(root), output_dir=str(out), project=None)
        rc = compile_cmd(args)
        assert rc == 0
        assert (out / "test_tc_001.py").exists()
        assert (out / "test_suite_001.py").exists()

    def test_compile_use_revised_keeps_original_suites(self, tmp_path, capsys) -> None:
        root = tmp_path / "specs"
        cases_dir = root / "cases"
        suites_dir = root / "suites"
        revised_dir = tmp_path / "review" / "revised"
        cases_dir.mkdir(parents=True)
        suites_dir.mkdir(parents=True)
        revised_dir.mkdir(parents=True)
        (cases_dir / "TC-001.md").write_text(
            "# TC-001 Original\n\n## Metadata\n- id: TC-001\n- level: case\n\n## When\n1. 返回主菜单\n",
            encoding="utf-8",
        )
        (revised_dir / "TC-001.md").write_text(
            "# TC-001 Revised\n\n## Metadata\n- id: TC-001\n- level: case\n\n## When\n1. 返回主菜单\n",
            encoding="utf-8",
        )
        (suites_dir / "SUITE-001.md").write_text(
            "# SUITE-001 Smoke\n\n## Metadata\n- id: SUITE-001\n- level: suite\n\n## Goal\n- 验证链路\n\n## Includes\n1. TC-001\n",
            encoding="utf-8",
        )
        out = tmp_path / "generated"
        args = Namespace(
            command="compile",
            spec_dir=str(root),
            output_dir=str(out),
            project=None,
            use_revised=True,
            revised_dir=str(revised_dir),
        )
        rc = compile_cmd(args)
        assert rc == 0
        assert (out / "test_tc_001.py").exists()
        assert (out / "test_suite_001.py").exists()


class TestOutputDirWritable:
    def test_recreates_generated_output_dir_when_probe_write_denied(self, tmp_path, monkeypatch) -> None:
        out = tmp_path / "generated"
        out.mkdir()
        (out / "test_old.py").write_text("old", encoding="utf-8")

        original_write_text = Path.write_text
        blocked = {"raised": False}

        def flaky_write_text(self: Path, data: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if self.parent == out and self.name == ".autotest-write-probe" and not blocked["raised"]:
                blocked["raised"] = True
                raise PermissionError("probe denied")
            return original_write_text(self, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", flaky_write_text)

        _ensure_output_dir_writable(str(out))

        assert out.is_dir()
        assert not (out / "test_old.py").exists()

    def test_raises_for_non_generated_output_dir_when_probe_write_denied(self, tmp_path, monkeypatch) -> None:
        out = tmp_path / "custom-output"
        out.mkdir()
        (out / "notes.txt").write_text("keep", encoding="utf-8")

        original_write_text = Path.write_text

        def deny_probe(self: Path, data: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if self.parent == out and self.name == ".autotest-write-probe":
                raise PermissionError("probe denied")
            return original_write_text(self, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", deny_probe)

        with pytest.raises(PermissionError, match="probe denied"):
            _ensure_output_dir_writable(str(out))
