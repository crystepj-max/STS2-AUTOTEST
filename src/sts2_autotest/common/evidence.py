"""Evidence pack data models for STS2-AUTOTEST (PRD FR23, FR64)."""

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
