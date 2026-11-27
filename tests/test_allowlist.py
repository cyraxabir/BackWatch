"""Tests for backup file allow-list.

Tests:
4. .sql.gz allow-list
5. .sql allow-list
6. .dump allow-list
7. .txt ignored
8. .sh ignored
9. .md ignored
"""

import pytest

from app.config import AppConfig


class TestAllowList:
    """Test backup file pattern matching."""

    @pytest.fixture
    def app_config(self):
        return AppConfig()

    # Test 4: .sql.gz allowed
    def test_sql_gz_allowed(self, app_config):
        assert app_config.matches_backup_pattern("database.sql.gz") is True

    # Test 5: .sql allowed
    def test_sql_allowed(self, app_config):
        assert app_config.matches_backup_pattern("database.sql") is True

    # Test 6: .dump allowed
    def test_dump_allowed(self, app_config):
        assert app_config.matches_backup_pattern("database.dump") is True

    def test_backup_allowed(self, app_config):
        assert app_config.matches_backup_pattern("database.backup") is True

    def test_tar_gz_allowed(self, app_config):
        assert app_config.matches_backup_pattern("backup.tar.gz") is True

    def test_tgz_allowed(self, app_config):
        assert app_config.matches_backup_pattern("backup.tgz") is True

    def test_zip_allowed(self, app_config):
        assert app_config.matches_backup_pattern("backup.zip") is True

    # Test 7: .txt ignored
    def test_txt_ignored(self, app_config):
        assert app_config.matches_backup_pattern("backup.txt") is False

    # Test 8: .sh ignored
    def test_sh_ignored(self, app_config):
        assert app_config.matches_backup_pattern("script.sh") is False

    # Test 9: .md ignored
    def test_md_ignored(self, app_config):
        assert app_config.matches_backup_pattern("README.md") is False

    def test_log_ignored(self, app_config):
        assert app_config.matches_backup_pattern("app.log") is False

    def test_py_ignored(self, app_config):
        assert app_config.matches_backup_pattern("script.py") is False

    def test_case_insensitive(self, app_config):
        assert app_config.matches_backup_pattern("DATABASE.SQL.GZ") is True
        assert app_config.matches_backup_pattern("Backup.TAR.GZ") is True


class TestAllowListInDiscovery:
    """Test that discovery actually filters files."""

    def test_only_backup_files_discovered(self, config):
        """Verify .txt and .sh files are not in discovered groups."""
        from app.checks.discovery import discover_all

        groups = discover_all(config)
        for group in groups:
            if group.group_name == "postgres_prod":
                filenames = [f.filename for f in group.files]
                assert "db1.sql.gz" in filenames
                assert "db2.sql.gz" in filenames
                assert "ignored.txt" not in filenames
                assert "script.sh" not in filenames
                # Should have exactly 2 valid backup files
                assert len(group.files) == 2

    def test_sql_and_dump_discovered(self, config):
        """Verify .sql and .dump files are discovered."""
        from app.checks.discovery import discover_all

        groups = discover_all(config)
        for group in groups:
            if group.group_name == "sql_group":
                filenames = [f.filename for f in group.files]
                assert "data.sql" in filenames
                assert "data.dump" in filenames
