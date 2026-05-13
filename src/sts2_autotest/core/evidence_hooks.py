"""Evidence hooks — lifecycle callbacks for evidence collection (FR20).

StubEvidenceHooks: no-op stub (MVP baseline).
RealEvidenceHooks: triggers screenshot + log collection on case end/crash,
and creates evidence pack on session end.
"""

from __future__ import annotations

from typing import Any, Protocol

from sts2_autotest.common.evidence import FailureInfo
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.types import CaptureResult, ScreenCaptureProtocol
from sts2_autotest.core.action_model import TestResult

logger = get_logger("core.evidence_hooks")

_DEFAULT_WINDOW_TITLE = "Slay the Spire 2"


class LogCollectorProtocol(Protocol):
    """Protocol for log collection — breaks layer isolation cleanly."""

    def collect(self, case_id: str) -> object: ...
    def collect_on_failure(self, case_id: str) -> object: ...


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
    ) -> None:
        self._capture = capture
        self._log_collector = log_collector
        self._packager = packager
        self._window_title = window_title

    def on_case_start(self, case_id: str) -> None:
        logger.debug("Case %s started", case_id)

    def on_case_end(self, result: TestResult) -> None:
        """Capture screenshot + log on case end. Failure gets filtered logs."""
        result_capture = self._capture.capture_with_validation(
            self._window_title, result.case_id
        )
        if result_capture.ok:
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
            self._log_collector.collect_on_failure(result.case_id)

    def on_crash(self, case_id: str, error: Exception) -> None:
        """Capture screenshot + logs immediately on crash."""
        crash_capture = self._capture.capture(
            self._window_title, case_id=f"{case_id}_crash"
        )
        if crash_capture.ok:
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
            self._log_collector.collect_on_failure(f"{case_id}_crash")

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
            run_result = "failed" if (failed + crashed) > 0 else "passed"
            pack_result = self._packager.create_pack(run_result=run_result)
            # Export artifact (Story 4.7, FR54) — non-blocking
            try:
                if pack_result is not None:
                    pack_name = getattr(pack_result, "name", None)
                    if pack_name is not None:
                        self._packager.export_artifact(str(pack_name), result=run_result)
            except Exception:
                logger.warning("Artifact export failed (non-blocking)", exc_info=True)
