"""End-to-end integration test for the spec pipeline.

Creates sample .md spec files -> runs review -> runs compile ->
verifies generated test files are syntactically valid Python.
Does NOT execute the generated tests (requires real game).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sts2_autotest.core.markdown_parser import MarkdownParser
from sts2_autotest.core.spec_reviewer import SpecReviewer
from sts2_autotest.core.code_generator import CodeGenerator


SAMPLE_CASE = """\
# TC-PREPARE-NEW-RUN 进入新局地图

## Metadata
- id: TC-PREPARE-NEW-RUN
- level: case
- tags: smoke, bootstrap
- priority: P0

## Start State
- 任意可恢复状态

## End State
- 到达 Act 1 地图

## Given
- 已安装 STS2-Cli-Mod
- 游戏可被启动

## When
1. 启动游戏
2. 选择 Ironclad
3. 开始新 run

## Then
- 不应出现 crash
- 最终应位于地图界面
"""

SAMPLE_SUITE = """\
# SUITE-FIRST-BATTLE-SMOKE 首次战斗冒烟

## Metadata
- id: SUITE-FIRST-BATTLE-SMOKE
- level: suite
- tags: smoke, first_battle
- priority: P0

## Goal
- 验证从启动到完成首次战斗的主链路

## Mode
- execution: sequential_shared_session

## Includes
1. TC-PREPARE-NEW-RUN
2. TC-RESOLVE-NEOW
3. TC-FINISH-FIRST-BATTLE

## Then
- 整条链路应可连续完成
"""


class TestSpecPipelineE2E:
    def test_full_pipeline_review_compile(self, tmp_path) -> None:
        """Review -> compile cycle produces valid Python files."""
        # Arrange: create spec files
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        (spec_dir / "TC-PREPARE-NEW-RUN.md").write_text(SAMPLE_CASE, encoding="utf-8")
        (spec_dir / "SUITE-FIRST-BATTLE-SMOKE.md").write_text(SAMPLE_SUITE, encoding="utf-8")

        output_dir = tmp_path / "generated"
        output_dir.mkdir()

        # Act 1: Parse
        parser = MarkdownParser()
        cases, suites = parser.discover_specs(str(spec_dir))
        assert len(cases) == 1
        assert len(suites) == 1
        assert cases[0].id == "TC-PREPARE-NEW-RUN"

        # Act 2: Review
        reviewer = SpecReviewer()
        report = reviewer.review(cases[0])
        # Should not have critical issues for this well-formed spec
        assert report.passed, f"Unexpected issues: {report.issues}"

        # Act 3: Generate
        generator = CodeGenerator()
        out_path = generator.generate_to_file(cases[0], str(output_dir))
        assert Path(out_path).exists()

        # Assert: generated code is syntactically valid
        code = Path(out_path).read_text(encoding="utf-8")
        try:
            compile(code, str(out_path), "exec")
        except SyntaxError as e:
            pytest.fail(f"Generated code has syntax error: {e}")

        # Assert: generated code references DSL fixtures
        assert "from sts2_autotest.dsl.fluent import define" in code
        assert "autotest" in code
        assert "TC-PREPARE-NEW-RUN" in code
        assert "define(" in code

    def test_review_detects_bad_spec(self, tmp_path) -> None:
        """Review catches issues in poorly written specs."""
        bad_spec = """\
# TC-BAD Bad spec

## Metadata
- id: TC-BAD
- level: case
- tags:
- priority: P3

## When
1. 适当操作
2. 正常继续
"""
        spec_dir = tmp_path / "bad_specs"
        spec_dir.mkdir()
        (spec_dir / "TC-BAD.md").write_text(bad_spec, encoding="utf-8")

        parser = MarkdownParser()
        cases, _ = parser.discover_specs(str(spec_dir))
        assert len(cases) == 1

        reviewer = SpecReviewer()
        report = reviewer.review(cases[0])
        assert not report.passed
        assert len(report.issues) >= 2  # ambiguity + missing items

    def test_compile_generates_skip_for_empty_spec(self, tmp_path) -> None:
        """Empty specs generate skipped tests rather than crashing."""
        empty_spec = """\
# TC-EMPTY Empty

## Metadata
- id: TC-EMPTY
- level: case
"""
        spec_dir = tmp_path / "empty_specs"
        spec_dir.mkdir()
        (spec_dir / "TC-EMPTY.md").write_text(empty_spec, encoding="utf-8")

        parser = MarkdownParser()
        cases, _ = parser.discover_specs(str(spec_dir))

        generator = CodeGenerator()
        output_dir = tmp_path / "generated"
        output_dir.mkdir()
        out_path = generator.generate_to_file(cases[0], str(output_dir))
        code = Path(out_path).read_text(encoding="utf-8")
        assert "pytest.skip" in code

    def test_suite_generates_valid_python(self, tmp_path) -> None:
        """Suite generation produces valid Python."""
        spec_dir = tmp_path / "suite_specs"
        spec_dir.mkdir()
        (spec_dir / "SUITE-TEST.md").write_text(SAMPLE_SUITE, encoding="utf-8")
        (spec_dir / "TC-PREPARE-NEW-RUN.md").write_text(SAMPLE_CASE, encoding="utf-8")

        parser = MarkdownParser()
        cases, suites = parser.discover_specs(str(spec_dir))

        specs_dict = {s.id: s for s in cases}
        generator = CodeGenerator()
        code = generator.generate_suite_test(suites[0], specs_dict)
        try:
            compile(code, "<test>", "exec")
        except SyntaxError as e:
            pytest.fail(f"Suite generated code has syntax error: {e}")
