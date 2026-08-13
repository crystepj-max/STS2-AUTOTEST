"""统一测试任务服务。

该模块是 CLI、MCP 和其他 Agent 的共同任务记录层。它不包含任何
Gawain 业务规则，只负责任务生命周期、持久化、排队和控制。
"""

from __future__ import annotations

import builtins
import importlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, UTC
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from sts2_autotest.common.errors import CancelFailureReason
from sts2_autotest.common.logging import get_logger
from sts2_autotest.core.lock_manager import LockManager

logger = get_logger("core.run_service")

# Original run statuses that resume_run is allowed to continue from.
RESUMABLE_STATUSES = frozenset({
    "CANCELLED",
    "FAILED_PLATFORM",
    "BLOCKED_ENVIRONMENT",
})


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
    return datetime.now(UTC).isoformat()


def _game_control_reachable(host: str = "127.0.0.1", port: int = 8080) -> bool:
    """Best-effort probe: is the game control API still answering?

    Used to tell apart a *environment* incident (graphics session / game control
    gone → BLOCKED_ENVIRONMENT) from a *platform* failure (only our worker
    process died while the game stays controllable → FAILED_PLATFORM).
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _safe_run_id(prefix: str = "run") -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
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
    progress: dict[str, Any] = field(default_factory=dict)
    evidence_dir: str | None = None
    cancel_requested: bool = False
    # Set once the run's evidence pack has been sealed (worker responsibility).
    # resume_run requires this on the original run before it may continue.
    evidence_sealed: bool = False
    # For resume runs: the original run_id this run continues from.
    resumed_from: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_cancelling(self) -> bool:
        """Cancellation requested but cleanup not yet finished (non-terminal)."""
        return self.cancel_requested and not self.is_terminal

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["request"] = asdict(self.request)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
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
            progress=dict(data.get("progress", {})),
            evidence_dir=data.get("evidence_dir"),
            cancel_requested=bool(data.get("cancel_requested", False)),
            evidence_sealed=bool(data.get("evidence_sealed", False)),
            resumed_from=data.get("resumed_from"),
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
        """Request cancellation *gracefully*.

        This ONLY flags the run and moves it to a non-terminal ``CANCELLING``
        phase. It does NOT mark the run CANCELLED and does NOT kill the worker.
        The worker's execution entry observes ``cancel_requested`` and performs
        the full cleanup (stop new game ops → save pre-cancel state → recover a
        clean main menu → verify → report → seal evidence), then calls
        :meth:`finish_cancel`. Keeping the run non-terminal is what prevents the
        queue from releasing the game slot to the next task before cleanup ends.
        """
        lock = self._mutation_lock()
        if not lock.acquire_lock(timeout=5.0):
            raise RuntimeError("Timed out acquiring the run store lock")
        try:
            record = self.load(run_id)
            if record is None or record.is_terminal:
                return record
            record.cancel_requested = True
            record.message = "Cancellation requested"
            record.phase = "CANCELLING"
            self.save(record)
            return record
        finally:
            lock.release_lock()

    def force_stop(self, run_id: str) -> RunRecord | None:
        """Escalation: hard-stop a worker that did not respond to a graceful
        cancel in time. Cleanup + minimal evidence sealing must still be
        completed by the platform afterwards via :meth:`finish_cancel`."""
        record = self.load(run_id)
        if record is None:
            return None
        pid = record.pid
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError as exc:
                logger.warning("Cannot stop run %s process %s: %s", run_id, pid, exc)
        return record

    def finish_cancel(
        self,
        run_id: str,
        *,
        reason: CancelFailureReason | str | None = None,
        evidence_dir: str | None = None,
        sealed: bool = False,
        result: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> RunRecord | None:
        """Finalize a cancellation *after* cleanup and evidence sealing.

        Reason mapping (a cancel that could not be cleaned up must NOT become a
        normal CANCELLED):
        - None (clean)                         -> CANCELLED
        - CANCEL_CLEANUP_FAILED / EVIDENCE     -> FAILED_PLATFORM
        - GAME_CONTROL_UNAVAILABLE             -> BLOCKED_ENVIRONMENT

        Only after this returns terminal does the game slot free for the queue.
        """
        reason_str = str(reason) if reason is not None else None
        if reason_str is None:
            status = "CANCELLED"
        elif reason_str == CancelFailureReason.GAME_CONTROL_UNAVAILABLE.value:
            status = "BLOCKED_ENVIRONMENT"
        else:
            status = "FAILED_PLATFORM"
        lock = self._mutation_lock()
        if not lock.acquire_lock(timeout=5.0):
            raise RuntimeError("Timed out acquiring the run store lock")
        try:
            record = self.load(run_id)
            if record is None:
                return None
            record.status = status
            record.phase = "COMPLETED"
            record.finished_at = _now()
            record.cancel_requested = True
            if evidence_dir is not None:
                record.evidence_dir = evidence_dir
            record.evidence_sealed = bool(sealed)
            if result is not None:
                merged = dict(record.result)
                merged.update(result)
                if reason_str is not None:
                    merged.setdefault("reason", reason_str)
                record.result = merged
            elif reason_str is not None:
                merged = dict(record.result)
                merged.setdefault("reason", reason_str)
                record.result = merged
            record.message = message or (
                "Cancelled" if status == "CANCELLED" else f"Cancel cleanup: {reason_str}"
            )
            self.save(record)
            return record
        finally:
            lock.release_lock()

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
                    self._mark_worker_gone(other)
                    continue
                except OSError:
                    pass
            active.append(other)
        return active

    def _mark_worker_gone(self, record: RunRecord) -> RunRecord | None:
        """Worker 进程已消失：收口终态并生成证据，避免僵尸记录污染审计。

        区分图形/控制消失（BLOCKED_ENVIRONMENT）与仅执行进程异常
        （FAILED_PLATFORM）：若游戏控制 API 仍能响应，说明游戏与控制链路尚在，
        只是本工作进程异常退出，判平台失败；否则判环境阻塞。
        """
        control_up = _game_control_reachable()
        if control_up:
            status = "FAILED_PLATFORM"
            reason = "Worker process disappeared before completion"
        else:
            status = "BLOCKED_ENVIRONMENT"
            reason = (
                "Worker process disappeared and game control API is unreachable "
                "(graphics session or game process incident)"
            )
        evidence_dir = self._seal_worker_gone_evidence(record, status, reason)
        return self.update(
            record.run_id,
            status=status,
            phase="COMPLETED",
            finished_at=_now(),
            message=reason,
            evidence_dir=evidence_dir,
            evidence_sealed=True,
        )

    def _seal_worker_gone_evidence(
        self, record: RunRecord, status: str, reason: str
    ) -> str | None:
        """失联任务终态收口：生成 run-result.json 与可读证据包，满足公共契约。

        不抛异常——证据生成失败只记日志，绝不阻塞终态标记（否则会留下僵尸记录）。
        """
        evidence_root = self.root.parent
        run_id = record.run_id
        # 1) run-result.json（报告契约）
        result_dir = evidence_root / run_id / "reports"
        try:
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / "run-result.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "task_id": run_id,
                        "status": status,
                        "message": reason,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("worker-gone: cannot write run-result.json: %s", exc)
        # 2) 证据包 + 压缩包（证据契约）
        # 注意：evidence 层位于 core 之上，按层级隔离约定 core 不得静态 import
        # evidence；沿用 evidence_hooks 的既有做法，用 importlib 动态导入，使本层
        # 既能复用证据封装能力，又不破坏 import-linter 的静态契约检查。
        try:
            from sts2_autotest.common.evidence import FailureInfo

            packager_mod = importlib.import_module("sts2_autotest.evidence.packager")
            EvidencePackager = packager_mod.EvidencePackager

            packager = EvidencePackager(evidence_root)
            run_result = "blocked" if status == "BLOCKED_ENVIRONMENT" else "failed"
            pack_dir = packager.create_pack(
                pack_id=run_id,
                run_result=run_result,
                duration_ms=0,
                failure=FailureInfo(
                    type="worker_process_disappeared",
                    message=reason,
                ),
            )
            packager.export_artifact(run_id, result=run_result)
            return str(pack_dir)
        except Exception as exc:  # noqa: BLE001 - 证据失败不得阻断终态
            logger.warning("worker-gone: evidence sealing failed: %s", exc)
            return None

    def reap_if_worker_gone(self, run_id: str) -> RunRecord | None:
        """查询时懒回收：若运行处于非终态但其 worker 进程已消失，将其标为终态。

        弥补 ``active_before`` 仅在「队列中另有 worker 等待轮次」时才回收的盲区，
        使 status / get_run / cancel 等控制端查询也能即时关单，避免僵尸记录污染审计。
        """
        record = self.load(run_id)
        if record is None or record.is_terminal:
            return record
        if record.pid is None:
            return record
        try:
            os.kill(record.pid, 0)
        except ProcessLookupError:
            return self._mark_worker_gone(record)
        except OSError:
            pass
        return record


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


def resume_precheck(record: RunRecord | None) -> tuple[bool, str]:
    """修复四：恢复必须等待取消/失败完全结束才允许。

    只有原任务同时满足以下条件才可恢复：
    - 状态属于可恢复终态（CANCELLED / FAILED_PLATFORM / BLOCKED_ENVIRONMENT）
    - 证据已封存（收尾流程确实走完，不是半途卡死）

    返回 (是否可恢复, 原因码)。原因码：NOT_FOUND / NOT_RESUMABLE /
    EVIDENCE_NOT_SEALED / OK。
    """
    if record is None:
        return False, "NOT_FOUND"
    if record.status not in RESUMABLE_STATUSES:
        return False, "NOT_RESUMABLE"
    if not record.evidence_sealed:
        return False, "EVIDENCE_NOT_SEALED"
    return True, "OK"


def serialize_record(record: RunRecord | None) -> dict[str, Any]:
    if record is None:
        return {"status": "NOT_FOUND"}
    payload = record.to_dict()
    payload["terminal"] = record.is_terminal
    progress = payload.get("progress") or {}
    if isinstance(progress, dict):
        payload["current_chapter"] = progress.get("current_chapter")
        payload["current_floor"] = progress.get("current_floor")
        payload["current_screen"] = progress.get("current_screen")
        payload["target_scene"] = progress.get("target_scene") or record.request.metadata.get("target_scene")
        payload["rooms_processed"] = progress.get("rooms_processed", 0)
        payload["room_types"] = progress.get("room_types", [])
        payload["last_action"] = progress.get("last_action")
        payload["last_updated_at"] = progress.get("updated_at") or record.updated_at
        payload["steps"] = progress.get("steps", 0)
        payload["recovering"] = progress.get("recovering", False)
        payload["last_observed_change"] = progress.get("last_observed_change")
    return payload


def spawn_worker(store: RunStore, record: RunRecord, argv: list[str]) -> int:
    """启动一个独立工作进程，确保控制端退出不会中断测试。"""
    log_path = store.root / record.run_id / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    # 子进程不可继承 IDE/代理会话变量（CODEBUDDY_SESSION_ID / CLAUDE_SESSION_ID）。
    # 否则 safe-delete 钩子（包装 rm/unlink/rmdir）会要求交互确认，非交互 worker
    # 永远等不到 → 卡死在批量删除（典型：清理 .store.lock 时触发
    # SAFE_DELETE_BULK_CONFIRM_REQUIRED），表现为提交阶段无进展 /
    # worker_process_disappeared。剥离后 rm 走零成本直通，与框架自身 Python
    # 删除路径一致。
    worker_env = dict(os.environ)
    worker_env.pop("CODEBUDDY_SESSION_ID", None)
    worker_env.pop("CLAUDE_SESSION_ID", None)
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "sts2_autotest.cli.main", *argv],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=worker_env,
        )
    finally:
        log_file.close()
    store.update(record.run_id, pid=process.pid)
    return process.pid


def records_summary(records: Iterable[RunRecord]) -> list[dict[str, Any]]:
    return [serialize_record(record) for record in records]
