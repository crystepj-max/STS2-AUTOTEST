"""统一任务服务的持久化、排队、取消和结果分类测试。"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from sts2_autotest.core.run_service import (
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


def test_cancel_marks_record_and_terminates_worker(tmp_path, monkeypatch):
    store = RunStore(tmp_path / "runs")
    record = store.create(RunRequest(), run_id="run-cancel")
    store.update(record.run_id, status="RUNNING", phase="RUNNING", pid=99999)
    killed: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        killed.append((pid, sig))

    monkeypatch.setattr(os, "kill", fake_kill)
    cancelled = store.request_cancel(record.run_id)

    assert cancelled is not None
    assert cancelled.status == "CANCELLED"
    assert killed and killed[0][0] == 99999


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
    first.created_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    store.save(first)
    second = store.create(RunRequest(), run_id="run-next")

    acquired = wait_for_turn(store, second.run_id, timeout=0.1, poll_interval=0.001)

    assert acquired.run_id == second.run_id
    stale = store.load(first.run_id)
    assert stale is not None
    assert stale.status == "FAILED_PLATFORM"
