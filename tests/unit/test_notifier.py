"""Tests for core/notifier.py — platform notifier implementations and factory."""

from unittest import mock

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


class TestCreateDesktopNotifier:
    """Factory function tests."""

    def test_returns_stub_on_unknown_platform(self) -> None:
        """create_desktop_notifier should return StubNotifier on unsupported platforms."""
        from sts2_autotest.core.notifier import StubNotifier, create_desktop_notifier

        with mock.patch("platform.system", return_value="FreeBSD"):
            notifier = create_desktop_notifier()
            assert isinstance(notifier, StubNotifier)
            # StubNotifier should not raise on notify
            notifier.notify("t", "m", "info")

    def test_returns_macos_notifier_on_darwin(self) -> None:
        """create_desktop_notifier should return MacOSNotifier on macOS."""
        from sts2_autotest.core.notifier import MacOSNotifier, create_desktop_notifier

        with mock.patch("platform.system", return_value="Darwin"):
            notifier = create_desktop_notifier()
            assert isinstance(notifier, MacOSNotifier)

    def test_returns_windows_notifier_on_windows(self) -> None:
        """create_desktop_notifier should return WindowsNotifier on Windows."""
        from sts2_autotest.core.notifier import WindowsNotifier, create_desktop_notifier

        with mock.patch("platform.system", return_value="Windows"):
            notifier = create_desktop_notifier()
            assert isinstance(notifier, WindowsNotifier)


class TestMacOSNotifier:
    """MacOSNotifier tests — subprocess osascript."""

    def test_notify_info_runs_osascript(self) -> None:
        """notify(info) should call osascript without caution icon."""
        from sts2_autotest.core.notifier import MacOSNotifier

        notifier = MacOSNotifier()
        with mock.patch("subprocess.run") as mock_run:
            notifier.notify("Test Title", "Test Message", "info")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "osascript"
            assert "-e" in args
            script = args[args.index("-e") + 1]
            assert "display notification" in script
            assert "Test Message" in script
            assert 'with title "Test Title"' in script
            assert "caution" not in script

    def test_notify_warning_adds_caution_icon(self) -> None:
        """notify(warning) should include 'with icon caution'."""
        from sts2_autotest.core.notifier import MacOSNotifier

        notifier = MacOSNotifier()
        with mock.patch("subprocess.run") as mock_run:
            notifier.notify("T", "M", "warning")
            args = mock_run.call_args[0][0]
            script = args[args.index("-e") + 1]
            assert "with icon caution" in script

    def test_notify_handles_subprocess_error(self) -> None:
        """notify should catch subprocess errors and not raise."""
        from sts2_autotest.core.notifier import MacOSNotifier

        notifier = MacOSNotifier()
        with mock.patch("subprocess.run", side_effect=OSError("spawn failed")):
            # Should not raise — errors are caught and logged
            notifier.notify("T", "M", "info")

    def test_notify_handles_timeout(self) -> None:
        """notify should handle subprocess timeout gracefully."""
        import subprocess as sp

        from sts2_autotest.core.notifier import MacOSNotifier

        notifier = MacOSNotifier()
        with mock.patch("subprocess.run", side_effect=sp.TimeoutExpired("cmd", 5)):
            notifier.notify("T", "M", "info")  # should not raise

    def test_build_osascript_escapes_script_delimiters(self) -> None:
        """Quotes, backslashes, and newlines should stay inside string literals."""
        from sts2_autotest.core.notifier import _build_osascript

        script = _build_osascript(
            'Title "quoted" \\ path\nnext',
            'Message "quoted" \\ path\rnext',
            "warning",
        )

        assert '\\"quoted\\"' in script
        assert "\\\\ path" in script
        assert "\\nnext" in script
        assert "\\rnext" in script
        assert "\n" not in script
        assert "\r" not in script
        assert "with icon caution" in script


class TestWindowsNotifier:
    """WindowsNotifier tests — ctypes Shell_NotifyIconW.

    Note: ctypes.windll is only available on Windows, so we mock it at
    the module level.  patch('ctypes') lets us inject a fake windll
    without accessing the real one.
    """

    @staticmethod
    def _build_windll_mock() -> mock.MagicMock:
        """Build a fake ctypes.windll for cross-platform testing."""
        windll = mock.MagicMock()
        windll.kernel32.GetConsoleWindow.return_value = 12345  # non-NULL handle
        windll.user32.LoadIconW.return_value = 67890
        return windll

    def test_notify_info_sets_info_flag(self) -> None:
        """notify(info) should add then update the tray icon and defer cleanup."""
        from sts2_autotest.core.notifier import WindowsNotifier

        notifier = WindowsNotifier()
        windll = self._build_windll_mock()
        with (
            mock.patch("ctypes.windll", windll, create=True),
            mock.patch("threading.Timer") as timer_cls,
        ):
            notifier.notify("Title", "Message", "info")
            calls = windll.shell32.Shell_NotifyIconW.call_args_list
            assert calls[0][0][0] == 0
            assert calls[1][0][0] == 1
            timer_cls.assert_called_once()
            timer_cls.return_value.start.assert_called_once()

    def test_notify_warning_sets_warning_flag(self) -> None:
        """notify(warning) should add then update the tray icon and defer cleanup."""
        from sts2_autotest.core.notifier import WindowsNotifier

        notifier = WindowsNotifier()
        windll = self._build_windll_mock()
        with (
            mock.patch("ctypes.windll", windll, create=True),
            mock.patch("threading.Timer") as timer_cls,
        ):
            notifier.notify("T", "M", "warning")
            calls = windll.shell32.Shell_NotifyIconW.call_args_list
            assert calls[0][0][0] == 0
            assert calls[1][0][0] == 1
            timer_cls.assert_called_once()
            timer_cls.return_value.start.assert_called_once()

    def test_notify_no_console_window_does_not_raise(self) -> None:
        """notify should log debug, not raise, when GetConsoleWindow returns NULL."""
        from sts2_autotest.core.notifier import WindowsNotifier

        notifier = WindowsNotifier()
        windll = mock.MagicMock()
        windll.kernel32.GetConsoleWindow.return_value = None
        with mock.patch("ctypes.windll", windll, create=True):
            notifier.notify("T", "M", "info")  # should log warning, not raise
            windll.shell32.Shell_NotifyIconW.assert_not_called()


class TestStubNotifier:
    """StubNotifier no-op behavior."""

    def test_notify_does_nothing(self) -> None:
        from sts2_autotest.core.notifier import StubNotifier

        stub = StubNotifier()
        stub.notify("any", "any", "info")
        # No exception, no side effects
