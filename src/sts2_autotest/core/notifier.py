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
        if level not in ("info", "warning"):
            _logger.warning("Unknown notification level %r; defaulting to 'info'", level)
            level = "info"
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
    # Escape backslashes, double quotes, and newlines in title/message
    safe_title = title.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')
    safe_message = message.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')
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

        # Validate level
        if level not in ("info", "warning"):
            _logger.warning("Unknown notification level %r; defaulting to 'info'", level)
            level = "info"

        # Flags
        NIIF_INFO = 0x00000001
        NIIF_WARNING = 0x00000002
        NIF_INFO = 0x00000010
        NIM_ADD = 0x00000000
        NIM_DELETE = 0x00000002

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

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()  # type: ignore[attr-defined]
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
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))  # type: ignore[attr-defined]
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))  # type: ignore[attr-defined]
        except OSError as exc:
            _logger.warning("Windows notification failed: %s", exc)


def create_desktop_notifier() -> DesktopNotifier:
    """Create the platform-appropriate DesktopNotifier.

    Returns:
        - WindowsNotifier on Windows
        - MacOSNotifier on macOS
        - StubNotifier on all other platforms

    The caller does not need to know which implementation is returned -
    all satisfy the DesktopNotifier Protocol.
    """
    system = platform.system()
    if system == "Windows":
        return WindowsNotifier()
    if system == "Darwin":
        return MacOSNotifier()
    _logger.debug("No native notifier for platform %s; using stub", system)
    return StubNotifier()
