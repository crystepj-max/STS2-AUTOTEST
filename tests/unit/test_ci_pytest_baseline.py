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


def test_classify_separates_three_categories() -> None:
    """比对历史清单与当前失败集，返回（新增、仍存在、已清偿）三类。"""
    allowed = {
        "tests/unit/test_old.py::test_still_failing",
        "tests/unit/test_old.py::test_recovered",
    }
    current = {
        "tests/unit/test_old.py::test_still_failing",
        "tests/unit/test_new.py::test_new_failure",
    }

    new_failures, historical_failures, resolved_failures = (
        check_pytest_baseline._classify(allowed, current)
    )

    assert new_failures == {"tests/unit/test_new.py::test_new_failure"}
    assert historical_failures == {"tests/unit/test_old.py::test_still_failing"}
    assert resolved_failures == {"tests/unit/test_old.py::test_recovered"}


def test_classify_empty_current_resolves_all() -> None:
    """当前无失败时，清单内所有项都归类为已清偿。"""
    allowed = {
        "tests/unit/test_old.py::test_recovered_a",
        "tests/unit/test_old.py::test_recovered_b",
    }
    current: set[str] = set()

    new_failures, historical_failures, resolved_failures = (
        check_pytest_baseline._classify(allowed, current)
    )

    assert new_failures == set()
    assert historical_failures == set()
    assert resolved_failures == allowed


def test_main_fails_when_recovered_item_not_removed_from_list(monkeypatch, capsys) -> None:
    """已恢复的历史豁免必须同步从清单移除；未移除时门禁失败并给出清偿记录。"""
    allowed = {"tests/unit/test_old.py::test_recovered"}
    current: set[str] = set()
    monkeypatch.setattr(check_pytest_baseline, "_load_baseline", lambda: allowed)
    monkeypatch.setattr(check_pytest_baseline, "_run_pytest", lambda: 0)
    monkeypatch.setattr(check_pytest_baseline, "_load_final_failures", lambda: current)

    assert check_pytest_baseline.main() == 1
    out = capsys.readouterr().out
    assert "已清偿" in out
    assert "tests/unit/test_old.py::test_recovered" in out


def test_main_accepts_when_recovered_item_removed_from_list(monkeypatch) -> None:
    """已恢复且已从清单移除时，门禁通过。"""
    current: set[str] = set()
    monkeypatch.setattr(check_pytest_baseline, "_load_baseline", lambda: set())
    monkeypatch.setattr(check_pytest_baseline, "_run_pytest", lambda: 0)
    monkeypatch.setattr(check_pytest_baseline, "_load_final_failures", lambda: current)

    assert check_pytest_baseline.main() == 0


def test_main_blocks_recurrence_after_recovery(monkeypatch) -> None:
    """同一项修复后再次失败（复发）按新增回归处理并阻止合并。"""
    # 清单已同步缩减（不含该项），但该项再次失败 → 视为新增回归
    current = {"tests/unit/test_old.py::test_recurrence"}
    monkeypatch.setattr(check_pytest_baseline, "_load_baseline", lambda: set())
    monkeypatch.setattr(check_pytest_baseline, "_run_pytest", lambda: 1)
    monkeypatch.setattr(check_pytest_baseline, "_load_final_failures", lambda: current)

    assert check_pytest_baseline.main() == 1
