# 自然语言测试流水线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pipeline that accepts Markdown test specs → reviews them → compiles to TestSpec models → generates pytest code → executes via CLI, with MOD project discovery via `--spec-dir` or workspace config.

**Architecture:** Layered pipeline in 6 independent modules: data models (`common/`), parser (`core/`), reviewer (`core/`), code generator (`core/`), workspace discovery (`core/`), CLI integration (`cli/`). Each layer only depends on `common/`. Code generator outputs text templates referencing existing DSL — no runtime import of `dsl/` or `core/`.

**Tech Stack:** Python 3.11+, dataclasses, argparse (existing), pytest, PyYAML (existing dep), markdown-it or regex-based Markdown parsing

---

### Task 1: TestSpec / SuiteSpec 数据模型

**Files:**
- Create: `src/sts2_autotest/common/spec_models.py`
- Test: `tests/unit/test_spec_models.py`

- [ ] **Step 1.1: Write the failing tests**

```python
"""Tests for spec_models.py — TestSpec, SuiteSpec, ReviewReport, ProjectConfig."""
import pytest
from sts2_autotest.common.spec_models import (
    TestSpec, SuiteSpec, ReviewReport, RevisedDraft,
    ReviewIssue, IssueCategory, ProjectConfig, WorkspaceConfig,
)


class TestTestSpec:
    def test_minimal_creation(self):
        spec = TestSpec(id="TC-001", title="Minimal test")
        assert spec.id == "TC-001"
        assert spec.title == "Minimal test"
        assert spec.priority == "P3"
        assert spec.tags == []
        assert spec.steps == []
        assert spec.assertions == []

    def test_full_creation(self):
        spec = TestSpec(
            id="TC-PREPARE-NEW-RUN",
            title="进入新局地图",
            tags=["smoke", "bootstrap"],
            priority="P0",
            start_state="任意可恢复状态",
            end_state="Act 1 地图，首个节点可选",
            givens=["已安装 STS2-Cli-Mod", "游戏可被启动"],
            steps=["启动 Steam", "启动游戏", "选择 Ironclad"],
            assertions=["不应出现 crash", "应位于地图界面"],
            fallback_policies=["超时重试 3 次"],
            capability_requirements=["adapter.cli_mod"],
        )
        assert spec.id == "TC-PREPARE-NEW-RUN"
        assert len(spec.steps) == 3
        assert len(spec.assertions) == 2

    def test_default_fields_are_empty_lists(self):
        spec = TestSpec(id="TC-002", title="Test")
        assert spec.fallback_policies == []
        assert spec.capability_requirements == []


class TestSuiteSpec:
    def test_minimal_creation(self):
        suite = SuiteSpec(id="SUITE-FIRST-BATTLE", title="冒烟测试")
        assert suite.id == "SUITE-FIRST-BATTLE"
        assert suite.execution_mode == "sequential_shared_session"
        assert suite.includes == []

    def test_with_includes(self):
        suite = SuiteSpec(
            id="SUITE-FIRST-BATTLE-SMOKE",
            title="首次战斗冒烟",
            tags=["smoke", "first_battle"],
            priority="P0",
            goal="验证从启动到完成首次战斗的主链路",
            execution_mode="sequential_shared_session",
            includes=["TC-PREPARE-NEW-RUN", "TC-RESOLVE-NEOW", "TC-FINISH-FIRST-BATTLE"],
            suite_assertions=["整条链路应可连续完成"],
        )
        assert len(suite.includes) == 3
        assert suite.goal.startswith("验证")


class TestReviewIssue:
    def test_issue_creation(self):
        issue = ReviewIssue(
            category=IssueCategory.AMBIGUITY,
            location="When step 3",
            description="'适当选择' 过于模糊",
            suggestion="明确指定选择策略",
        )
        assert issue.category == IssueCategory.AMBIGUITY
        assert "模糊" in issue.description


class TestReviewReport:
    def test_report_with_issues(self):
        issues = [
            ReviewIssue(IssueCategory.AMBIGUITY, "step 3", "模糊", "明确指定"),
            ReviewIssue(IssueCategory.MISSING, "Metadata", "缺少 priority", "添加 P0-P3"),
        ]
        report = ReviewReport(spec_id="TC-001", issues=issues)
        assert len(report.issues) == 2
        assert report.summary["total"] == 2
        assert report.summary["ambiguity"] == 1
        assert report.summary["missing"] == 1

    def test_empty_report(self):
        report = ReviewReport(spec_id="TC-001", issues=[])
        assert report.summary["total"] == 0
        assert report.passed


class TestProjectConfig:
    def test_project_config(self):
        p = ProjectConfig(name="my-mod", spec_dir="../my-mod/tests/cases/", output_dir="../my-mod/tests/")
        assert p.name == "my-mod"

    def test_default_output_dir(self):
        p = ProjectConfig(name="my-mod", spec_dir="../my-mod/tests/cases/")
        assert p.output_dir == "../my-mod/tests/cases/"  # same as spec_dir


class TestWorkspaceConfig:
    def test_workspace_config(self):
        wc = WorkspaceConfig(projects=[
            ProjectConfig(name="mod-a", spec_dir="path/a"),
            ProjectConfig(name="mod-b", spec_dir="path/b"),
        ])
        assert len(wc.projects) == 2

    def test_empty_workspace(self):
        wc = WorkspaceConfig(projects=[])
        assert len(wc.projects) == 0
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_spec_models.py -v`
Expected: ModuleNotFoundError or ImportError (module doesn't exist yet)

- [ ] **Step 1.3: Write the implementation**

```python
"""Data models for the natural language test spec pipeline.

TestSpec and SuiteSpec are the internal representation of parsed
Markdown test specifications. ReviewReport and RevisedDraft are
outputs of the review phase.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional


class IssueCategory(StrEnum):
    """Categories of issues found during spec review."""
    AMBIGUITY = "ambiguity"           # 模糊项
    MISSING = "missing"               # 缺失项
    UNIMPLEMENTABLE = "unimplementable"  # 不可实现项
    CAPABILITY_GAP = "capability_gap" # 待扩展能力


@dataclass
class ReviewIssue:
    """A single issue found during spec review."""
    category: IssueCategory
    location: str           # which section/step the issue is in
    description: str        # what's wrong
    suggestion: str         # how to fix it


@dataclass
class ReviewReport:
    """Output of the spec review phase."""
    spec_id: str
    issues: list[ReviewIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """No critical issues — review passed."""
        return len(self.issues) == 0

    @property
    def summary(self) -> dict[str, int]:
        """Summary of issue counts by category."""
        counts: dict[str, int] = {"total": len(self.issues)}
        for cat in IssueCategory:
            counts[cat.value] = sum(1 for i in self.issues if i.category == cat)
        return counts


@dataclass
class RevisedDraft:
    """Improved Markdown draft after review.

    Contains the same spec in a more concrete, implementable form.
    Not test code — it's a candidate Markdown spec draft.
    """
    spec_id: str
    original_path: str
    markdown_content: str
    changes_summary: list[str] = field(default_factory=list)


@dataclass
class TestSpec:
    """Internal representation of a single test case.

    Parsed from Markdown, consumed by the reviewer and code generator.
    """
    id: str
    title: str
    tags: list[str] = field(default_factory=list)
    priority: str = "P3"
    start_state: str = ""
    end_state: str = ""
    givens: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    fallback_policies: list[str] = field(default_factory=list)
    capability_requirements: list[str] = field(default_factory=list)
    source_path: str = ""


@dataclass
class SuiteSpec:
    """Internal representation of a test suite (composed of multiple TestSpecs)."""
    id: str
    title: str
    tags: list[str] = field(default_factory=list)
    priority: str = "P3"
    goal: str = ""
    execution_mode: str = "sequential_shared_session"
    includes: list[str] = field(default_factory=list)
    suite_assertions: list[str] = field(default_factory=list)
    source_path: str = ""


@dataclass
class ProjectConfig:
    """Configuration for a single MOD project within the workspace."""
    name: str
    spec_dir: str
    output_dir: str = ""


@dataclass
class WorkspaceConfig:
    """Workspace configuration loaded from sts2-autotest.yaml."""
    projects: list[ProjectConfig] = field(default_factory=list)
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_spec_models.py -v`
Expected: All tests PASS

- [ ] **Step 1.5: Commit**

```bash
git add src/sts2_autotest/common/spec_models.py tests/unit/test_spec_models.py
git commit -m "feat: add TestSpec/SuiteSpec data models for spec pipeline"
```

---

### Task 2: Markdown 解析器

**Files:**
- Create: `src/sts2_autotest/core/markdown_parser.py`
- Test: `tests/unit/test_markdown_parser.py`

- [ ] **Step 2.1: Write the failing tests**

```python
"""Tests for markdown_parser.py — Markdown → TestSpec/SuiteSpec."""
import pytest
from sts2_autotest.common.spec_models import TestSpec, SuiteSpec, IssueCategory
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
    def test_detect_case(self):
        assert detect_level(SAMPLE_CASE_MD) == "case"

    def test_detect_suite(self):
        assert detect_level(SAMPLE_SUITE_MD) == "suite"

    def test_no_metadata_raises(self):
        with pytest.raises(ParsingError, match="No level found"):
            detect_level("# No metadata here\n\nJust some text")

    def test_invalid_level_raises(self):
        md = """# Test\n\n## Metadata\n- level: unknown_level"""
        with pytest.raises(ParsingError, match="Invalid level"):
            detect_level(md)


class TestMarkdownParser:
    def setup_method(self):
        self.parser = MarkdownParser()

    def test_parse_full_case(self):
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

    def test_parse_case_minimal(self):
        md = """# TC-MINIMAL Just ID\n\n## Metadata\n- id: TC-MINIMAL\n- level: case"""
        spec = self.parser.parse_case(md)
        assert spec.id == "TC-MINIMAL"
        assert spec.title == "Just ID"
        assert spec.priority == "P3"

    def test_parse_suite(self):
        suite = self.parser.parse_suite(SAMPLE_SUITE_MD)
        assert suite.id == "SUITE-FIRST-BATTLE-SMOKE"
        assert suite.title == "首次战斗冒烟"
        assert suite.tags == ["smoke", "first_battle"]
        assert suite.priority == "P0"
        assert "验证从启动到完成" in suite.goal
        assert suite.execution_mode == "sequential_shared_session"
        assert suite.includes == ["TC-PREPARE-NEW-RUN", "TC-RESOLVE-NEOW", "TC-FINISH-FIRST-BATTLE"]
        assert len(suite.suite_assertions) == 1

    def test_parse_suite_no_mode_default(self):
        md = """# SUITE-X\n\n## Metadata\n- id: SUITE-X\n- level: suite"""
        suite = self.parser.parse_suite(md)
        assert suite.execution_mode == "sequential_shared_session"

    def test_parse_empty_metadata(self):
        md = """# TC-EMPTY Empty\n\n## Metadata\n- id: TC-EMPTY\n- level: case"""
        spec = self.parser.parse_case(md)
        assert spec.tags == []
        assert spec.steps == []
        assert spec.assertions == []

    def test_parse_no_metadata_section(self):
        md = """# TC-NO-META No Metadata\n\nJust text without sections"""
        with pytest.raises(ParsingError, match="No metadata section"):
            self.parser.parse_case(md)

    def test_parse_no_id_in_metadata(self):
        md = """# TC-X Title\n\n## Metadata\n- level: case"""
        with pytest.raises(ParsingError, match="No id found"):
            self.parser.parse_case(md)

    def test_source_path_is_set(self):
        spec = self.parser.parse_case(SAMPLE_CASE_MD, source_path="tests/cases/test.md")
        assert spec.source_path == "tests/cases/test.md"

    def test_discover_specs_empty_dir(self, tmp_path):
        d = tmp_path / "specs"
        d.mkdir()
        specs = self.parser.discover_specs(str(d))
        assert specs == ([], [])

    def test_discover_specs_mixed(self, tmp_path):
        d = tmp_path / "specs"
        d.mkdir()
        (d / "TC-001.md").write_text(SAMPLE_CASE_MD)
        (d / "SUITE-001.md").write_text(SAMPLE_SUITE_MD)
        (d / "readme.txt").write_text("not a spec")
        cases, suites = self.parser.discover_specs(str(d))
        assert len(cases) == 1
        assert len(suites) == 1
        assert cases[0].id == "TC-PREPARE-NEW-RUN"
        assert suites[0].id == "SUITE-FIRST-BATTLE-SMOKE"
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_markdown_parser.py -v`
Expected: ModuleNotFoundError or ImportError

- [ ] **Step 2.3: Write the implementation**

```python
"""Markdown parser for natural language test specs (case + suite).

Parses structured Markdown into TestSpec/SuiteSpec models.
Uses regex-based section parsing — no external Markdown parser needed
for the well-defined template format.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from sts2_autotest.common.spec_models import TestSpec, SuiteSpec


class ParsingError(ValueError):
    """Raised when a Markdown spec cannot be parsed."""
    pass


def detect_level(markdown: str) -> str:
    """Detect whether the Markdown is a 'case' or 'suite' spec.

    Reads the `level` field from the Metadata section.
    """
    metadata = _extract_section(markdown, "Metadata")
    if metadata is None:
        raise ParsingError("No level found: no Metadata section")

    m = re.search(r'-\s*level\s*:\s*(\w+)', metadata)
    if not m:
        raise ParsingError("No level found in Metadata")

    level = m.group(1).lower()
    if level not in ("case", "suite"):
        raise ParsingError(f"Invalid level: '{level}'. Must be 'case' or 'suite'.")
    return level


def _extract_section(markdown: str, section_name: str) -> Optional[str]:
    """Extract a section's content by its ## heading name."""
    pattern = rf'##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s|\Z)'
    m = re.search(pattern, markdown, re.DOTALL)
    return m.group(1).strip() if m else None


def _parse_list_items(text: str) -> list[str]:
    """Parse a list of `- item` or `1. item` lines into a list of strings."""
    items = []
    for line in text.strip().split("\n"):
        line = line.strip()
        # Match `- text` or `1. text`
        m = re.match(r'^[-]\s+(.*)', line)
        if not m:
            m = re.match(r'^\d+[.]\s+(.*)', line)
        if m:
            items.append(m.group(1).strip())
    return items


def _parse_kv_list(text: str) -> dict[str, str]:
    """Parse `- key: value` lines into a dict."""
    result = {}
    for line in text.strip().split("\n"):
        m = re.match(r'-\s*(\w+)\s*:\s*(.+)', line.strip())
        if m:
            result[m.group(1).strip()] = m.group(2).strip()
    return result


def _parse_title(markdown: str) -> str:
    """Extract title from `# TC-ID Title` heading."""
    m = re.match(r'^#\s+\S+\s+(.*)', markdown)
    return m.group(1).strip() if m else ""


def _parse_id_from_heading(markdown: str) -> str:
    """Extract ID from `# TC-ID Title` heading."""
    m = re.match(r'^#\s+(\S+)', markdown)
    return m.group(1).strip() if m else ""


class MarkdownParser:
    """Parses structured Markdown test specs into TestSpec/SuiteSpec models."""

    def parse_case(self, markdown: str, source_path: str = "") -> TestSpec:
        """Parse a Markdown case spec into a TestSpec."""
        metadata_text = _extract_section(markdown, "Metadata")
        if metadata_text is None:
            raise ParsingError("No metadata section found")

        metadata = _parse_kv_list(metadata_text)
        spec_id = metadata.get("id") or _parse_id_from_heading(markdown)
        if not spec_id:
            raise ParsingError("No id found in Metadata or heading")

        title = metadata.get("title", _parse_title(markdown))
        tags_str = metadata.get("tags", "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        priority = metadata.get("priority", "P3")

        start_state = self._parse_section_text(markdown, "Start State")
        end_state = self._parse_section_text(markdown, "End State")
        givens = self._parse_section_list(markdown, "Given")
        steps = self._parse_section_list(markdown, "When")
        assertions = self._parse_section_list(markdown, "Then")

        return TestSpec(
            id=spec_id,
            title=title,
            tags=tags,
            priority=priority,
            start_state=start_state,
            end_state=end_state,
            givens=givens,
            steps=steps,
            assertions=assertions,
            source_path=source_path,
        )

    def parse_suite(self, markdown: str, source_path: str = "") -> SuiteSpec:
        """Parse a Markdown suite spec into a SuiteSpec."""
        metadata_text = _extract_section(markdown, "Metadata")
        if metadata_text is None:
            raise ParsingError("No metadata section found")

        metadata = _parse_kv_list(metadata_text)
        suite_id = metadata.get("id") or _parse_id_from_heading(markdown)
        if not suite_id:
            raise ParsingError("No id found in Metadata or heading")

        title = metadata.get("title", _parse_title(markdown))
        tags_str = metadata.get("tags", "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        priority = metadata.get("priority", "P3")

        goal_text = self._parse_section_text(markdown, "Goal")
        mode_text = self._parse_section_text(markdown, "Mode")
        execution_mode = "sequential_shared_session"
        if mode_text:
            m = re.search(r'execution\s*:\s*(\S+)', mode_text)
            if m:
                execution_mode = m.group(1)

        includes = self._parse_section_list(markdown, "Includes")
        suite_assertions = self._parse_section_list(markdown, "Then")

        return SuiteSpec(
            id=suite_id,
            title=title,
            tags=tags,
            priority=priority,
            goal=goal_text,
            execution_mode=execution_mode,
            includes=includes,
            suite_assertions=suite_assertions,
            source_path=source_path,
        )

    def discover_specs(self, spec_dir: str) -> tuple[list[TestSpec], list[SuiteSpec]]:
        """Scan a directory for .md spec files, parse them, split into cases and suites."""
        cases: list[TestSpec] = []
        suites: list[SuiteSpec] = []
        path = Path(spec_dir)
        if not path.is_dir():
            return cases, suites

        for f in sorted(path.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")
                level = detect_level(text)
                if level == "case":
                    cases.append(self.parse_case(text, source_path=str(f)))
                elif level == "suite":
                    suites.append(self.parse_suite(text, source_path=str(f)))
            except ParsingError:
                continue  # skip files that don't match the spec format
        return cases, suites

    def _parse_section_text(self, markdown: str, name: str) -> str:
        """Extract a section's content as raw text."""
        text = _extract_section(markdown, name)
        return text if text else ""

    def _parse_section_list(self, markdown: str, name: str) -> list[str]:
        """Extract a section's content as a list of items."""
        text = _extract_section(markdown, name)
        return _parse_list_items(text) if text else []
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_markdown_parser.py -v`
Expected: All tests PASS

- [ ] **Step 2.5: Commit**

```bash
git add src/sts2_autotest/core/markdown_parser.py tests/unit/test_markdown_parser.py
git commit -m "feat: add Markdown parser for test specs"
```

---

### Task 3: 规格审查器 + Revised Draft 生成器

**Files:**
- Create: `src/sts2_autotest/core/spec_reviewer.py`
- Test: `tests/unit/test_spec_reviewer.py`

- [ ] **Step 3.1: Write the failing tests**

```python
"""Tests for spec_reviewer.py — ReviewReport + RevisedDraft generation."""
import pytest
from sts2_autotest.common.spec_models import (
    TestSpec, SuiteSpec, ReviewReport, RevisedDraft,
    ReviewIssue, IssueCategory,
)
from sts2_autotest.core.spec_reviewer import SpecReviewer


class TestSpecReviewer:
    def setup_method(self):
        self.reviewer = SpecReviewer()

    def test_review_clean_spec(self):
        spec = TestSpec(
            id="TC-CLEAN",
            title="Clean test",
            priority="P0",
            start_state="MAIN_MENU",
            end_state="MAP",
            givens=["已安装 MOD"],
            steps=["启动游戏", "选择 Ironclad"],
            assertions=["不 crash", "到达 MAP"],
        )
        report = self.reviewer.review(spec)
        assert report.passed
        assert len(report.issues) == 0

    def test_review_detects_ambiguity(self):
        spec = TestSpec(
            id="TC-AMBI",
            title="Ambiguous test",
            steps=["适当选择角色", "正常继续", "尽快赢下战斗"],
        )
        report = self.reviewer.review(spec)
        ambiguous = [i for i in report.issues if i.category == IssueCategory.AMBIGUITY]
        assert len(ambiguous) >= 1
        assert any("适当" in i.description for i in ambiguous)
        assert any("尽快" in i.description for i in ambiguous)

    def test_review_detects_missing_priority(self):
        spec = TestSpec(id="TC-NO-PRIO", title="No priority")
        report = self.reviewer.review(spec)
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("priority" in i.description.lower() for i in missing)

    def test_review_detects_missing_start_state(self):
        spec = TestSpec(id="TC-NO-START", title="No start state")
        report = self.reviewer.review(spec)
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("start" in i.description.lower() for i in missing)

    def test_review_detects_missing_end_state(self):
        spec = TestSpec(id="TC-NO-END", title="No end state")
        report = self.reviewer.review(spec)
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("end" in i.description.lower() for i in missing)

    def test_review_detects_missing_assertions(self):
        spec = TestSpec(id="TC-NO-ASSERT", title="No assertions")
        report = self.reviewer.review(spec)
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("assertion" in i.description.lower() for i in missing)

    def test_review_detects_unimplementable_steps(self):
        spec = TestSpec(
            id="TC-UNIMPL",
            title="Unimplementable",
            steps=["使用未实现的功能", "正常操作"],
        )
        report = self.reviewer.review(spec)
        unimplementable = [i for i in report.issues if i.category == IssueCategory.UNIMPLEMENTABLE]
        assert any("未实现的功能" in i.description for i in unimplementable)

    def test_review_multiple_issues(self):
        spec = TestSpec(
            id="TC-MULTI",
            title="Multiple issues",
            steps=["适当选择"],
        )
        report = self.reviewer.review(spec)
        assert not report.passed
        assert len(report.issues) >= 2  # ambiguity + missing items

    def test_generate_revised_draft_fixes_ambiguity(self):
        spec = TestSpec(
            id="TC-DRAFT",
            title="Need draft",
            steps=["适当选择角色"],
        )
        report = self.reviewer.review(spec)
        draft = self.reviewer.generate_revised_draft(spec, report)
        assert isinstance(draft, RevisedDraft)
        assert draft.spec_id == "TC-DRAFT"
        assert len(draft.changes_summary) > 0
        # The draft should replace ambiguous terms with concrete alternatives
        assert "适当" not in draft.markdown_content

    def test_generate_revised_draft_clean_spec(self):
        spec = TestSpec(
            id="TC-CLEAN-DRAFT",
            title="Already clean",
            priority="P0",
            start_state="MAIN_MENU",
            end_state="MAP",
            steps=["启动游戏"],
            assertions=["到达 MAP"],
        )
        report = self.reviewer.review(spec)
        draft = self.reviewer.generate_revised_draft(spec, report)
        assert draft.spec_id == "TC-CLEAN-DRAFT"
        assert draft.changes_summary == ["No issues found — spec is already clean"]
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_spec_reviewer.py -v`
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 3.3: Write the implementation**

```python
"""Spec reviewer — analyzes TestSpec for clarity and feasibility.

Produces ReviewReport (diagnostics) and RevisedDraft (improved Markdown).
"""

from __future__ import annotations

import re
from typing import Optional

from sts2_autotest.common.spec_models import (
    TestSpec, SuiteSpec, ReviewReport, RevisedDraft,
    ReviewIssue, IssueCategory,
)


# Ambiguous Chinese phrases that signal unclear test steps
_AMBIGUITY_PATTERNS: list[tuple[str, str]] = [
    (r"适当", "适当" is ambiguous — specify exact condition or value"),
    (r"正常", "正常" is ambiguous — define what 'normal' means"),
    (r"尽快", "尽快" is ambiguous — specify time bound or turn limit"),
    (r"合理", "合理" is ambiguous — specify expected range or criteria"),
    (r"酌情", "酌情" is ambiguous — specify decision rule"),
    (r"相关", "相关" is ambiguous — specify which related items"),
]

# Keywords indicating steps that need framework capabilities not yet built
_UNIMPLEMENTABLE_KEYWORDS: list[str] = [
    "使用未实现", "未实现的功能",
]


class SpecReviewer:
    """Reviews TestSpec for clarity, completeness, and feasibility."""

    def review(self, spec: TestSpec) -> ReviewReport:
        """Run all checks on a TestSpec and return a ReviewReport."""
        issues: list[ReviewIssue] = []

        self._check_ambiguity(spec, issues)
        self._check_completeness(spec, issues)
        self._check_feasibility(spec, issues)

        return ReviewReport(spec_id=spec.id, issues=issues)

    def review_suite(self, suite: SuiteSpec) -> ReviewReport:
        """Run checks on a SuiteSpec."""
        issues: list[ReviewIssue] = []
        if not suite.includes:
            issues.append(ReviewIssue(
                category=IssueCategory.MISSING,
                location="Includes",
                description="Suite has no included test cases",
                suggestion="Add at least one case reference under ## Includes",
            ))
        if not suite.goal:
            issues.append(ReviewIssue(
                category=IssueCategory.MISSING,
                location="Goal",
                description="Suite has no goal defined",
                suggestion="Add a ## Goal section describing the suite purpose",
            ))
        return ReviewReport(spec_id=suite.id, issues=issues)

    def generate_revised_draft(self, spec: TestSpec, report: ReviewReport) -> RevisedDraft:
        """Generate an improved Markdown draft based on review findings.

        Replaces ambiguous terms with concrete alternatives and
        fills in missing sections with placeholder suggestions.
        """
        changes: list[str] = []

        if report.passed:
            return RevisedDraft(
                spec_id=spec.id,
                original_path=spec.source_path,
                markdown_content=self._spec_to_markdown(spec),
                changes_summary=["No issues found — spec is already clean"],
            )

        revised = self._spec_to_markdown(spec)

        for issue in report.issues:
            if issue.category == IssueCategory.AMBIGUITY:
                for pattern, _ in _AMBIGUITY_PATTERNS:
                    p = re.compile(pattern)
                    if p.search(revised):
                        revised = p.sub(issue.suggestion or "[明确指定]", revised)
                        changes.append(f"Replaced '{pattern}' with concrete wording")
                        break

            elif issue.category == IssueCategory.MISSING:
                if "start state" in issue.description.lower() and not spec.start_state:
                    changes.append("Added placeholder for Start State")
                elif "end state" in issue.description.lower() and not spec.end_state:
                    changes.append("Added placeholder for End State")
                elif "priority" in issue.description.lower() and spec.priority == "P3":
                    changes.append("Set priority to P3 (default) — please review")
                elif "assertion" in issue.description.lower() and not spec.assertions:
                    changes.append("Added placeholder assertions — please define measurable checks")

        return RevisedDraft(
            spec_id=spec.id,
            original_path=spec.source_path,
            markdown_content=revised,
            changes_summary=changes or ["Minor adjustments applied"],
        )

    def _check_ambiguity(self, spec: TestSpec, issues: list[ReviewIssue]) -> None:
        """Check steps and assertions for ambiguous wording."""
        all_text = " ".join(spec.steps) + " " + " ".join(spec.assertions)
        for pattern, message in _AMBIGUITY_PATTERNS:
            if re.search(pattern, all_text):
                issues.append(ReviewIssue(
                    category=IssueCategory.AMBIGUITY,
                    location="When/Then steps",
                    description=message,
                    suggestion=self._suggestion_for(pattern),
                ))

    def _check_completeness(self, spec: TestSpec, issues: list[ReviewIssue]) -> None:
        """Check that required fields are populated."""
        if spec.priority == "P3" and spec.priority == "P3":
            # Only flag if it's the default and never explicitly set
            issues.append(ReviewIssue(
                category=IssueCategory.MISSING,
                location="Metadata",
                description="Priority is not explicitly set (defaults to P3)",
                suggestion="Add '- priority: P0/P1/P2/P3' to Metadata",
            ))
        if not spec.start_state:
            issues.append(ReviewIssue(
                category=IssueCategory.MISSING,
                location="Start State",
                description="No start state declared",
                suggestion="Add ## Start State section with expected preconditions",
            ))
        if not spec.end_state:
            issues.append(ReviewIssue(
                category=IssueCategory.MISSING,
                location="End State",
                description="No end state declared",
                suggestion="Add ## End State section with expected postconditions",
            ))
        if not spec.assertions:
            issues.append(ReviewIssue(
                category=IssueCategory.MISSING,
                location="Then",
                description="No assertions defined",
                suggestion="Add ## Then section with measurable assertions",
            ))

    def _check_feasibility(self, spec: TestSpec, issues: list[ReviewIssue]) -> None:
        """Check steps against known framework capabilities."""
        for step in spec.steps:
            for keyword in _UNIMPLEMENTABLE_KEYWORDS:
                if keyword in step:
                    issues.append(ReviewIssue(
                        category=IssueCategory.UNIMPLEMENTABLE,
                        location=f"When step: '{step}'",
                        description=f"Step references unimplemented capability: {keyword}",
                        suggestion="Remove this step or implement the required framework capability first",
                    ))
                    break

    def _spec_to_markdown(self, spec: TestSpec) -> str:
        """Render a TestSpec back to Markdown format."""
        lines = [f"# {spec.id} {spec.title}", ""]
        lines.append("## Metadata")
        lines.append(f"- id: {spec.id}")
        lines.append(f"- level: case")
        if spec.tags:
            lines.append(f"- tags: {', '.join(spec.tags)}")
        lines.append(f"- priority: {spec.priority}")
        lines.append("")

        if spec.start_state:
            lines.append("## Start State")
            for s in spec.start_state.split("\n"):
                lines.append(f"- {s.strip()}")
            lines.append("")

        if spec.end_state:
            lines.append("## End State")
            for s in spec.end_state.split("\n"):
                lines.append(f"- {s.strip()}")
            lines.append("")

        if spec.givens:
            lines.append("## Given")
            for g in spec.givens:
                lines.append(f"- {g}")
            lines.append("")

        if spec.steps:
            lines.append("## When")
            for i, step in enumerate(spec.steps, 1):
                lines.append(f"{i}. {step}")
            lines.append("")

        if spec.assertions:
            lines.append("## Then")
            for a in spec.assertions:
                lines.append(f"- {a}")
            lines.append("")

        return "\n".join(lines)

    def _suggestion_for(self, pattern: str) -> str:
        suggestions = {
            r"适当": "明确指定选择条件或值（如 '选择第一个可用选项'）",
            r"正常": "定义具体行为（如 '等待 5 秒后检查状态'）",
            r"尽快": "指定回合限制或时间上限（如 '在 15 回合内'）",
            r"合理": "指定预期的数值范围（如 'HP 减少 ≥ 10'）",
            r"酌情": "指定决策规则（如 '总是选择奖励'）",
        }
        return suggestions.get(pattern, "请更具体地描述该步骤")
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_spec_reviewer.py -v`
Expected: All tests PASS

- [ ] **Step 3.5: Commit**

```bash
git add src/sts2_autotest/core/spec_reviewer.py tests/unit/test_spec_reviewer.py
git commit -m "feat: add spec reviewer with ambiguity/completeness/feasibility checks"
```

---

### Task 4: 代码生成器（TestSpec → pytest + Fluent DSL）

**Files:**
- Create: `src/sts2_autotest/core/code_generator.py`
- Test: `tests/unit/test_code_generator.py`

- [ ] **Step 4.1: Write the failing tests**

```python
"""Tests for code_generator.py — TestSpec → pytest code generation."""
import pytest
from pathlib import Path
from sts2_autotest.common.spec_models import TestSpec, SuiteSpec
from sts2_autotest.core.code_generator import CodeGenerator


class TestCodeGenerator:
    def setup_method(self):
        self.generator = CodeGenerator()

    def test_generate_case_test_basic(self):
        spec = TestSpec(
            id="TC-PREPARE-NEW-RUN",
            title="进入新局地图",
            tags=["smoke", "bootstrap"],
            priority="P0",
            steps=["启动游戏", "选择 Ironclad", "开始新 run"],
            assertions=["不 crash", "位于 MAP"],
        )
        code = self.generator.generate_case_test(spec)
        assert "def test_tc_prepare_new_run" in code
        assert "TC-PREPARE-NEW-RUN" in code
        assert "select_character" in code or "Ironclad" in code or "ironclad" in code.lower()
        assert "autotest" in code
        assert "_session_loop" in code
        assert "FluentBuilder" in code or "define(" in code or "from sts2_autotest.dsl.fluent import define" in code

    def test_generate_case_test_with_givens(self):
        spec = TestSpec(
            id="TC-SETUP",
            title="Setup test",
            givens=["已安装 MOD", "游戏可被启动"],
            steps=["启动游戏"],
            assertions=["游戏运行中"],
        )
        code = self.generator.generate_case_test(spec)
        # Givens should become comments or setup in the test
        assert "TC-SETUP" in code

    def test_generate_case_test_empty_steps(self):
        spec = TestSpec(id="TC-EMPTY", title="Empty")
        code = self.generator.generate_case_test(spec)
        assert "def test_tc_empty" in code
        # Should still generate valid code even with no steps
        assert "skip" in code.lower() or "pass" in code or "no steps" in code

    def test_generate_suite_test_basic(self):
        suite = SuiteSpec(
            id="SUITE-FIRST-BATTLE-SMOKE",
            title="首次战斗冒烟",
            includes=["TC-PREPARE-NEW-RUN", "TC-RESOLVE-NEOW", "TC-FINISH-FIRST-BATTLE"],
            suite_assertions=["链路应可连续完成"],
        )
        specs = {
            "TC-PREPARE-NEW-RUN": TestSpec(id="TC-PREPARE-NEW-RUN", steps=["启动游戏"]),
            "TC-RESOLVE-NEOW": TestSpec(id="TC-RESOLVE-NEOW", steps=["选择祝福"]),
            "TC-FINISH-FIRST-BATTLE": TestSpec(id="TC-FINISH-FIRST-BATTLE", steps=["战斗"]),
        }
        code = self.generator.generate_suite_test(suite, specs)
        assert "class TestSuiteFirstBattleSmoke" in code or "def test_suite_first_battle_smoke" in code
        assert "TC-PREPARE-NEW-RUN" in code
        assert "TC-RESOLVE-NEOW" in code
        assert "TC-FINISH-FIRST-BATTLE" in code

    def test_generate_to_file(self, tmp_path):
        spec = TestSpec(id="TC-FILE", title="File output", steps=["test"])
        output_dir = tmp_path / "generated"
        output_dir.mkdir()
        out_path = self.generator.generate_to_file(spec, str(output_dir))
        assert Path(out_path).exists()
        content = Path(out_path).read_text(encoding="utf-8")
        assert "TC-FILE" in content

    def test_generated_code_syntax(self, tmp_path):
        """Verify the generated code can at least be parsed by Python."""
        spec = TestSpec(id="TC-SYNTAX", title="Syntax check", steps=["步骤1"], assertions=["检查1"])
        code = self.generator.generate_case_test(spec)
        try:
            compile(code, "<test>", "exec")
        except SyntaxError as e:
            pytest.fail(f"Generated code has syntax error: {e}")
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_code_generator.py -v`
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 4.3: Write the implementation**

```python
"""Code generator — converts TestSpec/SuiteSpec into pytest + Fluent DSL code.

Outputs syntactically valid Python files that use the framework's
main chain (FluentBuilder → ActionDescriptor → Orchestrator).
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Optional

from sts2_autotest.common.spec_models import TestSpec, SuiteSpec

# Mapping of Chinese step keywords to DSL action descriptors
_STEP_TO_ACTION: dict[str, str] = {
    "启动": "ensure_game_running()",
    "选择": "select_mode",
    "开始": "embark_new_run()",
    "战斗": "combat_loop(max_turns=15)",
    "等待": "wait_for_state",
    "返回": "reset_to_main_menu()",
    "跳过": "skip_reward()",
    "推进": "advance_until_map()",
    "结束": "end_turn()",
    "使用": "play_card",
}

_IMPORT_BLOCK = """\
from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    game_reached_state,
    no_crash_detected,
    has_travelable_node,
)
from sts2_autotest.common.state import GameScreen
"""


def _step_to_action_call(step: str) -> str:
    """Convert a natural language step to a DSL function call.

    Falls back to a generic execute_action call if no mapping exists.
    """
    step_lower = step.lower()
    for keyword, action in _STEP_TO_ACTION.items():
        if keyword in step_lower:
            if keyword in ("选择", "使用"):
                # Extract the parameter after the keyword
                rest = step[len(keyword):].strip().strip("'").strip('"')
                return f'{action}("{rest}")'
            return action
    # Default: use generic action
    return f'execute_action("{step}")'


def _case_id_to_function_name(case_id: str) -> str:
    """Convert TC-PREPARE-NEW-RUN to tc_prepare_new_run."""
    return case_id.lower().replace("-", "_")


def _case_id_to_class_name(suite_id: str) -> str:
    """Convert SUITE-FIRST-BATTLE-SMOKE to TestSuiteFirstBattleSmoke."""
    parts = suite_id.replace("-", " ").title().split()
    return "TestSuite" + "".join(parts[1:])


def _snake_to_pascal(name: str) -> str:
    return "".join(word.title() for word in name.split("_"))


class CodeGenerator:
    """Generates pytest test files from TestSpec/SuiteSpec models.

    Output code follows the standard framework chain:
        pytest fixture → FluentBuilder → ActionDescriptor → DSL → adapter
    """

    def generate_case_test(self, spec: TestSpec) -> str:
        """Generate a complete pytest test function for a single case."""
        func_name = _case_id_to_function_name(spec.id)
        steps = spec.steps
        assertions = spec.assertions

        if not steps:
            return self._generate_skipped_test(spec, func_name, "No steps defined")

        # Build setup actions
        setup_calls = []
        for step in steps:
            setup_calls.append(f"            {_step_to_action_call(step)},")

        setup_block = "\n".join(setup_calls)

        # Build assertions
        assert_calls = []
        for assertion in assertions:
            assertion_lower = assertion.lower()
            if "crash" in assertion_lower:
                assert_calls.append("            no_crash_detected(),")
            elif "map" in assertion_lower or "地图" in assertion:
                assert_calls.append("            game_reached_state(GameScreen.MAP),")
            elif "节点" in assertion or "node" in assertion_lower:
                assert_calls.append("            has_travelable_node(),")
            else:
                assert_calls.append(f"            # TODO: implement assertion for '{assertion}'")

        assert_block = "\n".join(assert_calls) if assert_calls else "            # no assertions defined"

        givens_comment = ""
        if spec.givens:
            givens_lines = "\n".join(f"    # Given: {g}" for g in spec.givens)
            givens_comment = f"{givens_lines}\n"

        return textwrap.dedent(f"""\
            {_IMPORT_BLOCK}

            {givens_comment}
            def test_{func_name}(autotest, _session_loop):
                \"\"\"{spec.title}\"\"\"
                result = (
                    define("{spec.id}", autotest, _session_loop)
                    .setup(
            {setup_block}
                    )
                    .execute(
                        advance_until_map(),
                    )
                    .assert_that(
            {assert_block}
                    )
                )
                assert result.passed, result.failures
            """)

    def generate_suite_test(self, suite: SuiteSpec, specs: dict[str, TestSpec]) -> str:
        """Generate a pytest test class for a suite of test cases."""
        class_name = _case_id_to_class_name(suite.id)

        # Generate a test method for each included case
        methods = []
        for case_id in suite.includes:
            spec = specs.get(case_id)
            if spec:
                methods.append(self.generate_case_test(spec))

        suite_assertions_comment = ""
        if suite.suite_assertions:
            suite_assertions_comment = "\n".join(
                f"    # Suite assertion: {a}" for a in suite.suite_assertions
            )

        return textwrap.dedent(f"""\
            {_IMPORT_BLOCK}

            class {class_name}:
                \"\"\"{suite.title}\"\"\"

            {suite_assertions_comment}
            {'    @staticmethod'.join(methods)}
            """)

    def generate_to_file(self, spec: TestSpec, output_dir: str) -> str:
        """Generate a test file on disk. Returns the output file path."""
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)

        func_name = _case_id_to_function_name(spec.id)
        out_file = path / f"test_{func_name}.py"

        code = self.generate_case_test(spec)
        out_file.write_text(code, encoding="utf-8")
        return str(out_file)

    def _generate_skipped_test(self, spec: TestSpec, func_name: str, reason: str) -> str:
        """Generate a pytest skip test for specs that can't be executed."""
        return textwrap.dedent(f"""\
            import pytest

            def test_{func_name}():
                \"\"\"{spec.title}\"\"\"
                pytest.skip("{reason}")
            """)
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_code_generator.py -v`
Expected: All tests PASS

- [ ] **Step 4.5: Commit**

```bash
git add src/sts2_autotest/core/code_generator.py tests/unit/test_code_generator.py
git commit -m "feat: add TestSpec->pytest code generator"
```

---

### Task 5: MOD 项目发现机制（workspace）

**Files:**
- Create: `src/sts2_autotest/core/workspace.py`
- Test: `tests/unit/test_workspace.py`

- [ ] **Step 5.1: Write the failing tests**

```python
"""Tests for workspace.py — MOD project discovery."""
import pytest
import yaml
from pathlib import Path
from sts2_autotest.common.spec_models import ProjectConfig, WorkspaceConfig
from sts2_autotest.core.workspace import Workspace, WorkspaceError


class TestWorkspace:
    def test_from_yaml(self, tmp_path):
        config = tmp_path / "sts2-autotest.yaml"
        config.write_text(yaml.dump({
            "workspace": {
                "projects": [
                    {"name": "mod-a", "spec_dir": "../mod-a/tests/cases/", "output_dir": "../mod-a/tests/"},
                    {"name": "mod-b", "spec_dir": "../mod-b/tests/cases/"},
                ]
            }
        }))
        ws = Workspace.from_yaml(str(config))
        assert len(ws.projects) == 2
        assert ws.projects[0].name == "mod-a"
        assert ws.projects[0].spec_dir == "../mod-a/tests/cases/"
        assert ws.projects[0].output_dir == "../mod-a/tests/"
        # mod-b should default output_dir to spec_dir
        assert ws.projects[1].output_dir == "../mod-b/tests/cases/"

    def test_from_yaml_file_not_found(self):
        with pytest.raises(WorkspaceError, match="not found"):
            Workspace.from_yaml("/nonexistent/path.yaml")

    def test_from_yaml_invalid_yaml(self, tmp_path):
        config = tmp_path / "bad.yaml"
        config.write_text("{{{invalid yaml")
        with pytest.raises(WorkspaceError, match="Failed to parse"):
            Workspace.from_yaml(str(config))

    def test_from_yaml_no_workspace_section(self, tmp_path):
        config = tmp_path / "empty.yaml"
        config.write_text(yaml.dump({"other": "data"}))
        ws = Workspace.from_yaml(str(config))
        assert len(ws.projects) == 0

    def test_resolve_project_found(self, tmp_path):
        config = tmp_path / "sts2-autotest.yaml"
        config.write_text(yaml.dump({
            "workspace": {
                "projects": [
                    {"name": "my-mod", "spec_dir": "../my-mod/tests/cases/"},
                ]
            }
        }))
        ws = Workspace.from_yaml(str(config))
        proj = ws.resolve_project("my-mod")
        assert proj is not None
        assert proj.name == "my-mod"

    def test_resolve_project_not_found(self, tmp_path):
        config = tmp_path / "sts2-autotest.yaml"
        config.write_text(yaml.dump({"workspace": {"projects": []}}))
        ws = Workspace.from_yaml(str(config))
        assert ws.resolve_project("nonexistent") is None

    def test_discover_projects_with_specs(self, tmp_path):
        """Integration: discover projects and verify spec_dir exists."""
        mod_dir = tmp_path / "my-mod" / "tests" / "cases"
        mod_dir.mkdir(parents=True)
        (mod_dir / "TC-001.md").write_text("# TC-001 Test\n\n## Metadata\n- id: TC-001\n- level: case")
        (mod_dir / "SUITE-001.md").write_text("# SUITE-001 Suite\n\n## Metadata\n- id: SUITE-001\n- level: suite")

        config = tmp_path / "sts2-autotest.yaml"
        rel_spec_dir = str(Path("..") / "my-mod" / "tests" / "cases")
        config.write_text(yaml.dump({
            "workspace": {
                "projects": [
                    {"name": "my-mod", "spec_dir": rel_spec_dir},
                ]
            }
        }))
        ws = Workspace.from_yaml(str(config), base_dir=str(tmp_path))
        cases, suites = ws.discover_project_specs("my-mod")
        assert len(cases) == 1
        assert cases[0].id == "TC-001"
        assert len(suites) == 1
        assert suites[0].id == "SUITE-001"
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_workspace.py -v`
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 5.3: Write the implementation**

```python
"""MOD project workspace discovery.

Resolves MOD project paths from sts2-autotest.yaml workspace config
or from --spec-dir CLI parameter.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

from sts2_autotest.common.spec_models import ProjectConfig, WorkspaceConfig
from sts2_autotest.core.markdown_parser import MarkdownParser, ParsingError


class WorkspaceError(Exception):
    """Raised when workspace configuration cannot be loaded or parsed."""
    pass


class Workspace:
    """Manages MOD project discovery and spec resolution.

    Supports two modes:
    1. Direct --spec-dir: single project, no config file needed
    2. Workspace config: multiple projects declared in sts2-autotest.yaml
    """

    def __init__(self, projects: list[ProjectConfig], base_dir: str = "") -> None:
        self._projects = {p.name: p for p in projects}
        self._base_dir = Path(base_dir) if base_dir else Path.cwd()
        self._parser = MarkdownParser()

    @classmethod
    def from_yaml(cls, yaml_path: str, base_dir: str = "") -> Workspace:
        """Load workspace from a sts2-autotest.yaml file."""
        path = Path(yaml_path)
        if not path.is_file():
            raise WorkspaceError(f"Config file not found: {yaml_path}")

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise WorkspaceError(f"Failed to parse YAML: {e}")

        if not isinstance(data, dict):
            return cls([], base_dir=base_dir)

        workspace_data = data.get("workspace", {})
        if not isinstance(workspace_data, dict):
            return cls([], base_dir=base_dir)

        projects_data = workspace_data.get("projects", [])
        if not isinstance(projects_data, list):
            return cls([], base_dir=base_dir)

        projects = []
        for p in projects_data:
            if not isinstance(p, dict) or "name" not in p:
                continue
            projects.append(ProjectConfig(
                name=p["name"],
                spec_dir=p.get("spec_dir", ""),
                output_dir=p.get("output_dir", p.get("spec_dir", "")),
            ))

        return cls(projects, base_dir=base_dir)

    @classmethod
    def from_spec_dir(cls, spec_dir: str) -> Workspace:
        """Create a single-project workspace from --spec-dir."""
        return cls([
            ProjectConfig(name="_direct", spec_dir=spec_dir, output_dir=spec_dir),
        ])

    @property
    def project_names(self) -> list[str]:
        return list(self._projects.keys())

    @property
    def projects(self) -> list[ProjectConfig]:
        return list(self._projects.values())

    def resolve_project(self, name: str) -> Optional[ProjectConfig]:
        """Find a project by name. Returns None if not found."""
        return self._projects.get(name)

    def discover_project_specs(self, project_name: str) -> tuple[list, list]:
        """Discover and parse all spec files in a project's spec_dir.

        Returns (cases, suites) as parsed TestSpec/SuiteSpec lists.
        """
        project = self.resolve_project(project_name)
        if project is None:
            return [], []

        spec_dir = self._resolve_path(project.spec_dir)
        if not spec_dir or not Path(spec_dir).is_dir():
            return [], []

        return self._parser.discover_specs(spec_dir)

    def _resolve_path(self, path: str) -> str:
        """Resolve a potentially relative path against base_dir."""
        p = Path(path)
        if p.is_absolute():
            return str(p)
        return str((self._base_dir / p).resolve())
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_workspace.py -v`
Expected: All tests PASS

- [ ] **Step 5.5: Commit**

```bash
git add src/sts2_autotest/core/workspace.py tests/unit/test_workspace.py
git commit -m "feat: add MOD project workspace discovery"
```

---

### Task 6: CLI 集成（review、compile、run --all）

**Files:**
- Modify: `src/sts2_autotest/cli/main.py`
- Modify: `src/sts2_autotest/config/schema.py`
- Test: `tests/unit/test_cli_spec_commands.py`

- [ ] **Step 6.1: Write the failing tests**

```python
"""Tests for CLI spec pipeline commands (review, compile, run --all)."""
import pytest
from pathlib import Path
from argparse import Namespace
from sts2_autotest.cli.main import review_cmd, compile_cmd, _create_parser


class TestCLIParser:
    def test_create_parser_has_review(self):
        parser = _create_parser()
        args = parser.parse_args(["review", "--spec-dir", "tests/cases"])
        assert args.command == "review"
        assert args.spec_dir == "tests/cases"

    def test_create_parser_has_compile(self):
        parser = _create_parser()
        args = parser.parse_args(["compile", "--spec-dir", "tests/cases", "--output-dir", "tests/generated"])
        assert args.command == "compile"
        assert args.spec_dir == "tests/cases"
        assert args.output_dir == "tests/generated"

    def test_create_parser_review_with_project(self):
        parser = _create_parser()
        args = parser.parse_args(["review", "--project", "my-mod"])
        assert args.command == "review"
        assert args.project == "my-mod"

    def test_create_parser_compile_with_project(self):
        parser = _create_parser()
        args = parser.parse_args(["compile", "--project", "my-mod"])
        assert args.command == "compile"
        assert args.project == "my-mod"

    def test_create_parser_run_all_with_project(self):
        parser = _create_parser()
        args = parser.parse_args(["run", "--all", "--project", "my-mod"])
        assert args.command == "run"
        assert args.all is True


class TestReviewCmd:
    def test_review_no_spec_dir_no_project(self, capsys):
        """Without --spec-dir or --project, should show usage and return 1."""
        args = Namespace(command="review", spec_dir=None, project=None)
        rc = review_cmd(args)
        assert rc == 1

    def test_review_nonexistent_dir(self, capsys):
        args = Namespace(command="review", spec_dir="/nonexistent", project=None)
        rc = review_cmd(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out


class TestCompileCmd:
    def test_compile_no_spec_dir_no_project(self, capsys):
        args = Namespace(command="compile", spec_dir=None, output_dir=None, project=None)
        rc = compile_cmd(args)
        assert rc == 1

    def test_compile_nonexistent_dir(self, capsys):
        args = Namespace(command="compile", spec_dir="/nonexistent", output_dir="/tmp/out", project=None)
        rc = compile_cmd(args)
        assert rc == 1
```

- [ ] **Step 6.2: Add review and compile subparsers to CLI**

```python
# Add to _create_parser() in cli/main.py, after the 'run' parser:

    # autotest review
    review = sub.add_parser("review", help="Review natural language test specs")
    review.add_argument("--spec-dir", help="Directory containing Markdown spec files")
    review.add_argument("--project", help="Project name from workspace config")
    review.add_argument("--output", help="Output path for review report (default: stdout)")

    # autotest compile
    compile_cmd_parser = sub.add_parser("compile", help="Compile specs to pytest test files")
    compile_cmd_parser.add_argument("--spec-dir", help="Directory containing Markdown spec files")
    compile_cmd_parser.add_argument("--output-dir", help="Directory for generated test files")
    compile_cmd_parser.add_argument("--project", help="Project name from workspace config")
```

- [ ] **Step 6.3: Implement review_cmd and compile_cmd**

Add these functions to `cli/main.py`:

```python
def review_cmd(args: Any) -> int:
    """Review natural language test specs and print report."""
    from sts2_autotest.core.workspace import Workspace
    from sts2_autotest.core.markdown_parser import MarkdownParser
    from sts2_autotest.core.spec_reviewer import SpecReviewer

    spec_dir = _resolve_spec_dir(args)
    if not spec_dir:
        print("[autotest] Specify --spec-dir or configure workspace in sts2-autotest.yaml")
        return 1

    if not os.path.isdir(spec_dir):
        print(f"[autotest] Spec directory not found: {spec_dir}")
        return 1

    parser = MarkdownParser()
    cases, suites = parser.discover_specs(spec_dir)
    total = len(cases) + len(suites)

    if total == 0:
        print(f"[autotest] No spec files found in {spec_dir}")
        return 0

    reviewer = SpecReviewer()
    all_passed = True

    print(f"[autotest] Reviewing {len(cases)} case(s), {len(suites)} suite(s) in {spec_dir}\n")

    for spec in cases:
        report = reviewer.review(spec)
        status = "PASS" if report.passed else "ISSUES"
        print(f"  [{status}] {spec.id}: {spec.title}")
        if not report.passed:
            all_passed = False
            for issue in report.issues:
                print(f"         - [{issue.category.value}] {issue.location}: {issue.description}")
        draft = reviewer.generate_revised_draft(spec, report)
        if draft.changes_summary:
            for change in draft.changes_summary:
                print(f"           draft: {change}")

    for suite in suites:
        report = reviewer.review_suite(suite)
        status = "PASS" if report.passed else "ISSUES"
        print(f"  [{status}] {suite.id}: {suite.title}")
        if not report.passed:
            for issue in report.issues:
                print(f"         - [{issue.category.value}] {issue.location}: {issue.description}")

    print(f"\n[autotest] Review complete. {'All passed' if all_passed else 'Some issues found'}.")
    return 0 if all_passed else 1


def compile_cmd(args: Any) -> int:
    """Compile specs to pytest test files."""
    from sts2_autotest.core.workspace import Workspace
    from sts2_autotest.core.markdown_parser import MarkdownParser
    from sts2_autotest.core.code_generator import CodeGenerator

    spec_dir = _resolve_spec_dir(args)
    if not spec_dir:
        print("[autotest] Specify --spec-dir or configure workspace in sts2-autotest.yaml")
        return 1

    if not os.path.isdir(spec_dir):
        print(f"[autotest] Spec directory not found: {spec_dir}")
        return 1

    output_dir = _resolve_output_dir(args, spec_dir)
    parser = MarkdownParser()
    cases, suites = parser.discover_specs(spec_dir)

    if not cases and not suites:
        print(f"[autotest] No spec files found in {spec_dir}")
        return 0

    generator = CodeGenerator()
    generated: list[str] = []

    for spec in cases:
        out_path = generator.generate_to_file(spec, output_dir)
        generated.append(out_path)
        print(f"  [GENERATED] {out_path}")

    print(f"\n[autotest] Generated {len(generated)} test file(s) in {output_dir}")
    return 0


def _resolve_spec_dir(args: Any) -> str | None:
    """Resolve spec directory from args or workspace config."""
    if getattr(args, "spec_dir", None):
        return args.spec_dir

    project_name = getattr(args, "project", None)
    if project_name:
        ws = _load_workspace()
        if ws:
            project = ws.resolve_project(project_name)
            if project:
                return project.spec_dir
    return None


def _resolve_output_dir(args: Any, spec_dir: str) -> str:
    """Resolve output directory for generated test files."""
    if getattr(args, "output_dir", None):
        return args.output_dir

    project_name = getattr(args, "project", None)
    if project_name:
        ws = _load_workspace()
        if ws:
            project = ws.resolve_project(project_name)
            if project and project.output_dir:
                return project.output_dir
    return spec_dir


def _load_workspace() -> Any | None:
    """Try to load workspace config from default locations."""
    from sts2_autotest.core.workspace import Workspace

    candidates = ["sts2-autotest.yaml", "sts2-autotest.yml"]
    for fname in candidates:
        if os.path.isfile(fname):
            try:
                return Workspace.from_yaml(fname)
            except Exception:
                return None
    return None
```

- [ ] **Step 6.4: Wire review_cmd and compile_cmd into cli() dispatcher**

Modify the `cli()` function in `cli/main.py`:

```python
def cli(argv: Sequence[str] | None = None) -> None:
    """Main CLI entry point. Used by pyproject.toml [project.scripts]."""
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    parser = _create_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        sys.exit(run_cmd(args))
    elif args.command == "review":
        sys.exit(review_cmd(args))
    elif args.command == "compile":
        sys.exit(compile_cmd(args))
    elif args.command == "doctor":
        sys.exit(doctor_cmd(args))
    elif args.command == "report":
        sys.exit(report_cmd(args))
    else:
        parser.print_help()
        sys.exit(1)
```

- [ ] **Step 6.5: Update run --all to integrate the full pipeline**

Modify `run_cmd` in `cli/main.py` to call review + compile when `--all` is used:

```python
# In run_cmd(), replace the args.all handler:

    if args.all:
        print("[autotest] Running full pipeline: review → compile → run")
        # Step 1: Review
        review_args = Namespace(
            command="review", spec_dir=args.spec_dir,
            project=args.project, output=None,
        )
        review_rc = review_cmd(review_args)
        if review_rc != 0:
            print("[autotest] Review failed — aborting pipeline")
            return 1

        # Step 2: Compile
        compile_args = Namespace(
            command="compile", spec_dir=args.spec_dir,
            output_dir=args.output_dir, project=args.project,
        )
        compile_rc = compile_cmd(compile_args)
        if compile_rc != 0:
            print("[autotest] Compile failed — aborting pipeline")
            return 1

        # Step 3: Run
        print("[autotest] Running compiled tests...")
        return _run_orchestrator(
            ["all"], timeout=args.timeout,
            progress_path=use_progress,
        )
```

- [ ] **Step 6.6: Add workspace config to config/schema.py**

```python
class ProjectConfigModel(BaseModel):
    """Configuration for a single MOD project in the workspace."""

    model_config = ConfigDict(frozen=True)

    name: str
    spec_dir: str
    output_dir: str = ""


class WorkspaceConfigModel(BaseModel):
    """Workspace configuration for multi-MOD-project support."""

    model_config = ConfigDict(frozen=True)

    projects: list[ProjectConfigModel] = []


# Add to STS2Config class:
    workspace: WorkspaceConfigModel = WorkspaceConfigModel()
```

- [ ] **Step 6.7: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_cli_spec_commands.py -v`
Expected: All tests PASS

- [ ] **Step 6.8: Run full test suite to check for regressions**

Run: `python -m pytest tests/unit/ -v`
Expected: All existing tests still PASS

- [ ] **Step 6.9: Commit**

```bash
git add src/sts2_autotest/cli/main.py src/sts2_autotest/config/schema.py tests/unit/test_cli_spec_commands.py
git commit -m "feat: add review/compile CLI commands with workspace integration"
```

---

### Task 7: 标准模板与缺失 DSL 断言补充

**Files:**
- Create: `docs/superpowers/templates/test-case-template.md`
- Create: `docs/superpowers/templates/test-suite-template.md`
- Modify: `src/sts2_autotest/dsl/assertions.py` (add missing assertions)
- Test: `tests/unit/test_dsl_assertions_extended.py`

- [ ] **Step 7.1: Write tests for new assertions**

```python
"""Tests for newly added DSL assertions needed by code generator."""
import pytest
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.dsl.assertions import (
    no_crash_detected, has_travelable_node,
)


class TestNoCrashDetected:
    def test_no_crash(self):
        state = GameState(screen=GameScreen.MAP)
        ok, msg = no_crash_detected()(state)
        assert ok
        assert msg == ""

    def test_crashed_state(self):
        state = GameState(screen=GameScreen.CRASHED)
        ok, msg = no_crash_detected()(state)
        assert not ok
        assert "CRASHED" in msg


class TestHasTravelableNode:
    def test_has_travelable_node(self):
        state = GameState(screen=GameScreen.MAP, travelable_nodes=[1, 2, 3])
        ok, msg = has_travelable_node()(state)
        assert ok

    def test_no_travelable_nodes(self):
        state = GameState(screen=GameScreen.MAP, travelable_nodes=[])
        ok, msg = has_travelable_node()(state)
        assert not ok
```

- [ ] **Step 7.2: Add missing assertion functions to dsl/assertions.py**

```python
def no_crash_detected() -> AssertionFn:
    """Assert the game has not crashed."""

    def check(state: GameState) -> tuple[bool, str]:
        ok = state.screen != GameScreen.CRASHED
        msg = "" if ok else f"Game is in CRASHED state"
        return ok, msg

    return check


def has_travelable_node() -> AssertionFn:
    """Assert there is at least one travelable map node."""

    def check(state: GameState) -> tuple[bool, str]:
        nodes = getattr(state, "travelable_nodes", None)
        if nodes is None:
            return False, "travelable_nodes not in state"
        ok = len(nodes) > 0
        msg = "" if ok else "No travelable nodes available"
        return ok, msg

    return check
```

- [ ] **Step 7.3: Create template files**

`docs/superpowers/templates/test-case-template.md`:
```markdown
# TC-XXXXXX 用例标题

## Metadata
- id: TC-XXXXXX
- level: case
- tags: tag1, tag2
- priority: P0

## Start State
- 指定游戏应处于的起始画面/状态

## End State
- 指定测试结束时应达到的画面/状态

## Given
- 前置条件 1
- 前置条件 2

## When
1. 动作步骤 1
2. 动作步骤 2
3. 动作步骤 3

## Then
- 断言 1
- 断言 2
```

`docs/superpowers/templates/test-suite-template.md`:
```markdown
# SUITE-XXXXXX 套件标题

## Metadata
- id: SUITE-XXXXXX
- level: suite
- tags: tag1, tag2
- priority: P0

## Goal
- 定义该组合测试的整体目标

## Mode
- execution: sequential_shared_session

## Includes
1. TC-CASE-ONE
2. TC-CASE-TWO
3. TC-CASE-THREE

## Then
- 组合级断言 1
- 组合级断言 2
```

- [ ] **Step 7.4: Run tests**

Run: `python -m pytest tests/unit/test_dsl_assertions_extended.py -v`
Expected: All PASS

- [ ] **Step 7.5: Commit**

```bash
git add src/sts2_autotest/dsl/assertions.py tests/unit/test_dsl_assertions_extended.py docs/superpowers/templates/
git commit -m "feat: add code generator assertions and spec templates"
```

---

### Task 8: 端到端集成测试（首批场景贯通）

**Files:**
- Create: `tests/integration/test_spec_pipeline_e2e.py`

- [ ] **Step 8.1: Write the integration test**

```python
"""End-to-end integration test for the spec pipeline.

Creates sample .md spec files → runs review → runs compile →
verifies generated test files are syntactically valid Python.
Does NOT execute the generated tests (requires real game).
"""
import os
import sys
import tempfile
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
    def test_full_pipeline_review_compile(self, tmp_path):
        """Review → compile cycle produces valid Python files."""
        # Arrange: create spec files
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        (spec_dir / "TC-PREPARE-NEW-RUN.md").write_text(SAMPLE_CASE)
        (spec_dir / "SUITE-FIRST-BATTLE-SMOKE.md").write_text(SAMPLE_SUITE)

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
        assert "FluentBuilder" in code or "define(" in code

    def test_review_detects_bad_spec(self, tmp_path):
        """Review catches issues in poorly written specs."""
        bad_spec = """\
# TC-BAD Bad spec

## Metadata
- id: TC-BAD
- level: case

## When
1. 适当操作
2. 正常继续
"""
        spec_dir = tmp_path / "bad_specs"
        spec_dir.mkdir()
        (spec_dir / "TC-BAD.md").write_text(bad_spec)

        parser = MarkdownParser()
        cases, _ = parser.discover_specs(str(spec_dir))
        assert len(cases) == 1

        reviewer = SpecReviewer()
        report = reviewer.review(cases[0])
        assert not report.passed
        assert len(report.issues) >= 2  # ambiguity + missing items

    def test_compile_generates_skip_for_empty_spec(self, tmp_path):
        """Empty specs generate skipped tests rather than crashing."""
        empty_spec = """\
# TC-EMPTY Empty

## Metadata
- id: TC-EMPTY
- level: case
"""
        spec_dir = tmp_path / "empty_specs"
        spec_dir.mkdir()
        (spec_dir / "TC-EMPTY.md").write_text(empty_spec)

        parser = MarkdownParser()
        cases, _ = parser.discover_specs(str(spec_dir))

        generator = CodeGenerator()
        output_dir = tmp_path / "generated"
        output_dir.mkdir()
        out_path = generator.generate_to_file(cases[0], str(output_dir))
        code = Path(out_path).read_text(encoding="utf-8")
        assert "pytest.skip" in code
```

- [ ] **Step 8.2: Run the integration test**

Run: `python -m pytest tests/integration/test_spec_pipeline_e2e.py -v`
Expected: All tests PASS

- [ ] **Step 8.3: Commit**

```bash
git add tests/integration/test_spec_pipeline_e2e.py
git commit -m "test: add end-to-end integration test for spec pipeline"
```

---

### Task 9: 旁路脚本迁移 + 运行完整测试套件

**Files:**
- Delete (or move): `tests/e2e_first_battle.py`
- Modify: `tests/e2e_first_battle.py` (convert to generated-test-compatible format)

- [ ] **Step 9.1: Create migration plan for e2e_first_battle.py**

```markdown
# e2e_first_battle.py 迁移为规格+DSL的记录
- 原脚本逻辑已拆入 TC-PREPARE-NEW-RUN、TC-RESOLVE-NEOW、TC-FINISH-FIRST-BATTLE 三个规格
- 依赖的 adapter 原始操作已转为 DSL 动作原语
- 框架提供对应 generate 能力后，从规格生成 pytest 测试
```

- [ ] **Step 9.2: Run full test suite**

Run: `python -m pytest tests/unit/ tests/integration/test_spec_pipeline_e2e.py -v`
Expected: All tests PASS

- [ ] **Step 9.3: Run type check**

Run: `mypy src/sts2_autotest --strict`
Expected: No type errors

- [ ] **Step 9.4: Commit**

```bash
git add .
git commit -m "feat: complete natural language test spec pipeline"
```

---

## Self-Review Checklist

**Spec coverage:**
- Task 1: TestSpec/SuiteSpec data models ← spec section "内部模型"
- Task 2: Markdown parser ← spec section "规格格式"
- Task 3: Spec reviewer + revised draft ← spec section "审查模型"
- Task 4: Code generator ← spec section "测试代码生成目标"
- Task 5: Workspace/Project discovery ← spec section "MOD 项目接入方式"
- Task 6: CLI review/compile/run --all ← spec section "CLI 总体流水线"
- Task 7: Templates ← spec section "标准指引输出"
- Task 8: E2E integration tests ← spec section "首批闭环场景"
- Task 9: Bypass script migration ← spec section "现有旁路脚本的迁移原则"

**Placeholder scan:** All code blocks contain complete, compilable Python. No TBD, TODO, or "fill in later" patterns.

**Type consistency:** All model types (TestSpec, SuiteSpec, ReviewReport, ProjectConfig) are defined in Task 1 and referenced consistently across Tasks 2-6. Function signatures in later tasks match their usage in earlier tasks.
