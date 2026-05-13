"""Process-level mutex lock using portalocker (FR65)."""

from __future__ import annotations

import os
import time
from io import IOBase
from pathlib import Path
from typing import Any

import portalocker

from sts2_autotest.common.logging import get_logger

logger = get_logger("core.lock_manager")


class LockManager:
    """Process-level mutual exclusion via portalocker file lock.

    Usage:
        lock = LockManager(lock_path)
        if lock.acquire_lock(timeout=0):
            # Critical section
            lock.release_lock()
    """

    def __init__(self, lock_path: str) -> None:
        self._lock_path = Path(lock_path)
        self._lock_file: IOBase | None = None

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def acquire_lock(self, timeout: float = 0.0) -> bool:
        """Acquire the file lock.

        Args:
            timeout: Max wait time in seconds. 0 = non-blocking.

        Returns:
            True if lock acquired, False if another process holds it.
        """
        if self._lock_file is not None:
            return True  # Already locked

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)

        if timeout > 0:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._try_acquire():
                    return True
                time.sleep(0.1)
            logger.warning(
                "Lock acquisition timed out after %.1fs for %s",
                timeout, self._lock_path,
            )
            # Check if stale lock should be cleaned
            self._clean_stale_lock()
            return False

        return self._try_acquire()

    def _try_acquire(self) -> bool:
        """Non-blocking lock attempt. Returns True on success."""
        fd: IOBase | None = None
        try:
            fd = open(str(self._lock_path), "a", encoding="utf-8")
            portalocker.lock(fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
            # Truncate and write PID for stale lock detection
            fd.seek(0)
            fd.truncate()
            fd.write(str(os.getpid()))
            fd.flush()
            self._lock_file = fd
            logger.debug("Lock acquired: %s", self._lock_path)
            return True
        except Exception:
            if fd is not None:
                try:
                    fd.close()
                except OSError:
                    pass
            return False

    def release_lock(self) -> None:
        """Release the lock and clean up the lock file."""
        if self._lock_file is None:
            return
        try:
            portalocker.unlock(self._lock_file)
            self._lock_file.close()
        except OSError:
            pass
        finally:
            self._lock_file = None
            try:
                self._lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            logger.debug("Lock released: %s", self._lock_path)

    def is_locked(self) -> bool:
        """Check if the lock is held by another process.

        Non-mutating probe: uses a temporary file descriptor that is
        opened and closed within this call. Never leaves this manager
        holding the lock or mutates self._lock_file.
        """
        if self._lock_file is not None:
            return True  # We hold the lock

        try:
            fd = open(str(self._lock_path), "a", encoding="utf-8")
        except OSError:
            return False

        try:
            portalocker.lock(fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
            # Lock was free — release immediately (no truncation)
            portalocker.unlock(fd)
            fd.close()
            return False
        except Exception:
            fd.close()
            return True

    def _clean_stale_lock(self) -> None:
        """Check if the lock file exists and the holding process is gone.

        If the process is gone, remove the stale lock file so a new
        LockManager can acquire the lock.
        """
        if not self._lock_path.is_file():
            return

        # Try to read the PID from the lock file
        try:
            content = self._lock_path.read_text(encoding="utf-8").strip()
            if content.isdigit():
                pid = int(content)
                if not self._is_pid_alive(pid):
                    logger.warning("Removing stale lock from dead PID %d", pid)
                    self._lock_path.unlink(missing_ok=True)
                    return
        except OSError:
            pass

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """Check if a PID is alive (cross-platform)."""
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
