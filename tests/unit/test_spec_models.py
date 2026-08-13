"""Tests for spec_models.py — TestSpec, SuiteSpec, ReviewReport, ProjectConfig."""
from __future__ import annotations

from sts2_autotest.common.spec_models import (
    IssueCategory,
    ProjectConfig,
    ReviewIssue,
    ReviewReport,
    SuiteSpec,
    TestSpec,
    WorkspaceConfig,
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
