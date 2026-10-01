"""Error handler callbacks for the Fluent API (FR15).

具名错误处理函数库：用户经 ``define().on_error(log_state, capture_screenshot)``
以名字订阅。实现细节已折叠进 orchestrator 的公开方法
（log_state_on_failure / capture_failure_screenshot），本模块只保留
DSL 侧的稳定命名入口，不再触碰任何私有字段。
"""

from sts2_autotest.common.logging import get_logger
from sts2_autotest.core.orchestrator import TestOrchestrator

logger = get_logger("dsl.handlers")


def log_state(orchestrator: TestOrchestrator, case_id: str) -> None:
    """Log the current game state on error. Also collect filtered logs if available."""
    orchestrator.log_state_on_failure(case_id)


def capture_screenshot(orchestrator: TestOrchestrator, case_id: str) -> None:
    """Capture a bug snapshot on error. Falls back to log-only if unavailable."""
    result = orchestrator.capture_failure_screenshot(case_id)
    if result is None:
        logger.warning(
            "[%s] No screenshot capture available — evidence hooks not configured",
            case_id,
        )
        return
    if result.ok:
        logger.info("[%s] Screenshot saved: %s", case_id, result.path)
    elif result.status == "skipped":
        logger.warning("[%s] Screenshot skipped: %s", case_id, result.message)
    else:
        logger.warning("[%s] Screenshot error: %s", case_id, result.message)
