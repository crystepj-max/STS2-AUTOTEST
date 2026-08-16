"""Unit tests for core.lifecycle.GameLifecycleManager.

Covers:
- adversarial-state detectors (is_phantom_combat, travel_hang_expired)
- API readiness (is_api_up, wait_for_api, ensure_game_up)
- self-healing (relaunch_run) with mocked process + adapter

No real game process is launched; subprocess.Popen and psutil are mocked.
Async methods are driven via asyncio.run (no pytest-asyncio dependency).
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from sts2_autotest.core.lifecycle import GameLifecycleManager


class FakeState:
    def __init__(self, **kw):
        self._d = kw

    def model_dump(self):
        return self._d


class FakeAdapter:
    """Minimal async adapter stub.

    responses: list of state dicts returned by get_state in order (cycles last).
    down_first: how many initial get_state calls raise (simulate API-down).
    """

    def __init__(self, responses, down_first=0):
        self.endpoint = "http://127.0.0.1:8080"
        self.debug_actions = True
        self.responses = list(responses)
        self.down_first = down_first
        self._idx = 0
        self.calls = []

    def _next(self):
        if self.down_first > 0:
            self.down_first -= 1
            raise ConnectionError("refused")
        if self._idx < len(self.responses):
            s = self.responses[self._idx]
            self._idx += 1
        else:
            s = self.responses[-1]
        return s

    async def get_state(self):
        s = self._next()
        if isinstance(s, dict):
            return FakeState(**s)
        return s

    async def act(self, action, args=None):
        self.calls.append((action, args))
        return MagicMock(status="success")


async def _no_sleep(*a, **k):
    return None


def _make_proc(pid):
    proc = MagicMock()
    proc.pid = pid
    proc.poll.return_value = None
    return proc


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _no_sleep)


@pytest.fixture
def fake_popen(monkeypatch):
    procs = []

    def _popen(args, **kw):
        p = _make_proc(pid=1000 + len(procs))
        procs.append(p)
        return p

    monkeypatch.setattr("sts2_autotest.core.lifecycle.subprocess.Popen", _popen)
    monkeypatch.setattr(
        "sts2_autotest.core.lifecycle.psutil.Process",
        MagicMock(return_value=MagicMock(is_running=lambda: False)),
    )
    return procs


def _mgr(adapter):
    return GameLifecycleManager(
        adapter, game_exe="/tmp/fake_game", game_dir="/tmp/fake_dir"
    )


# ── static detectors ──────────────────────────────────────


def test_is_phantom_combat():
    assert (
        GameLifecycleManager.is_phantom_combat(
            {"screen": "COMBAT", "combat": None, "in_combat": False}
        )
        is True
    )
    # real combat object present -> not phantom
    assert (
        GameLifecycleManager.is_phantom_combat(
            {"screen": "COMBAT", "combat": {"x": 1}, "in_combat": False}
        )
        is False
    )
    # in_combat True -> not phantom
    assert (
        GameLifecycleManager.is_phantom_combat(
            {"screen": "COMBAT", "combat": None, "in_combat": True}
        )
        is False
    )
    assert GameLifecycleManager.is_phantom_combat({"screen": "MAP"}) is False
    # accepts GameState-like object
    assert (
        GameLifecycleManager.is_phantom_combat(
            FakeState(screen="COMBAT", combat=None, in_combat=False)
        )
        is True
    )


def test_travel_hang_expired():
    mgr = _mgr(FakeAdapter([{"screen": "MAP"}]))
    mgr.hang_threshold = 18.0
    st = {"screen": "MAP", "map": {"is_traveling": True, "available_nodes": []}}
    now = 1000.0
    assert mgr.travel_hang_expired(st, now - 5, now=now) is False
    assert mgr.travel_hang_expired(st, now - 20, now=now) is True
    # not traveling
    assert (
        mgr.travel_hang_expired(
            {"screen": "MAP", "map": {"is_traveling": False, "available_nodes": []}},
            now - 20,
            now=now,
        )
        is False
    )
    # nodes available -> not hung
    assert (
        mgr.travel_hang_expired(
            {"screen": "MAP", "map": {"is_traveling": True, "available_nodes": [1]}},
            now - 20,
            now=now,
        )
        is False
    )
    # wrong screen
    assert mgr.travel_hang_expired({"screen": "COMBAT"}, now - 20, now=now) is False
    # no start timestamp
    assert mgr.travel_hang_expired(st, None, now=now) is False


# ── API readiness ─────────────────────────────────────────


def test_is_api_up_and_wait(no_sleep, fake_popen):
    adapter = FakeAdapter([{"screen": "MAIN_MENU"}])
    mgr = _mgr(adapter)
    assert asyncio.run(mgr.is_api_up()) is True
    assert asyncio.run(mgr.wait_for_api(timeout=2.0)) is True


def test_is_api_down(no_sleep, fake_popen):
    adapter = FakeAdapter([], down_first=1000)
    mgr = _mgr(adapter)
    assert asyncio.run(mgr.is_api_up()) is False
    assert asyncio.run(mgr.wait_for_api(timeout=0.05)) is False


def test_ensure_game_up_launches_when_down(no_sleep, monkeypatch):
    """游戏不可控时 ensure_game_up 恰好启动一次。

    与启动机制无关（macOS 经 open、其他平台经 Popen）——直接拦截 launch()
    计数；旧版用 fake_popen 断言，与 macOS 启动方式不一致（基线失败）。
    """
    adapter = FakeAdapter([], down_first=0)
    launched = {"count": 0}

    async def _down_until_launch():
        if launched["count"] == 0:
            raise ConnectionError("refused")
        return FakeState(screen="MAIN_MENU")

    adapter.get_state = _down_until_launch  # type: ignore[method-assign]
    mgr = _mgr(adapter)

    def _launch():
        launched["count"] += 1
        return 1234

    monkeypatch.setattr(mgr, "launch", _launch)
    ok = asyncio.run(mgr.ensure_game_up(api_timeout=0.05))
    assert ok is True
    assert launched["count"] == 1


def test_relaunch_run(no_sleep, fake_popen):
    adapter = FakeAdapter(
        [
            {"screen": "MAIN_MENU"},
            {"screen": "CHARACTER_SELECT"},
            {"screen": "MAP"},
        ],
        down_first=1,
    )
    mgr = _mgr(adapter)
    ok = asyncio.run(mgr.relaunch_run(api_timeout=2.0))
    assert ok is True
    assert mgr.relaunch_count == 1
    assert ("set_hp", {"value": 0}) in adapter.calls
    assert ("start_new_run", None) in adapter.calls
    assert any(c[0] == "select_character" for c in adapter.calls)
    assert any(c[0] == "embark" for c in adapter.calls)
    assert len(fake_popen) == 1  # single launch inside relaunch_run


def test_relaunch_run_respects_max_cap(no_sleep, fake_popen):
    adapter = FakeAdapter([{"screen": "MAP"}], down_first=1)
    mgr = _mgr(adapter)
    mgr.max_relaunches = 0
    ok = asyncio.run(mgr.relaunch_run(api_timeout=1.0))
    assert ok is False
    assert mgr.relaunch_count == 0
    assert len(fake_popen) == 0


# ── pre-run readiness + bounded auto-recovery (P1 fix one) ──

from sts2_autotest.common.errors import EnvironmentBlockReason  # noqa: E402


class _ProbeAdapter:
    """Adapter whose health/state/actions are individually controllable."""

    def __init__(self, *, health=True, state=None, actions=None):
        self.endpoint = "http://127.0.0.1:8080"
        self.debug_actions = True
        self._health = health
        self._state = state if state is not None else {"screen": "MAIN_MENU"}
        self._actions = actions if actions is not None else ["start_new_run"]

    async def health_check(self):
        if isinstance(self._health, Exception):
            raise self._health
        return MagicMock(healthy=self._health)

    async def get_state(self):
        if isinstance(self._state, Exception):
            raise self._state
        return FakeState(**self._state)

    async def get_available_actions(self):
        if isinstance(self._actions, Exception):
            raise self._actions
        return self._actions


def _probe(mgr):
    return asyncio.run(mgr._probe_ready())


class TestProbeReady:
    def test_all_good_is_ready(self):
        mgr = _mgr(_ProbeAdapter(state={"screen": "MAP"}))
        ok, reason, checks = _probe(mgr)
        assert ok is True and reason is None
        assert checks["health"] and checks["state"] and checks["actions"]
        assert checks["screen"] == "MAP"

    def test_health_refused_is_control_unavailable(self):
        mgr = _mgr(_ProbeAdapter(health=ConnectionError("refused")))
        ok, reason, _ = _probe(mgr)
        assert ok is False
        assert reason == EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE

    def test_state_refused_is_control_unavailable(self):
        mgr = _mgr(_ProbeAdapter(state=ConnectionError("refused")))
        ok, reason, _ = _probe(mgr)
        assert reason == EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE

    def test_actions_refused_is_control_unavailable(self):
        mgr = _mgr(_ProbeAdapter(actions=ConnectionError("refused")))
        ok, reason, _ = _probe(mgr)
        assert reason == EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE

    def test_unknown_screen_is_stale(self):
        mgr = _mgr(_ProbeAdapter(state={"screen": "UNKNOWN"}))
        ok, reason, _ = _probe(mgr)
        assert ok is False
        assert reason == EnvironmentBlockReason.GAME_PROCESS_STALE

    def test_main_menu_empty_actions_is_not_ready(self):
        """V11 实测：主菜单动作列表为空 = 控制模组仍在加载，不算真就绪。"""
        mgr = _mgr(_ProbeAdapter(state={"screen": "MAIN_MENU"}, actions=[]))
        ok, reason, checks = _probe(mgr)
        assert ok is False
        assert reason == EnvironmentBlockReason.GAME_PROCESS_STALE
        assert checks["actions"] is False

    def test_non_menu_screen_tolerates_empty_actions(self):
        """非主菜单页面（如过场）动作可为空，不影响就绪判定。"""
        mgr = _mgr(_ProbeAdapter(state={"screen": "MAP"}, actions=[]))
        ok, reason, _ = _probe(mgr)
        assert ok is True and reason is None


def _scripted_probe(results):
    """Return an async _probe_ready that yields queued (ok, reason, checks)."""
    seq = list(results)

    async def _p():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return _p


class TestEnsureEnvironmentReady:
    def _mgr_with(self, monkeypatch, *, probe_results, api_up=True,
                 process_present=False, port_released=True):
        mgr = _mgr(_ProbeAdapter())
        monkeypatch.setattr(mgr, "_probe_ready", _scripted_probe(probe_results))
        monkeypatch.setattr(mgr, "_game_process_present", lambda: process_present)
        monkeypatch.setattr(mgr, "_wait_port_released", lambda *a, **k: port_released)
        monkeypatch.setattr(
            "sts2_autotest.core.lifecycle._PROBE_RETRY_GAP_SECONDS", 0
        )

        async def _wait(*a, **k):
            return api_up
        monkeypatch.setattr(mgr, "wait_for_api", _wait)
        mgr._launch_calls = 0
        mgr._term_calls = 0

        def _launch():
            mgr._launch_calls += 1
            return 1234

        def _term():
            mgr._term_calls += 1

        monkeypatch.setattr(mgr, "launch", _launch)
        monkeypatch.setattr(mgr, "terminate", _term)
        return mgr

    def test_ready_does_not_launch(self, monkeypatch):
        good = (True, None, {"health": True, "state": True, "actions": True, "screen": "MAP"})
        mgr = self._mgr_with(monkeypatch, probe_results=[good])
        res = asyncio.run(mgr.ensure_environment_ready())
        assert res.ready is True and res.recovered is False
        assert mgr._launch_calls == 0
        assert res.pre_control_ready is True

    def test_transient_probe_failure_recovers_without_relaunch(self, monkeypatch):
        """V11 实测：单次探测抖动（如模组瞬时未就绪）不得触发破坏性重启。"""
        bad = (False, EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE, {})
        good = (True, None, {"health": True, "state": True, "actions": True, "screen": "MAP"})
        mgr = self._mgr_with(monkeypatch, probe_results=[bad, good])
        res = asyncio.run(mgr.ensure_environment_ready())
        assert res.ready is True and res.recovered is False
        assert mgr._launch_calls == 0
        assert mgr._term_calls == 0

    def test_launch_once_when_no_process(self, monkeypatch):
        bad = (False, EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE, {})
        good = (True, None, {"screen": "MAIN_MENU"})
        mgr = self._mgr_with(
            monkeypatch, probe_results=[bad, bad, bad, good], process_present=False
        )
        res = asyncio.run(mgr.ensure_environment_ready())
        assert res.ready is True and res.recovered is True
        assert mgr._launch_calls == 1
        assert mgr._term_calls == 0
        assert "launch" in res.actions_taken

    def test_stale_process_terminates_then_launches(self, monkeypatch):
        bad = (False, EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE, {})
        good = (True, None, {"screen": "MAIN_MENU"})
        mgr = self._mgr_with(
            monkeypatch, probe_results=[bad, bad, bad, good], process_present=True
        )
        res = asyncio.run(mgr.ensure_environment_ready())
        assert res.ready is True
        assert mgr._term_calls == 1
        assert mgr._launch_calls == 1
        assert res.actions_taken.index("controlled_terminate") < res.actions_taken.index("launch")

    def test_launched_but_state_unreadable_is_not_ready(self, monkeypatch):
        bad = (False, EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE, {})
        stale = (False, EnvironmentBlockReason.GAME_PROCESS_STALE, {"screen": "UNKNOWN"})
        mgr = self._mgr_with(monkeypatch, probe_results=[bad, stale], process_present=False)
        res = asyncio.run(mgr.ensure_environment_ready())
        assert res.ready is False
        assert res.reason is not None

    def test_api_never_up_returns_blocked(self, monkeypatch):
        bad = (False, EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE, {})
        mgr = self._mgr_with(monkeypatch, probe_results=[bad, bad], api_up=False, process_present=False)
        res = asyncio.run(mgr.ensure_environment_ready())
        assert res.ready is False
        assert res.reason == EnvironmentBlockReason.GAME_READINESS_TIMEOUT

    def test_only_one_recovery_attempt(self, monkeypatch):
        bad = (False, EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE, {})
        still_bad = (False, EnvironmentBlockReason.GAME_PROCESS_STALE, {"screen": "UNKNOWN"})
        mgr = self._mgr_with(monkeypatch, probe_results=[bad, still_bad, still_bad], process_present=False)
        res = asyncio.run(mgr.ensure_environment_ready(max_recoveries=1))
        assert res.ready is False
        assert mgr._launch_calls == 1  # exactly one launch, no infinite restart
        assert res.recovery_attempts == 1

    def test_gui_check_failure_blocks_before_launch(self, monkeypatch):
        bad = (False, EnvironmentBlockReason.GAME_CONTROL_UNAVAILABLE, {})
        mgr = self._mgr_with(monkeypatch, probe_results=[bad])
        res = asyncio.run(mgr.ensure_environment_ready(gui_check=lambda: False))
        assert res.ready is False
        assert res.reason == EnvironmentBlockReason.GUI_SESSION_UNAVAILABLE
        assert mgr._launch_calls == 0


def test_game_exe_not_hardcoded_to_user_path():
    """The resolved exe must derive from the provided game_dir, not a literal home path."""
    import inspect

    import sts2_autotest.core.lifecycle as lc
    src = inspect.getsource(lc)
    assert "/Users/chris" not in src
    mgr = GameLifecycleManager(_ProbeAdapter(), game_dir="/opt/steam/StS2")
    assert mgr.game_exe.startswith("/opt/steam/StS2")


# ── 阶段 C：重拉上限默认下调（issue #37）───────────────────


class TestMaxRelaunches:
    def test_default_max_relaunches_downgraded_to_3(self) -> None:
        """默认重拉上限从 15 下调至 3——确定性失败不再空转 15 次。"""
        mgr = _mgr(FakeAdapter([]))
        assert mgr.max_relaunches == 3

    def test_custom_max_relaunches(self) -> None:
        mgr = GameLifecycleManager(
            FakeAdapter([]), game_exe="/tmp/fake_game",
            game_dir="/tmp/fake_dir", max_relaunches=5,
        )
        assert mgr.max_relaunches == 5

    def test_relaunch_run_stops_at_custom_cap(self, no_sleep, fake_popen) -> None:
        """自定义上限 1：第 1 次重拉后到达上限，后续调用拒绝且计数不涨。"""
        mgr = GameLifecycleManager(
            FakeAdapter([], down_first=0), game_exe="/tmp/fake_game",
            game_dir="/tmp/fake_dir", max_relaunches=1,
        )
        ok1 = asyncio.run(mgr.relaunch_run(api_timeout=1.0))
        ok2 = asyncio.run(mgr.relaunch_run(api_timeout=1.0))
        assert ok1 is False  # API 未恢复（down_first=0 但响应为空 → is_api_up False）
        assert mgr.relaunch_count == 1
        assert ok2 is False
        assert mgr.relaunch_count == 1  # 已达上限，不再递增
