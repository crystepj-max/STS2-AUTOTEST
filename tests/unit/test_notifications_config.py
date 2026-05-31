"""Tests for NotificationsConfig — defaults, validation, STS2Config integration."""

import pytest
from pydantic import ValidationError

from sts2_autotest.config.schema import NotificationsConfig, STS2Config


class TestNotificationsConfig:
    """NotificationsConfig defaults and validation."""

    def test_defaults(self) -> None:
        cfg = NotificationsConfig()
        assert cfg.enabled is True
        assert cfg.on_success is True
        assert cfg.on_failure is True
        assert cfg.on_crash is True

    def test_custom_values(self) -> None:
        cfg = NotificationsConfig(
            enabled=False,
            on_success=False,
            on_failure=True,
            on_crash=True,
        )
        assert cfg.enabled is False
        assert cfg.on_success is False

    def test_frozen(self) -> None:
        cfg = NotificationsConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = False  # type: ignore[misc]

    def test_integrated_into_sts2config_default(self) -> None:
        """NotificationsConfig should be present on STS2Config with defaults."""
        cfg = STS2Config()
        assert cfg.notifications.enabled is True
        assert cfg.notifications.on_success is True
