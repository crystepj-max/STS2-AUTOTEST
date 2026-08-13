"""pytest plugin for STS2-AUTOTEST — session management and async bridge (FR16, FR51).

Hooks:
- pytest_addoption: register custom CLI flags
- pytest_configure: register custom markers
- pytest_collection_modifyitems: filter by sts2_state marker
- pytest_runtest_setup: skip if sts2_adapter required and adapter unavailable
- pytest_runtest_call: apply sts2_timeout per-case timeout
- pytest_sessionstart / pytest_sessionfinish: fire lifecycle hooks
"""

import _thread
import json
import os
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

import pytest

from sts2_autotest.common.types import DesktopNotifier
from sts2_autotest.pytest_plugin.fixtures import (
    _orchestrator,
    _session_loop,
    autotest,
    game_state,
)
from sts2_autotest.pytest_plugin.hooks import fire
from sts2_autotest.pytest_plugin.markers import MARKERS


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


def _read_latest_summary() -> dict[str, Any] | None:
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
            try:
                dirs = sorted(
                    (d for d in base.iterdir() if d.is_dir()),
                    key=lambda d: d.stat().st_mtime,
                    reverse=True,
                )
            except OSError:
                return None
            for run_dir in dirs:
                candidate = run_dir / "summary.json"
                if candidate.exists():
                    summary_path = candidate
                    break
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return cast(dict[str, Any], data)


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
    notifier: DesktopNotifier | None = None,
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

    def _callback(**kwargs: object) -> None:
        exitstatus = kwargs.get("exitstatus", 1)
        if isinstance(exitstatus, int):
            try:
                from sts2_autotest.core.notifier import create_desktop_notifier

                notifier = create_desktop_notifier()
            except Exception:
                import logging

                _logger = logging.getLogger("sts2_autotest.pytest_plugin")
                _logger.warning("Failed to create desktop notifier", exc_info=True)
                return
            _on_session_end_notify(exitstatus, notifier)

    register("session_end", _callback)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--sts2-adapter-available",
        action="store_true",
        default=False,
        help="Mark adapter as available (skip sts2_adapter-marked tests otherwise)",
    )


def pytest_configure(config: pytest.Config) -> None:
    for name, description in MARKERS:
        config.addinivalue_line("markers", f"{name}: {description}")
    _register_notification_callback()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Filter collected items based on sts2_state marker requirements."""
    adapter_available = config.getoption("--sts2-adapter-available", default=False)
    for item in items:
        state_marker = item.get_closest_marker("sts2_state")
        if state_marker:
            # sts2_state requires a positional arg naming the expected state
            if state_marker.args:
                item.user_properties.append(
                    ("sts2_expected_state", state_marker.args[0])
                )

        adapter_marker = item.get_closest_marker("sts2_adapter")
        if adapter_marker and not adapter_available:
            skip_reason = adapter_marker.kwargs.get("reason", "Adapter not available")
            item.add_marker(pytest.mark.skip(reason=skip_reason))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """Apply per-case timeout from sts2_timeout marker.

    Uses threading.Thread + interrupt_main() to interrupt hanging tests.
    On Windows, this raises KeyboardInterrupt in the main thread.
    """
    timeout_marker = item.get_closest_marker("sts2_timeout")
    if not timeout_marker:
        yield
        return

    timeout_sec = timeout_marker.args[0] if timeout_marker.args else 30.0

    test_finished = threading.Event()

    def _timeout_guard() -> None:
        if not test_finished.wait(timeout=timeout_sec):
            _thread.interrupt_main()

    guard = threading.Thread(target=_timeout_guard, daemon=True)
    guard.start()
    try:
        yield
    except KeyboardInterrupt:
        raise TimeoutError(
            f"Test {item.nodeid} exceeded {timeout_sec}s timeout"
        )
    finally:
        test_finished.set()
        guard.join(timeout=0.1)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Fire lifecycle hook at session start."""
    fire("session_start")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fire lifecycle hook at session end with exit status."""
    fire("session_end", exitstatus=exitstatus)


__all__ = [
    "_orchestrator",
    "_session_loop",
    "autotest",
    "game_state",
]
