"""Tests for core/lock_manager.py — process mutex with portalocker (Story 4.6, FR65)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sts2_autotest.core.lock_manager import LockManager


class TestLockManagerInit:
    def test_lock_path(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lm = LockManager(str(lock_path))
        assert lm.lock_path == lock_path


class TestLockManagerAcquire:
    def test_acquire_lock_success(self, tmp_path: Path) -> None:
        """Acquiring lock creates the lock file."""
        lock_path = tmp_path / "test.lock"
        lm = LockManager(str(lock_path))

        with patch("portalocker.lock") as mock_lock:
            result = lm.acquire_lock(timeout=0)

        assert result is True
        assert lm._lock_file is not None

    def test_acquire_lock_failure(self, tmp_path: Path) -> None:
        """When portalocker raises, acquire returns False."""
        lock_path = tmp_path / "test.lock"
        lm = LockManager(str(lock_path))

        with patch("portalocker.lock", side_effect=Exception("lock denied")):
            result = lm.acquire_lock(timeout=0)

        assert result is False

    def test_acquire_lock_timeout(self, tmp_path: Path) -> None:
        """acquire_lock with timeout > 0 waits and returns False on timeout."""
        lock_path = tmp_path / "test.lock"
        lm = LockManager(str(lock_path))

        # portalocker always fails
        with patch("portalocker.lock", side_effect=Exception("locked")):
            result = lm.acquire_lock(timeout=0.3)

        assert result is False

    def test_already_locked_returns_true(self, tmp_path: Path) -> None:
        """If already holding the lock, acquire returns True immediately."""
        lock_path = tmp_path / "test.lock"
        lm = LockManager(str(lock_path))

        with patch("portalocker.lock"):
            lm.acquire_lock(timeout=0)
            assert lm.acquire_lock(timeout=0) is True


class TestLockManagerRelease:
    def test_release_lock_removes_file(self, tmp_path: Path) -> None:
        """Releasing the lock removes the lock file."""
        lock_path = tmp_path / "test.lock"
        lm = LockManager(str(lock_path))

        with patch("portalocker.lock"), patch("portalocker.unlock"):
            lm.acquire_lock(timeout=0)
            # Write something so the file exists
            lock_path.write_text("12345")
            assert lock_path.is_file()

            lm.release_lock()

        assert not lock_path.exists()
        assert lm._lock_file is None

    def test_release_without_acquire(self, tmp_path: Path) -> None:
        """Releasing without acquiring does not raise."""
        lock_path = tmp_path / "test.lock"
        lm = LockManager(str(lock_path))
        lm.release_lock()  # Should not raise


class TestLockManagerIsLocked:
    def test_is_locked_when_held(self, tmp_path: Path) -> None:
        """is_locked returns True when we hold the lock."""
        lock_path = tmp_path / "test.lock"
        lm = LockManager(str(lock_path))

        with patch("portalocker.lock"):
            lm.acquire_lock(timeout=0)
            assert lm.is_locked() is True

    def test_is_locked_by_other(self, tmp_path: Path) -> None:
        """is_locked returns True when another process holds the lock."""
        lock_path = tmp_path / "test.lock"
        lm = LockManager(str(lock_path))

        with patch("portalocker.lock", side_effect=Exception("locked")):
            assert lm.is_locked() is True

    def test_is_locked_when_free(self, tmp_path: Path) -> None:
        """is_locked returns False when no one holds the lock."""
        lock_path = tmp_path / "test.lock"
        lm = LockManager(str(lock_path))

        with patch("portalocker.lock"), patch("portalocker.unlock"):
            assert lm.is_locked() is False

    def test_is_locked_does_not_hold_lock_after_probe(self, tmp_path: Path) -> None:
        """Regression: is_locked on a free lock does not leave this manager holding it."""
        lock_path = tmp_path / "test.lock"
        lm = LockManager(str(lock_path))

        with patch("portalocker.lock"), patch("portalocker.unlock"):
            lm.is_locked()  # probe

        # Must not leave self._lock_file populated
        assert lm._lock_file is None

    def test_is_locked_does_not_block_other_manager(self, tmp_path: Path) -> None:
        """Regression: is_locked probe does not prevent another LockManager from acquiring."""
        lock_path = tmp_path / "test.lock"
        lm1 = LockManager(str(lock_path))
        lm2 = LockManager(str(lock_path))

        with patch("portalocker.lock"), patch("portalocker.unlock"):
            lm1.is_locked()  # probe

        # lm2 can still acquire
        with patch("portalocker.lock"):
            assert lm2.acquire_lock(timeout=0) is True


class TestCleanStaleLock:
    def test_clean_stale_lock_removes_file(self, tmp_path: Path) -> None:
        """Stale lock from dead PID is cleaned up."""
        lock_path = tmp_path / "test.lock"
        lock_path.write_text("999999")  # Non-existent PID
        lm = LockManager(str(lock_path))

        lm._clean_stale_lock()

        assert not lock_path.exists()

    def test_clean_stale_lock_keeps_alive(self, tmp_path: Path) -> None:
        """Lock from alive PID is preserved."""
        lock_path = tmp_path / "test.lock"
        import os
        lock_path.write_text(str(os.getpid()))
        lm = LockManager(str(lock_path))

        lm._clean_stale_lock()

        assert lock_path.exists()
