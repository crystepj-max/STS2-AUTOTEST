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
        info = RunInfo(run_id="run-001", result="PASS", duration_ms=5000)
        assert info.run_id == "run-001"
        assert info.result == "PASS"
        assert info.duration_ms == 5000

    def test_frozen(self) -> None:
        info = RunInfo(run_id="run-001", result="PASS", duration_ms=5000)
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


class TestSummaryJson:
    """SummaryJson model tests (PRD FR23)."""

    def _make_summary(self, **overrides: object) -> SummaryJson:
        defaults = {
            "pack_id": "pack-001",
            "test_run": RunInfo(run_id="run-001", result="PASS", duration_ms=5000),
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


class TestEvidencePack:
    """EvidencePack model tests."""

    def test_schema_version_default(self) -> None:
        pack = EvidencePack(
            pack_id="pack-001",
            output_dir="evidence/pack-001",
            test_run=RunInfo(run_id="run-001", result="PASS", duration_ms=5000),
            environment=EnvironmentInfo(
                framework="0.1.0", adapter="CliMod", game="STS2", os="Win", python="3.11"
            ),
        )
        assert pack.schema_version == SCHEMA_VERSION

    def test_frozen(self) -> None:
        pack = EvidencePack(
            pack_id="pack-001",
            output_dir="evidence/pack-001",
            test_run=RunInfo(run_id="run-001", result="PASS", duration_ms=5000),
            environment=EnvironmentInfo(
                framework="0.1.0", adapter="CliMod", game="STS2", os="Win", python="3.11"
            ),
        )
        with pytest.raises(ValidationError):
            pack.pack_id = "other"  # type: ignore[misc]
