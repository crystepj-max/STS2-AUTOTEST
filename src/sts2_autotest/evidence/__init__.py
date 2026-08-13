"""Evidence & observability package for STS2-AUTOTEST."""

from sts2_autotest.evidence.capture import ScreenCapture
from sts2_autotest.evidence.logs import LogCollectionResult, LogCollector
from sts2_autotest.evidence.metrics import MetricEvent, MetricsCollector
from sts2_autotest.evidence.packager import EvidencePackager

__all__ = [
    "EvidencePackager",
    "LogCollectionResult",
    "LogCollector",
    "MetricEvent",
    "MetricsCollector",
    "ScreenCapture",
]
