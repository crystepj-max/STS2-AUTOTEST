"""Tests for core/disk_guard.py — DiskGuard pre-write space check (Story 4.4, AC3)."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from sts2_autotest.core.disk_guard import DiskGuard, check_disk_space


class TestDiskGuardInit:
    def test_default_threshold(self) -> None:
        dg = DiskGuard("/tmp")
        assert dg.threshold_mb == 100

    def test_custom_threshold(self) -> None:
        dg = DiskGuard("/tmp", threshold_mb=500)
        assert dg.threshold_mb == 500


class TestDiskGuardCanWrite:
    def test_enough_space_returns_true(self) -> None:
        """With plenty of free space, can_write returns True."""
        with patch("shutil.disk_usage") as mock_usage:
            mock_usage.return_value = shutil._ntuple_diskusage(
                total=1_000_000_000_000,
                used=500_000_000_000,
                free=500_000_000_000,
            )
            dg = DiskGuard("/fake", threshold_mb=100)
            assert dg.can_write() is True

    def test_insufficient_space_returns_false(self) -> None:
        """When free space is below threshold, can_write returns False."""
        with patch("shutil.disk_usage") as mock_usage:
            mock_usage.return_value = shutil._ntuple_diskusage(
                total=1_000_000,
                used=990_000,
                free=10_000,
            )
            dg = DiskGuard("/fake", threshold_mb=100)
            assert dg.can_write() is False

    def test_oserror_returns_false(self) -> None:
        """OSError from disk_usage returns False (degraded check)."""
        with patch("shutil.disk_usage", side_effect=OSError("no such path")):
            dg = DiskGuard("/nonexistent", threshold_mb=100)
            assert dg.can_write() is False


class TestDiskGuardSafeWrite:
    def test_safe_write_success(self, tmp_path: Path) -> None:
        """Successful safe_write writes content and no .tmp file remains."""
        dg = DiskGuard(str(tmp_path), threshold_mb=100)
        target = tmp_path / "test.txt"
        result = dg.safe_write(target, b"hello world")
        assert result is True
        assert target.read_bytes() == b"hello world"
        assert not target.with_suffix(".txt.tmp").exists()

    def test_safe_write_insufficient_space(self, tmp_path: Path) -> None:
        """When disk space is low, safe_write returns False without writing."""
        with patch.object(DiskGuard, "can_write", return_value=False):
            dg = DiskGuard(str(tmp_path), threshold_mb=100)
            target = tmp_path / "test.txt"
            result = dg.safe_write(target, b"hello world")
            assert result is False
            assert not target.exists()

    def test_safe_write_oserror_cleans_tmp(self, tmp_path: Path) -> None:
        """OSError during write cleans up tmp file."""
        dg = DiskGuard(str(tmp_path), threshold_mb=100)
        target = tmp_path / "test.txt"

        with patch.object(Path, "write_bytes", side_effect=OSError("write failed")):
            with pytest.raises(OSError):
                dg.safe_write(target, b"data")
            # .tmp file should be cleaned up
            tmp = target.with_suffix(".txt.tmp")
            assert not tmp.exists()


# ── check_disk_space helper ─────────────────────────────────


class TestCheckDiskSpace:
    def test_enough_space_returns_true(self) -> None:
        """Module-level helper returns True when space is sufficient."""
        with patch("shutil.disk_usage") as mock_usage:
            mock_usage.return_value = shutil._ntuple_diskusage(
                total=1_000_000_000_000,
                used=500_000_000_000,
                free=500_000_000_000,
            )
            assert check_disk_space("/fake", threshold_mb=100) is True

    def test_insufficient_space_returns_false(self) -> None:
        """Module-level helper returns False when space is low."""
        with patch("shutil.disk_usage") as mock_usage:
            mock_usage.return_value = shutil._ntuple_diskusage(
                total=1_000_000,
                used=990_000,
                free=10_000,
            )
            assert check_disk_space("/fake", threshold_mb=100) is False

    def test_default_threshold(self) -> None:
        """check_disk_space defaults to 100 MB threshold."""
        with patch("shutil.disk_usage") as mock_usage:
            mock_usage.return_value = shutil._ntuple_diskusage(
                total=1_000_000_000,
                used=0,
                free=200_000_000,  # ~190 MB — above default 100
            )
            assert check_disk_space("/fake") is True
