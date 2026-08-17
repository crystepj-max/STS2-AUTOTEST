"""统一任务服务的持久化、排队、取消和结果分类测试。"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sts2_autotest.common.errors import CancelFailureReason
from sts2_autotest.core.run_service import (
    RESUMABLE_STATUSES,
    RunCancelled,
    RunRequest,
    RunStore,
    complete_record,
    serialize_record,
    wait_for_turn,
)


def test_run_record_round_trips_request_and_result(tmp_path):
    store = RunStore(tmp_path / "runs")
    record = store.create(
        RunRequest(
            project="example",
            suite="smoke",
            cases=["TC-1"],
            mode="resume",
            argv=["run", "--suite", "smoke"],
        ),
        run_id="run-001",
    )
    store.update(record.run_id, status="PASSED", phase="COMPLETED", result={"passed": 1})

    loaded = store.load("run-001")
    assert loaded is not None
    assert loaded.request.mode == "resume"
    assert loaded.request.cases == ["TC-1"]
    assert loaded.result == {"passed": 1}
    assert serialize_record(loaded)["terminal"] is True


def test_wait_for_turn_obeys_fifo(tmp_path):
    store = RunStore(tmp_path / "runs")
    first = store.create(RunRequest(), run_id="run-first")
    second = store.create(RunRequest(), run_id="run-second")
    store.update(first.run_id, status="RUNNING", phase="RUNNING", pid=os.getpid())

    with pytest.raises(RunCancelled, match="queue timeout"):
        wait_for_turn(store, second.run_id, timeout=0.01, poll_interval=0.001)

    first_record = store.load(first.run_id)
    second_record = store.load(second.run_id)
    assert first_record is not None
    assert second_record is not None
    assert second_record.status == "BLOCKED_ENVIRONMENT"


def test_request_cancel_keeps_run_non_terminal_and_does_not_kill(tmp_path, monkeypatch):
    # 修复三：收到取消请求只做"标记"，任务保持非终态，且不立即杀 worker。
    # 这样队列在收尾（存状态→恢复主菜单→报告→封存证据）完成前不会把游戏名额放给下一个任务。
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-cancel")
    store.update(record.run_id, status="RUNNING", phase="RUNNING", pid=99999)
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    cancelled = store.request_cancel(record.run_id)

    assert cancelled is not None
    assert cancelled.cancel_requested is True
    assert cancelled.is_cancelling is True
    assert cancelled.is_terminal is False  # 尚未终态
    assert cancelled.status == "RUNNING"
    assert cancelled.phase == "CANCELLING"
    assert killed == []  # request_cancel 不再直接杀进程


def test_force_stop_terminates_worker(tmp_path, monkeypatch):
    # 升级手段：只有在优雅取消超时无响应时才硬停 worker。
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-force")
    store.update(record.run_id, status="RUNNING", phase="CANCELLING", pid=99999)
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    store.force_stop(record.run_id)

    assert killed and killed[0][0] == 99999


def test_finish_cancel_clean_marks_cancelled_and_seals_evidence(tmp_path):
    # 收尾干净 → 终态 CANCELLED，证据封存标志置真，名额此刻才释放。
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-clean")
    store.update(record.run_id, status="RUNNING", phase="CANCELLING", cancel_requested=True)

    finished = store.finish_cancel(
        record.run_id,
        reason=None,
        evidence_dir="tests/output/x/run-clean",
        sealed=True,
        result={"cleanup": "ok"},
    )

    assert finished is not None
    assert finished.status == "CANCELLED"
    assert finished.phase == "COMPLETED"
    assert finished.is_terminal is True
    assert finished.evidence_sealed is True
    assert finished.evidence_dir == "tests/output/x/run-clean"
    assert finished.result["cleanup"] == "ok"


def test_finish_cancel_cleanup_failure_becomes_failed_platform(tmp_path):
    # 清理失败 → 不能当作正常 CANCELLED，归类平台失败并带原因码。
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-cleanup-fail")
    store.update(record.run_id, status="RUNNING", phase="CANCELLING", cancel_requested=True)

    finished = store.finish_cancel(
        record.run_id,
        reason=CancelFailureReason.CANCEL_CLEANUP_FAILED,
        sealed=True,
    )

    assert finished is not None
    assert finished.status == "FAILED_PLATFORM"
    assert finished.is_terminal is True
    assert finished.result["reason"] == CancelFailureReason.CANCEL_CLEANUP_FAILED.value


def test_finish_cancel_evidence_failure_becomes_failed_platform(tmp_path):
    # 证据封存失败 → 同样归类平台失败，带 CANCEL_EVIDENCE_FAILED。
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-evi-fail")
    store.update(record.run_id, status="RUNNING", phase="CANCELLING", cancel_requested=True)

    finished = store.finish_cancel(
        record.run_id,
        reason=CancelFailureReason.CANCEL_EVIDENCE_FAILED,
        sealed=False,
    )

    assert finished is not None
    assert finished.status == "FAILED_PLATFORM"
    assert finished.evidence_sealed is False
    assert finished.result["reason"] == CancelFailureReason.CANCEL_EVIDENCE_FAILED.value


def test_finish_cancel_game_control_lost_becomes_blocked_environment(tmp_path):
    # 收尾时游戏控制入口不可用 → 归类环境阻塞，可被 resume 继续。
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-ctrl-lost")
    store.update(record.run_id, status="RUNNING", phase="CANCELLING", cancel_requested=True)

    finished = store.finish_cancel(
        record.run_id,
        reason=CancelFailureReason.GAME_CONTROL_UNAVAILABLE,
        sealed=True,
    )

    assert finished is not None
    assert finished.status == "BLOCKED_ENVIRONMENT"
    assert finished.status in RESUMABLE_STATUSES


def test_finish_cancel_missing_run_returns_none(tmp_path):
    store = RunStore(tmp_path / "runs")
    assert store.finish_cancel("does-not-exist", reason=None) is None


def test_resumable_statuses_are_exactly_the_recoverable_terminals():
    # 修复四门控依据：只有这三种原状态允许 resume。
    assert RESUMABLE_STATUSES == frozenset(
        {"CANCELLED", "FAILED_PLATFORM", "BLOCKED_ENVIRONMENT"}
    )


def test_resume_precheck_rejects_missing_run(tmp_path):
    from sts2_autotest.core.run_service import resume_precheck

    ok, reason = resume_precheck(None)
    assert ok is False
    assert reason == "NOT_FOUND"


def test_resume_precheck_rejects_non_resumable_status(tmp_path):
    # 修复四：恢复必须等待取消完全结束——运行中/已通过的任务不能被 resume。
    from sts2_autotest.core.run_service import resume_precheck

    store = RunStore(tmp_path / "runs")
    running = store.create(RunRequest(), run_id="run-running")
    store.update(running.run_id, status="RUNNING", phase="RUNNING")
    ok, reason = resume_precheck(store.load(running.run_id))
    assert ok is False
    assert reason == "NOT_RESUMABLE"

    passed = store.create(RunRequest(), run_id="run-passed2")
    store.update(passed.run_id, status="PASSED", phase="COMPLETED")
    ok, reason = resume_precheck(store.load(passed.run_id))
    assert ok is False
    assert reason == "NOT_RESUMABLE"


def test_resume_precheck_requires_sealed_evidence(tmp_path):
    # 取消/失败但证据尚未封存，说明收尾没走完，禁止恢复。
    from sts2_autotest.core.run_service import resume_precheck

    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-unsealed")
    store.finish_cancel(record.run_id, reason=None, sealed=False)
    ok, reason = resume_precheck(store.load(record.run_id))
    assert ok is False
    assert reason == "EVIDENCE_NOT_SEALED"


def test_resume_precheck_accepts_sealed_cancelled_run(tmp_path):
    from sts2_autotest.core.run_service import resume_precheck

    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-ok-resume")
    store.finish_cancel(record.run_id, reason=None, sealed=True)
    ok, reason = resume_precheck(store.load(record.run_id))
    assert ok is True
    assert reason == "OK"


def test_cancel_does_not_overwrite_a_completed_result(tmp_path):
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-passed")
    store.update(record.run_id, status="PASSED", phase="COMPLETED", exit_code=0)

    cancelled = store.request_cancel(record.run_id)

    assert cancelled is not None
    assert cancelled.status == "PASSED"


def test_complete_record_classifies_blocked_run(tmp_path):
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-blocked")
    complete_record(
        store,
        record.run_id,
        exit_code=1,
        result={"status": "TIMEOUT", "blocked": True},
    )
    loaded = store.load(record.run_id)
    assert loaded is not None
    assert loaded.status == "BLOCKED_ENVIRONMENT"


def test_complete_record_does_not_hide_declared_failure_behind_zero_exit_code(tmp_path):
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-declared-failure")
    complete_record(
        store,
        record.run_id,
        exit_code=0,
        result={"status": "FAILED"},
    )

    loaded = store.load(record.run_id)
    assert loaded is not None
    assert loaded.status == "FAILED_PRODUCT"


def test_idempotency_key_does_not_create_duplicate_run(tmp_path):
    store = RunStore(tmp_path / "runs")
    first = store.create(RunRequest(idempotency_key="same-request"))
    second = store.create(RunRequest(idempotency_key="same-request"))
    assert second.run_id == first.run_id
    assert len(store.list()) == 1


def test_stale_queued_worker_does_not_block_following_run(tmp_path):
    store = RunStore(tmp_path / "runs", stale_queue_seconds=1)
    first = store.create(RunRequest(), run_id="run-stale")
    first.created_at = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    store.save(first)
    second = store.create(RunRequest(), run_id="run-next")

    acquired = wait_for_turn(store, second.run_id, timeout=0.1, poll_interval=0.001)

    assert acquired.run_id == second.run_id
    stale = store.load(first.run_id)
    assert stale is not None
    assert stale.status == "FAILED_PLATFORM"


def _dead_pid() -> int:
    """返回一个已彻底退出的进程 pid，用于模拟『worker 异常退出』。"""
    proc = subprocess.Popen(["true"])
    pid = proc.pid
    proc.wait()
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        time.sleep(0.01)
    return pid


def test_reap_if_worker_gone_marks_failed_platform_when_control_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """仅执行进程异常退出、游戏控制 API 仍可响应 → FAILED_PLATFORM（P1#2）。"""
    monkeypatch.setattr(
        "sts2_autotest.core.run_service._game_control_reachable",
        lambda *a, **k: True,
    )
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-gone")
    store.update(record.run_id, status="RUNNING", phase="RUNNING", pid=_dead_pid())

    reaped = store.reap_if_worker_gone("run-gone")

    assert reaped is not None
    assert reaped.status == "FAILED_PLATFORM"
    assert reaped.is_terminal
    assert reaped.message == "Worker process disappeared before completion"
    # 终态收口：生成 run-result.json 与可读证据包，满足公共契约
    assert (tmp_path / "run-gone" / "reports" / "run-result.json").is_file()
    assert (tmp_path / "artifacts").is_dir()
    assert reaped.evidence_sealed is True
    # 幂等：再次查询已终态记录不应改变结果或报错
    again = store.reap_if_worker_gone("run-gone")
    assert again.status == "FAILED_PLATFORM"


def test_reap_if_worker_gone_marks_blocked_when_control_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """图形/控制同时消失 → BLOCKED_ENVIRONMENT（P1#2）而非误判平台失败。"""
    monkeypatch.setattr(
        "sts2_autotest.core.run_service._game_control_reachable",
        lambda *a, **k: False,
    )
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-gone-blocked")
    store.update(record.run_id, status="RUNNING", phase="RUNNING", pid=_dead_pid())

    reaped = store.reap_if_worker_gone("run-gone-blocked")

    assert reaped.status == "BLOCKED_ENVIRONMENT"
    assert reaped.evidence_sealed is True
    assert "unreachable" in (reaped.message or "")


def test_reap_if_worker_gone_leaves_live_worker_untouched(tmp_path):
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-live")
    store.update(record.run_id, status="RUNNING", phase="RUNNING", pid=os.getpid())

    reaped = store.reap_if_worker_gone("run-live")

    assert reaped.status == "RUNNING"
    assert not reaped.is_terminal


def test_reap_if_worker_gone_ignores_already_terminal_run(tmp_path):
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-done")
    store.update(record.run_id, status="CANCELLED", phase="COMPLETED", pid=_dead_pid())

    reaped = store.reap_if_worker_gone("run-done")

    assert reaped.status == "CANCELLED"
