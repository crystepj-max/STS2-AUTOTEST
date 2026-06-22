"""Tests for pytest_plugin/plugin.py — hook integration and notification callback."""

import os
import json
from pathlib import Path
from unittest import mock



class TestPytestSessionfinish:
    """pytest_sessionfinish should fire session_end with exitstatus."""

    def test_fires_session_end_with_exitstatus(self) -> None:
        """session_end hook receives exitstatus kwarg."""
        from sts2_autotest.pytest_plugin.hooks import fire

        received: list[dict] = []

        def _capture(**kwargs: object) -> None:
            received.append(dict(kwargs))

        from sts2_autotest.pytest_plugin.hooks import register
        register("session_end", _capture)
        try:
            fire("session_end", exitstatus=0)
            assert len(received) == 1
            assert received[0]["exitstatus"] == 0

            received.clear()
            fire("session_end", exitstatus=1)
            assert len(received) == 1
            assert received[0]["exitstatus"] == 1
        finally:
            from sts2_autotest.pytest_plugin.hooks import clear
            clear()


class TestCallbackRegistration:
    """_register_notification_callback should respect config and CI env."""

    def test_registers_when_enabled(self) -> None:
        """Callback should be registered when notifications are enabled."""
        from sts2_autotest.pytest_plugin.hooks import _default_registry
        from sts2_autotest.pytest_plugin.plugin import (
            _register_notification_callback,
        )

        # Clear existing hooks
        _default_registry.clear()
        try:
            with mock.patch.dict(os.environ, {
                "STS2_NOTIFICATIONS__ENABLED": "true",
            }, clear=True):
                # Remove CI env var
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("CI", None)
                    _register_notification_callback()
                    hooks = _default_registry._hooks["session_end"]
                    assert len(hooks) == 1, f"Expected 1 hook, got {len(hooks)}"
        finally:
            _default_registry.clear()

    def test_skips_when_disabled(self) -> None:
        """Callback should NOT be registered when notifications disabled."""
        from sts2_autotest.pytest_plugin.hooks import _default_registry
        from sts2_autotest.pytest_plugin.plugin import (
            _register_notification_callback,
        )

        _default_registry.clear()
        try:
            with mock.patch.dict(os.environ, {
                "STS2_NOTIFICATIONS__ENABLED": "false",
            }, clear=True):
                os.environ.pop("CI", None)
                _register_notification_callback()
                hooks = _default_registry._hooks["session_end"]
                assert len(hooks) == 0
        finally:
            _default_registry.clear()

    def test_skips_in_ci_environment(self) -> None:
        """Callback should NOT be registered when CI env var is set."""
        from sts2_autotest.pytest_plugin.hooks import _default_registry
        from sts2_autotest.pytest_plugin.plugin import (
            _register_notification_callback,
        )

        _default_registry.clear()
        try:
            with mock.patch.dict(os.environ, {
                "STS2_NOTIFICATIONS__ENABLED": "true",
                "CI": "true",
            }, clear=True):
                _register_notification_callback()
                hooks = _default_registry._hooks["session_end"]
                assert len(hooks) == 0
        finally:
            _default_registry.clear()


class TestOnSessionEndNotify:
    """_on_session_end_notify callback logic tests.

    Uses mock.Mock() as the notifier to avoid depending on internal
    StubNotifier behavior (which is pure no-op without recording).
    """

    def test_skips_when_disabled(self) -> None:
        """Should return early when config.enabled is false."""
        from sts2_autotest.pytest_plugin.plugin import (
            _on_session_end_notify,
        )

        stub_notifier = mock.Mock()
        with mock.patch.dict(os.environ, {
            "STS2_NOTIFICATIONS__ENABLED": "false",
        }, clear=True):
            _on_session_end_notify(0, stub_notifier)
            stub_notifier.notify.assert_not_called()

    def test_notifies_on_success_when_enabled(self) -> None:
        """exitstatus=0 with on_success=True should trigger notification."""
        from sts2_autotest.pytest_plugin.plugin import (
            _on_session_end_notify,
        )

        stub_notifier = mock.Mock()
        with mock.patch.dict(os.environ, {
            "STS2_NOTIFICATIONS__ENABLED": "true",
            "STS2_NOTIFICATIONS__ON_SUCCESS": "true",
        }, clear=True):
            _on_session_end_notify(0, stub_notifier)
            stub_notifier.notify.assert_called_once()
            call_kwargs = stub_notifier.notify.call_args.kwargs
            assert call_kwargs["level"] == "info"
            assert "测试完成" in call_kwargs["title"]

    def test_notifies_on_failure_with_warning_level(self) -> None:
        """exitstatus=1 should warn with warning level."""
        from sts2_autotest.pytest_plugin.plugin import (
            _on_session_end_notify,
        )

        stub_notifier = mock.Mock()
        with mock.patch.dict(os.environ, {
            "STS2_NOTIFICATIONS__ENABLED": "true",
            "STS2_NOTIFICATIONS__ON_FAILURE": "true",
        }, clear=True):
            _on_session_end_notify(1, stub_notifier)
            stub_notifier.notify.assert_called_once()
            call_kwargs = stub_notifier.notify.call_args.kwargs
            assert call_kwargs["level"] == "warning"

    def test_exception_in_callback_is_caught(self) -> None:
        """HookRegistry.fire() already wraps callbacks in try/except.
        Verify a buggy callback doesn't propagate."""
        from sts2_autotest.pytest_plugin.hooks import _default_registry

        _default_registry.clear()
        try:
            def _bad_callback(**kwargs: object) -> None:
                raise RuntimeError("buggy callback")

            _default_registry.register("session_end", _bad_callback)
            # Should not raise — fire catches exceptions
            _default_registry.fire("session_end", exitstatus=0)
        finally:
            _default_registry.clear()


class TestNotificationMessageHelpers:
    """Direct coverage for notification summary and duration helpers."""

    def test_build_message_uses_latest_summary(self, tmp_path: Path) -> None:
        """Summary data should be reflected in the desktop notification body."""
        from sts2_autotest.pytest_plugin.plugin import _build_notification_message

        output_dir = tmp_path / "output"
        latest_dir = output_dir / "latest"
        latest_dir.mkdir(parents=True)
        summary = {
            "test_run": {
                "passed": 3,
                "failed": 1,
                "crashed": 1,
                "duration_ms": 2_520_000,
            }
        }
        (latest_dir / "summary.json").write_text(
            json.dumps(summary),
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, {
            "STS2_FRAMEWORK__EVIDENCE_DIR": str(output_dir),
        }, clear=True):
            title, message = _build_notification_message(1)

        assert "测试完成" in title
        assert "通过: 3" in message
        assert "失败: 1" in message
        assert "崩溃: 1" in message
        assert "42 分钟" in message

    def test_build_message_falls_back_without_summary(self, tmp_path: Path) -> None:
        """Missing summary.json should still produce a useful notification."""
        from sts2_autotest.pytest_plugin.plugin import _build_notification_message

        with mock.patch.dict(os.environ, {
            "STS2_FRAMEWORK__EVIDENCE_DIR": str(tmp_path / "missing"),
        }, clear=True):
            title, message = _build_notification_message(4)

        assert "测试完成" in title
        assert message == "⚠️ 测试会话完成 (exit code: 4)"

    def test_callback_handles_notifier_creation_failure(self) -> None:
        """Callback should not crash when notifier creation fails.

        The lazy-notifier pattern in _register_notification_callback
        must not propagate exceptions from create_desktop_notifier().
        """
        from sts2_autotest.pytest_plugin.hooks import _default_registry
        from sts2_autotest.pytest_plugin.plugin import (
            _register_notification_callback,
        )

        _default_registry.clear()
        try:
            with mock.patch.dict(os.environ, {
                "STS2_NOTIFICATIONS__ENABLED": "true",
                "STS2_NOTIFICATIONS__ON_SUCCESS": "true",
            }, clear=True):
                os.environ.pop("CI", None)

                # Force create_desktop_notifier to raise
                with mock.patch(
                    "sts2_autotest.core.notifier.create_desktop_notifier",
                    side_effect=RuntimeError("failed to create notifier"),
                ):
                    _register_notification_callback()
                    hooks = _default_registry._hooks.get("session_end", [])
                    assert len(hooks) == 1

                    # Fire the hook — should NOT raise
                    _default_registry.fire("session_end", exitstatus=0)
        finally:
            _default_registry.clear()
