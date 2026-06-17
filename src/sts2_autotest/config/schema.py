"""Configuration schema definitions for STS2-AUTOTEST (FR37, FR38, FR39).

All config models are frozen pydantic BaseModel instances.
Field validators enforce business rules at parse time.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sts2_autotest.common.visual_qa import DEFAULT_LOW_VARIANCE_THRESHOLD


class CliAdapterConfig(BaseModel):
    """STS2-Cli-Mod adapter configuration."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    cli_path: str = "sts2"
    timeout: float = Field(default=30.0, gt=0)


class AgentAdapterConfig(BaseModel):
    """STS2-Agent adapter configuration (Beta, disabled by default)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    transport: Literal["http", "mcp"] = "http"
    endpoint: str = "http://localhost:8080"
    mcp_endpoint: str = "http://127.0.0.1:8765/mcp"
    timeout: float = Field(default=30.0, gt=0)
    tool_profile: str = Field(default="guided", pattern=r"^(guided|layered|full)$")
    health_path: str = "health"
    state_path: str = "game_state"
    actions_path: str = "available_actions"
    act_path: str = "act"
    wait_path: str = "wait_until_actionable"
    debug_actions: bool = False


class AdapterConfig(BaseModel):
    """Adapter subsystem configuration."""

    model_config = ConfigDict(frozen=True)

    cli: CliAdapterConfig = CliAdapterConfig()
    agent: AgentAdapterConfig = AgentAdapterConfig()


class FrameworkConfig(BaseModel):
    """Framework-level configuration."""

    model_config = ConfigDict(frozen=True)

    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    screenshot_dir: str = "tests/output/screenshots"
    evidence_dir: str = "tests/output"
    evidence_retention: int = Field(default=20, ge=1)
    metrics_filename: str = "metrics.jsonl"
    screenshot_rgb_threshold: int = Field(default=3, ge=1)
    screenshot_target_resolution: str = "1920x1080"
    screenshot_resolution_tolerance: int = Field(default=2, ge=0)
    screenshot_min_file_bytes: int = Field(default=1024, ge=1)
    screenshot_max_retries: int = Field(default=3, ge=0)
    log_levels: str = "ERROR,WARN,WARNING"
    log_max_entries: int = Field(default=10000, ge=1)
    log_custom_paths: str = ""
    log_backup_dir: str = ""
    log_lock_retries: int = Field(default=5, ge=0)
    log_lock_base_delay: float = Field(default=0.1, gt=0)
    log_retention_days: int = Field(default=7, ge=1)
    log_retention_max_bytes: int = Field(default=10 * 1024 * 1024 * 1024, ge=1)
    strict_validation: bool = False
    progress_dir: str = "tests/output/.progress"
    progress_filename: str = "session-progress.json"
    artifact_dir: str = "tests/output/artifacts"
    disk_threshold_mb: int = Field(default=100, ge=1)
    lock_file: str = "tests/output/.sts2-autotest.lock"
    visual_qa_enabled: bool = True
    visual_qa_ocr_provider: Literal["disabled", "tesseract"] = "disabled"
    visual_qa_tesseract_cmd: str = "tesseract"
    visual_qa_tesseract_lang: str = "chi_sim+eng"
    visual_qa_timeout_seconds: float = Field(default=10.0, gt=0)
    visual_qa_health_enabled: bool = True
    visual_qa_health_provider: Literal["disabled", "opencv"] = "disabled"
    visual_qa_low_variance_threshold: float = Field(
        default=DEFAULT_LOW_VARIANCE_THRESHOLD,
        gt=0,
    )
    visual_qa_low_brightness_threshold: float = Field(default=5.0, gt=0)


class ExecutionConfig(BaseModel):
    """Test execution configuration."""

    model_config = ConfigDict(frozen=True)

    game_timeout: float = Field(default=60.0, gt=0)
    game_startup_timeout: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    max_consecutive_failures: int = Field(default=3, ge=1)
    heartbeat_timeout: float = Field(default=60.0, gt=0)
    session_queue_timeout: float = Field(default=60.0, gt=0)
    session_queue_max_depth: int = Field(default=10, ge=1)
    parallel: bool = False


class StateMachineConfig(BaseModel):
    """State machine polling and timeout configuration."""

    model_config = ConfigDict(frozen=True)

    transition_timeout: float = Field(default=10.0, gt=0)
    poll_interval: float = Field(default=0.5, gt=0)


class NotificationsConfig(BaseModel):
    """Desktop notification configuration (B13)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    on_success: bool = True
    on_failure: bool = True
    on_crash: bool = True


class ServerConfig(BaseModel):
    """Health check HTTP server configuration (B17)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=8766, ge=1024, le=65535)


class ProjectConfigModel(BaseModel):
    """Configuration for a single MOD project in the workspace.

    Extended (B20): added mod_id, manifest, source_dirs, design_docs,
    suite_dirs for workspace manifest and autotest discovery.
    """
    model_config = ConfigDict(frozen=True)

    name: str
    mod_id: str = ""
    manifest: str = ""
    spec_dir: str
    output_dir: str = ""
    source_dirs: list[str] = []
    design_docs: list[str] = []
    suite_dirs: list[str] = []
    agent_context_dir: str = ".sts2/context"
    default_suite: str = ""
    autotest_config: str = ""

    @model_validator(mode="after")
    def _ensure_mod_id(self) -> "ProjectConfigModel":
        if not self.mod_id:
            object.__setattr__(self, "mod_id", self.name)
        return self

class WorkspaceConfigModel(BaseModel):
    """Workspace configuration for multi-MOD-project support."""
    model_config = ConfigDict(frozen=True)
    projects: list[ProjectConfigModel] = []


class STS2Config(BaseModel):
    """Top-level configuration model with four-layer inheritance support.

    Layers (latter overrides former):
    1. Defaults (defined in Field defaults)
    2. YAML file (sts2-autotest.yaml)
    3. Environment variables (STS2_ prefix)
    4. CLI arguments
    """

    model_config = ConfigDict(frozen=True)

    framework: FrameworkConfig = FrameworkConfig()
    adapter: AdapterConfig = AdapterConfig()
    execution: ExecutionConfig = ExecutionConfig()
    state_machine: StateMachineConfig = StateMachineConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    server: ServerConfig = ServerConfig()
    workspace: WorkspaceConfigModel = WorkspaceConfigModel()

    @model_validator(mode="after")
    def _check_adapter_mutual_exclusion(self) -> "STS2Config":
        if self.adapter.cli.enabled and self.adapter.agent.enabled:
            raise ValueError(
                "Mutual exclusion violation: adapter.cli.enabled and "
                "adapter.agent.enabled cannot both be True"
            )
        return self
