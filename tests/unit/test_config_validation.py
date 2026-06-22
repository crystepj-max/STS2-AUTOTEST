"""Tests for config/errors.py and validation error precision (NFR29)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from sts2_autotest.config.errors import ConfigValidationError
from sts2_autotest.config.loader import load_config
from sts2_autotest.config.schema import ExecutionConfig


class TestConfigValidationError:
    """ConfigValidationError wraps pydantic errors with precision."""

    def test_error_contains_field_name(self) -> None:
        try:
            ExecutionConfig(game_timeout=-1)
        except ValidationError as exc:
            err = ConfigValidationError(exc, "test")
            assert len(err.errors) == 1
            assert "game_timeout" in err.errors[0].field

    def test_error_contains_invalid_value(self) -> None:
        try:
            ExecutionConfig(game_timeout=-5.0)
        except ValidationError as exc:
            err = ConfigValidationError(exc, "yaml")
            assert err.errors[0].invalid_value == -5.0
            assert err.errors[0].source == "yaml"

    def test_error_message_is_readable(self) -> None:
        try:
            ExecutionConfig(game_timeout=-1)
        except ValidationError as exc:
            err = ConfigValidationError(exc, "env")
            msg = str(err)
            assert "game_timeout" in msg
            assert "env" in msg


class TestLoadConfigValidation:
    """Validation errors from load_config are properly wrapped."""

    def test_invalid_yaml_value(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "sts2-autotest.yaml"
        yaml_file.write_text("execution:\n  game_timeout: -10.0\n")
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config(project_dir=tmp_path)
        err = exc_info.value
        assert any("game_timeout" in e.field for e in err.errors)

    def test_mutual_exclusion_via_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "sts2-autotest.yaml"
        yaml_file.write_text(
            "adapter:\n  cli:\n    enabled: true\n  agent:\n    enabled: true\n"
        )
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config(project_dir=tmp_path)
        assert any("Mutual exclusion" in e.message for e in exc_info.value.errors)

    def test_invalid_log_level_via_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "sts2-autotest.yaml"
        yaml_file.write_text("framework:\n  log_level: VERBOSE\n")
        with pytest.raises(ConfigValidationError):
            load_config(project_dir=tmp_path)
