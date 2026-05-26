"""Tests for markdown_parser.py — Markdown → TestSpec/SuiteSpec."""
from __future__ import annotations

import pytest
from sts2_autotest.common.spec_models import TestSpec, SuiteSpec
from sts2_autotest.core.markdown_parser import (
    MarkdownParser, ParsingError, detect_level,
)

SAMPLE_CASE_MD = """# TC-PREPARE-NEW-RUN 进入新局地图

## Metadata
- id: TC-PREPARE-NEW-RUN
- level: case
- tags: smoke, bootstrap
- priority: P0

## Start State
- 任意可恢复状态
- 允许当前处于 MAIN_MENU

## End State
- 到达 Act 1 地图

## Given
- 已安装并可连接 STS2-Cli-Mod
- 游戏可被启动

## When
1. 如 Steam 未启动，则启动 Steam
2. 选择 Ironclad
3. 开始新 run

## Then
- 不应出现 crash
- 最终应位于地图界面
"""

SAMPLE_SUITE_MD = """# SUITE-FIRST-BATTLE-SMOKE 首次战斗冒烟

## Metadata
- id: SUITE-FIRST-BATTLE-SMOKE
- level: suite
- tags: smoke, first_battle
- priority: P0

## Goal
- 验证从启动游戏到完成首次战斗的主链路可用

## Mode
- execution: sequential_shared_session

## Includes
1. TC-PREPARE-NEW-RUN
2. TC-RESOLVE-NEOW
3. TC-FINISH-FIRST-BATTLE

## Then
- 整条链路应可连续完成
"""


class TestDetectLevel:
    def test_detect_case(self) -> None:
        assert detect_level(SAMPLE_CASE_MD) == "case"

    def test_detect_suite(self) -> None:
        assert detect_level(SAMPLE_SUITE_MD) == "suite"

    def test_no_metadata_raises(self) -> None:
        with pytest.raises(ParsingError, match="No level found"):
            detect_level("# No metadata here\n\nJust some text")

    def test_invalid_level_raises(self) -> None:
        md = "# Test\n\n## Metadata\n- level: unknown_level"
        with pytest.raises(ParsingError, match="Invalid level"):
            detect_level(md)


class TestMarkdownParser:
    def setup_method(self) -> None:
        self.parser = MarkdownParser()

    def test_parse_full_case(self) -> None:
        spec = self.parser.parse_case(SAMPLE_CASE_MD)
        assert spec.id == "TC-PREPARE-NEW-RUN"
        assert spec.title == "进入新局地图"
        assert spec.tags == ["smoke", "bootstrap"]
        assert spec.priority == "P0"
        assert "任意可恢复状态" in spec.start_state
        assert "Act 1 地图" in spec.end_state
        assert len(spec.givens) == 2
        assert len(spec.steps) == 3
        assert len(spec.assertions) == 2
        assert spec.givens[0] == "已安装并可连接 STS2-Cli-Mod"
        assert spec.steps[1] == "选择 Ironclad"
        assert spec.assertions[0] == "不应出现 crash"

    def test_parse_case_minimal(self) -> None:
        md = "# TC-MINIMAL Just ID\n\n## Metadata\n- id: TC-MINIMAL\n- level: case"
        spec = self.parser.parse_case(md)
        assert spec.id == "TC-MINIMAL"
        assert spec.title == "Just ID"
        assert spec.priority == "P3"

    def test_parse_suite(self) -> None:
        suite = self.parser.parse_suite(SAMPLE_SUITE_MD)
        assert suite.id == "SUITE-FIRST-BATTLE-SMOKE"
        assert suite.title == "首次战斗冒烟"
        assert suite.tags == ["smoke", "first_battle"]
        assert suite.priority == "P0"
        assert "验证从启动游戏到完成" in suite.goal
        assert suite.execution_mode == "sequential_shared_session"
        assert suite.includes == ["TC-PREPARE-NEW-RUN", "TC-RESOLVE-NEOW", "TC-FINISH-FIRST-BATTLE"]
        assert len(suite.suite_assertions) == 1

    def test_parse_suite_no_mode_default(self) -> None:
        md = "# SUITE-X\n\n## Metadata\n- id: SUITE-X\n- level: suite"
        suite = self.parser.parse_suite(md)
        assert suite.execution_mode == "sequential_shared_session"

    def test_parse_empty_metadata(self) -> None:
        md = "# TC-EMPTY Empty\n\n## Metadata\n- id: TC-EMPTY\n- level: case"
        spec = self.parser.parse_case(md)
        assert spec.tags == []
        assert spec.steps == []
        assert spec.assertions == []

    def test_parse_no_metadata_section(self) -> None:
        md = "# TC-NO-META No Metadata\n\nJust text without sections"
        with pytest.raises(ParsingError, match="No metadata section"):
            self.parser.parse_case(md)

    def test_parse_no_id_in_metadata_with_heading_fallback(self) -> None:
        """No id in metadata; heading provides fallback TC-X."""
        md = "# TC-X Title\n\n## Metadata\n- level: case"
        spec = self.parser.parse_case(md)
        assert spec.id == "TC-X"

    def test_parse_no_id_anywhere_raises(self) -> None:
        """No id in metadata and no valid id in heading."""
        md = "# \n\n## Metadata\n- level: case"
        with pytest.raises(ParsingError, match="No id found"):
            self.parser.parse_case(md)

    def test_source_path_is_set(self) -> None:
        spec = self.parser.parse_case(SAMPLE_CASE_MD, source_path="tests/cases/test.md")
        assert spec.source_path == "tests/cases/test.md"

    def test_discover_specs_empty_dir(self, tmp_path) -> None:
        d = tmp_path / "specs"
        d.mkdir()
        cases, suites = self.parser.discover_specs(str(d))
        assert cases == []
        assert suites == []

    def test_discover_specs_mixed(self, tmp_path) -> None:
        d = tmp_path / "specs"
        d.mkdir()
        (d / "TC-001.md").write_text(SAMPLE_CASE_MD, encoding="utf-8")
        (d / "SUITE-001.md").write_text(SAMPLE_SUITE_MD, encoding="utf-8")
        (d / "readme.txt").write_text("not a spec", encoding="utf-8")
        cases, suites = self.parser.discover_specs(str(d))
        assert len(cases) == 1
        assert len(suites) == 1
        assert cases[0].id == "TC-PREPARE-NEW-RUN"
        assert suites[0].id == "SUITE-FIRST-BATTLE-SMOKE"

    def test_discover_specs_recursively_from_root_specs_dir(self, tmp_path) -> None:
        root = tmp_path / "specs"
        cases_dir = root / "cases"
        suites_dir = root / "suites"
        cases_dir.mkdir(parents=True)
        suites_dir.mkdir(parents=True)
        (cases_dir / "TC-001.md").write_text(SAMPLE_CASE_MD, encoding="utf-8")
        (suites_dir / "SUITE-001.md").write_text(SAMPLE_SUITE_MD, encoding="utf-8")
        cases, suites = self.parser.discover_specs(str(root))
        assert len(cases) == 1
        assert len(suites) == 1
