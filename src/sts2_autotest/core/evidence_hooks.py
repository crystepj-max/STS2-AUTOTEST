"""Evidence hooks — lifecycle callbacks for evidence collection (FR20).

StubEvidenceHooks: no-op stub (MVP baseline).
RealEvidenceHooks: triggers screenshot + log collection on case end/crash,
and creates evidence pack on session end.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Protocol

from sts2_autotest.common.evidence import FailureInfo
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.types import ScreenCaptureProtocol
from sts2_autotest.core.action_model import TestResult

logger = get_logger("core.evidence_hooks")

_DEFAULT_WINDOW_TITLE = "Slay the Spire 2"


class LogCollectorProtocol(Protocol):
    """Protocol for log collection — breaks layer isolation cleanly."""

    def collect(self, case_id: str) -> object: ...
    def collect_on_failure(self, case_id: str) -> object: ...
    def collect_snapshot(self, case_id: str, *, max_bytes: int = 8 * 1024 * 1024) -> object: ...


class EvidencePackagerProtocol(Protocol):
    """Protocol for evidence packaging — breaks layer isolation cleanly."""

    def create_pack(
        self,
        pack_id: str | None = None,
        *,
        run_result: str = "unknown",
        duration_ms: int = 0,
        failure: FailureInfo | None = None,
    ) -> object: ...

    def export_artifact(self, pack_id: str, result: str = "unknown") -> object: ...

    def list_packs(self) -> list[object]: ...


class EvidenceHooks(Protocol):
    """Hooks triggered by the Orchestrator at evidence collection points."""

    def on_case_start(self, case_id: str) -> None: ...
    def on_case_end(self, result: TestResult) -> None: ...
    def on_crash(self, case_id: str, error: Exception) -> None: ...
    def on_session_end(self, summary: dict[str, Any]) -> None: ...
    def capture_state(self, case_id: str, state: dict[str, Any]) -> None: ...


class StubEvidenceHooks:
    """MVP no-op implementation."""

    def on_case_start(self, case_id: str) -> None:
        pass

    def on_case_end(self, result: TestResult) -> None:
        pass

    def on_crash(self, case_id: str, error: Exception) -> None:
        pass

    def on_session_end(self, summary: dict[str, Any]) -> None:
        pass

    def capture_state(self, case_id: str, state: dict[str, Any]) -> None:
        pass


def build_evidence_hooks(
    evidence_root: Path,
    *,
    pack_id: str | None = None,
) -> EvidenceHooks:
    """按统一策略构造证据采集器。

    证据实现属于可替换的运行期扩展，使用动态加载保持核心入口的分层边界。
    """
    if os.environ.get("STS2_AUTOTEST_EVIDENCE", "full").lower() == "none":
        return StubEvidenceHooks()

    screen_capture = importlib.import_module("sts2_autotest.evidence.capture").ScreenCapture
    log_collector = importlib.import_module("sts2_autotest.evidence.logs").LogCollector
    evidence_packager = importlib.import_module(
        "sts2_autotest.evidence.packager"
    ).EvidencePackager
    return RealEvidenceHooks(
        screen_capture(evidence_root / "screenshots"),
        log_collector=log_collector(evidence_root / "logs"),
        packager=evidence_packager(evidence_root),
        pack_id=pack_id,
        capture_screenshots=os.environ.get("STS2_AUTOTEST_EVIDENCE", "full").lower() == "full",
    )


class RealEvidenceHooks:
    """Evidence hooks with screenshot, log, and packager integration.

    Uses injected protocols for actual evidence collection.
    Failed cases get screenshot + filtered logs; passed cases get screenshot.
    Crash events get immediate screenshot + log capture.
    Session end creates evidence pack via packager.
    """

    def __init__(
        self,
        capture: ScreenCaptureProtocol,
        log_collector: LogCollectorProtocol | None = None,
        packager: EvidencePackagerProtocol | None = None,
        window_title: str = _DEFAULT_WINDOW_TITLE,
        pack_id: str | None = None,
        capture_screenshots: bool = True,
    ) -> None:
        self._capture = capture
        self._log_collector = log_collector
        self._packager = packager
        self._window_title = window_title
        self._pack_id = pack_id
        self._capture_screenshots = capture_screenshots
        self._last_failure: FailureInfo | None = None
        self._captured_screenshots: list[Path] = []
        self._captured_logs: list[Path] = []

    def on_case_start(self, case_id: str) -> None:
        logger.debug("Case %s started", case_id)

    def on_case_end(self, result: TestResult) -> None:
        """Capture screenshot + log on case end. Failure gets filtered logs."""
        if self._capture_screenshots:
            result_capture = self._capture.capture_with_validation(
                self._window_title, result.case_id
            )
            if result_capture.ok:
                if result_capture.path is not None:
                    self._captured_screenshots.append(Path(result_capture.path))
                logger.info(
                    "Screenshot saved for case %s: %s",
                    result.case_id,
                    result_capture.path,
                )
            elif result_capture.status == "skipped":
                logger.warning(
                    "Screenshot skipped for case %s: %s",
                    result.case_id,
                    result_capture.message,
                )
            else:
                logger.warning(
                    "Screenshot validation failed for case %s: %s",
                    result.case_id,
                    result_capture.message,
                )

        # Collect filtered logs on failure
        if result.status == "fail" and self._log_collector is not None:
            log_result = self._log_collector.collect_on_failure(result.case_id)
            log_path = getattr(log_result, "dest_path", None)
            if log_path is not None:
                self._captured_logs.append(Path(log_path))
        elif self._log_collector is not None:
            collect_snapshot = getattr(self._log_collector, "collect_snapshot", None)
            if callable(collect_snapshot):
                log_result = collect_snapshot(result.case_id)
            else:
                log_result = self._log_collector.collect(result.case_id)
            log_path = getattr(log_result, "dest_path", None)
            if log_path is not None:
                self._captured_logs.append(Path(log_path))

        # B10: capture failure info from non-crash failures when on_crash didn't fire first
        if self._last_failure is None and result.status in ("fail", "crash", "deterministic_fail"):
            self._last_failure = FailureInfo(
                type=(
                    "crash_error" if result.status == "crash"
                    else "assertion_error" if result.status == "fail"
                    else "session_error"
                ),
                message=result.detail or "",
            )

    def on_crash(self, case_id: str, error: Exception) -> None:
        """Capture screenshot + logs immediately on crash."""
        # B10: store crash details for repair suggestion generation at session end
        import traceback as tb_module

        from sts2_autotest.common.errors import STS2Error

        if isinstance(error, STS2Error):
            error_type = error.category.value
            exit_code = error.detail.get("exit_code") if error.detail else None
        else:
            error_type = type(error).__name__
            exit_code = None

        self._last_failure = FailureInfo(
            type=error_type,
            message=str(error),
            stack_trace="".join(
                tb_module.format_exception(type(error), error, error.__traceback__),
            ),
            exit_code=exit_code,
            stuck_screen=(
                (getattr(error, "last_state", None) or {}).get("screen")
                if isinstance(getattr(error, "last_state", None), dict)
                else None
            ),
            last_action=getattr(error, "last_action", None),
            last_state=getattr(error, "last_state", None),
        )

        if self._capture_screenshots:
            crash_capture = self._capture.capture(
                self._window_title, case_id=f"{case_id}_crash"
            )
            if crash_capture.ok:
                if crash_capture.path is not None:
                    self._captured_screenshots.append(Path(crash_capture.path))
                logger.info(
                    "Crash screenshot saved for case %s: %s",
                    case_id,
                    crash_capture.path,
                )
            else:
                logger.warning(
                    "Crash screenshot failed for case %s: %s",
                    case_id,
                    crash_capture.message,
                )

        if self._log_collector is not None:
            log_result = self._log_collector.collect_on_failure(f"{case_id}_crash")
            log_path = getattr(log_result, "dest_path", None)
            if log_path is not None:
                self._captured_logs.append(Path(log_path))

    def on_session_end(self, summary: dict[str, Any]) -> None:
        """Create evidence pack if packager is available."""
        logger.info(
            "Session ended: %d passed, %d failed, %d crashed, %d skipped",
            summary.get("passed", 0),
            summary.get("failed", 0),
            summary.get("crashed", 0),
            summary.get("skipped", 0),
        )

        if self._packager is not None:
            failed = summary.get("failed", 0)
            crashed = summary.get("crashed", 0)
            declared_status = str(summary.get("status", "")).upper()
            run_result = (
                "blocked"
                if declared_status == "BLOCKED_ENVIRONMENT"
                else "failed"
                if (failed + crashed) > 0
                else "passed"
            )
            # 真实耗时由调用方透传，避免报告里的 duration_ms 恒为 0。
            try:
                duration_ms = int(summary.get("duration_ms", 0) or 0)
            except (TypeError, ValueError):
                duration_ms = 0
            pack_kwargs: dict[str, Any] = {
                "run_result": run_result,
                "duration_ms": duration_ms,
            }
            if self._pack_id:
                pack_kwargs["pack_id"] = self._pack_id
            if self._last_failure is None:
                pack_result = self._packager.create_pack(**pack_kwargs)
            else:
                pack_kwargs["failure"] = self._last_failure
                pack_result = self._packager.create_pack(**pack_kwargs)
            self._last_failure = None  # Reset for next session
            # Export artifact (Story 4.7, FR54) — non-blocking
            try:
                if pack_result is not None:
                    copy_artifacts = getattr(self._packager, "copy_artifacts", None)
                    if callable(copy_artifacts):
                        copy_artifacts(
                            getattr(pack_result, "name", str(pack_result)),
                            screenshots=self._captured_screenshots,
                            logs=self._captured_logs,
                        )
                    pack_name = getattr(pack_result, "name", None)
                    if pack_name is not None:
                        self._packager.export_artifact(str(pack_name), result=run_result)
            except Exception:
                logger.warning("Artifact export failed (non-blocking)", exc_info=True)
            self._captured_screenshots.clear()
            self._captured_logs.clear()

    def capture_state(self, case_id: str, state: dict[str, Any]) -> None:
        """为关键场景保留一张游戏窗口截图。"""
        if not self._capture_screenshots:
            return
        result = self._capture.capture_with_validation(self._window_title, case_id)
        if result.ok and result.path is not None:
            self._captured_screenshots.append(Path(result.path))
        else:
            logger.warning("State screenshot skipped for %s: %s", case_id, result.message)
