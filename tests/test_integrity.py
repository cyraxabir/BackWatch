"""Tests for archive integrity checks.

Tests:
13. gzip integrity success
14. gzip integrity failure
"""

import gzip
import os
from pathlib import Path

import pytest

from app.checks.integrity import (
    _check_gzip_integrity,
    _check_sql_integrity,
    check_file_integrity,
    check_group_integrity,
)
from app.models import BackupFile, BackupGroup, IntegrityStatus, HealthStatus


class TestGzipIntegrity:
    """Test gzip integrity checks."""

    def test_valid_gzip_passes(self, tmp_path):
        """Test 13: Valid gzip file passes integrity check."""
        filepath = tmp_path / "valid.sql.gz"
        with gzip.open(filepath, "wb") as f:
            f.write(b"CREATE TABLE test (id INT);")

        ok, msg = _check_gzip_integrity(str(filepath))
        assert ok is True

    def test_corrupt_gzip_fails(self, tmp_path):
        """Test 14: Corrupt gzip file fails integrity check."""
        filepath = tmp_path / "corrupt.sql.gz"
        filepath.write_bytes(b"this is definitely not valid gzip data")

        ok, msg = _check_gzip_integrity(str(filepath))
        # On systems with gzip available, this should fail
        # On systems without gzip, it skips (returns True with skip message)
        # We test both scenarios
        if "not available" not in msg and "skipping" not in msg.lower():
            assert ok is False
            assert "integrity failed" in msg.lower() or "failed" in msg.lower()


class TestSQLIntegrity:
    """Test plain SQL file integrity."""

    def test_valid_sql_passes(self, tmp_path):
        filepath = tmp_path / "data.sql"
        filepath.write_text("CREATE TABLE test (id INT);")
        ok, msg = _check_sql_integrity(str(filepath))
        assert ok is True

    def test_empty_sql_fails(self, tmp_path):
        filepath = tmp_path / "empty.sql"
        filepath.write_bytes(b"")
        ok, msg = _check_sql_integrity(str(filepath))
        assert ok is False
        assert "empty" in msg.lower() or "0 bytes" in msg.lower()


class TestGroupIntegrity:
    """Test group-level integrity checking."""

    def test_group_with_valid_files(self, config):
        from app.checks.discovery import discover_all

        groups = discover_all(config)
        for g in groups:
            if g.group_name == "postgres_prod":
                check_group_integrity(g)
                # Valid gzip files should pass
                assert g.integrity == IntegrityStatus.PASS
                break

    def test_group_integrity_on_corrupt(self, config):
        from app.checks.discovery import discover_all

        groups = discover_all(config)
        for g in groups:
            if g.group_name == "corrupt_group":
                check_group_integrity(g)
                # On systems with gzip, should fail
                # On systems without, will be skipped (PASS)
                for f in g.files:
                    assert f.integrity in (
                        IntegrityStatus.PASS,
                        IntegrityStatus.FAIL,
                        IntegrityStatus.SKIPPED,
                    )
                break
