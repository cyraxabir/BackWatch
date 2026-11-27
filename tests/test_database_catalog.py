"""Tests for database-level aggregation, actual size, and retention scopes."""

from datetime import datetime, timedelta

import pytest

from app.models import BackupFile, BackupGroup, HealthStatus, ScanResult


class TestDatabaseCatalogAggregation:
    """Test consolidating raw folder paths into logical databases with retention scopes."""

    def test_database_aggregation_and_retention_scope(self):
        # Create a database with 3 historical snapshot folders: Nov 20, Nov 21, Nov 22
        now = datetime.now()

        # Nov 22 (Newest)
        snap_nov22 = BackupGroup(
            server="192.0.2.20",
            group_name="app_prod_db",
            folder_date="2026-11-22",
            path="/backups/192.0.2.20/2026/11/22/app_prod_db",
            files=[
                BackupFile(
                    path="/backups/192.0.2.20/2026/11/22/app_prod_db/data.sql.gz",
                    filename="data.sql.gz",
                    size_bytes=5600000000,  # ~5.6 GB
                    mtime=now - timedelta(hours=5),
                    ctime=now - timedelta(hours=5),
                )
            ],
            health=HealthStatus.PASS,
        )

        # Nov 21 (Retained)
        snap_nov21 = BackupGroup(
            server="192.0.2.20",
            group_name="app_prod_db",
            folder_date="2026-11-21",
            path="/backups/192.0.2.20/2026/11/21/app_prod_db",
            files=[
                BackupFile(
                    path="/backups/192.0.2.20/2026/11/21/app_prod_db/data.sql.gz",
                    filename="data.sql.gz",
                    size_bytes=5500000000,
                    mtime=now - timedelta(hours=29),
                    ctime=now - timedelta(hours=29),
                )
            ],
            health=HealthStatus.FAIL,  # Older folder marked stale individually
        )

        # Nov 20 (Retained)
        snap_nov20 = BackupGroup(
            server="192.0.2.20",
            group_name="app_prod_db",
            folder_date="2026-11-20",
            path="/backups/192.0.2.20/2026/11/20/app_prod_db",
            files=[
                BackupFile(
                    path="/backups/192.0.2.20/2026/11/20/app_prod_db/data.sql.gz",
                    filename="data.sql.gz",
                    size_bytes=5400000000,
                    mtime=now - timedelta(hours=53),
                    ctime=now - timedelta(hours=53),
                )
            ],
            health=HealthStatus.FAIL,
        )

        scan_result = ScanResult(
            timestamp=now,
            groups=[snap_nov20, snap_nov21, snap_nov22],
            retention_candidates=[],
        )

        catalog = scan_result.get_database_catalog()

        # Should aggregate 3 folder groups into 1 logical database
        assert len(catalog) == 1
        db = catalog[0]

        assert db["server"] == "192.0.2.20"
        assert db["database"] == "app_prod_db"
        # Database health should be determined by the LATEST snapshot (Nov 22: PASS)
        assert db["health"] == "PASS"

        # Actual size of the latest backup
        assert db["actual_size_bytes"] == 5600000000

        # Last successful backup date and mtime
        assert db["latest_folder_date"] == "2026-11-22"
        assert db["last_successful_date"] == "2026-11-22"

        # Retention scope (3 snapshots on disk)
        assert db["retained_count"] == 3
        assert "2026-11-22" in db["retained_dates"]
        assert "2026-11-21" in db["retained_dates"]
        assert "2026-11-20" in db["retained_dates"]

    def test_server_catalog_aggregation(self):
        now = datetime.now()
        g1 = BackupGroup(
            server="192.0.2.10",
            group_name="db_a",
            folder_date="2026-11-22",
            path="/p1",
            files=[BackupFile(path="/f1", filename="f1.sql.gz", size_bytes=1000, mtime=now, ctime=now)],
            health=HealthStatus.PASS,
        )
        g2 = BackupGroup(
            server="192.0.2.10",
            group_name="db_b",
            folder_date="2026-11-22",
            path="/p2",
            files=[BackupFile(path="/f2", filename="f2.sql.gz", size_bytes=2000, mtime=now, ctime=now)],
            health=HealthStatus.PASS,
        )
        g3 = BackupGroup(
            server="192.0.2.20",
            group_name="db_c",
            folder_date="2026-11-22",
            path="/p3",
            files=[BackupFile(path="/f3", filename="f3.sql.gz", size_bytes=5000, mtime=now, ctime=now)],
            health=HealthStatus.FAIL,
        )

        scan_result = ScanResult(timestamp=now, groups=[g1, g2, g3])
        servers = scan_result.get_server_catalog()

        assert len(servers) == 2
        s50 = next(s for s in servers if s["server"] == "192.0.2.10")
        assert s50["database_count"] == 2
        assert s50["total_bytes"] == 3000
        assert s50["health"] == "PASS"

        s60 = next(s for s in servers if s["server"] == "192.0.2.20")
        assert s60["database_count"] == 1
        assert s60["health"] == "FAIL"

    def test_folder_tree_aggregation(self):
        now = datetime.now()
        g1 = BackupGroup(
            server="192.0.2.10",
            group_name="db_a",
            folder_date="2026-11-22",
            path="/p1",
            files=[BackupFile(path="/f1", filename="f1.sql.gz", size_bytes=1000, mtime=now, ctime=now)],
            health=HealthStatus.PASS,
        )
        g2 = BackupGroup(
            server="SPECIAL_PROJ",
            group_name="SPECIAL_PROJ",
            folder_date=None,
            path="/sp1",
            files=[BackupFile(path="/sf1", filename="sf1.sql.gz", size_bytes=3000, mtime=now, ctime=now)],
            health=HealthStatus.WARNING,
            is_special=True,
        )
        scan_result = ScanResult(timestamp=now, groups=[g1, g2])
        tree = scan_result.get_folder_tree()

        assert len(tree) == 2
        prod_cat = next(c for c in tree if c["name"] == "Production Servers (IP)")
        spec_cat = next(c for c in tree if c["name"] == "Special / Project Folders")
        assert prod_cat["size_bytes"] == 1000
        assert prod_cat["pct_of_total"] == 25.0
        assert spec_cat["size_bytes"] == 3000
        assert spec_cat["pct_of_total"] == 75.0
        assert prod_cat["children"][0]["children"][0]["children"][0]["children"][0]["name"] == "f1.sql.gz"

    def test_storage_history_and_projection(self, tmp_path):
        from datetime import timedelta
        from app.database import HistoryDB, compute_storage_projection

        db = HistoryDB(str(tmp_path / "hist.db"))
        base = datetime(2026, 11, 1, 12, 0, 0)
        for day_idx in range(5):
            ts = base + timedelta(days=day_idx)
            db.record_storage_snapshot(
                timestamp=ts,
                mount_point="/var/backups/db",
                total_bytes=1000_000_000,
                used_bytes=200_000_000 + day_idx * 50_000_000,
                free_bytes=800_000_000 - day_idx * 50_000_000,
                backup_bytes=100_000_000 + day_idx * 50_000_000,
            )

        hist = db.get_storage_history(days=60)
        assert len(hist) == 5
        proj = compute_storage_projection(
            hist,
            {"total_bytes": 1000_000_000, "used_bytes": 400_000_000, "free_bytes": 600_000_000},
        )
        assert proj["source"] == "storage_history"
        assert proj["status"] == "growing"
        assert proj["daily_growth_bytes"] == 50_000_000
        assert proj["days_until_full"] == 12
        assert len(proj["forecast"]) == 6
        db.close()
