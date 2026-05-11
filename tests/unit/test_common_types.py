"""Tests for common/types.py — Capabilities dataclass."""

import dataclasses

import pytest

from sts2_autotest.common.types import Capabilities


class TestCapabilities:
    """Capabilities dataclass tests."""

    def test_defaults(self) -> None:
        caps = Capabilities()
        assert caps.supports_multiplayer is False
        assert caps.supports_metadata is False
        assert caps.supports_debug_actions is False

    def test_custom_values(self) -> None:
        caps = Capabilities(
            supports_multiplayer=True,
            supports_metadata=True,
            supports_debug_actions=False,
        )
        assert caps.supports_multiplayer is True
        assert caps.supports_metadata is True
        assert caps.supports_debug_actions is False

    def test_frozen(self) -> None:
        caps = Capabilities()
        with pytest.raises(dataclasses.FrozenInstanceError):
            caps.supports_multiplayer = True  # type: ignore[misc]

    def test_equality(self) -> None:
        caps1 = Capabilities(supports_multiplayer=True)
        caps2 = Capabilities(supports_multiplayer=True)
        assert caps1 == caps2
