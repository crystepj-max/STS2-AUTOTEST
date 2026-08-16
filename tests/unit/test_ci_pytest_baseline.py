"""单元测试历史基线门禁。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / ".github/scripts"
_SCRIPT = _SCRIPTS_DIR / "check_pytest_baseline.py"
_SPEC = importlib.util.spec_from_file_location("check_pytest_baseline_script", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
check_pytest_baseline = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = check_pytest_baseline
# 脚本内 `from runner_utils import ...` 需要 .github/scripts 在 sys.path 上
sys.path.insert(0, str(_SCRIPTS_DIR))
_SPEC.loader.exec_module(check_pytest_baseline)
runner_utils = sys.modules["runner_utils"]


def test_run_pytest_passes_command_to_run_timed(monkeypatch) -> None:
    recorded: list[tuple[str, list[str], bool]] = []

    def fake_run_timed(name: str, cmd: list[str], log_path, *, timeout: float, echo: bool):
        recorded.append((name, cmd, echo))
        return runner_utils.TimedResult(returncode=1, output="", error="", timed_out=False)

    monkeypatch.setattr(check_pytest_baseline, "run_timed", fake_run_timed)

    assert check_pytest_baseline._run_pytest() == (1, False)
    name, cmd, echo = recorded[0]
    assert name == "pytest"
    assert echo is True
    assert cmd == [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/",
        "-v",
        f"--junitxml={check_pytest_baseline.JUNIT_PATH}",
    ]


def test_run_pytest_reports_timeout_flag(monkeypatch) -> None:
    def fake_run_timed(name: str, cmd: list[str], log_path, *, timeout: float, echo: bool):
        return runner_utils.TimedResult(returncode=124, output="partial", error="", timed_out=True)

    monkeypatch.setattr(check_pytest_baseline, "run_timed", fake_run_timed)

    assert check_pytest_baseline._run_pytest() == (124, True)


def test_main_returns_timeout_code_when_pytest_times_out(monkeypatch) -> None:
    monkeypatch.setattr(check_pytest_baseline, "_load_baseline", lambda: set())
    monkeypatch.setattr(check_pytest_baseline, "_run_pytest", lambda: (0, True))

    assert check_pytest_baseline.main() == check_pytest_baseline.TIMEOUT_EXIT_CODE == 124


def test_main_accepts_only_historical_failures(monkeypatch) -> None:
    historical = {"tests/unit/test_old.py::test_known_failure"}
    monkeypatch.setattr(check_pytest_baseline, "_load_baseline", lambda: historical)
    monkeypatch.setattr(check_pytest_baseline, "_run_pytest", lambda: (1, False))
    monkeypatch.setattr(
        check_pytest_baseline,
        "_load_final_failures",
        lambda: historical,
    )

    assert check_pytest_baseline.main() == 0
