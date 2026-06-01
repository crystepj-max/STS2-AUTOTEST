"""Evidence pack data models for STS2-AUTOTEST (PRD FR23, FR64)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = "1.0.0"


class RunInfo(BaseModel):  # noqa: N801 — renamed to avoid pytest collection warning
    """Test run metadata."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    result: str
    duration_ms: int = Field(ge=0)


class EnvironmentInfo(BaseModel):
    """Runtime environment information."""

    model_config = ConfigDict(frozen=True)

    framework: str
    adapter: str
    game: str
    os: str
    python: str


class ArtifactsInfo(BaseModel):
    """Artifact file references."""

    model_config = ConfigDict(frozen=True)

    screenshots: list[str] = []
    logs: list[str] = []


class FailureInfo(BaseModel):
    """Failure details for failed test cases."""

    model_config = ConfigDict(frozen=True)

    type: str
    message: str
    stack_trace: str | None = None
    expected: str | None = None
    actual: str | None = None
    exit_code: int | None = None


class RepairSuggestion(BaseModel):
    """Single repair suggestion generated from crash analysis (B10).

    Fields source_location and patch are None when the analysis cannot
    pinpoint a specific code location — this is a valid state, not an error.
    """

    model_config = ConfigDict(frozen=True)

    confidence: float = Field(ge=0.0, le=1.0)
    category: Literal["code_fix", "config_change", "env_fix", "investigation_needed"]
    title: str
    description: str
    source_location: str | None = None
    patch: str | None = None
    related_docs: list[str] = []


class RepairReport(BaseModel):
    """Complete repair analysis report for a single crash event (B10).

    Contains the crash signature, a list of RepairSuggestion entries,
    and metadata about how the analysis was generated.
    """

    model_config = ConfigDict(frozen=True)

    crash_signature: str
    suggestions: list[RepairSuggestion]
    generated_at: str  # ISO 8601 UTC
    source: str  # "rule_engine" | "rule_engine+stack_trace" | "replay_capture"
    analysis_duration_ms: float


class SummaryJson(BaseModel):
    """Machine-readable evidence pack summary (PRD FR23).

    Matches the standard evidence pack structure:
    summary.json containing schema_version, pack_id, test_run,
    environment, artifacts, and optional failure fields.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    pack_id: str
    test_run: RunInfo
    environment: EnvironmentInfo
    artifacts: ArtifactsInfo = ArtifactsInfo()
    failure: FailureInfo | None = None
    artifact_path: str | None = None
    repair_report: RepairReport | None = None


class EvidencePack(BaseModel):
    """Top-level evidence pack metadata.

    Represents a complete evidence pack directory with all test artifacts.
    Distinguished from SummaryJson by including directory-level metadata.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    pack_id: str
    output_dir: str
    test_run: RunInfo
    environment: EnvironmentInfo
    artifacts: ArtifactsInfo = ArtifactsInfo()
    failure: FailureInfo | None = None
    artifact_path: str | None = None
