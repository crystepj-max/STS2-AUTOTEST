"""Tests for config/loader.py — four-layer inheritance, env vars, YAML."""

from pathlib import Path

import pytest

from sts2_autotest.config.loader import load_config


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Temporary directory for config files."""
    return tmp_path


class TestDefaultsOnly:
    """Layer 1: defaults with no config files."""

    def test_loads_without_any_files(self, config_dir: Path) -> None:
        cfg = load_config(project_dir=config_dir)
        assert cfg.framework.log_level == "INFO"
        assert cfg.execution.game_timeout == 60.0
        assert cfg.adapter.cli.enabled is True

    def test_frozen_config_returned(self, config_dir: Path) -> None:
        cfg = load_config(project_dir=config_dir)
        with pytest.raises(Exception):
            cfg.execution = cfg.execution.model_copy(update={"game_timeout": 999})  # type: ignore[misc]


class TestYAMLOverride:
    """Layer 2: YAML overrides defaults."""

    def test_yaml_overrides_default(self, config_dir: Path) -> None:
        yaml_file = config_dir / "sts2-autotest.yaml"
        yaml_file.write_text("execution:\n  game_timeout: 120.0\n")
        cfg = load_config(project_dir=config_dir)
        assert cfg.execution.game_timeout == 120.0
        assert cfg.framework.log_level == "INFO"  # default preserved

    def test_yaml_partial_override(self, config_dir: Path) -> None:
        yaml_file = config_dir / "sts2-autotest.yaml"
        yaml_file.write_text("framework:\n  log_level: DEBUG\n")
        cfg = load_config(project_dir=config_dir)
        assert cfg.framework.log_level == "DEBUG"
        assert cfg.execution.game_timeout == 60.0

    def test_missing_yaml_is_ok(self, config_dir: Path) -> None:
        cfg = load_config(project_dir=config_dir)
        assert cfg.execution.game_timeout == 60.0

    def test_empty_yaml_is_ok(self, config_dir: Path) -> None:
        yaml_file = config_dir / "sts2-autotest.yaml"
        yaml_file.write_text("")
        cfg = load_config(project_dir=config_dir)
        assert cfg.execution.game_timeout == 60.0


class TestEnvOverride:
    """Layer 3: environment variables override YAML and defaults."""

    def test_env_overrides_default(self, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STS2_EXECUTION__GAME_TIMEOUT", "90.0")
        cfg = load_config(project_dir=config_dir)
        assert cfg.execution.game_timeout == 90.0

    def test_env_overrides_yaml(self, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_file = config_dir / "sts2-autotest.yaml"
        yaml_file.write_text("execution:\n  game_timeout: 120.0\n")
        monkeypatch.setenv("STS2_EXECUTION__GAME_TIMEOUT", "45.0")
        cfg = load_config(project_dir=config_dir)
        assert cfg.execution.game_timeout == 45.0

    def test_env_bool_coercion(self, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STS2_EXECUTION__PARALLEL", "true")
        cfg = load_config(project_dir=config_dir)
        assert cfg.execution.parallel is True

    def test_env_int_coercion(self, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STS2_FRAMEWORK__EVIDENCE_RETENTION", "50")
        cfg = load_config(project_dir=config_dir)
        assert cfg.framework.evidence_retention == 50

    def test_env_nested_adapter(self, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STS2_ADAPTER__CLI__TIMEOUT", "15.0")
        cfg = load_config(project_dir=config_dir)
        assert cfg.adapter.cli.timeout == 15.0

    def test_non_sts2_env_ignored(self, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTHER_VAR", "ignored")
        cfg = load_config(project_dir=config_dir)
        assert cfg.execution.game_timeout == 60.0


class TestDotenvOverride:
    """Layer 3: .env file loading."""

    def test_dotenv_overrides_default(self, config_dir: Path) -> None:
        env_file = config_dir / ".env"
        env_file.write_text("STS2_EXECUTION__GAME_TIMEOUT=75.0\n")
        cfg = load_config(project_dir=config_dir)
        assert cfg.execution.game_timeout == 75.0

    def test_env_overrides_dotenv(self, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_file = config_dir / ".env"
        env_file.write_text("STS2_EXECUTION__GAME_TIMEOUT=75.0\n")
        monkeypatch.setenv("STS2_EXECUTION__GAME_TIMEOUT", "30.0")
        cfg = load_config(project_dir=config_dir)
        assert cfg.execution.game_timeout == 30.0


class TestCLIOverrides:
    """Layer 4: CLI overrides are highest priority."""

    def test_cli_overrides_default(self, config_dir: Path) -> None:
        cfg = load_config(
            project_dir=config_dir,
            cli_overrides={"execution": {"game_timeout": 15.0}},
        )
        assert cfg.execution.game_timeout == 15.0

    def test_cli_overrides_env(self, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STS2_EXECUTION__GAME_TIMEOUT", "90.0")
        cfg = load_config(
            project_dir=config_dir,
            cli_overrides={"execution": {"game_timeout": 10.0}},
        )
        assert cfg.execution.game_timeout == 10.0

    def test_cli_overrides_yaml(self, config_dir: Path) -> None:
        yaml_file = config_dir / "sts2-autotest.yaml"
        yaml_file.write_text("execution:\n  game_timeout: 120.0\n")
        cfg = load_config(
            project_dir=config_dir,
            cli_overrides={"execution": {"game_timeout": 5.0}},
        )
        assert cfg.execution.game_timeout == 5.0


class TestFullLayerStack:
    """All four layers combined."""

    def test_four_layer_priority(self, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Layer 2: YAML
        yaml_file = config_dir / "sts2-autotest.yaml"
        yaml_file.write_text("execution:\n  game_timeout: 120.0\n")
        # Layer 3: env var
        monkeypatch.setenv("STS2_FRAMEWORK__LOG_LEVEL", "DEBUG")
        # Layer 4: CLI
        cfg = load_config(
            project_dir=config_dir,
            cli_overrides={"execution": {"max_retries": 5}},
        )
        assert cfg.execution.game_timeout == 120.0  # from YAML
        assert cfg.framework.log_level == "DEBUG"  # from env
        assert cfg.execution.max_retries == 5  # from CLI
        assert cfg.state_machine.poll_interval == 0.5  # default

    def test_string_project_dir(self, config_dir: Path) -> None:
        cfg = load_config(project_dir=str(config_dir))
        assert cfg.execution.game_timeout == 60.0
