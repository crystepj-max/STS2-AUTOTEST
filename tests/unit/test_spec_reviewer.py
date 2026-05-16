"""Tests for spec_reviewer.py — ReviewReport + RevisedDraft generation."""
import pytest
from sts2_autotest.common.spec_models import (
    TestSpec, SuiteSpec, ReviewReport, RevisedDraft,
    ReviewIssue, IssueCategory,
)
from sts2_autotest.core.spec_reviewer import SpecReviewer


class TestSpecReviewer:
    def setup_method(self) -> None:
        self.reviewer = SpecReviewer()

    def test_review_clean_spec(self) -> None:
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

    def test_review_detects_ambiguity(self) -> None:
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

    def test_review_detects_missing_priority(self) -> None:
        spec = TestSpec(id="TC-NO-PRIO", title="No priority")
        report = self.reviewer.review(spec)
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("priority" in i.description.lower() for i in missing)

    def test_review_detects_missing_start_state(self) -> None:
        spec = TestSpec(id="TC-NO-START", title="No start state")
        report = self.reviewer.review(spec)
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("start" in i.description.lower() for i in missing)

    def test_review_detects_missing_end_state(self) -> None:
        spec = TestSpec(id="TC-NO-END", title="No end state")
        report = self.reviewer.review(spec)
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("end" in i.description.lower() for i in missing)

    def test_review_detects_missing_assertions(self) -> None:
        spec = TestSpec(id="TC-NO-ASSERT", title="No assertions")
        report = self.reviewer.review(spec)
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("assertion" in i.description.lower() for i in missing)

    def test_review_detects_unimplementable_steps(self) -> None:
        spec = TestSpec(
            id="TC-UNIMPL",
            title="Unimplementable",
            steps=["使用未实现的功能", "正常操作"],
        )
        report = self.reviewer.review(spec)
        unimplementable = [i for i in report.issues if i.category == IssueCategory.UNIMPLEMENTABLE]
        assert any("使用未实现" in i.description for i in unimplementable)

    def test_review_multiple_issues(self) -> None:
        spec = TestSpec(
            id="TC-MULTI",
            title="Multiple issues",
            steps=["适当选择"],
        )
        report = self.reviewer.review(spec)
        assert not report.passed
        assert len(report.issues) >= 2  # ambiguity + missing items

    def test_generate_revised_draft_fixes_ambiguity(self) -> None:
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

    def test_generate_revised_draft_clean_spec(self) -> None:
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

    def test_review_suite_missing_includes(self) -> None:
        from sts2_autotest.common.spec_models import SuiteSpec
        suite = SuiteSpec(id="SUITE-NO-INC", title="No includes")
        report = self.reviewer.review_suite(suite)
        assert not report.passed
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("includes" in i.location.lower() for i in missing)

    def test_review_suite_missing_goal(self) -> None:
        from sts2_autotest.common.spec_models import SuiteSpec
        suite = SuiteSpec(id="SUITE-NO-GOAL", title="No goal", includes=["TC-001"])
        report = self.reviewer.review_suite(suite)
        assert not report.passed
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("goal" in i.location.lower() for i in missing)
