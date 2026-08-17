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
# sys.modules 常驻条目是刻意的（脚本与 src/ 命名空间隔离，仓库内无同名模块）；
# 若未来新增同名模块需改为 fixture 作用域化加载
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


def test_main_returns_timeout_code_when_pytest_times_out(monkeypatch, tmp_path) -> None:
    """pytest 超时时 main 返回 124，且不进入 baseline 比较。"""
    # CI fail-closed（GITHUB_ACTIONS）分支要求显式 --baseline-json，测试必须传参
    baseline_json = tmp_path / "pytest-baseline.json"
    baseline_json.write_text('{"darwin": []}', encoding="utf-8")
    monkeypatch.setattr(check_pytest_baseline, "_load_baseline", lambda baseline_json: set())
    monkeypatch.setattr(check_pytest_baseline, "_run_pytest", lambda: (0, True))

    assert (
        check_pytest_baseline.main(argv=["--baseline-json", str(baseline_json)])
        == check_pytest_baseline.TIMEOUT_EXIT_CODE
        == 124
    )


def test_main_accepts_only_historical_failures(monkeypatch, tmp_path) -> None:
    historical = {"tests/unit/test_old.py::test_known_failure"}
    baseline_json = tmp_path / "pytest-baseline.json"
    baseline_json.write_text('{"darwin": []}', encoding="utf-8")
    monkeypatch.setattr(check_pytest_baseline, "_load_baseline", lambda baseline_json: historical)
    monkeypatch.setattr(check_pytest_baseline, "_run_pytest", lambda: (1, False))
    monkeypatch.setattr(
        check_pytest_baseline,
        "_load_final_failures",
        lambda: historical,
    )

    assert check_pytest_baseline.main(argv=["--baseline-json", str(baseline_json)]) == 0


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


def test_main_fails_when_recovered_item_not_removed_from_list(monkeypatch, capsys, tmp_path) -> None:
    """已恢复的历史豁免必须同步从清单移除；未移除时门禁失败并给出清偿记录。"""
    allowed = {"tests/unit/test_old.py::test_recovered"}
    current: set[str] = set()
    baseline_json = tmp_path / "pytest-baseline.json"
    baseline_json.write_text('{"darwin": []}', encoding="utf-8")
    monkeypatch.setattr(check_pytest_baseline, "_load_baseline", lambda baseline_json: allowed)
    monkeypatch.setattr(check_pytest_baseline, "_run_pytest", lambda: (0, False))
    monkeypatch.setattr(check_pytest_baseline, "_load_final_failures", lambda: current)

    assert check_pytest_baseline.main(argv=["--baseline-json", str(baseline_json)]) == 1
    out = capsys.readouterr().out
    assert "已清偿" in out
    assert "tests/unit/test_old.py::test_recovered" in out


def test_main_accepts_when_recovered_item_removed_from_list(monkeypatch, tmp_path) -> None:
    """已恢复且已从清单移除时，门禁通过。"""
    current: set[str] = set()
    baseline_json = tmp_path / "pytest-baseline.json"
    baseline_json.write_text('{"darwin": []}', encoding="utf-8")
    monkeypatch.setattr(check_pytest_baseline, "_load_baseline", lambda baseline_json: set())
    monkeypatch.setattr(check_pytest_baseline, "_run_pytest", lambda: (0, False))
    monkeypatch.setattr(check_pytest_baseline, "_load_final_failures", lambda: current)

    assert check_pytest_baseline.main(argv=["--baseline-json", str(baseline_json)]) == 0


def test_main_fails_closed_without_baseline_json_in_ci(monkeypatch) -> None:
    """CI 环境未传 --baseline-json 时必须失败，防止「修改当前清单即扩大允许范围」的退化路径。"""

    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert check_pytest_baseline.main(argv=[]) == 2


def test_main_baseline_json_overrides_current_list(monkeypatch, tmp_path) -> None:
    """传入 base SHA 版本基线时，即使当前清单被篡改（新增失败被加入），新增失败仍被识别。"""

    # base SHA 版本：不含任何失败
    baseline_json = tmp_path / "pytest-baseline.json"
    baseline_json.write_text('{"darwin": []}', encoding="utf-8")

    monkeypatch.setattr(check_pytest_baseline.sys, "platform", "darwin")
    monkeypatch.setattr(check_pytest_baseline, "_run_pytest", lambda: (1, False))
    # 实际失败包含新失败
    monkeypatch.setattr(
        check_pytest_baseline,
        "_load_final_failures",
        lambda: {"tests/unit/test_new.py::test_failure"},
    )

    # 脚本读取 base 版本（无失败），因此新失败不属于历史清单 → 返回 1（CI 失败）
    assert check_pytest_baseline.main(argv=["--baseline-json", str(baseline_json)]) == 1
