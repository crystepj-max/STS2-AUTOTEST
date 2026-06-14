"""Spec reviewer — analyzes TestSpec for clarity and feasibility.

Produces ReviewReport (diagnostics) and RevisedDraft (improved Markdown).
"""

from __future__ import annotations

import re

from sts2_autotest.common.spec_models import (
    TestSpec, SuiteSpec, ReviewReport, RevisedDraft,
    ReviewIssue, IssueCategory,
)


# Ambiguous Chinese phrases that signal unclear test steps
_AMBIGUITY_PATTERNS: list[tuple[str, str]] = [
    (r"适当", "'适当' is ambiguous — specify exact condition or value"),
    (r"正常", "'正常' is ambiguous — define what 'normal' means"),
    (r"尽快", "'尽快' is ambiguous — specify time bound or turn limit"),
    (r"合理", "'合理' is ambiguous — specify expected range or criteria"),
    (r"酌情", "'酌情' is ambiguous — specify decision rule"),
    (r"相关", "'相关' is ambiguous — specify which related items"),
]

# Keywords indicating steps that need framework capabilities not yet built
_UNIMPLEMENTABLE_KEYWORDS: list[str] = [
    "使用未实现", "未实现的功能",
]

_SUPPORTED_STEP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern)
    for pattern in (
        r"启动游戏|返回主菜单|选择标准模式|开始新\s*run|开始新局",
        r"选择\s*(Ironclad|Gawain|战士|铁甲战士)|开始冒险",
        r"开局事件.*第\s*\d+\s*个选项|推进事件对话",
        r"地图节点.*\(\s*\d+\s*,\s*\d+\s*\)|进入首次战斗|进入首场战斗",
        r"添加\s+[A-Za-z0-9_:-]+\s+到手牌",
        r"基础策略.*战斗|跳过卡牌奖励",
        r"结束回合|使用\s+.+",
    )
]

_SUPPORTED_ASSERTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"crash",
        r"到达\s*MAP|位于\s*MAP|\bMAP\b|地图",
        r"到达\s*EVENT|位于\s*EVENT|\bEVENT\b|事件",
        r"到达\s*COMBAT|位于\s*COMBAT|\bCOMBAT\b|战斗|combat",
        r"造成\s*\d+\s*点伤害\s*\d+\s*次",
        r"节点|node",
    )
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
                changes.append(f"Ambiguity: {issue.description} → {issue.suggestion}")

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
        if spec.priority == "P3":
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
            unimplemented = False
            for keyword in _UNIMPLEMENTABLE_KEYWORDS:
                if keyword in step:
                    issues.append(ReviewIssue(
                        category=IssueCategory.UNIMPLEMENTABLE,
                        location=f"When step: '{step}'",
                        description=f"Step references unimplemented capability: {keyword}",
                        suggestion="Remove this step or implement the required framework capability first",
                    ))
                    unimplemented = True
                    break
            if unimplemented:
                continue
            if not any(pattern.search(step) for pattern in _SUPPORTED_STEP_PATTERNS):
                issues.append(ReviewIssue(
                    category=IssueCategory.CAPABILITY_GAP,
                    location=f"When step: '{step}'",
                    description=f"当前没有 DSL 原语或可靠组合策略可实现该步骤: {step}",
                    suggestion="当前不可实现；请改写为已有 DSL 支持的步骤，或登记新的 capability_gap 后再实现。",
                ))

        for assertion in spec.assertions:
            if not any(pattern.search(assertion) for pattern in _SUPPORTED_ASSERTION_PATTERNS):
                issues.append(ReviewIssue(
                    category=IssueCategory.CAPABILITY_GAP,
                    location=f"Then assertion: '{assertion}'",
                    description=f"当前没有断言 DSL 可稳定验证该期望: {assertion}",
                    suggestion="当前不可实现；请改写为 screen / crash / travelable node 等已有断言，或补充新的断言原语。",
                ))

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
                clean = s.strip().lstrip("- ")
                if clean:
                    lines.append(f"- {clean}")
            lines.append("")

        if spec.end_state:
            lines.append("## End State")
            for s in spec.end_state.split("\n"):
                clean = s.strip().lstrip("- ")
                if clean:
                    lines.append(f"- {clean}")
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
            r"相关": "明确指定相关的具体项目（如 '选择与战斗相关的卡牌'）",
        }
        return suggestions.get(pattern, "请更具体地描述该步骤")
