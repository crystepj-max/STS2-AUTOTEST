# B13 Desktop Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send system desktop notifications when test sessions complete, with platform-native notifications on Windows 10+ and macOS.

**Architecture:** Add `DesktopNotifier` Protocol to `common/types.py`, platform implementations (Windows `ctypes` + macOS `osascript`) in `core/notifier.py`, `NotificationsConfig` in `config/schema.py`, and register a `session_end` hook callback in `pytest_plugin/plugin.py`. Zero new dependencies — pure `ctypes` (Windows) and `subprocess` (macOS).

**Tech Stack:** Python >=3.11, ctypes (Windows), osascript subprocess (macOS), pydantic (config), pytest hooks

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/sts2_autotest/common/types.py` | Modify (+8 lines) | Add `DesktopNotifier` Protocol |
| `src/sts2_autotest/config/schema.py` | Modify (+18 lines) | Add `NotificationsConfig`, integrate into `STS2Config` |
| `src/sts2_autotest/core/notifier.py` | Create (~110 lines) | `WindowsNotifier`, `MacOSNotifier`, `StubNotifier`, `create_desktop_notifier()` |
| `src/sts2_autotest/pytest_plugin/plugin.py` | Modify (+55 lines) | Pass exitstatus in `pytest_sessionfinish`, register callback in `pytest_configure` |
| `tests/unit/test_notifier.py` | Create (~130 lines) | Tests for all notifier implementations + factory |
| `tests/unit/test_notifications_config.py` | Create (~70 lines) | Tests for `NotificationsConfig` defaults, env var overrides, STS2Config integration |
| `tests/unit/test_plugin.py` | Create (~60 lines) | Tests for hook registration, exitstatus passthrough, CI disable |

---

### Task 1: DesktopNotifier Protocol (common/types.py)

**Files:**
- Modify: `src/sts2_autotest/common/types.py` (append after line 191)
- Create: `tests/unit/test_notifier.py` (StubNotifier tests)

- [ ] **Step 1: Add DesktopNotifier Protocol to common/types.py**

Append after the last class in `src/sts2_autotest/common/types.py` (after `PrecheckSettings` at line 191):

```python
class DesktopNotifier(Protocol):
    """Protocol for desktop notification — implemented by platform backends.

    level values: "info" | "warning". Platform implementations map
    these to native notification severity levels.
    """

    def notify(self, title: str, message: str, level: str) -> None: ...
```

- [ ] **Step 2: Write StubNotifier test in tests/unit/test_notifier.py**

```python
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
```

- [ ] **Step 3: Run test to verify it passes**

```bash
python -m pytest tests/unit/test_notifier.py::TestDesktopNotifierProtocol -v
```

Expected: 3 PASS (StubNotifier works, Protocol compliance verified)

- [ ] **Step 4: Commit**

```bash
git add src/sts2_autotest/common/types.py tests/unit/test_notifier.py
git commit -m "feat: add DesktopNotifier Protocol and StubNotifier tests"
```

---

### Task 2: NotificationsConfig (config/schema.py)

**Files:**
- Modify: `src/sts2_autotest/config/schema.py` (add class + integrate into STS2Config)
- Create: `tests/unit/test_notifications_config.py`

- [ ] **Step 1: Write failing config test in tests/unit/test_notifications_config.py**

```python
"""Tests for NotificationsConfig — defaults, validation, STS2Config integration."""

import os

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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/unit/test_notifications_config.py -v
```

Expected: FAIL — `NotificationsConfig` and `STS2Config.notifications` not yet defined

- [ ] **Step 3: Add NotificationsConfig to config/schema.py**

Add after `StateMachineConfig` (before `ServerConfig`, around line 103):

```python
class NotificationsConfig(BaseModel):
    """Desktop notification configuration (B13)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    on_success: bool = True
    on_failure: bool = True
    on_crash: bool = True
```

Then add to `STS2Config` — insert after the `state_machine` field:

```python
    notifications: NotificationsConfig = NotificationsConfig()
```

Full `STS2Config` fields become:
```python
class STS2Config(BaseModel):
    model_config = ConfigDict(frozen=True)

    framework: FrameworkConfig = FrameworkConfig()
    adapter: AdapterConfig = AdapterConfig()
    execution: ExecutionConfig = ExecutionConfig()
    state_machine: StateMachineConfig = StateMachineConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    server: ServerConfig = ServerConfig()
    workspace: WorkspaceConfigModel = WorkspaceConfigModel()
```

Also update the `__init__.py` / `__all__` export if `config/__init__.py` exists. If schema exports are re-exported from `config/__init__.py`, add `NotificationsConfig` to the imports.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_notifications_config.py -v
```

Expected: 4 PASS

- [ ] **Step 5: Run existing config tests to verify no regression**

```bash
python -m pytest tests/unit/test_config_schema.py -v
```

Expected: all existing tests still PASS (STS2Config model_validator for mutual exclusion still works with the new field)

- [ ] **Step 6: Commit**

```bash
git add src/sts2_autotest/config/schema.py tests/unit/test_notifications_config.py
git commit -m "feat: add NotificationsConfig with defaults and STS2Config integration"
```

---

### Task 3: Platform Notifier Implementations (core/notifier.py)

**Files:**
- Create: `src/sts2_autotest/core/notifier.py`
- Modify: `tests/unit/test_notifier.py` (add platform implementation tests)

- [ ] **Step 1: Write tests for all notifier implementations**

Append to `tests/unit/test_notifier.py`:

```python
class TestCreateDesktopNotifier:
    """Factory function tests."""

    def test_returns_stub_on_unknown_platform(self) -> None:
        """create_desktop_notifier should return StubNotifier on unsupported platforms."""
        from sts2_autotest.core.notifier import create_desktop_notifier

        with mock.patch("platform.system", return_value="FreeBSD"):
            notifier = create_desktop_notifier()
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


class TestWindowsNotifier:
    """WindowsNotifier tests — ctypes Shell_NotifyIconW."""

    def test_notify_info_sets_info_flag(self) -> None:
        """notify(info) should use NIIF_INFO flag (0x1)."""
        from sts2_autotest.core.notifier import WindowsNotifier

        notifier = WindowsNotifier()
        with mock.patch("ctypes.windll.shell32.Shell_NotifyIconW") as mock_shell:
            notifier.notify("Title", "Message", "info")
            mock_shell.assert_called_once()

    def test_notify_warning_sets_warning_flag(self) -> None:
        """notify(warning) should use NIIF_WARNING flag (0x2)."""
        from sts2_autotest.core.notifier import WindowsNotifier

        notifier = WindowsNotifier()
        with mock.patch("ctypes.windll.shell32.Shell_NotifyIconW") as mock_shell:
            notifier.notify("T", "M", "warning")
            mock_shell.assert_called_once()

    def test_notify_no_console_window_does_not_raise(self) -> None:
        """notify should handle GetConsoleWindow returning NULL gracefully."""
        from sts2_autotest.core.notifier import WindowsNotifier

        notifier = WindowsNotifier()
        with mock.patch("ctypes.windll.kernel32.GetConsoleWindow", return_value=None):
            notifier.notify("T", "M", "info")  # should log warning, not raise


class TestStubNotifier:
    """StubNotifier no-op behavior."""

    def test_notify_does_nothing(self) -> None:
        from sts2_autotest.core.notifier import StubNotifier

        stub = StubNotifier()
        stub.notify("any", "any", "info")
        # No exception, no side effects
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_notifier.py::TestCreateDesktopNotifier tests/unit/test_notifier.py::TestMacOSNotifier tests/unit/test_notifier.py::TestWindowsNotifier tests/unit/test_notifier.py::TestStubNotifier -v
```

Expected: FAIL — `core/notifier.py` not yet created

- [ ] **Step 3: Create core/notifier.py**

```python
"""Platform-specific desktop notification backends (B13).

Uses zero-dependency approaches:
- Windows: ctypes -> Shell_NotifyIconW (balloon notification via console window)
- macOS: subprocess -> osascript (Notification Center)
- Others: StubNotifier (no-op)

All implementations satisfy the DesktopNotifier Protocol from common/types.py.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sts2_autotest.common.types import DesktopNotifier

_logger = logging.getLogger("sts2_autotest.core.notifier")

OSASCRIPT_TIMEOUT = 5


class StubNotifier:
    """No-op notifier for unsupported platforms and CI environments."""

    def notify(self, title: str, message: str, level: str) -> None:
        pass


class MacOSNotifier:
    """Send notifications via macOS Notification Center using osascript."""

    def notify(self, title: str, message: str, level: str) -> None:
        script = _build_osascript(title, message, level)
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                timeout=OSASCRIPT_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _logger.warning("macOS notification failed: %s", exc)


def _build_osascript(title: str, message: str, level: str) -> str:
    """Build an AppleScript display notification command."""
    # Escape double quotes in title/message
    safe_title = title.replace('"', '\\"')
    safe_message = message.replace('"', '\\"')
    parts = [f'display notification "{safe_message}" with title "{safe_title}"']
    if level == "warning":
        parts.append("with icon caution")
    return " ".join(parts)


class WindowsNotifier:
    """Send balloon notifications via Windows Shell_NotifyIconW (ctypes).

    Associates the notification with the console window handle.
    Falls back to no-op if no console window is available (e.g., running
    from an IDE without a terminal).
    """

    def notify(self, title: str, message: str, level: str) -> None:
        import ctypes
        from ctypes import wintypes

        # Flags
        NIIF_INFO = 0x00000001
        NIIF_WARNING = 0x00000002
        NIF_INFO = 0x00000010
        NIM_MODIFY = 0x00000001

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uTimeout", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
            ]

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            _logger.debug("No console window handle; skipping notification")
            return

        flags = NIIF_INFO if level == "info" else NIIF_WARNING

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = title
        nid.szInfo = message
        nid.dwInfoFlags = flags

        try:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except OSError as exc:
            _logger.warning("Windows notification failed: %s", exc)


def create_desktop_notifier() -> DesktopNotifier:
    """Create the platform-appropriate DesktopNotifier.

    Returns:
        - WindowsNotifier on Windows
        - MacOSNotifier on macOS
        - StubNotifier on all other platforms

    The caller does not need to know which implementation is returned —
    all satisfy the DesktopNotifier Protocol.
    """
    system = platform.system()
    if system == "Windows":
        return WindowsNotifier()  # type: ignore[return-value]
    if system == "Darwin":
        return MacOSNotifier()  # type: ignore[return-value]
    _logger.debug("No native notifier for platform %s; using stub", system)
    return StubNotifier()  # type: ignore[return-value]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_notifier.py -v
```

Expected: 12 PASS (3 Protocol + 3 factory + 3 macOS + 3 Windows + 1 Stub)

Note: The Windows tests that mock `ctypes.windll` may fail on macOS/Linux if `ctypes.windll` doesn't exist. See Step 4b for handling.

- [ ] **Step 4b: Handle cross-platform test compatibility**

If `ctypes.windll` is not available on the test platform (macOS/Linux), the `TestWindowsNotifier` tests will fail with an import error inside the notifier. To handle this, the factory test for Windows already uses `mock.patch("platform.system")`. The `TestWindowsNotifier` tests also mock `Shell_NotifyIconW`, so the actual `ctypes.windll` import only happens inside `notify()`, which is mocked.

However, the import statement `import ctypes` still runs. To ensure tests work on all platforms, mark Windows-specific tests with `@pytest.mark.skipif`:

Add this import and decorator to the `TestWindowsNotifier` class:

```python
class TestWindowsNotifier:
    """WindowsNotifier tests — ctypes Shell_NotifyIconW."""

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only implementation")
    def test_notify_info_sets_info_flag(self) -> None:
        ...
```

But actually, since we're mocking `ctypes.windll`, the tests should work on any platform because `ctypes` itself is available everywhere (it's part of stdlib). The `ctypes.windll` access only happens inside the mocked method. Let me verify: `ctypes.windll.shell32.Shell_NotifyIconW` — on macOS, `ctypes.windll` exists but accessing `.shell32` would fail. But we're mocking the whole path, so it's fine.

The issue is: inside `WindowsNotifier.notify()`, before the mock can intercept, the code does `import ctypes` and `from ctypes import wintypes` and `class NOTIFYICONDATAW(ctypes.Structure)`. This imports should work on all platforms since `ctypes` is stdlib.

The only problematic line is `ctypes.windll.kernel32.GetConsoleWindow()` and `ctypes.windll.shell32.Shell_NotifyIconW()`, both of which we mock.

So the tests should work as-is on macOS too. Run them to confirm:

```bash
python -m pytest tests/unit/test_notifier.py -v
```

If any Windows tests fail on macOS due to `ctypes.windll` access during import (not during test), wrap the Windows-specific tests with `@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only")`.

- [ ] **Step 5: Verify the module is importable and satisfies the Protocol**

```bash
python -c "from sts2_autotest.core.notifier import create_desktop_notifier; n = create_desktop_notifier(); n.notify('test', 'msg', 'info'); print('OK:', type(n).__name__)"
```

Expected: `OK: MacOSNotifier` (on macOS) or `OK: StubNotifier` (on Linux) or `OK: WindowsNotifier` (on Windows)

- [ ] **Step 6: Commit**

```bash
git add src/sts2_autotest/core/notifier.py tests/unit/test_notifier.py
git commit -m "feat: add platform notifier implementations (Windows/MacOS/Stub)"
```

---

### Task 4: pytest Plugin Integration (pytest_plugin/plugin.py)

**Files:**
- Modify: `src/sts2_autotest/pytest_plugin/plugin.py`
- Create: `tests/unit/test_plugin.py`

- [ ] **Step 1: Write tests for plugin integration**

Create `tests/unit/test_plugin.py`:

```python
"""Tests for pytest_plugin/plugin.py — hook integration and notification callback."""

import os
from unittest import mock

import pytest


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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_plugin.py -v
```

Expected: FAIL — `_register_notification_callback` and `_on_session_end_notify` not yet defined in plugin.py

- [ ] **Step 3: Modify pytest_sessionfinish to pass exitstatus**

In `src/sts2_autotest/pytest_plugin/plugin.py`, change line 102:

```python
# Old:
    fire("session_end")

# New:
    fire("session_end", exitstatus=exitstatus)
```

- [ ] **Step 4: Add notification callback and registration to plugin.py**

In `src/sts2_autotest/pytest_plugin/plugin.py`:

Add imports at the top (after existing imports):

```python
import os
import json
import platform
from pathlib import Path
```

Add these functions before the `pytest_addoption` function (after the `import` block):

```python
def _load_notifications_config() -> dict[str, bool]:
    """Read notification settings from environment variables.

    Returns a dict with keys: enabled, on_success, on_failure, on_crash.
    Uses STS2_NOTIFICATIONS__ prefix convention.
    """
    return {
        "enabled": os.environ.get("STS2_NOTIFICATIONS__ENABLED", "true").lower()
        in ("true", "1", "yes"),
        "on_success": os.environ.get("STS2_NOTIFICATIONS__ON_SUCCESS", "true").lower()
        in ("true", "1", "yes"),
        "on_failure": os.environ.get("STS2_NOTIFICATIONS__ON_FAILURE", "true").lower()
        in ("true", "1", "yes"),
        "on_crash": os.environ.get("STS2_NOTIFICATIONS__ON_CRASH", "true").lower()
        in ("true", "1", "yes"),
    }


def _read_latest_summary() -> dict | None:
    """Attempt to read the latest summary.json from the evidence directory.

    Returns the parsed JSON dict, or None if unavailable.
    """
    evidence_dir = os.environ.get("STS2_FRAMEWORK__EVIDENCE_DIR", "tests/output")
    base = Path(evidence_dir)
    # Try latest/ first
    summary_path = base / "latest" / "summary.json"
    if not summary_path.exists():
        # Scan for any run directory with summary.json (newest first)
        if base.exists():
            dirs = sorted(
                (d for d in base.iterdir() if d.is_dir()),
                key=lambda d: d.stat().st_mtime,
                reverse=True,
            )
            for run_dir in dirs:
                candidate = run_dir / "summary.json"
                if candidate.exists():
                    summary_path = candidate
                    break
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _format_duration(duration_ms: int) -> str:
    """Format a millisecond duration into a human-readable string."""
    if duration_ms < 60_000:
        return "<1 分钟"
    total_minutes = duration_ms // 60_000
    if total_minutes < 60:
        return f"{total_minutes} 分钟"
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if minutes == 0:
        return f"{hours} 小时"
    return f"{hours} 小时 {minutes} 分钟"


def _build_notification_message(exitstatus: int) -> tuple[str, str]:
    """Build notification title and message from exitstatus and summary data.

    Returns (title, message). If summary.json is unavailable, uses
    a simple fallback message based on exit code.
    """
    title = "STS2 Autotest — 测试完成"

    summary = _read_latest_summary()
    if summary is not None:
        try:
            test_run = summary.get("test_run", {})
            passed = test_run.get("passed", "?")
            failed = test_run.get("failed", "?")
            crashed = test_run.get("crashed", "?")
            duration_ms_val = test_run.get("duration_ms", 0)
        except (AttributeError, KeyError):
            passed = failed = crashed = "?"
            duration_ms_val = 0

        emoji = "✅" if exitstatus == 0 else "⚠️"
        duration_str = _format_duration(int(duration_ms_val))
        message = (
            f"{emoji} 测试会话完成"
            f"（通过: {passed}, 失败: {failed}, 崩溃: {crashed}）"
            f" — {duration_str}"
        )
    else:
        # Fallback: no summary.json available
        emoji = "✅" if exitstatus == 0 else "⚠️"
        message = f"{emoji} 测试会话完成 (exit code: {exitstatus})"

    return title, message


def _on_session_end_notify(
    exitstatus: int,
    notifier: object | None = None,
) -> None:
    """session_end hook callback: send desktop notification.

    Args:
        exitstatus: pytest exit code (0 = all passed).
        notifier: DesktopNotifier instance. If None, creates one.
    """
    cfg = _load_notifications_config()
    if not cfg["enabled"]:
        return

    # Determine if we should notify based on exit status
    if exitstatus == 0:
        if not cfg["on_success"]:
            return
        level = "info"
    else:
        # exitstatus 2 (INTERRUPTED) = crash, 3 (INTERNAL_ERROR) = crash
        if exitstatus in (2, 3) and not cfg["on_crash"]:
            return
        if exitstatus not in (2, 3) and not cfg["on_failure"]:
            return
        level = "warning"

    if notifier is None:
        from sts2_autotest.core.notifier import create_desktop_notifier
        notifier = create_desktop_notifier()

    title, message = _build_notification_message(exitstatus)
    notifier.notify(title=title, message=message, level=level)


def _register_notification_callback() -> None:
    """Register the session_end notification callback if enabled and not in CI."""
    # CI always suppresses notifications
    if os.environ.get("CI"):
        return

    cfg = _load_notifications_config()
    if not cfg["enabled"]:
        return

    from sts2_autotest.pytest_plugin.hooks import register
    from sts2_autotest.core.notifier import create_desktop_notifier

    notifier = create_desktop_notifier()

    def _callback(**kwargs: object) -> None:
        exitstatus = kwargs.get("exitstatus", 1)
        if isinstance(exitstatus, int):
            _on_session_end_notify(exitstatus, notifier)

    register("session_end", _callback)
```

Modify `pytest_configure` to call `_register_notification_callback()`:

```python
def pytest_configure(config: pytest.Config) -> None:
    for name, description in MARKERS:
        config.addinivalue_line("markers", f"{name}: {description}")
    _register_notification_callback()
```

- [ ] **Step 5: Run plugin tests to verify they pass**

```bash
python -m pytest tests/unit/test_plugin.py -v
```

Expected: 8 PASS

- [ ] **Step 6: Run existing tests to verify no regression**

```bash
python -m pytest tests/unit/ -v --ignore=tests/unit/test_notifier.py --ignore=tests/unit/test_notifications_config.py --ignore=tests/unit/test_plugin.py -x
```

Expected: all ~60+ existing tests still PASS

- [ ] **Step 7: Commit**

```bash
git add src/sts2_autotest/pytest_plugin/plugin.py tests/unit/test_plugin.py
git commit -m "feat: wire desktop notifications into session_end hook"
```

---

### Task 5: Final Verification

**Files:**
- No new files. Verification-only task.

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/unit/ -v
```

Expected: all tests PASS (~60 existing + ~24 new)

- [ ] **Step 2: Run type checking**

```bash
mypy src/sts2_autotest --strict
```

Expected: no new errors. If `DesktopNotifier` Protocol triggers new type errors (e.g., `[return-value]` on factory), add `# type: ignore[return-value]` annotations as needed.

- [ ] **Step 3: Run import linter**

```bash
lint-imports
```

Expected: PASS — `core/notifier.py` only imports from `common/` (Protocol) and stdlib. `pytest_plugin/plugin.py` imports from `core/` and `common/`, which follows the allowed import chain (`pytest_plugin` → `core` → `common`).

- [ ] **Step 4: Manual smoke test — verify notification appears**

On macOS:
```bash
python -c "
from sts2_autotest.core.notifier import MacOSNotifier
n = MacOSNotifier()
n.notify('STS2 Autotest — 测试完成', '✅ 测试会话完成（通过: 5, 失败: 0, 崩溃: 0）— <1 分钟', 'info')
"
```

On Windows:
```bash
python -c "from sts2_autotest.core.notifier import WindowsNotifier; n = WindowsNotifier(); n.notify('STS2 Autotest', 'test', 'info')"
```

Expected: System notification appears in Notification Center (macOS) or as a balloon tip (Windows). On unsupported platforms, no output (StubNotifier no-op).

- [ ] **Step 5: Update .env.example with new config keys**

Add to `STS2-AUTOTEST/.env.example` after the State Machine section:

```
# Notifications (B13)
# STS2_NOTIFICATIONS__ENABLED=true
# STS2_NOTIFICATIONS__ON_SUCCESS=true
# STS2_NOTIFICATIONS__ON_FAILURE=true
# STS2_NOTIFICATIONS__ON_CRASH=true
```

- [ ] **Step 6: Final commit**

```bash
git add .env.example
git commit -m "docs: add B13 notification env vars to .env.example"
```
