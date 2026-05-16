"""Tests for CLI spec pipeline commands (review, compile, run --all)."""
from __future__ import annotations

import pytest
from pathlib import Path
from argparse import Namespace
from sts2_autotest.cli.main import review_cmd, compile_cmd, _create_parser


class TestCLIParser:
    def test_create_parser_has_review(self) -> None:
        parser = _create_parser()
        args = parser.parse_args(["review", "--spec-dir", "tests/cases"])
        assert args.command == "review"
        assert args.spec_dir == "tests/cases"

    def test_create_parser_has_compile(self) -> None:
        parser = _create_parser()
        args = parser.parse_args(["compile", "--spec-dir", "tests/cases", "--output-dir", "tests/generated"])
        assert args.command == "compile"
        assert args.spec_dir == "tests/cases"
        assert args.output_dir == "tests/generated"

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
        assert rc == 1

    def test_review_nonexistent_dir(self, capsys) -> None:
        args = Namespace(command="review", spec_dir="/nonexistent", project=None, output=None)
        rc = review_cmd(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out or "not found" in captured.err

    def test_review_empty_dir(self, tmp_path, capsys) -> None:
        d = tmp_path / "empty_specs"
        d.mkdir()
        args = Namespace(command="review", spec_dir=str(d), project=None, output=None)
        rc = review_cmd(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No spec files" in captured.out


class TestCompileCmd:
    def test_compile_no_spec_dir_no_project(self, capsys) -> None:
        args = Namespace(command="compile", spec_dir=None, output_dir=None, project=None)
        rc = compile_cmd(args)
        assert rc == 1

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
