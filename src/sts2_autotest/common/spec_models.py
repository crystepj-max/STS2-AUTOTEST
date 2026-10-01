"""Data models for the natural language test spec pipeline.

TestSpec and SuiteSpec are the internal representation of parsed
Markdown test specifications. ReviewReport and RevisedDraft are
outputs of the review phase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
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
    """Configuration for a single MOD project within the workspace.

    Extended (B20): added mod_id, manifest, source_dirs, design_docs,
    suite_dirs, default_suite, autotest_config for workspace manifest
    and autotest discovery.
    """
    name: str
    spec_dir: str
    mod_id: str = ""
    manifest: str = ""
    output_dir: str = ""
    source_dirs: list[str] = field(default_factory=list)
    design_docs: list[str] = field(default_factory=list)
    suite_dirs: list[str] = field(default_factory=list)
    default_suite: str = ""
    autotest_config: str = ""

    def __post_init__(self) -> None:
        if not self.mod_id:
            self.mod_id = self.name
        if not self.output_dir:
            self.output_dir = self.spec_dir


@dataclass
class WorkspaceConfig:
    """Workspace configuration loaded from sts2-autotest.yaml."""
    projects: list[ProjectConfig] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 规格启动状态（Given/Start State）的结构化解析
#
# 历史上这段正则解析住在 dsl/fluent.py 且在**运行时**对规格文本执行——
# 生成测试的 Given 文本必须包含特定中文子串才能通过校验（魔法子串）。
# 现在解析在**生成期**完成（code_generator 调用本函数产出结构化要求），
# fluent 消费结构化结果；文本解析保留作为手工测试的回退路径。
# ---------------------------------------------------------------------------

# 与 dsl/fluent 历史正则逐条对应（ 词边界保留）。
_START_STATE_SCREEN_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("MAIN_MENU", (r"MAIN_MENU", r"主菜单")),
    ("CHARACTER_SELECT", (r"CHARACTER_SELECT", r"角色选择")),
    ("MAP", (r"\bMAP\b", r"地图")),
    ("COMBAT", (r"\bCOMBAT\b", r"战斗")),
    ("EVENT", (r"\bEVENT\b", r"事件")),
    ("CARD_REWARD", (r"CARD_REWARD", r"卡牌奖励", r"奖励界面")),
    ("RELIC_REWARD", (r"RELIC_REWARD", r"遗物奖励")),
    ("GAME_OVER", (r"GAME_OVER",)),
    ("VICTORY", (r"VICTORY",)),
    ("UNKNOWN", (r"UNKNOWN",)),
]

# 豁免标记（历史魔法子串的唯一权威登记处）
_EXEMPT_NEOW_RESOLVED_MARKERS = ("开局事件", "已进入新 run")
_EXEMPT_FIRST_BATTLE_FINISHED_MARKERS = ("地图界面", "普通战斗节点")
_EXEMPT_RECOVERABLE_REWARD_MARKERS = ("任意可恢复状态",)


def parse_start_state_requirements(text: str) -> dict[str, Any]:
    """把规格启动状态文本解析为结构化要求（生成期单源实现）。

    返回 dict（screen/allowed_screens 为屏幕名字符串，便于跨层序列化）。
    """
    matched: list[str] = []
    for screen_name, patterns in _START_STATE_SCREEN_PATTERNS:
        if any(re.search(pattern, text) for pattern in patterns):
            matched.append(screen_name)
    uses_screen_list = "/" in text or len(matched) > 1
    screen = None if uses_screen_list else (matched[0] if matched else None)
    allowed_screens = matched if uses_screen_list else []
    joined = text.lower()
    return {
        "screen": screen,
        "allowed_screens": allowed_screens,
        "needs_travelable_node": bool(
            "节点" in text
            and ("可达" in text or "到达" in text or "travelable" in joined)
        ),
        "exempt_neow_resolved": all(m in text for m in _EXEMPT_NEOW_RESOLVED_MARKERS),
        "exempt_first_battle_finished": all(
            m in text for m in _EXEMPT_FIRST_BATTLE_FINISHED_MARKERS
        ),
        "exempt_recoverable_reward": any(
            m in text for m in _EXEMPT_RECOVERABLE_REWARD_MARKERS
        ),
    }
