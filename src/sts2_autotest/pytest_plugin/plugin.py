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
import threading
from typing import Generator

import pytest

from sts2_autotest.pytest_plugin.fixtures import (
    _orchestrator,
    _session_loop,
    autotest,
    game_state,
)
from sts2_autotest.pytest_plugin.hooks import fire
from sts2_autotest.pytest_plugin.markers import MARKERS


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
    """Fire lifecycle hook at session end."""
    fire("session_end")


__all__ = [
    "autotest",
    "game_state",
    "_session_loop",
    "_orchestrator",
]
