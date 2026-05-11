"""Evidence & observability package for STS2-AUTOTEST."""

from sts2_autotest.evidence.capture import ScreenCapture
from sts2_autotest.evidence.logs import LogCollector, LogCollectionResult
from sts2_autotest.evidence.metrics import MetricEvent, MetricsCollector
from sts2_autotest.evidence.packager import EvidencePackager

__all__ = [
    "EvidencePackager",
    "LogCollector",
    "LogCollectionResult",
    "MetricEvent",
    "MetricsCollector",
    "ScreenCapture",
]
