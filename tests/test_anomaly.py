"""Tests for size anomaly detection.

Test 15: Size anomaly detection
"""

from datetime import datetime, timedelta

import pytest

from app.checks.anomaly import calculate_baseline, check_size_anomaly, _human_size
from app.database import HistoryDB
from app.models import BackupFile, BackupGroup, HealthStatus


class TestSizeBaseline:
    """Test baseline calculation."""

    def test_baseline_with_data(self):
        history = [
            ("2026-11-20", 1800000000),
            ("2026-11-21", 1700000000),
            ("2026-11-22", 1900000000),
            ("2026-11-23", 1800000000),
            ("2026-11-24", 1800000000),
        ]
        baseline = calculate_baseline(history)
        assert baseline is not None
        assert baseline == 1800000000  # median

    def test_baseline_insufficient_data(self):
        history = [("2026-11-20", 1800000000)]
        baseline = calculate_baseline(history)
        assert baseline is None

    def test_baseline_empty(self):
        baseline = calculate_baseline([])
        assert baseline is None


class TestSizeAnomaly:
    """Test 15: Size anomaly detection."""

    def test_anomaly_detected(self, config, db):
        """When current size is 93% below baseline, detect anomaly."""
        # Insert historical data
        for i in range(5):
            ts = datetime.now() - timedelta(days=5-i)
            db.record_scan(
                scan_timestamp=ts,
                server="192.0.2.10",
                backup_group="test_group",
                file_count=25,
                total_bytes=1800000000,  # ~1.8GB baseline
                latest_mtime=ts,
                health="PASS",
                integrity="PASS",
                size_anomaly=False,
            )

        # Create a group with anomalously small size
        group = BackupGroup(
            server="192.0.2.10",
            group_name="test_group",
            folder_date="2026-11-22",
            path="/fake",
            files=[
                BackupFile(
                    path="/fake/db.sql.gz",
                    filename="db.sql.gz",
                    size_bytes=120000000,  # ~120MB — 93% decrease
                    mtime=datetime.now(),
                    ctime=datetime.now(),
                ),
            ],
        )

        check_size_anomaly(group, config, db)
        assert group.size_anomaly is True
        assert group.size_anomaly_detail is not None
        assert "SIZE_ANOMALY" in group.size_anomaly_detail

    def test_no_anomaly_normal_size(self, config, db):
        """When current size is within threshold, no anomaly."""
        for i in range(5):
            ts = datetime.now() - timedelta(days=5-i)
            db.record_scan(
                scan_timestamp=ts,
                server="192.0.2.10",
                backup_group="normal_group",
                file_count=25,
                total_bytes=1800000000,
                latest_mtime=ts,
                health="PASS",
                integrity="PASS",
                size_anomaly=False,
            )

        group = BackupGroup(
            server="192.0.2.10",
            group_name="normal_group",
            folder_date="2026-11-22",
            path="/fake",
            files=[
                BackupFile(
                    path="/fake/db.sql.gz",
                    filename="db.sql.gz",
                    size_bytes=1750000000,  # ~1.75GB — within threshold
                    mtime=datetime.now(),
                    ctime=datetime.now(),
                ),
            ],
        )

        check_size_anomaly(group, config, db)
        assert group.size_anomaly is False


class TestHumanSize:
    def test_bytes(self):
        assert _human_size(500) == "500.0 B"

    def test_megabytes(self):
        result = _human_size(120000000)
        assert "MB" in result

    def test_gigabytes(self):
        result = _human_size(1800000000)
        assert "GB" in result
