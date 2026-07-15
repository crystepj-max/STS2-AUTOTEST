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


def test_ensure_game_up_launches_when_down(no_sleep, fake_popen):
    adapter = FakeAdapter([{"screen": "MAIN_MENU"}], down_first=1000)
    mgr = _mgr(adapter)
    ok = asyncio.run(mgr.ensure_game_up(api_timeout=2.0))
    assert ok is True
    assert len(fake_popen) == 1  # launch() was called


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
