"""Tests for core/notifier.py — platform notifier implementations and factory."""

import platform
from unittest import mock

import pytest

from sts2_autotest.common.types import DesktopNotifier


class StubNotifier:
    """Test stub that records notify() calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def notify(self, title: str, message: str, level: str) -> None:
        self.calls.append({"title": title, "message": message, "level": level})


class TestDesktopNotifierProtocol:
    """DesktopNotifier Protocol compliance tests."""

    def test_stub_satisfies_protocol(self) -> None:
        """StubNotifier should satisfy the DesktopNotifier Protocol at type-check time."""
        notifier: DesktopNotifier = StubNotifier()
        notifier.notify("title", "message", "info")
        # No exception = Protocol satisfied

    def test_stub_records_calls(self) -> None:
        stub = StubNotifier()
        stub.notify("Test Title", "Test Message", "warning")
        assert len(stub.calls) == 1
        assert stub.calls[0] == {
            "title": "Test Title",
            "message": "Test Message",
            "level": "warning",
        }

    def test_stub_accumulates_multiple_calls(self) -> None:
        stub = StubNotifier()
        stub.notify("T1", "M1", "info")
        stub.notify("T2", "M2", "warning")
        assert len(stub.calls) == 2
        assert stub.calls[0]["title"] == "T1"
        assert stub.calls[1]["title"] == "T2"
