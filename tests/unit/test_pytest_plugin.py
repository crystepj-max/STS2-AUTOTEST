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

    def test_session_init_error_message_mentions_external_launch(self) -> None:
        from sts2_autotest.pytest_plugin.fixtures import _session_init_error_message

        message = _session_init_error_message()

        assert "2868840" in message
        assert "sts2" in message and "ping" in message

    def test_bootstrap_runtime_starts_steam_and_game(self, monkeypatch) -> None:
        from sts2_autotest.pytest_plugin import fixtures as fixtures_module

        calls: list[object] = []

        class FakeSteamController:
            def __init__(
                self,
                *,
                startup_timeout: float,
                game_dir: str | None,
                steam_exe: str,
            ) -> None:
                calls.append(("init", startup_timeout, game_dir, steam_exe))

            def start_steam(self) -> int:
                calls.append("start_steam")
                return 1

            def start_game(self, *, reuse_existing: bool = False) -> int:
                calls.append(("start_game", reuse_existing))
                return 2

        monkeypatch.setattr(fixtures_module, "_find_game_dir_for_bootstrap", lambda: "D:/Games/STS2")
        monkeypatch.setattr(fixtures_module, "_find_steam_exe_for_bootstrap", lambda: "C:/Steam/steam.exe")
        monkeypatch.setattr(fixtures_module, "SteamController", FakeSteamController)

        assert fixtures_module._bootstrap_runtime() is True
        assert calls == [
            ("init", 60.0, "D:/Games/STS2", "C:/Steam/steam.exe"),
            "start_steam",
            ("start_game", True),
        ]

    def test_start_orchestrator_session_retries_after_bootstrap(self, monkeypatch) -> None:
        from sts2_autotest.pytest_plugin import fixtures as fixtures_module

        orch = MagicMock()
        orch.start_session = AsyncMock(side_effect=[False, True])
        adapter = MagicMock()
        adapter.cleanup = AsyncMock()
        loop = asyncio.new_event_loop()
        try:
            monkeypatch.setattr(fixtures_module, "_bootstrap_runtime", lambda: True)
            monkeypatch.setattr(fixtures_module, "_wait_for_adapter_ready", lambda _loop, _adapter: True)
            ok = fixtures_module._start_orchestrator_session(loop, orch, adapter)
        finally:
            loop.close()

        assert ok is True
        assert orch.start_session.await_count == 2
        adapter.cleanup.assert_awaited_once()

    def test_agent_start_orchestrator_session_bootstraps_steam_when_not_ready(self, monkeypatch) -> None:
        from sts2_autotest.pytest_plugin import fixtures as fixtures_module

        calls: list[str] = []
        orch = MagicMock()
        orch.start_session = AsyncMock(side_effect=[False, True])
        adapter = MagicMock()
        adapter.cleanup = AsyncMock()
        loop = asyncio.new_event_loop()
        try:
            monkeypatch.setenv("STS2_ADAPTER__AGENT__ENABLED", "true")
            monkeypatch.setattr(
                fixtures_module,
                "_bootstrap_runtime",
                lambda: calls.append("bootstrap") or True,
            )
            monkeypatch.setattr(fixtures_module, "_wait_for_adapter_ready", lambda _loop, _adapter: True)

            ok = fixtures_module._start_orchestrator_session(loop, orch, adapter)
        finally:
            loop.close()

        assert ok is True
        assert calls == ["bootstrap"]
        assert orch.start_session.await_count == 2
        adapter.cleanup.assert_awaited_once()

    def test_start_orchestrator_session_allows_external_start_when_bootstrap_fails(self, monkeypatch) -> None:
        from sts2_autotest.pytest_plugin import fixtures as fixtures_module

        orch = MagicMock()
        orch.start_session = AsyncMock(side_effect=[False, True])
        adapter = MagicMock()
        adapter.cleanup = AsyncMock()
        loop = asyncio.new_event_loop()
        try:
            monkeypatch.setattr(fixtures_module, "_bootstrap_runtime", lambda: False)
            monkeypatch.setattr(fixtures_module, "_wait_for_adapter_ready", lambda _loop, _adapter: True)
            ok = fixtures_module._start_orchestrator_session(loop, orch, adapter)
        finally:
            loop.close()

        assert ok is True
        assert orch.start_session.await_count == 2
        adapter.cleanup.assert_awaited_once()

    def test_start_orchestrator_session_returns_false_when_adapter_never_ready(self, monkeypatch) -> None:
        from sts2_autotest.pytest_plugin import fixtures as fixtures_module

        orch = MagicMock()
        orch.start_session = AsyncMock(return_value=False)
        adapter = MagicMock()
        adapter.cleanup = AsyncMock()
        loop = asyncio.new_event_loop()
        try:
            monkeypatch.setattr(fixtures_module, "_bootstrap_runtime", lambda: True)
            monkeypatch.setattr(fixtures_module, "_wait_for_adapter_ready", lambda _loop, _adapter: False)
            ok = fixtures_module._start_orchestrator_session(loop, orch, adapter)
        finally:
            loop.close()

        assert ok is False
        assert orch.start_session.await_count == 1
        adapter.cleanup.assert_awaited_once()

    def test_agent_adapter_factory_preserves_debug_actions(self, monkeypatch) -> None:
        from sts2_autotest.adapters.agent import AgentAdapter
        from sts2_autotest.pytest_plugin import fixtures as fixtures_module

        monkeypatch.setenv("STS2_ADAPTER__AGENT__ENABLED", "true")
        monkeypatch.setenv("STS2_ADAPTER__AGENT__ENDPOINT", "http://127.0.0.1:8080")
        monkeypatch.setenv("STS2_ADAPTER__AGENT__DEBUG_ACTIONS", "true")
        monkeypatch.setenv("STS2_ADAPTER__AGENT__TIMEOUT", "7")

        adapter = fixtures_module._create_adapter_factory()()

        assert isinstance(adapter, AgentAdapter)
        assert adapter.endpoint == "http://127.0.0.1:8080"
        assert adapter.debug_actions is True
        assert adapter.timeout == 7


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
