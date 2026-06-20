"""Tests for spec_reviewer.py — ReviewReport + RevisedDraft generation."""
from sts2_autotest.common.spec_models import (
    TestSpec, SuiteSpec, RevisedDraft,
    IssueCategory,
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

    def test_review_reports_capability_gap_for_unknown_dsl_step(self) -> None:
        spec = TestSpec(
            id="TC-CAP-GAP",
            title="Capability gap",
            priority="P0",
            start_state="SHOP",
            end_state="SHOP",
            steps=["在商店购买遗物"],
            assertions=["不 crash"],
        )
        report = self.reviewer.review(spec)
        gaps = [i for i in report.issues if i.category == IssueCategory.CAPABILITY_GAP]
        assert any("没有 DSL 原语" in i.description for i in gaps)
        assert any("当前不可实现" in i.suggestion for i in gaps)

    def test_review_accepts_first_battle_supported_steps(self) -> None:
        spec = TestSpec(
            id="TC-FIRST-BATTLE",
            title="First battle supported",
            priority="P0",
            start_state="MAIN_MENU",
            end_state="MAP",
            steps=[
                "返回主菜单",
                "选择标准模式",
                "开始新 run",
                "选择 Ironclad",
                "开始冒险",
                "选择开局事件的第 0 个选项",
                "推进事件对话",
                "选择地图节点 (2, 1)",
                "进入首次战斗",
                "按基础策略完成战斗",
                "跳过卡牌奖励",
            ],
            assertions=["不 crash", "到达 MAP"],
        )
        report = self.reviewer.review(spec)
        assert not [i for i in report.issues if i.category == IssueCategory.CAPABILITY_GAP]

    def test_review_accepts_gawain_character_selection(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-PREPARE",
            title="Prepare Gawain",
            priority="P0",
            start_state="MAIN_MENU",
            end_state="EVENT",
            steps=["开始新局", "选择 Gawain", "开始冒险"],
            assertions=["不 crash"],
        )

        report = self.reviewer.review(spec)

        assert not [i for i in report.issues if i.category == IssueCategory.CAPABILITY_GAP]

    def test_review_accepts_chinese_ironclad_selection_without_space(self) -> None:
        spec = TestSpec(
            id="TC-IRONCLAD-PREPARE",
            title="Prepare Ironclad",
            priority="P0",
            start_state="MAIN_MENU",
            end_state="EVENT",
            steps=["开始新局", "选择战士", "开始冒险"],
            assertions=["不 crash"],
        )

        report = self.reviewer.review(spec)

        assert not [i for i in report.issues if i.category == IssueCategory.CAPABILITY_GAP]

    def test_review_accepts_event_and_combat_state_assertions(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-SCREENS",
            title="Gawain screens",
            priority="P0",
            start_state="MAIN_MENU",
            end_state="COMBAT",
            steps=["开始新局"],
            assertions=["game reached event", "game reached combat", "不 crash"],
        )

        report = self.reviewer.review(spec)

        assert not [i for i in report.issues if i.category == IssueCategory.CAPABILITY_GAP]

    def test_review_accepts_give_card_and_exact_hit_assertion(self) -> None:
        spec = TestSpec(
            id="TC-IRONCLAD-TWIN-STRIKE",
            title="Ironclad Twin Strike",
            priority="P0",
            start_state="COMBAT",
            end_state="COMBAT",
            steps=["添加 TWIN_STRIKE 到手牌", "使用 TWIN_STRIKE"],
            assertions=["造成 5 点伤害 2 次", "不 crash"],
        )

        report = self.reviewer.review(spec)

        assert not [i for i in report.issues if i.category == IssueCategory.CAPABILITY_GAP]

    def test_review_accepts_effect_and_rest_assertions(self) -> None:
        spec = TestSpec(
            id="TC-GAWAIN-EFFECT-REST",
            title="Gawain effect and rest",
            priority="P0",
            start_state="COMBAT",
            end_state="REST",
            steps=["使用 gawain:defend", "选择地图节点 (1, 0)"],
            assertions=[
                "敌人受到 6 点伤害",
                "玩家格挡增加 5",
                "玩家能量减少 1",
                "玩家回复 1 点生命",
                "game reached state REST",
                "不 crash",
            ],
        )

        report = self.reviewer.review(spec)

        assert not [i for i in report.issues if i.category == IssueCategory.CAPABILITY_GAP]

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
        # The draft should list ambiguity issues with suggestions
        assert any("Ambiguity" in c for c in draft.changes_summary)
        assert "适当" in draft.markdown_content  # original preserved, changes in summary

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
        suite = SuiteSpec(id="SUITE-NO-INC", title="No includes")
        report = self.reviewer.review_suite(suite)
        assert not report.passed
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("includes" in i.location.lower() for i in missing)

    def test_review_suite_missing_goal(self) -> None:
        suite = SuiteSpec(id="SUITE-NO-GOAL", title="No goal", includes=["TC-001"])
        report = self.reviewer.review_suite(suite)
        assert not report.passed
        missing = [i for i in report.issues if i.category == IssueCategory.MISSING]
        assert any("goal" in i.location.lower() for i in missing)
