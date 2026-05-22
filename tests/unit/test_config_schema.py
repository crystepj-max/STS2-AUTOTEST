"""Tests for config/schema.py — default values, validators, mutual exclusion."""

import pytest
from pydantic import ValidationError

from sts2_autotest.config.schema import (
    AdapterConfig,
    AgentAdapterConfig,
    CliAdapterConfig,
    ExecutionConfig,
    FrameworkConfig,
    STS2Config,
    StateMachineConfig,
)


class TestFrameworkConfig:
    """FrameworkConfig defaults and validation."""

    def test_defaults(self) -> None:
        cfg = FrameworkConfig()
        assert cfg.log_level == "INFO"
        assert cfg.screenshot_dir == "tests/output/screenshots"
        assert cfg.evidence_dir == "tests/output"
        assert cfg.evidence_retention == 20

    def test_valid_log_levels(self) -> None:
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            cfg = FrameworkConfig(log_level=level)
            assert cfg.log_level == level

    def test_invalid_log_level(self) -> None:
        with pytest.raises(ValidationError):
            FrameworkConfig(log_level="VERBOSE")

    def test_negative_retention(self) -> None:
        with pytest.raises(ValidationError):
            FrameworkConfig(evidence_retention=0)


class TestExecutionConfig:
    """ExecutionConfig field validators."""

    def test_defaults(self) -> None:
        cfg = ExecutionConfig()
        assert cfg.game_timeout == 60.0
        assert cfg.game_startup_timeout == 60.0
        assert cfg.max_retries == 3
        assert cfg.parallel is False

    def test_negative_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionConfig(game_timeout=-1)

    def test_zero_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionConfig(game_timeout=0)

    def test_negative_retries_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionConfig(max_retries=-1)

    def test_zero_retries_allowed(self) -> None:
        cfg = ExecutionConfig(max_retries=0)
        assert cfg.max_retries == 0


class TestAdapterConfig:
    """Adapter mutual exclusion and defaults."""

    def test_defaults_cli_enabled_agent_disabled(self) -> None:
        cfg = AdapterConfig()
        assert cfg.cli.enabled is True
        assert cfg.agent.enabled is False

    def test_cli_only(self) -> None:
        cfg = STS2Config(adapter=AdapterConfig(cli=CliAdapterConfig(enabled=True)))
        assert cfg.adapter.cli.enabled is True

    def test_agent_only(self) -> None:
        cfg = STS2Config(adapter=AdapterConfig(
            cli=CliAdapterConfig(enabled=False),
            agent=AgentAdapterConfig(enabled=True),
        ))
        assert cfg.adapter.agent.enabled is True

    def test_agent_transport_defaults_to_http(self) -> None:
        cfg = AgentAdapterConfig()
        assert cfg.transport == "http"

    def test_agent_transport_accepts_mcp(self) -> None:
        cfg = AgentAdapterConfig(transport="mcp")
        assert cfg.transport == "mcp"

    def test_agent_transport_rejects_unknown_value(self) -> None:
        with pytest.raises(ValidationError):
            AgentAdapterConfig(transport="websocket")

    def test_mutual_exclusion_both_enabled(self) -> None:
        with pytest.raises(ValidationError, match="Mutual exclusion"):
            STS2Config(adapter=AdapterConfig(
                cli=CliAdapterConfig(enabled=True),
                agent=AgentAdapterConfig(enabled=True),
            ))

    def test_both_disabled_is_valid(self) -> None:
        cfg = STS2Config(adapter=AdapterConfig(
            cli=CliAdapterConfig(enabled=False),
        ))
        assert cfg.adapter.cli.enabled is False
        assert cfg.adapter.agent.enabled is False


class TestSTS2Config:
    """Top-level config defaults and frozen enforcement."""

    def test_full_defaults(self) -> None:
        cfg = STS2Config()
        assert cfg.framework.log_level == "INFO"
        assert cfg.adapter.cli.enabled is True
        assert cfg.execution.game_timeout == 60.0
        assert cfg.state_machine.poll_interval == 0.5

    def test_frozen(self) -> None:
        cfg = STS2Config()
        with pytest.raises(ValidationError):
            cfg.framework = FrameworkConfig()  # type: ignore[misc]

    def test_nested_override(self) -> None:
        cfg = STS2Config(execution=ExecutionConfig(game_timeout=120.0))
        assert cfg.execution.game_timeout == 120.0
        assert cfg.execution.max_retries == 3  # default preserved
