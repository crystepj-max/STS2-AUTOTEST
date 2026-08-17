"""Unit tests for core.anti_sleep.AntiSleepGuard (P1 fix five-A).

subprocess.Popen is mocked; no real caffeinate is launched.
"""

from unittest.mock import MagicMock

from sts2_autotest.core import anti_sleep
from sts2_autotest.core.anti_sleep import AntiSleepGuard


def _fake_proc(alive: bool = True):
    proc = MagicMock()
    proc.pid = 4242
    # poll() returns None while alive, 0 once terminated
    proc._alive = alive

    def _poll():
        return None if proc._alive else 0

    def _terminate():
        proc._alive = False

    def _kill():
        proc._alive = False

    proc.poll.side_effect = _poll
    proc.terminate.side_effect = _terminate
    proc.kill.side_effect = _kill
    proc.wait.return_value = 0
    return proc


class TestMacOS:
    def test_start_launches_caffeinate(self, monkeypatch):
        monkeypatch.setattr(anti_sleep, "_IS_MACOS", True)
        proc = _fake_proc()
        popen = MagicMock(return_value=proc)
        monkeypatch.setattr(anti_sleep.subprocess, "Popen", popen)

        guard = AntiSleepGuard(enabled=True)
        assert guard.start() is True
        assert guard.active is True
        args = popen.call_args[0][0]
        assert args[0] == "caffeinate"
        assert "-dimsu" in args

    def test_stop_terminates_child_no_orphan(self, monkeypatch):
        monkeypatch.setattr(anti_sleep, "_IS_MACOS", True)
        proc = _fake_proc()
        monkeypatch.setattr(anti_sleep.subprocess, "Popen", MagicMock(return_value=proc))
        # pretend pid is gone after stop
        monkeypatch.setattr(anti_sleep.AntiSleepGuard, "_confirm_gone", staticmethod(lambda pid: None))

        guard = AntiSleepGuard(enabled=True)
        guard.start()
        guard.stop()
        assert proc.terminate.called
        assert guard.active is False

    def test_double_start_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(anti_sleep, "_IS_MACOS", True)
        popen = MagicMock(return_value=_fake_proc())
        monkeypatch.setattr(anti_sleep.subprocess, "Popen", popen)
        guard = AntiSleepGuard(enabled=True)
        guard.start()
        guard.start()
        assert popen.call_count == 1

    def test_start_failure_recorded_not_raised(self, monkeypatch):
        monkeypatch.setattr(anti_sleep, "_IS_MACOS", True)
        monkeypatch.setattr(
            anti_sleep.subprocess, "Popen",
            MagicMock(side_effect=FileNotFoundError("caffeinate")),
        )
        guard = AntiSleepGuard(enabled=True)
        assert guard.start() is False
        assert guard.start_error is not None
        assert guard.active is False

    def test_context_manager(self, monkeypatch):
        monkeypatch.setattr(anti_sleep, "_IS_MACOS", True)
        proc = _fake_proc()
        monkeypatch.setattr(anti_sleep.subprocess, "Popen", MagicMock(return_value=proc))
        monkeypatch.setattr(anti_sleep.AntiSleepGuard, "_confirm_gone", staticmethod(lambda pid: None))
        with AntiSleepGuard(enabled=True) as guard:
            assert guard.active is True
        assert guard.active is False


class TestNonMacOS:
    def test_start_is_noop_off_macos(self, monkeypatch):
        monkeypatch.setattr(anti_sleep, "_IS_MACOS", False)
        popen = MagicMock()
        monkeypatch.setattr(anti_sleep.subprocess, "Popen", popen)
        guard = AntiSleepGuard(enabled=True)
        assert guard.start() is False
        assert guard.active is False
        popen.assert_not_called()

    def test_disabled_is_noop_on_macos(self, monkeypatch):
        monkeypatch.setattr(anti_sleep, "_IS_MACOS", True)
        popen = MagicMock()
        monkeypatch.setattr(anti_sleep.subprocess, "Popen", popen)
        guard = AntiSleepGuard(enabled=False)
        assert guard.start() is False
        popen.assert_not_called()
