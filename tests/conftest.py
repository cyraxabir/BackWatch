"""Shared test fixtures and helpers.

Creates a fake backup tree for testing:
  test-data/
  ├── 192.0.2.10/
  │   ├── 2026/
  │   │   └── 11/
  │   │       ├── 22/
  │   │       │   ├── postgres_prod/
  │   │       │   │   ├── db1.sql.gz      (valid gzip, fresh mtime)
  │   │       │   │   ├── db2.sql.gz      (valid gzip, fresh mtime)
  │   │       │   │   ├── ignored.txt     (must be ignored)
  │   │       │   │   └── script.sh       (must be ignored)
  │   │       │   └── analytics_db/
  │   │       │       └── analytics.sql.gz     (valid gzip, fresh mtime)
  │   │       └── 15/                     (old date, retention candidate)
  │   │           └── old_group/
  │   │               └── old.sql.gz
  │   └── 2026/
  │       └── ...
  ├── 192.0.2.30/
  │   └── 2026/
  │       └── 11/
  │           └── 19/                     (stale — 4 days ago)
  │               └── database/
  │                   └── stale.sql.gz    (mtime = Nov 19, stale)
  ├── SPECIAL_DIR/
  │   └── special.sql.gz
  ├── backup.txt                          (must be ignored at root)
  └── helper-script.sh                      (must be ignored at root)
"""

import gzip
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add project root to path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def test_backup_root(tmp_path):
    """Create a realistic fake backup tree for testing."""
    root = tmp_path / "backups"
    root.mkdir()

    # ── IP 192.0.2.10 — fresh backups ───────────────────
    fresh_group = root / "192.0.2.10" / "2026" / "11" / "22" / "postgres_prod"
    fresh_group.mkdir(parents=True)

    # Create valid gzip files with fresh mtime
    _create_valid_gzip(fresh_group / "db1.sql.gz", b"-- SQL dump db1\nCREATE TABLE t1 (id INT);\n")
    _create_valid_gzip(fresh_group / "db2.sql.gz", b"-- SQL dump db2\nCREATE TABLE t2 (id INT);\n")

    # Files that must be IGNORED (not matching allow-list)
    (fresh_group / "ignored.txt").write_text("this is not a backup")
    (fresh_group / "script.sh").write_text("#!/bin/bash\necho hello")

    # Second group under same date
    hmis_group = root / "192.0.2.10" / "2026" / "11" / "22" / "analytics_db"
    hmis_group.mkdir(parents=True)
    _create_valid_gzip(hmis_group / "analytics.sql.gz", b"-- HMIS dump\nCREATE TABLE hmis (id INT);\n")

    # Old date directory (retention candidate, 8 days ago)
    old_group = root / "192.0.2.10" / "2026" / "11" / "15" / "old_group"
    old_group.mkdir(parents=True)
    _create_valid_gzip(old_group / "old.sql.gz", b"-- old data\n")
    # Set mtime to 8 days ago
    old_time = time.time() - (8 * 86400)
    os.utime(old_group / "old.sql.gz", (old_time, old_time))

    # ── IP 192.0.2.30 — stale backup ──────────────────
    stale_group = root / "192.0.2.30" / "2026" / "11" / "19" / "database"
    stale_group.mkdir(parents=True)
    _create_valid_gzip(stale_group / "stale.sql.gz", b"-- stale data\n")
    # Set mtime to 4 days ago (stale: > 26 hours)
    stale_time = time.time() - (4 * 86400)
    os.utime(stale_group / "stale.sql.gz", (stale_time, stale_time))

    # ── Zero-byte backup ─────────────────────────────────
    zero_group = root / "192.0.2.10" / "2026" / "11" / "22" / "zero_group"
    zero_group.mkdir(parents=True)
    (zero_group / "empty.sql.gz").write_bytes(b"")

    # ── Corrupt gzip ─────────────────────────────────────
    corrupt_group = root / "192.0.2.10" / "2026" / "11" / "22" / "corrupt_group"
    corrupt_group.mkdir(parents=True)
    (corrupt_group / "corrupt.sql.gz").write_bytes(b"this is not valid gzip data at all")

    # ── Plain SQL file ───────────────────────────────────
    sql_group = root / "192.0.2.10" / "2026" / "11" / "22" / "sql_group"
    sql_group.mkdir(parents=True)
    (sql_group / "data.sql").write_text("CREATE TABLE test (id INT);\nINSERT INTO test VALUES (1);\n")
    (sql_group / "data.dump").write_bytes(b"\x00pg_dump_data")

    # ── Special (non-IP) directory ───────────────────────
    special = root / "SPECIAL_DIR"
    special.mkdir()
    _create_valid_gzip(special / "special.sql.gz", b"-- special backup\n")

    # ── Root-level files that must be ignored ────────────
    (root / "backup.txt").write_text("backup notes")
    (root / "helper-script.sh").write_text("#!/bin/bash\necho script")
    (root / "README.md").write_text("# Backup info")

    # ── Non-IP named directory (should not be auto-discovered) ──
    named_dir = root / "SPECIAL_ARCHIVE"
    named_dir.mkdir()
    _create_valid_gzip(named_dir / "acct.sql.gz", b"-- account backup\n")

    return root


def _create_valid_gzip(path: Path, content: bytes) -> None:
    """Create a valid gzip file with given content."""
    with gzip.open(path, "wb") as f:
        f.write(content)


@pytest.fixture
def config(test_backup_root):
    """Create a test configuration."""
    from app.config import AppConfig, HealthConfig, RetentionConfig, \
        RestoreTestConfig, SpecialDirConfig, NotificationsConfig, AIConfig

    return AppConfig(
        backup_root=str(test_backup_root),
        scan_interval_seconds=60,
        state_dir=str(test_backup_root / "_state"),
        db_path=str(test_backup_root / "_state" / "test.db"),
        health=HealthConfig(
            expected_interval_hours=26.0,
            minimum_file_size_bytes=1,
            size_anomaly_threshold_percent=60.0,
        ),
        retention=RetentionConfig(
            enabled=True,
            days=7,
            dry_run=True,
            minimum_good_backups=2,
        ),
        restore_test=RestoreTestConfig(enabled=False),
        special={
            "SPECIAL_DIR": SpecialDirConfig(
                path=str(test_backup_root / "SPECIAL_DIR"),
                expected_interval_hours=48.0,
            ),
        },
        notifications=NotificationsConfig(),
        ai=AIConfig(enabled=False),
    )


@pytest.fixture
def db(config):
    """Create a test database."""
    from app.database import HistoryDB
    Path(config.state_dir).mkdir(parents=True, exist_ok=True)
    database = HistoryDB(config.db_path)
    yield database
    database.close()
