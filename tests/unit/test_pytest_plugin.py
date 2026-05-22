"""Tests for pytest_plugin — fixtures, hooks, and marker behavior."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from sts2_autotest.pytest_plugin.fixtures import SessionInitError, UserError
from sts2_autotest.pytest_plugin.hooks import HookRegistry, clear, fire, register


class TestUserError:
    """UserError provides clear guidance for wrong test patterns."""

    def test_user_error_is_exception(self) -> None:
        err = UserError("Something went wrong")
        assert isinstance(err, Exception)
        assert str(err) == "Something went wrong"


class TestSessionInitError:
    """SessionInitError raised when session fails to start."""

    def test_session_init_error_is_exception(self) -> None:
        err = SessionInitError("start failed")
        assert isinstance(err, Exception)
        assert "start failed" in str(err)


class TestCustomMarkers:
    """Custom pytest markers are registered."""

    def test_markers_listed(self) -> None:
        from sts2_autotest.pytest_plugin.markers import MARKERS
        names = {m[0] for m in MARKERS}
        assert "sts2_state" in names
        assert "sts2_adapter" in names
        assert "sts2_timeout" in names


class TestPytestPlugin:
    """Plugin can be imported and markers register."""

    def test_plugin_imports(self) -> None:
        from sts2_autotest.pytest_plugin.plugin import autotest, game_state
        assert autotest is not None
        assert game_state is not None

    def test_session_init_error_raised_on_failure(self) -> None:
        """start_session returns False when health check fails."""
        mock_adapter = MagicMock()
        mock_adapter.health_check = AsyncMock(return_value=MagicMock(healthy=False))
        from sts2_autotest.core.orchestrator import TestOrchestrator
        orch = TestOrchestrator(adapter=mock_adapter)
        loop = asyncio.new_event_loop()
        ok = loop.run_until_complete(orch.start_session())
        loop.close()
        assert ok is False

    def test_teardown_timeout_constant(self) -> None:
        from sts2_autotest.pytest_plugin.fixtures import SESSION_TEARDOWN_TIMEOUT
        assert SESSION_TEARDOWN_TIMEOUT == 10.0


class TestLifecycleHooks:
    """Hook register/fire/clear lifecycle."""

    def setup_method(self) -> None:
        clear()

    def teardown_method(self) -> None:
        clear()

    def test_register_and_fire(self) -> None:
        calls: list[str] = []
        register("session_start", lambda: calls.append("started"))
        fire("session_start")
        assert calls == ["started"]

    def test_fire_with_kwargs(self) -> None:
        calls: list[dict] = []
        register("case_start", lambda **kw: calls.append(kw))
        fire("case_start", case_id="TC-001")
        assert calls == [{"case_id": "TC-001"}]

    def test_fire_unknown_hook_point_does_nothing(self) -> None:
        fire("nonexistent")  # should not raise

    def test_register_unknown_hook_point_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown hook point"):
            register("nonexistent", lambda: None)

    def test_clear_removes_all_hooks(self) -> None:
        register("session_start", lambda: None)
        register("session_end", lambda: None)
        clear()
        # Fire both — no callbacks should run
        fire("session_start")
        fire("session_end")

    def test_multiple_callbacks_per_hook(self) -> None:
        calls: list[str] = []
        register("session_start", lambda: calls.append("a"))
        register("session_start", lambda: calls.append("b"))
        fire("session_start")
        assert calls == ["a", "b"]

    def test_registry_instances_are_isolated(self) -> None:
        calls: list[str] = []
        first = HookRegistry()
        second = HookRegistry()

        first.register("session_start", lambda: calls.append("first"))
        second.fire("session_start")
        first.fire("session_start")

        assert calls == ["first"]
