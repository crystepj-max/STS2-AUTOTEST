"""统一测试任务服务。

该模块是 CLI、MCP 和其他 Agent 的共同任务记录层。它不包含任何
Gawain 业务规则，只负责任务生命周期、持久化、排队和控制。
"""

from __future__ import annotations

import json
import os
import builtins
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sts2_autotest.common.logging import get_logger
from sts2_autotest.core.lock_manager import LockManager

logger = get_logger("core.run_service")


RUN_PHASES = (
    "QUEUED",
    "PRECHECK",
    "PREPARING",
    "STARTING",
    "RUNNING",
    "RECOVERING",
    "COLLECTING",
    "COMPLETED",
)

TERMINAL_STATUSES = frozenset({
    "PASSED",
    "FAILED_PRODUCT",
    "FAILED_PLATFORM",
    "BLOCKED_ENVIRONMENT",
    "CANCELLED",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_run_id(prefix: str = "run") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


@dataclass
class RunRequest:
    """可持久化的测试请求。

    ``argv`` 保存兼容入口的原始参数，使控制端退出后仍可由平台继续执行。
    ``metadata`` 用于项目扩展，不允许通用平台读取 Gawain 业务字段。
    """

    project: str | None = None
    suite: str | None = None
    cases: list[str] = field(default_factory=list)
    mode: str = "new"
    timeout: int = 30
    adapter: str | None = None
    spec_dir: str | None = None
    priority: int = 1
    evidence: str = "full"
    idempotency_key: str | None = None
    argv: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunRecord:
    """任务状态和最终结果的持久化记录。"""

    run_id: str
    request: RunRequest
    status: str = "QUEUED"
    phase: str = "QUEUED"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    message: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    evidence_dir: str | None = None
    cancel_requested: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["request"] = asdict(self.request)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        request_data = data.get("request", {})
        request = RunRequest(
            project=request_data.get("project"),
            suite=request_data.get("suite"),
            cases=list(request_data.get("cases", [])),
            mode=str(request_data.get("mode", "new")),
            timeout=int(request_data.get("timeout", 30)),
            adapter=request_data.get("adapter"),
            spec_dir=request_data.get("spec_dir"),
            priority=int(request_data.get("priority", 1)),
            evidence=str(request_data.get("evidence", "full")),
            idempotency_key=request_data.get("idempotency_key"),
            argv=list(request_data.get("argv", [])),
            metadata=dict(request_data.get("metadata", {})),
        )
        return cls(
            run_id=str(data["run_id"]),
            request=request,
            status=str(data.get("status", "QUEUED")),
            phase=str(data.get("phase", "QUEUED")),
            created_at=str(data.get("created_at", _now())),
            updated_at=str(data.get("updated_at", _now())),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            pid=int(data["pid"]) if data.get("pid") is not None else None,
            exit_code=int(data["exit_code"]) if data.get("exit_code") is not None else None,
            message=data.get("message"),
            result=dict(data.get("result", {})),
            evidence_dir=data.get("evidence_dir"),
            cancel_requested=bool(data.get("cancel_requested", False)),
        )


class RunStore:
    """原子保存任务记录，并提供跨进程控制。"""

    def __init__(
        self,
        root: Path | str = "tests/output/.runs",
        *,
        stale_queue_seconds: float = 120.0,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.stale_queue_seconds = float(stale_queue_seconds)

    def _mutation_lock(self) -> LockManager:
        return LockManager(str(self.root / ".store.lock"))

    def path_for(self, run_id: str) -> Path:
        return self.root / run_id / "run.json"

    def create(self, request: RunRequest, run_id: str | None = None) -> RunRecord:
        lock = self._mutation_lock()
        if not lock.acquire_lock(timeout=5.0):
            raise RuntimeError("Timed out acquiring the run store lock")
        try:
            if request.idempotency_key:
                for existing in self.list(include_terminal=True):
                    if existing.request.idempotency_key == request.idempotency_key:
                        return existing
            record = RunRecord(run_id=run_id or _safe_run_id(), request=request)
            self.save(record)
            return record
        finally:
            lock.release_lock()

    def save(self, record: RunRecord) -> None:
        path = self.path_for(record.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record.updated_at = _now()
        temp = path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, path)

    def load(self, run_id: str) -> RunRecord | None:
        path = self.path_for(run_id)
        if not path.is_file():
            return None
        try:
            return RunRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.warning("Cannot load run record %s: %s", run_id, exc)
            return None

    def list(self, *, include_terminal: bool = True) -> list[RunRecord]:
        records: list[RunRecord] = []
        for path in sorted(self.root.glob("*/run.json")):
            record = self.load(path.parent.name)
            if record is None:
                continue
            if include_terminal or not record.is_terminal:
                records.append(record)
        return sorted(records, key=lambda item: (item.created_at, item.run_id))

    def update(self, run_id: str, **changes: Any) -> RunRecord | None:
        lock = self._mutation_lock()
        if not lock.acquire_lock(timeout=5.0):
            raise RuntimeError("Timed out acquiring the run store lock")
        try:
            record = self.load(run_id)
            if record is None:
                return None
            if record.status == "CANCELLED" and changes.get("status") != "CANCELLED":
                return record
            for key, value in changes.items():
                if not hasattr(record, key):
                    raise ValueError(f"Unknown run field: {key}")
                setattr(record, key, value)
            self.save(record)
            return record
        finally:
            lock.release_lock()

    def request_cancel(self, run_id: str) -> RunRecord | None:
        lock = self._mutation_lock()
        if not lock.acquire_lock(timeout=5.0):
            raise RuntimeError("Timed out acquiring the run store lock")
        try:
            record = self.load(run_id)
            if record is None or record.is_terminal:
                return record
            record.cancel_requested = True
            record.message = "Cancellation requested"
            record.status = "CANCELLED"
            record.phase = "COMPLETED"
            record.finished_at = _now()
            pid = record.pid
            self.save(record)
        finally:
            lock.release_lock()
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError as exc:
                logger.warning("Cannot stop run %s process %s: %s", run_id, pid, exc)
        return self.load(run_id) or record

    def active_before(self, record: RunRecord) -> builtins.list[RunRecord]:
        """返回当前任务之前创建且未结束的任务，用于稳定的先进先出排队。"""
        active: list[RunRecord] = []
        for other in self.list(include_terminal=False):
            if other.run_id == record.run_id:
                continue
            if (other.created_at, other.run_id) >= (record.created_at, record.run_id):
                continue
            if other.pid is None:
                try:
                    created = datetime.fromisoformat(other.created_at).timestamp()
                except ValueError:
                    created = time.time()
                if time.time() - created >= self.stale_queue_seconds:
                    self.update(
                        other.run_id,
                        status="FAILED_PLATFORM",
                        phase="COMPLETED",
                        finished_at=_now(),
                        message="Queued worker disappeared before starting",
                    )
                    continue
                active.append(other)
                continue
            if other.pid is not None:
                try:
                    os.kill(other.pid, 0)
                except ProcessLookupError:
                    self.update(
                        other.run_id,
                        status="FAILED_PLATFORM",
                        phase="COMPLETED",
                        finished_at=_now(),
                        message="Worker process disappeared before completion",
                    )
                    continue
                except OSError:
                    pass
            active.append(other)
        return active


class RunCancelled(RuntimeError):
    """任务被外部取消。"""


def wait_for_turn(
    store: RunStore,
    run_id: str,
    *,
    paused_path: Path | str | None = None,
    poll_interval: float = 0.25,
    timeout: float | None = None,
) -> RunRecord:
    """等待任务轮到自己；支持全局暂停、取消和超时。"""
    record = store.load(run_id)
    if record is None:
        raise RunCancelled(f"Unknown run: {run_id}")
    started = time.monotonic()
    pause_marker = Path(paused_path) if paused_path else store.root / "queue.paused"
    while True:
        record = store.load(run_id)
        if record is None or record.cancel_requested or record.status == "CANCELLED":
            raise RunCancelled(f"Run cancelled: {run_id}")
        if not pause_marker.exists() and not store.active_before(record):
            updated = store.update(
                run_id,
                status="STARTING",
                phase="STARTING",
                started_at=record.started_at or _now(),
                pid=os.getpid(),
            )
            return updated or record
        if timeout is not None and time.monotonic() - started >= timeout:
            store.update(
                run_id,
                status="BLOCKED_ENVIRONMENT",
                phase="COMPLETED",
                finished_at=_now(),
                message="Timed out while waiting for the game session slot",
            )
            raise RunCancelled(f"Run queue timeout: {run_id}")
        time.sleep(poll_interval)


def complete_record(
    store: RunStore,
    run_id: str,
    *,
    exit_code: int,
    result: dict[str, Any] | None = None,
    evidence_dir: str | None = None,
    message: str | None = None,
) -> RunRecord | None:
    """把旧入口的退出码映射为统一任务结果。"""
    record = store.load(run_id)
    if record is None or record.status == "CANCELLED":
        return record
    payload = result or {}
    declared_status = str(payload.get("status", "")).upper()
    if declared_status in {"FAILED_PLATFORM", "PLATFORM_ERROR"}:
        status = "FAILED_PLATFORM"
    elif payload.get("blocked") or declared_status in {"BLOCKED", "TIMEOUT", "BLOCKED_ENVIRONMENT"}:
        status = "BLOCKED_ENVIRONMENT"
    elif exit_code == 0 and declared_status not in {"FAILED", "FAILED_PRODUCT", "ERROR"}:
        status = "PASSED"
    else:
        status = "FAILED_PRODUCT"
    return store.update(
        run_id,
        status=status,
        phase="COMPLETED",
        finished_at=_now(),
        exit_code=exit_code,
        result=payload,
        evidence_dir=evidence_dir,
        message=message,
    )


def serialize_record(record: RunRecord | None) -> dict[str, Any]:
    if record is None:
        return {"status": "NOT_FOUND"}
    payload = record.to_dict()
    payload["terminal"] = record.is_terminal
    return payload


def spawn_worker(store: RunStore, record: RunRecord, argv: list[str]) -> int:
    """启动一个独立工作进程，确保控制端退出不会中断测试。"""
    log_path = store.root / record.run_id / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "sts2_autotest.cli.main", *argv],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_file.close()
    store.update(record.run_id, pid=process.pid)
    return process.pid


def records_summary(records: Iterable[RunRecord]) -> list[dict[str, Any]]:
    return [serialize_record(record) for record in records]
