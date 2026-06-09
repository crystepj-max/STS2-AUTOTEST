"""Tests for common/evidence.py — EvidencePack, SummaryJson, schema_version."""

import pytest
from pydantic import ValidationError

from sts2_autotest.common.evidence import (
    SCHEMA_VERSION,
    ArtifactsInfo,
    EnvironmentInfo,
    EvidencePack,
    FailureInfo,
    RunInfo,
    SummaryJson,
)


class TestSchemaVersion:
    """SCHEMA_VERSION constant tests."""

    def test_is_semver_string(self) -> None:
        parts = SCHEMA_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_starts_at_1_0_0(self) -> None:
        assert SCHEMA_VERSION == "1.0.0"


class TestRunInfo:
    """RunInfo model tests."""

    def test_create_with_required_fields(self) -> None:
        info = RunInfo(
            run_id="run-001",
            result="PASS",
            duration_ms=5000,
            autotest_version="0.1.0",
        )
        assert info.run_id == "run-001"
        assert info.result == "PASS"
        assert info.duration_ms == 5000

    def test_create_without_autotest_version_for_backward_compatibility(self) -> None:
        info = RunInfo(run_id="run-001", result="PASS", duration_ms=5000)
        assert info.autotest_version is None

    def test_frozen(self) -> None:
        info = RunInfo(
            run_id="run-001",
            result="PASS",
            duration_ms=5000,
            autotest_version="0.1.0",
        )
        with pytest.raises(ValidationError):
            info.result = "FAIL"  # type: ignore[misc]


class TestEnvironmentInfo:
    """EnvironmentInfo model tests."""

    def test_create_with_all_fields(self) -> None:
        info = EnvironmentInfo(
            framework="0.1.0",
            adapter="CliMod",
            game="Slay the Spire 2",
            os="Windows 11",
            python="3.11",
        )
        assert info.framework == "0.1.0"

    def test_frozen(self) -> None:
        info = EnvironmentInfo(
            framework="0.1.0", adapter="CliMod", game="STS2", os="Win", python="3.11"
        )
        with pytest.raises(ValidationError):
            info.framework = "0.2.0"  # type: ignore[misc]


class TestArtifactsInfo:
    """ArtifactsInfo model tests."""

    def test_defaults_to_empty_lists(self) -> None:
        info = ArtifactsInfo()
        assert info.screenshots == []
        assert info.logs == []

    def test_with_paths(self) -> None:
        info = ArtifactsInfo(
            screenshots=["screenshots/test1.png"],
            logs=["logs/game.log"],
        )
        assert len(info.screenshots) == 1


class TestFailureInfo:
    """FailureInfo model tests."""

    def test_create_with_required_fields(self) -> None:
        info = FailureInfo(type="assertion_error", message="HP mismatch")
        assert info.type == "assertion_error"
        assert info.stack_trace is None

    def test_with_stack_trace(self) -> None:
        info = FailureInfo(
            type="crash_error",
            message="Game crashed",
            stack_trace="Traceback...",
        )
        assert info.stack_trace == "Traceback..."

    def test_with_exit_code(self) -> None:
        info = FailureInfo(
            type="crash_error",
            message="Game crashed",
            exit_code=0xC0000005,
        )
        assert info.exit_code == 0xC0000005

    def test_exit_code_defaults_to_none(self) -> None:
        info = FailureInfo(type="crash_error", message="Game crashed")
        assert info.exit_code is None


class TestSummaryJson:
    """SummaryJson model tests (PRD FR23)."""

    def _make_summary(self, **overrides: object) -> SummaryJson:
        defaults = {
            "pack_id": "pack-001",
            "test_run": RunInfo(
                run_id="run-001",
                result="PASS",
                duration_ms=5000,
                autotest_version="0.1.0",
            ),
            "environment": EnvironmentInfo(
                framework="0.1.0", adapter="CliMod", game="STS2", os="Win", python="3.11"
            ),
        }
        defaults.update(overrides)
        return SummaryJson(**defaults)

    def test_schema_version_default(self) -> None:
        summary = self._make_summary()
        assert summary.schema_version == SCHEMA_VERSION

    def test_frozen(self) -> None:
        summary = self._make_summary()
        with pytest.raises(ValidationError):
            summary.pack_id = "other"  # type: ignore[misc]

    def test_with_failure(self) -> None:
        failure = FailureInfo(type="assertion_error", message="HP mismatch")
        summary = self._make_summary(failure=failure)
        assert summary.failure is not None
        assert summary.failure.type == "assertion_error"

    def test_without_failure(self) -> None:
        summary = self._make_summary()
        assert summary.failure is None

    def test_summary_json_carries_autotest_version(self) -> None:
        summary = SummaryJson(
            pack_id="run_demo",
            test_run=RunInfo(
                run_id="run_demo",
                result="blocked",
                duration_ms=0,
                autotest_version="0.1.0",
            ),
            environment=EnvironmentInfo(
                framework="sts2-autotest",
                adapter="agent",
                game="Slay the Spire 2",
                os="macOS",
                python="3.11.0",
            ),
        )
        assert summary.test_run.autotest_version == "0.1.0"

    def test_summary_json_roundtrip_preserves_compatibility_block_reason(self) -> None:
        summary = SummaryJson(
            pack_id="run_blocked",
            test_run=RunInfo(
                run_id="run_blocked",
                result="blocked",
                duration_ms=0,
                autotest_version="0.1.0",
            ),
            environment=EnvironmentInfo(
                framework="sts2-autotest",
                adapter="agent",
                game="Slay the Spire 2",
                os="macOS",
                python="3.11.0",
            ),
            compatibility_block_reason="autotest_compatibility_blocked",
        )
        restored = SummaryJson.model_validate(summary.model_dump(mode="json"))
        assert restored.compatibility_block_reason == "autotest_compatibility_blocked"

    def test_summary_json_model_validate_accepts_legacy_run_info_without_autotest_version(
        self,
    ) -> None:
        restored = SummaryJson.model_validate(
            {
                "schema_version": SCHEMA_VERSION,
                "pack_id": "legacy-pack",
                "test_run": {
                    "run_id": "legacy-run",
                    "result": "blocked",
                    "duration_ms": 0,
                },
                "environment": {
                    "framework": "sts2-autotest",
                    "adapter": "agent",
                    "game": "Slay the Spire 2",
                    "os": "macOS",
                    "python": "3.11.0",
                },
            }
        )
        assert restored.test_run.autotest_version is None


class TestEvidencePack:
    """EvidencePack model tests."""

    def test_schema_version_default(self) -> None:
        pack = EvidencePack(
            pack_id="pack-001",
            output_dir="evidence/pack-001",
            test_run=RunInfo(
                run_id="run-001",
                result="PASS",
                duration_ms=5000,
                autotest_version="0.1.0",
            ),
            environment=EnvironmentInfo(
                framework="0.1.0", adapter="CliMod", game="STS2", os="Win", python="3.11"
            ),
        )
        assert pack.schema_version == SCHEMA_VERSION

    def test_frozen(self) -> None:
        pack = EvidencePack(
            pack_id="pack-001",
            output_dir="evidence/pack-001",
            test_run=RunInfo(
                run_id="run-001",
                result="PASS",
                duration_ms=5000,
                autotest_version="0.1.0",
            ),
            environment=EnvironmentInfo(
                framework="0.1.0", adapter="CliMod", game="STS2", os="Win", python="3.11"
            ),
        )
        with pytest.raises(ValidationError):
            pack.pack_id = "other"  # type: ignore[misc]


class TestRepairSuggestion:
    """RepairSuggestion model tests (B10)."""

    def test_create_minimal(self) -> None:
        from sts2_autotest.common.evidence import RepairSuggestion

        s = RepairSuggestion(
            confidence=0.5,
            category="code_fix",
            title="测试建议",
            description="这是一条测试建议",
        )
        assert s.confidence == 0.5
        assert s.category == "code_fix"
        assert s.title == "测试建议"
        assert s.description == "这是一条测试建议"
        assert s.source_location is None
        assert s.patch is None
        assert s.related_docs == []

    def test_with_optional_fields(self) -> None:
        from sts2_autotest.common.evidence import RepairSuggestion

        s = RepairSuggestion(
            confidence=0.8,
            category="config_change",
            title="配置错误",
            description="需要修改配置文件",
            source_location="src/mod.py:42",
            patch='@@ -1,3 +1,3 @@\n-old\n+new',
            related_docs=["https://example.com/doc"],
        )
        assert s.source_location == "src/mod.py:42"
        assert s.patch is not None
        assert len(s.related_docs) == 1

    def test_frozen(self) -> None:
        from pydantic import ValidationError
        from sts2_autotest.common.evidence import RepairSuggestion

        s = RepairSuggestion(
            confidence=0.5, category="code_fix", title="t", description="d",
        )
        with pytest.raises(ValidationError):
            s.confidence = 0.9  # type: ignore[misc]

    def test_confidence_must_be_float(self) -> None:
        from pydantic import ValidationError
        from sts2_autotest.common.evidence import RepairSuggestion

        with pytest.raises(ValidationError):
            RepairSuggestion(
                confidence="high",  # type: ignore[arg-type]
                category="code_fix",
                title="t",
                description="d",
            )

    def test_roundtrip(self) -> None:
        from sts2_autotest.common.evidence import RepairSuggestion

        s = RepairSuggestion(
            confidence=0.75,
            category="env_fix",
            title="环境问题",
            description="检查环境变量",
            source_location="config/settings.yaml:10",
        )
        data = s.model_dump(mode="json")
        restored = RepairSuggestion.model_validate(data)
        assert restored.confidence == 0.75
        assert restored.category == "env_fix"
        assert restored.source_location == "config/settings.yaml:10"


class TestRepairReport:
    """RepairReport model tests (B10)."""

    def test_create_empty(self) -> None:
        from sts2_autotest.common.evidence import RepairReport

        r = RepairReport(
            crash_signature="ValueError:1",
            suggestions=[],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine",
            analysis_duration_ms=1.5,
        )
        assert r.crash_signature == "ValueError:1"
        assert r.suggestions == []
        assert r.source == "rule_engine"

    def test_with_suggestions(self) -> None:
        from sts2_autotest.common.evidence import RepairReport, RepairSuggestion

        s = RepairSuggestion(
            confidence=0.6, category="code_fix", title="修复", description="修",
        )
        r = RepairReport(
            crash_signature="crash:0xC0000005",
            suggestions=[s],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine+stack_trace",
            analysis_duration_ms=3.2,
        )
        assert len(r.suggestions) == 1
        assert r.suggestions[0].title == "修复"

    def test_frozen(self) -> None:
        from pydantic import ValidationError
        from sts2_autotest.common.evidence import RepairReport

        r = RepairReport(
            crash_signature="x:0",
            suggestions=[],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine",
            analysis_duration_ms=0.0,
        )
        with pytest.raises(ValidationError):
            r.source = "replay_capture"  # type: ignore[misc]

    def test_roundtrip(self) -> None:
        from sts2_autotest.common.evidence import RepairReport, RepairSuggestion

        s = RepairSuggestion(
            confidence=0.5, category="investigation_needed", title="调查", description="需进一步分析",
        )
        r = RepairReport(
            crash_signature="Error:none",
            suggestions=[s],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine",
            analysis_duration_ms=5.0,
        )
        data = r.model_dump(mode="json")
        restored = RepairReport.model_validate(data)
        assert restored.crash_signature == "Error:none"
        assert len(restored.suggestions) == 1
        assert restored.suggestions[0].category == "investigation_needed"


class TestSummaryJsonWithRepairReport:
    """SummaryJson.repair_report optional field (B10)."""

    def test_default_is_none(self) -> None:
        from sts2_autotest.common.evidence import (
            SummaryJson, RunInfo, EnvironmentInfo,
        )

        s = SummaryJson(
            pack_id="p1",
            test_run=RunInfo(
                run_id="r1",
                result="passed",
                duration_ms=100,
                autotest_version="0.1.0",
            ),
            environment=EnvironmentInfo(
                framework="fw", adapter="ad", game="g", os="os", python="3.11",
            ),
        )
        assert s.repair_report is None

    def test_with_repair_report(self) -> None:
        from sts2_autotest.common.evidence import (
            SummaryJson, RunInfo, EnvironmentInfo, RepairReport,
        )

        report = RepairReport(
            crash_signature="e:0",
            suggestions=[],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine",
            analysis_duration_ms=0.0,
        )
        s = SummaryJson(
            pack_id="p2",
            test_run=RunInfo(
                run_id="r2",
                result="failed",
                duration_ms=200,
                autotest_version="0.1.0",
            ),
            environment=EnvironmentInfo(
                framework="fw", adapter="ad", game="g", os="os", python="3.11",
            ),
            repair_report=report,
        )
        assert s.repair_report is not None
        assert s.repair_report.crash_signature == "e:0"

    def test_roundtrip_with_repair_report(self) -> None:
        from sts2_autotest.common.evidence import (
            SummaryJson, RunInfo, EnvironmentInfo, RepairReport, RepairSuggestion,
        )

        suggestion = RepairSuggestion(
            confidence=0.5, category="code_fix", title="t", description="d",
        )
        report = RepairReport(
            crash_signature="X:1",
            suggestions=[suggestion],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine+stack_trace",
            analysis_duration_ms=2.0,
        )
        s = SummaryJson(
            pack_id="p3",
            test_run=RunInfo(
                run_id="r3",
                result="crashed",
                duration_ms=0,
                autotest_version="0.1.0",
            ),
            environment=EnvironmentInfo(
                framework="fw", adapter="ad", game="g", os="os", python="3.11",
            ),
            repair_report=report,
        )
        data = s.model_dump(mode="json")
        restored = SummaryJson.model_validate(data)
        assert restored.repair_report is not None
        assert restored.repair_report.suggestions[0].confidence == 0.5
