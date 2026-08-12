"""单元测试历史基线门禁。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/check_pytest_baseline.py"
_SPEC = importlib.util.spec_from_file_location("check_pytest_baseline_script", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
check_pytest_baseline = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = check_pytest_baseline
_SPEC.loader.exec_module(check_pytest_baseline)


def test_run_pytest_uses_separate_process(monkeypatch) -> None:
    recorded: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        recorded.append(command)
        assert check is False
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(check_pytest_baseline.subprocess, "run", fake_run)

    assert check_pytest_baseline._run_pytest() == 1
    assert recorded == [
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/",
            "-v",
            f"--junitxml={check_pytest_baseline.JUNIT_PATH}",
        ]
    ]


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
