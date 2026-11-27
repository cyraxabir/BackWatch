"""Tests for backup freshness/staleness detection.

Tests:
10. Stale mtime detection
11. Fresh mtime detection
12. Zero-byte backup detection
13. Folder date does NOT override mtime

CRITICAL: Tests verify that actual file mtime is used, not folder date.
"""

import os
import time

import pytest

from app.checks.discovery import discover_all
from app.checks.filesystem import check_freshness, check_file_sizes, check_group_health
from app.models import BackupGroup, HealthStatus


class TestFreshnessDetection:
    """Test freshness based on actual file mtime."""

    def test_stale_mtime_detected(self, config):
        """Test 10: File with mtime > 26h ago is detected as stale.

        The file at 192.0.2.30/2026/11/19/database/stale.sql.gz
        has mtime set to 4 days ago — it must be STALE/FAIL.
        """
        groups = discover_all(config)
        stale_group = None
        for g in groups:
            if g.server == "192.0.2.30" and g.group_name == "database":
                stale_group = g
                break

        assert stale_group is not None, "Stale group not found"
        assert len(stale_group.files) > 0

        check_group_health(stale_group, config)
        assert stale_group.health == HealthStatus.FAIL
        assert any("STALE" in e for e in stale_group.errors)

    def test_fresh_mtime_passes(self, config):
        """Test 11: File with recent mtime passes freshness check."""
        groups = discover_all(config)
        fresh_group = None
        for g in groups:
            if g.server == "192.0.2.10" and g.group_name == "postgres_prod":
                fresh_group = g
                break

        assert fresh_group is not None, "Fresh group not found"
        check_group_health(fresh_group, config)
        # Fresh files should not have STALE errors
        assert not any("STALE" in e for e in fresh_group.errors)

    def test_zero_byte_detected(self, config):
        """Test 12: Zero-byte backup file is detected."""
        groups = discover_all(config)
        zero_group = None
        for g in groups:
            if g.group_name == "zero_group":
                zero_group = g
                break

        assert zero_group is not None, "Zero group not found"
        check_group_health(zero_group, config)
        assert zero_group.health == HealthStatus.FAIL
        assert any("ZERO_BYTE" in e for e in zero_group.errors)


class TestFolderDateVsMtime:
    """Test that folder date does NOT determine freshness."""

    def test_folder_date_does_not_override_mtime(self, config, test_backup_root):
        """Test 13: A file under 2026/11/22 with mtime = Nov 19 is STALE.

        This is the MOST CRITICAL test.

        Folder: 2026/11/22/
        File mtime: 4 days ago

        The system must detect this as STALE based on mtime,
        NOT consider it fresh because the folder says "Nov 22".
        """
        # Create a file under a "recent" folder date but with OLD mtime
        trick_group = test_backup_root / "192.0.2.10" / "2026" / "11" / "22" / "trick_group"
        trick_group.mkdir(parents=True, exist_ok=True)

        import gzip
        trick_file = trick_group / "tricky.sql.gz"
        with gzip.open(trick_file, "wb") as f:
            f.write(b"-- old backup\n")

        # Set mtime to 4 days ago (96 hours > 26 hours = STALE)
        old_time = time.time() - (4 * 86400)
        os.utime(trick_file, (old_time, old_time))

        # Discover and check
        groups = discover_all(config)
        trick = None
        for g in groups:
            if g.group_name == "trick_group":
                trick = g
                break

        assert trick is not None, "Trick group not found"
        assert trick.folder_date == "2026-11-22"

        check_group_health(trick, config)

        # Must be FAIL/STALE — folder date says Nov 22 but mtime is Nov 19
        assert trick.health == HealthStatus.FAIL
        assert any("STALE" in e for e in trick.errors)

        # Verify the age is based on mtime
        newest = trick.newest_file
        assert newest is not None
        assert newest.age_hours > 26, (
            f"Age should be >26h based on mtime, got {newest.age_hours:.1f}h"
        )
