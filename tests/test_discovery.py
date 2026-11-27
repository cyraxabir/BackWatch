"""Tests for backup discovery.

Tests:
1. IP directory discovery
2. YYYY/MM/DD discovery
3. Backup group discovery
4. Non-IP directories ignored in auto-discovery
5. Special directories discovered when configured
"""

import os

import pytest

from app.checks.discovery import (
    _is_ip_directory,
    _parse_df_output,
    discover_all,
    discover_date_directories,
    discover_df_mounts,
    discover_system_storage,
    discover_system_files,
)


class TestIPDirectoryDetection:
    """Test 1: IP directory discovery."""

    def test_valid_ip(self):
        assert _is_ip_directory("192.0.2.10") is True

    def test_valid_ip_2(self):
        assert _is_ip_directory("192.168.1.1") is True

    def test_valid_ip_3(self):
        assert _is_ip_directory("192.0.2.30") is True

    def test_non_ip_name(self):
        assert _is_ip_directory("SPECIAL_ARCHIVE") is False

    def test_non_ip_name_2(self):
        assert _is_ip_directory("LEGACY_BACKUP_DIR") is False

    def test_non_ip_name_3(self):
        assert _is_ip_directory("prpdb_prod") is False

    def test_non_ip_name_4(self):
        assert _is_ip_directory("custom-project") is False


class TestDateDiscovery:
    """Test 2: YYYY/MM/DD discovery."""

    def test_discovers_date_dirs(self, config):
        date_dirs = discover_date_directories(config)
        dates = [d for _, _, d in date_dirs]
        assert "2026-11-22" in dates
        assert "2026-11-15" in dates
        assert "2026-11-19" in dates

    def test_date_dirs_have_server(self, config):
        date_dirs = discover_date_directories(config)
        servers = set(s for _, s, _ in date_dirs)
        assert "192.0.2.10" in servers
        assert "192.0.2.30" in servers

    def test_non_ip_dirs_excluded(self, config):
        date_dirs = discover_date_directories(config)
        servers = set(s for _, s, _ in date_dirs)
        assert "SPECIAL_ARCHIVE" not in servers
        assert "SPECIAL_DIR" not in servers


class TestBackupGroupDiscovery:
    """Test 3: Backup group discovery."""

    def test_discovers_groups(self, config):
        groups = discover_all(config)
        group_names = [g.group_name for g in groups]
        assert "postgres_prod" in group_names
        assert "analytics_db" in group_names
        assert "database" in group_names

    def test_groups_have_server(self, config):
        groups = discover_all(config)
        for g in groups:
            if g.group_name == "postgres_prod":
                assert g.server == "192.0.2.10"
            elif g.group_name == "database":
                assert g.server == "192.0.2.30"

    def test_groups_have_folder_date(self, config):
        groups = discover_all(config)
        for g in groups:
            if g.group_name == "postgres_prod":
                assert g.folder_date == "2026-11-22"

    def test_special_dir_discovered(self, config):
        groups = discover_all(config)
        special_groups = [g for g in groups if g.is_special]
        assert len(special_groups) > 0
        special_names = [g.group_name for g in special_groups]
        assert "SPECIAL_DIR" in special_names

    def test_discovers_all_expected_groups(self, config):
        """Verify total count of discovered groups."""
        groups = discover_all(config)
        # Should find: postgres_prod, analytics_db, old_group, database,
        #              zero_group, corrupt_group, sql_group, SPECIAL_DIR
        assert len(groups) >= 7


class TestSystemStorageAndFiles:
    """Test storage capacity and helper files discovery."""

    def test_discover_system_storage(self, tmp_path):
        storage = discover_system_storage(str(tmp_path))
        assert "total_bytes" in storage
        assert "used_bytes" in storage
        assert "free_bytes" in storage
        assert "percent_used" in storage
        assert storage["total_bytes"] > 0
        assert storage["mount_point"]

    def test_discover_system_files(self, tmp_path):
        # Create helper scripts and logs
        sh_file = tmp_path / "sync_backup.sh"
        sh_file.write_text("#!/bin/bash\necho backup\n")
        log_file = tmp_path / "upload.log"
        log_file.write_text("Upload finished at 04:00\n")

        res = discover_system_files(str(tmp_path))
        assert res["total_count"] == 2
        assert res["scripts_count"] == 1
        assert res["logs_count"] == 1
        assert len(res["sample_files"]) == 2
        names = [f["filename"] for f in res["sample_files"]]
        assert "sync_backup.sh" in names
        assert "upload.log" in names

    def test_parse_df_output_linux(self):
        sample_df = (
            "Filesystem     Type     1B-blocks        Used    Available Capacity Mounted on\n"
            "/dev/sda1      ext4   100000000000 40000000000 60000000000      40% /\n"
            "/dev/sdb1      xfs   2000000000000 800000000000 1200000000000   40% /var/backups/db\n"
            "tmpfs          tmpfs    8000000000     1000000   7999000000       1% /dev/shm\n"
        )
        mounts = _parse_df_output(
            sample_df,
            backup_root="/var/backups/db",
            state_dir="/var/lib/backup-health-monitor",
        )
        # tmpfs should be excluded
        assert len(mounts) == 2
        assert mounts[0]["mounted_on"] == "/var/backups/db"
        assert mounts[0]["fs_type"] == "xfs"
        assert mounts[0]["is_backup_mount"] is True
        assert mounts[1]["mounted_on"] == "/"
        assert mounts[1]["is_state_mount"] is True

    def test_discover_df_mounts_fallback(self, tmp_path):
        mounts = discover_df_mounts(str(tmp_path), str(tmp_path))
        assert len(mounts) >= 1
        assert mounts[0]["size_bytes"] > 0
        assert mounts[0]["is_backup_mount"] is True
