"""SQLite-based persistent history for backup health monitoring."""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_timestamp TEXT NOT NULL,
    server TEXT NOT NULL,
    backup_group TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL,
    latest_mtime TEXT,
    health TEXT NOT NULL,
    integrity TEXT NOT NULL,
    size_anomaly INTEGER NOT NULL DEFAULT 0,
    restore_test_status TEXT,
    extra_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_scan_history_server_group
    ON scan_history (server, backup_group);

CREATE INDEX IF NOT EXISTS idx_scan_history_timestamp
    ON scan_history (scan_timestamp);

CREATE TABLE IF NOT EXISTS retention_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    server TEXT NOT NULL,
    folder_date TEXT NOT NULL,
    path TEXT NOT NULL,
    action TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 1,
    details TEXT
);

CREATE TABLE IF NOT EXISTS storage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    mount_point TEXT NOT NULL,
    total_bytes INTEGER NOT NULL,
    used_bytes INTEGER NOT NULL,
    free_bytes INTEGER NOT NULL,
    backup_bytes INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_storage_history_ts
    ON storage_history (timestamp);
"""


class HistoryDB:
    """Lightweight persistent history using SQLite."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_dir()
        self._init_db()

    def _ensure_dir(self) -> None:
        db_dir = Path(self._db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

    def _init_db(self) -> None:
        """Initialize database and create tables."""
        self._conn = sqlite3.connect(self._db_path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(CREATE_TABLES)

        # Check/set schema version
        cursor = self._conn.execute("SELECT COUNT(*) FROM schema_version")
        if cursor.fetchone()[0] == 0:
            self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        self._conn.commit()
        logger.info("History database initialized at %s", self._db_path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def record_scan(
        self,
        scan_timestamp: datetime,
        server: str,
        backup_group: str,
        file_count: int,
        total_bytes: int,
        latest_mtime: Optional[datetime],
        health: str,
        integrity: str,
        size_anomaly: bool,
        restore_test_status: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        """Record a backup group scan result."""
        if not self._conn:
            return
        self._conn.execute(
            """INSERT INTO scan_history
               (scan_timestamp, server, backup_group, file_count, total_bytes,
                latest_mtime, health, integrity, size_anomaly, restore_test_status, extra_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scan_timestamp.isoformat(),
                server,
                backup_group,
                file_count,
                total_bytes,
                latest_mtime.isoformat() if latest_mtime else None,
                health,
                integrity,
                1 if size_anomaly else 0,
                restore_test_status,
                json.dumps(extra) if extra else None,
            ),
        )
        self._conn.commit()

    def get_size_history(
        self,
        server: str,
        backup_group: str,
        days: int = 30,
    ) -> List[Tuple[str, int]]:
        """Get total_bytes history for size baseline calculations.

        Returns list of (scan_timestamp, total_bytes) tuples.
        """
        if not self._conn:
            return []
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            """SELECT scan_timestamp, total_bytes
               FROM scan_history
               WHERE server = ? AND backup_group = ? AND scan_timestamp >= ?
               ORDER BY scan_timestamp ASC""",
            (server, backup_group, cutoff),
        )
        return [(row["scan_timestamp"], row["total_bytes"]) for row in cursor.fetchall()]

    def get_healthy_dates(
        self,
        server: str,
        backup_group: str,
    ) -> List[str]:
        """Get distinct dates where backup group was healthy.

        Used for retention minimum-good-backup protection.
        Returns list of scan_timestamp date strings.
        """
        if not self._conn:
            return []
        cursor = self._conn.execute(
            """SELECT DISTINCT DATE(scan_timestamp) as scan_date
               FROM scan_history
               WHERE server = ? AND backup_group = ? AND health = 'PASS'
               ORDER BY scan_date DESC""",
            (server, backup_group),
        )
        return [row["scan_date"] for row in cursor.fetchall()]

    def record_retention_action(
        self,
        server: str,
        folder_date: str,
        path: str,
        action: str,
        dry_run: bool,
        details: Optional[str] = None,
    ) -> None:
        """Record a retention action (delete or dry-run)."""
        if not self._conn:
            return
        self._conn.execute(
            """INSERT INTO retention_log
               (timestamp, server, folder_date, path, action, dry_run, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                server,
                folder_date,
                path,
                action,
                1 if dry_run else 0,
                details,
            ),
        )
        self._conn.commit()

    def get_scan_count(self) -> int:
        """Get total number of scan records."""
        if not self._conn:
            return 0
        cursor = self._conn.execute("SELECT COUNT(*) FROM scan_history")
        return cursor.fetchone()[0]

    def get_latest_scan_summary(self) -> Dict[str, int]:
        """Get summary of the latest scan."""
        if not self._conn:
            return {}
        cursor = self._conn.execute(
            """SELECT health, COUNT(*) as cnt
               FROM scan_history
               WHERE scan_timestamp = (SELECT MAX(scan_timestamp) FROM scan_history)
               GROUP BY health"""
        )
        return {row["health"]: row["cnt"] for row in cursor.fetchall()}

    def cleanup_old_records(self, days: int = 90) -> int:
        """Remove scan records older than N days."""
        if not self._conn:
            return 0
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM scan_history WHERE scan_timestamp < ?",
            (cutoff,),
        )
        self._conn.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info("Cleaned up %d old scan records", deleted)
        return deleted

    def record_storage_snapshot(
        self,
        timestamp: datetime,
        mount_point: str,
        total_bytes: int,
        used_bytes: int,
        free_bytes: int,
        backup_bytes: int,
    ) -> None:
        """Record a disk storage snapshot for projection trending."""
        if not self._conn:
            return
        self._conn.execute(
            """INSERT INTO storage_history
               (timestamp, mount_point, total_bytes, used_bytes, free_bytes, backup_bytes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                timestamp.isoformat(),
                mount_point,
                total_bytes,
                used_bytes,
                free_bytes,
                backup_bytes,
            ),
        )
        self._conn.commit()

    def get_storage_history(self, days: int = 30) -> List[Dict]:
        """Get daily aggregated storage snapshots (latest reading per day)."""
        if not self._conn:
            return []
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            """SELECT DATE(timestamp) as day,
                      MAX(timestamp) as ts,
                      total_bytes,
                      used_bytes,
                      free_bytes,
                      backup_bytes
               FROM storage_history
               WHERE timestamp >= ?
               GROUP BY DATE(timestamp)
               ORDER BY day ASC""",
            (cutoff,),
        )
        return [
            {
                "date": row["day"],
                "timestamp": row["ts"],
                "total_bytes": row["total_bytes"],
                "used_bytes": row["used_bytes"],
                "free_bytes": row["free_bytes"],
                "backup_bytes": row["backup_bytes"],
            }
            for row in cursor.fetchall()
        ]


def compute_storage_projection(
    history_points: List[Dict],
    current_storage: Dict,
    groups: Optional[List] = None,
    forecast_days: int = 30,
) -> Dict:
    """Compute linear storage growth projection from history or on-disk snapshots.

    Returns a dictionary containing historical series, 30-day forecast series,
    daily growth rate (bytes/day), and estimated days remaining.
    """
    total_bytes = int(current_storage.get("total_bytes", 0) or 0)
    used_bytes = int(current_storage.get("used_bytes", 0) or 0)
    free_bytes = int(current_storage.get("free_bytes", 0) or 0)
    today = datetime.now().date()

    # Build daily series: prefer DB storage_history if >= 2 days, else synthesize from disk dates
    series: List[Dict] = []
    source = "storage_history"

    if history_points and len(history_points) >= 2:
        for pt in history_points:
            series.append({"date": pt["date"], "used_bytes": int(pt["used_bytes"])})
    elif groups:
        # Synthesize cumulative/daily backup trend from on-disk YYYY-MM-DD folders
        date_totals: Dict[str, int] = {}
        for g in groups:
            fdate = getattr(g, "folder_date", None)
            if fdate:
                date_totals[fdate] = date_totals.get(fdate, 0) + int(getattr(g, "total_bytes", 0) or 0)

        if len(date_totals) >= 2:
            source = "disk_snapshots"
            sorted_dates = sorted(date_totals.keys())
            # Anchor latest disk used_bytes and step backward by daily backup sizes
            # Or show cumulative growth anchored to current used_bytes
            total_bk = sum(date_totals.values())
            base_non_backup = max(0, used_bytes - total_bk)
            running = base_non_backup
            for dt in sorted_dates:
                running += date_totals[dt]
                series.append({"date": dt, "used_bytes": min(total_bytes, running) if total_bytes > 0 else running})

    if not series:
        series = [{"date": today.isoformat(), "used_bytes": used_bytes}]
        source = "current_only"

    # Compute least-squares linear regression slope (bytes per day)
    daily_growth_bytes = 0.0
    if len(series) >= 2:
        try:
            d0 = datetime.strptime(series[0]["date"], "%Y-%m-%d").date()
            xs = [(datetime.strptime(p["date"], "%Y-%m-%d").date() - d0).days for p in series]
            ys = [float(p["used_bytes"]) for p in series]
            n = len(xs)
            mean_x = sum(xs) / n
            mean_y = sum(ys) / n
            denom = sum((x - mean_x) ** 2 for x in xs)
            if denom > 0:
                daily_growth_bytes = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
        except (ValueError, ZeroDivisionError):
            daily_growth_bytes = 0.0

    # Generate 30-day forecast points (every 5 days + day 30)
    forecast: List[Dict] = []
    step_days = [5, 10, 15, 20, 25, forecast_days]
    for offset in step_days:
        proj_date = (today + timedelta(days=offset)).isoformat()
        proj_bytes = max(0.0, used_bytes + daily_growth_bytes * offset)
        if total_bytes > 0:
            proj_bytes = min(float(total_bytes) * 1.15, proj_bytes)
        forecast.append({
            "date": proj_date,
            "projected_bytes": int(round(proj_bytes)),
            "days_ahead": offset,
        })

    days_until_full: Optional[int] = None
    projected_full_date: Optional[str] = None
    days_until_80: Optional[int] = None

    if daily_growth_bytes > 1024 and total_bytes > 0:
        if free_bytes > 0:
            days_until_full = max(1, int(round(free_bytes / daily_growth_bytes)))
            projected_full_date = (today + timedelta(days=days_until_full)).isoformat()
        thresh_80 = int(total_bytes * 0.80)
        if used_bytes < thresh_80:
            days_until_80 = max(1, int(round((thresh_80 - used_bytes) / daily_growth_bytes)))

    status = "growing" if daily_growth_bytes > 1024 else ("reclaiming" if daily_growth_bytes < -1024 else "stable")

    return {
        "source": source,
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "free_bytes": free_bytes,
        "daily_growth_bytes": int(round(daily_growth_bytes)),
        "days_until_full": days_until_full,
        "days_until_80_pct": days_until_80,
        "projected_full_date": projected_full_date,
        "status": status,
        "history": series,
        "forecast": forecast,
    }
