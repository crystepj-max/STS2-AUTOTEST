"""Disk space guard — pre-write space check with atomic write fallback (AC3)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from sts2_autotest.common.logging import get_logger

logger = get_logger("core.disk_guard")


def check_disk_space(path: str, threshold_mb: int = 100) -> bool:
    """Quick disk space check — returns True if free space >= threshold.

    Module-level convenience wrapper around DiskGuard.can_write().
    """
    return DiskGuard(path, threshold_mb=threshold_mb).can_write()


class DiskGuard:
    """Pre-write disk space check. Skips non-critical writes when space is low."""

    def __init__(self, path: str, threshold_mb: int = 100) -> None:
        self._path = path
        self._threshold_bytes = threshold_mb * 1024 * 1024

    @property
    def threshold_mb(self) -> int:
        return self._threshold_bytes // (1024 * 1024)

    def can_write(self) -> bool:
        """Check whether the disk containing *path* has enough free space.

        Returns True if free space >= threshold, False otherwise.
        """
        try:
            usage = shutil.disk_usage(self._path)
            return usage.free >= self._threshold_bytes
        except OSError:
            logger.warning("Cannot check disk usage for %s", self._path)
            return False

    def safe_write(self, target: Path, content: bytes) -> bool:
        """Atomic write with pre-write space check.

        Returns True on success, False if disk space is insufficient.
        On False, a WARNING is logged and the caller should skip the write.
        """
        if not self.can_write():
            logger.warning(
                "Insufficient disk space for %s (threshold: %d MB) — skipping",
                target, self.threshold_mb,
            )
            return False

        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            tmp.write_bytes(content)
            os.replace(str(tmp), str(target))
            return True
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
