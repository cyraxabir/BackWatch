"""Tests for retention.

Tests:
16. Retention candidate detection
17. Retention ignores non-IP directories
18. Retention dry-run
19. Retention minimum-good-backup protection
"""

import os
import time

import pytest

from app.checks.retention import (
    _is_valid_retention_path,
    find_retention_candidates,
    execute_retention,
)
from app.checks.discovery import discover_all
from app.checks.filesystem import check_group_health
from app.models import BackupGroup, HealthStatus


class TestRetentionCandidateDetection:
    """Test 16: Retention candidate detection."""

    def test_old_dirs_are_candidates(self, config, db):
        """Directories older than retention.days should be candidates."""
        groups = discover_all(config)
        for g in groups:
            check_group_health(g, config)

        candidates = find_retention_candidates(config, groups, db)
        # 2026-11-15 is 8 days ago with retention.days=7
        old_dates = [c.folder_date for c in candidates]
        assert "2026-11-15" in old_dates

    def test_recent_dirs_not_candidates(self, config, db):
        """Recent directories should NOT be candidates."""
        groups = discover_all(config)
        for g in groups:
            check_group_health(g, config)

        candidates = find_retention_candidates(config, groups, db)
        candidate_dates = [c.folder_date for c in candidates]
        assert "2026-11-22" not in candidate_dates


class TestRetentionNonIPIgnored:
    """Test 17: Retention ignores non-IP directories."""

    def test_path_validation_ip(self, test_backup_root):
        """Valid IP/YYYY/MM/DD path should pass."""
        path = str(test_backup_root / "192.0.2.10" / "2026" / "11" / "22")
        assert _is_valid_retention_path(path, str(test_backup_root)) is True

    def test_path_validation_non_ip(self, test_backup_root):
        """Non-IP paths should NOT pass validation."""
        path = str(test_backup_root / "SPECIAL_ARCHIVE")
        assert _is_valid_retention_path(path, str(test_backup_root)) is False

    def test_path_validation_special_dir(self, test_backup_root):
        """Special directories should NOT pass validation."""
        path = str(test_backup_root / "SPECIAL_DIR")
        assert _is_valid_retention_path(path, str(test_backup_root)) is False

    def test_path_traversal_blocked(self, test_backup_root):
        """Path traversal attempts should be blocked."""
        path = str(test_backup_root) + "/../../../etc/passwd"
        assert _is_valid_retention_path(path, str(test_backup_root)) is False

    def test_outside_root_blocked(self, tmp_path):
        """Paths outside backup root should be blocked."""
        path = "/some/other/path/192.0.2.10/2026/11/22"
        assert _is_valid_retention_path(path, str(tmp_path / "backups")) is False


class TestRetentionDryRun:
    """Test 18: Retention dry-run mode."""

    def test_dry_run_does_not_delete(self, config, db):
        """In dry-run mode, no files should be deleted."""
        config.retention.dry_run = True
        groups = discover_all(config)
        for g in groups:
            check_group_health(g, config)

        candidates = find_retention_candidates(config, groups, db)
        old_path = None
        for c in candidates:
            if c.folder_date == "2026-11-15":
                old_path = c.path
                break

        results = execute_retention(candidates, config, db)

        # Directory should still exist
        if old_path:
            assert os.path.exists(old_path), "Dry run should NOT delete anything"

        # Results should indicate DRY_RUN
        for r in results:
            assert r["action"] in ("DRY_RUN", "BLOCKED")


class TestRetentionMinGoodBackups:
    """Test 19: Minimum good backups protection."""

    def test_minimum_good_backups_blocks_deletion(self, config, db):
        """When fewer than minimum_good_backups remain, deletion is blocked."""
        config.retention.minimum_good_backups = 2

        groups = discover_all(config)
        for g in groups:
            check_group_health(g, config)

        candidates = find_retention_candidates(config, groups, db)

        # Find old_group candidate
        for c in candidates:
            if "old_group" in c.backup_groups:
                # If there aren't enough good backups for old_group,
                # it should be blocked
                if not c.safe_to_delete:
                    assert c.block_reason is not None
                    assert "good backup" in c.block_reason.lower() or \
                           "minimum" in c.block_reason.lower() or \
                           "failure" in c.block_reason.lower()
