"""Data models for the natural language test spec pipeline.

TestSpec and SuiteSpec are the internal representation of parsed
Markdown test specifications. ReviewReport and RevisedDraft are
outputs of the review phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


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
    __test__ = False  # pytest: not a test class despite "Test" prefix

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
    __test__ = False  # pytest: not a test class despite "Suite" prefix

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

    def __post_init__(self) -> None:
        if not self.output_dir:
            self.output_dir = self.spec_dir


@dataclass
class WorkspaceConfig:
    """Workspace configuration loaded from sts2-autotest.yaml."""
    projects: list[ProjectConfig] = field(default_factory=list)
