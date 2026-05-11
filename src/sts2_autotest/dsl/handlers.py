"""Error handler callbacks for the Fluent API (FR15)."""

from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.types import CaptureResult
from sts2_autotest.core.orchestrator import TestOrchestrator

logger = get_logger("dsl.handlers")

_DEFAULT_WINDOW_TITLE = "Slay the Spire 2"


def log_state(orchestrator: TestOrchestrator, case_id: str) -> None:
    """Log the current game state on error. Also collect filtered logs if available."""
    screen = orchestrator._current_screen
    logger.info("[%s] Error handler: current screen = %s", case_id, screen.value)

    log_collector = getattr(orchestrator.evidence, "_log_collector", None)
    if log_collector is not None:
        log_collector.collect_on_failure(case_id)


def capture_screenshot(orchestrator: TestOrchestrator, case_id: str) -> None:
    """Capture a bug snapshot on error.

    Accesses ScreenCaptureProtocol through the evidence hooks' _capture
    attribute (RealEvidenceHooks). Falls back to log-only if unavailable.
    """
    capture = getattr(orchestrator.evidence, "_capture", None)
    if capture is None:
        logger.warning(
            "[%s] No screenshot capture available — evidence hooks not configured",
            case_id,
        )
        return

    result: CaptureResult = capture.capture_with_validation(
        _DEFAULT_WINDOW_TITLE, case_id
    )
    if result.ok:
        logger.info("[%s] Screenshot saved: %s", case_id, result.path)
    elif result.status == "skipped":
        logger.warning("[%s] Screenshot skipped: %s", case_id, result.message)
    else:
        logger.warning("[%s] Screenshot error: %s", case_id, result.message)
