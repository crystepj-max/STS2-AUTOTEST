"""单元测试历史基线门禁。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/check_pytest_baseline.py"
_SPEC = importlib.util.spec_from_file_location("check_pytest_baseline_script", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
check_pytest_baseline = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = check_pytest_baseline
_SPEC.loader.exec_module(check_pytest_baseline)


def test_run_pytest_uses_separate_process(monkeypatch) -> None:
    recorded: list[tuple[list[str], int]] = []

    class FakeProcess:
        def wait(self) -> int:
            return 1

    def fake_popen(command: list[str], *, creationflags: int) -> FakeProcess:
        recorded.append((command, creationflags))
        return FakeProcess()

    monkeypatch.setattr(check_pytest_baseline.subprocess, "Popen", fake_popen)

    assert check_pytest_baseline._run_pytest() == 1
    assert recorded == [
        (
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/",
                "-v",
                f"--junitxml={check_pytest_baseline.JUNIT_PATH}",
            ],
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    ]


def test_run_pytest_uses_completed_child_status_after_late_interrupt(
    monkeypatch,
) -> None:
    class FakeProcess:
        def wait(self) -> int:
            raise KeyboardInterrupt

        def poll(self) -> int:
            return 1

    monkeypatch.setattr(
        check_pytest_baseline.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(check_pytest_baseline.sys, "platform", "win32")

    assert check_pytest_baseline._run_pytest() == 1


def test_run_pytest_propagates_interrupt_while_child_is_running(monkeypatch) -> None:
    class FakeProcess:
        def wait(self) -> int:
            raise KeyboardInterrupt

        def poll(self) -> None:
            return None

    monkeypatch.setattr(
        check_pytest_baseline.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(check_pytest_baseline.sys, "platform", "win32")

    with pytest.raises(KeyboardInterrupt):
        check_pytest_baseline._run_pytest()


def test_run_pytest_propagates_interrupt_on_non_windows(monkeypatch) -> None:
    class FakeProcess:
        def wait(self) -> int:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        check_pytest_baseline.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(check_pytest_baseline.sys, "platform", "linux")

    with pytest.raises(KeyboardInterrupt):
        check_pytest_baseline._run_pytest()


def test_main_accepts_only_historical_failures(monkeypatch) -> None:
    historical = {"tests/unit/test_old.py::test_known_failure"}
    monkeypatch.setattr(check_pytest_baseline, "_load_baseline", lambda: historical)
    monkeypatch.setattr(check_pytest_baseline, "_run_pytest", lambda: 1)
    monkeypatch.setattr(
        check_pytest_baseline,
        "_load_final_failures",
        lambda: historical,
    )

    assert check_pytest_baseline.main() == 0
