"""Data models for backup health monitoring."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, List, Optional


class HealthStatus(Enum):
    """Health status for a backup group."""
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class IntegrityStatus(Enum):
    """Integrity check result."""
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


@dataclass
class BackupFile:
    """Represents a single backup file with filesystem metadata."""
    path: str
    filename: str
    size_bytes: int
    mtime: datetime
    ctime: datetime  # inode metadata-change time, NOT creation time
    birth_time: Optional[datetime] = None  # true creation time if available
    integrity: IntegrityStatus = IntegrityStatus.UNKNOWN

    @property
    def age_hours(self) -> float:
        """Age in hours based on mtime (modification time)."""
        delta = datetime.now() - self.mtime
        return delta.total_seconds() / 3600.0


@dataclass
class BackupGroup:
    """A backup group represents a directory of related backup files."""
    server: str
    group_name: str
    folder_date: Optional[str]  # YYYY-MM-DD from directory structure
    path: str
    files: List[BackupFile] = field(default_factory=list)
    health: HealthStatus = HealthStatus.UNKNOWN
    integrity: IntegrityStatus = IntegrityStatus.UNKNOWN
    size_anomaly: bool = False
    size_anomaly_detail: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    is_special: bool = False

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    @property
    def newest_file(self) -> Optional[BackupFile]:
        if not self.files:
            return None
        return max(self.files, key=lambda f: f.mtime)

    @property
    def oldest_file(self) -> Optional[BackupFile]:
        if not self.files:
            return None
        return min(self.files, key=lambda f: f.mtime)

    @property
    def age_hours(self) -> Optional[float]:
        newest = self.newest_file
        if newest is None:
            return None
        return newest.age_hours

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON reports."""
        newest = self.newest_file
        oldest = self.oldest_file
        return {
            "server": self.server,
            "group": self.group_name,
            "folder_date": self.folder_date,
            "path": self.path,
            "latest_backup_file": newest.filename if newest else None,
            "latest_mtime": newest.mtime.isoformat() if newest else None,
            "latest_ctime": newest.ctime.isoformat() if newest else None,
            "birth_time": newest.birth_time.isoformat() if newest and newest.birth_time else None,
            "oldest_backup_file": oldest.filename if oldest else None,
            "age_hours": round(self.age_hours, 2) if self.age_hours is not None else None,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "integrity": self.integrity.value,
            "size_anomaly": self.size_anomaly,
            "size_anomaly_detail": self.size_anomaly_detail,
            "health": self.health.value,
            "warnings": self.warnings,
            "errors": self.errors,
            "is_special": self.is_special,
        }


@dataclass
class RetentionCandidate:
    """A directory that is a candidate for retention-based deletion."""
    path: str
    server: str
    folder_date: str
    age_days: float
    backup_groups: List[str] = field(default_factory=list)
    safe_to_delete: bool = False
    block_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "server": self.server,
            "folder_date": self.folder_date,
            "age_days": round(self.age_days, 2),
            "backup_groups": self.backup_groups,
            "safe_to_delete": self.safe_to_delete,
            "block_reason": self.block_reason,
        }


@dataclass
class ScanResult:
    """Complete result of a backup scan with database-level aggregation."""
    timestamp: datetime
    groups: List[BackupGroup] = field(default_factory=list)
    retention_candidates: List[RetentionCandidate] = field(default_factory=list)
    restore_results: List[Any] = field(default_factory=list)
    system_storage: Optional[dict] = None
    system_files: Optional[dict] = None
    df_mounts: List[dict] = field(default_factory=list)
    storage_projection: Optional[dict] = None

    @property
    def total_groups(self) -> int:
        return len(self.groups)

    @property
    def healthy_count(self) -> int:
        return sum(1 for g in self.groups if g.health == HealthStatus.PASS)

    @property
    def warning_count(self) -> int:
        return sum(1 for g in self.groups if g.health == HealthStatus.WARNING)

    @property
    def failed_count(self) -> int:
        return sum(1 for g in self.groups if g.health == HealthStatus.FAIL)

    @property
    def unknown_count(self) -> int:
        return sum(1 for g in self.groups if g.health == HealthStatus.UNKNOWN)

    def get_database_catalog(self) -> List[dict]:
        """Aggregate raw folder groups into unique logical databases (Server + DB Name).

        Determines:
        - Date of last successful/latest backup
        - Actual size of latest backup
        - Active health based on latest backup
        - Full retention scopes (all retained snapshots on disk)
        """
        # Group by (server, group_name)
        db_map: dict = {}
        for g in self.groups:
            key = (g.server, g.group_name)
            if key not in db_map:
                db_map[key] = []
            db_map[key].append(g)

        catalog = []
        for (server, db_name), snapshots in db_map.items():
            # Sort snapshots newest first by mtime (or folder_date if mtime unavailable)
            def _sort_key(snap: BackupGroup):
                newest = snap.newest_file
                if newest and newest.mtime:
                    return newest.mtime
                if snap.folder_date:
                    try:
                        return datetime.strptime(snap.folder_date, "%Y-%m-%d")
                    except ValueError:
                        pass
                return datetime.min

            sorted_snaps = sorted(snapshots, key=_sort_key, reverse=True)
            active_snap = sorted_snaps[0]

            # Find latest successful (PASS) snapshot if any
            successful_snaps = [s for s in sorted_snaps if s.health == HealthStatus.PASS]
            last_good = successful_snaps[0] if successful_snaps else None
            last_good_newest = last_good.newest_file if last_good else None

            newest_file = active_snap.newest_file
            retained_list = []
            for s in sorted_snaps:
                s_newest = s.newest_file
                retained_list.append({
                    "folder_date": s.folder_date,
                    "path": s.path,
                    "mtime": s_newest.mtime.isoformat() if s_newest else None,
                    "size_bytes": s.total_bytes,
                    "file_count": s.file_count,
                    "health": s.health.value,
                })

            catalog.append({
                "server": server,
                "database": db_name,
                "health": active_snap.health.value,
                "is_special": active_snap.is_special,
                # Active/Latest Snapshot info
                "latest_folder_date": active_snap.folder_date,
                "latest_mtime": newest_file.mtime.isoformat() if newest_file else None,
                "latest_ctime": newest_file.ctime.isoformat() if newest_file else None,
                "birth_time": newest_file.birth_time.isoformat() if newest_file and newest_file.birth_time else None,
                "age_hours": round(active_snap.age_hours, 2) if active_snap.age_hours is not None else None,
                "actual_size_bytes": active_snap.total_bytes,
                "file_count": active_snap.file_count,
                "latest_backup_file": newest_file.filename if newest_file else None,
                "integrity": active_snap.integrity.value,
                "size_anomaly": active_snap.size_anomaly,
                "size_anomaly_detail": active_snap.size_anomaly_detail,
                "errors": active_snap.errors,
                "warnings": active_snap.warnings,
                # Last known successful backup (if different from latest)
                "last_successful_date": last_good.folder_date if last_good else None,
                "last_successful_mtime": last_good_newest.mtime.isoformat() if last_good_newest else None,
                "last_successful_size_bytes": last_good.total_bytes if last_good else None,
                # Retention scopes: all dates on disk
                "retained_count": len(sorted_snaps),
                "retained_dates": [s.folder_date for s in sorted_snaps if s.folder_date],
                "retained_snapshots": retained_list,
            })

        # Sort catalog by server, then database
        catalog.sort(key=lambda d: (d["server"], d["database"]))
        return catalog

    def get_server_catalog(self) -> List[dict]:
        """Aggregate database inventory by server/host IP."""
        databases = self.get_database_catalog()
        server_map: dict = {}
        for db in databases:
            srv = db["server"]
            if srv not in server_map:
                server_map[srv] = {
                    "server": srv,
                    "databases": [],
                    "total_bytes": 0,
                    "healthy_count": 0,
                    "warning_count": 0,
                    "failed_count": 0,
                    "latest_mtime": None,
                    "all_dates": set(),
                }
            s = server_map[srv]
            s["databases"].append(db["database"])
            s["total_bytes"] += db["actual_size_bytes"]
            if db["health"] == "PASS":
                s["healthy_count"] += 1
            elif db["health"] == "WARNING":
                s["warning_count"] += 1
            else:
                s["failed_count"] += 1

            if db["latest_mtime"]:
                if s["latest_mtime"] is None or db["latest_mtime"] > s["latest_mtime"]:
                    s["latest_mtime"] = db["latest_mtime"]

            for dt in db.get("retained_dates", []):
                if dt:
                    s["all_dates"].add(dt)

        servers = []
        for srv, s in server_map.items():
            overall_health = "PASS"
            if s["failed_count"] > 0:
                overall_health = "FAIL"
            elif s["warning_count"] > 0:
                overall_health = "WARNING"

            servers.append({
                "server": srv,
                "health": overall_health,
                "database_count": len(s["databases"]),
                "databases": sorted(s["databases"]),
                "total_bytes": s["total_bytes"],
                "healthy_count": s["healthy_count"],
                "warning_count": s["warning_count"],
                "failed_count": s["failed_count"],
                "latest_mtime": s["latest_mtime"],
                "retained_dates": sorted(list(s["all_dates"]), reverse=True),
            })

        servers.sort(key=lambda x: x["server"])
        return servers

    def get_folder_tree(self) -> List[dict]:
        """Build a categorized hierarchical folder tree with consumed space at each level.

        Hierarchy: Category -> Server/Root Folder -> Project/Database -> Snapshot Date -> Files.
        """
        total_all_bytes = sum(g.total_bytes for g in self.groups)

        def _worst_health(statuses: List[str]) -> str:
            if "FAIL" in statuses:
                return "FAIL"
            if "WARNING" in statuses:
                return "WARNING"
            if "PASS" in statuses:
                return "PASS"
            return "UNKNOWN"

        categories_map: dict = {
            "Production Servers (IP)": {},
            "Special / Project Folders": {},
        }

        for g in self.groups:
            cat_name = "Special / Project Folders" if g.is_special else "Production Servers (IP)"
            srv_map = categories_map[cat_name]
            if g.server not in srv_map:
                srv_map[g.server] = {}
            proj_map = srv_map[g.server]
            if g.group_name not in proj_map:
                proj_map[g.group_name] = []
            proj_map[g.group_name].append(g)

        tree: List[dict] = []
        for cat_name, srv_map in categories_map.items():
            if not srv_map:
                continue

            server_nodes: List[dict] = []
            cat_bytes = 0
            cat_healths: List[str] = []

            for srv_name, proj_map in srv_map.items():
                proj_nodes: List[dict] = []
                srv_bytes = 0
                srv_healths: List[str] = []

                for proj_name, snaps in proj_map.items():
                    snap_nodes: List[dict] = []
                    proj_bytes = 0
                    proj_healths: List[str] = []

                    # Sort snapshots newest date first
                    sorted_snaps = sorted(
                        snaps,
                        key=lambda s: (s.folder_date or "", s.newest_file.mtime.isoformat() if s.newest_file else ""),
                        reverse=True,
                    )

                    for s in sorted_snaps:
                        s_bytes = s.total_bytes
                        proj_bytes += s_bytes
                        proj_healths.append(s.health.value)

                        file_nodes: List[dict] = []
                        for f in sorted(s.files, key=lambda x: x.size_bytes, reverse=True):
                            file_nodes.append({
                                "name": f.filename,
                                "node_type": "file",
                                "size_bytes": f.size_bytes,
                                "pct_of_total": round((f.size_bytes / total_all_bytes * 100.0), 1) if total_all_bytes > 0 else 0.0,
                                "pct_of_parent": round((f.size_bytes / s_bytes * 100.0), 1) if s_bytes > 0 else 0.0,
                                "item_count": 1,
                                "health": s.health.value,
                                "path": f.path,
                                "mtime": f.mtime.isoformat() if f.mtime else None,
                                "children": [],
                            })

                        snap_nodes.append({
                            "name": s.folder_date or "Root",
                            "node_type": "snapshot",
                            "size_bytes": s_bytes,
                            "pct_of_total": round((s_bytes / total_all_bytes * 100.0), 1) if total_all_bytes > 0 else 0.0,
                            "pct_of_parent": 0.0,  # filled once proj_bytes is known
                            "item_count": len(file_nodes),
                            "health": s.health.value,
                            "path": s.path,
                            "mtime": s.newest_file.mtime.isoformat() if s.newest_file else None,
                            "children": file_nodes,
                        })

                    for sn in snap_nodes:
                        sn["pct_of_parent"] = round((sn["size_bytes"] / proj_bytes * 100.0), 1) if proj_bytes > 0 else 0.0

                    proj_health = _worst_health(proj_healths)
                    srv_bytes += proj_bytes
                    srv_healths.append(proj_health)

                    proj_nodes.append({
                        "name": proj_name,
                        "node_type": "project",
                        "size_bytes": proj_bytes,
                        "pct_of_total": round((proj_bytes / total_all_bytes * 100.0), 1) if total_all_bytes > 0 else 0.0,
                        "pct_of_parent": 0.0,
                        "item_count": len(snap_nodes),
                        "health": proj_health,
                        "path": sorted_snaps[0].path if sorted_snaps else "",
                        "children": snap_nodes,
                    })

                proj_nodes.sort(key=lambda n: n["size_bytes"], reverse=True)
                for pn in proj_nodes:
                    pn["pct_of_parent"] = round((pn["size_bytes"] / srv_bytes * 100.0), 1) if srv_bytes > 0 else 0.0

                srv_health = _worst_health(srv_healths)
                cat_bytes += srv_bytes
                cat_healths.append(srv_health)

                server_nodes.append({
                    "name": srv_name,
                    "node_type": "server",
                    "size_bytes": srv_bytes,
                    "pct_of_total": round((srv_bytes / total_all_bytes * 100.0), 1) if total_all_bytes > 0 else 0.0,
                    "pct_of_parent": 0.0,
                    "item_count": len(proj_nodes),
                    "health": srv_health,
                    "children": proj_nodes,
                })

            server_nodes.sort(key=lambda n: n["size_bytes"], reverse=True)
            for sn in server_nodes:
                sn["pct_of_parent"] = round((sn["size_bytes"] / cat_bytes * 100.0), 1) if cat_bytes > 0 else 0.0

            tree.append({
                "name": cat_name,
                "node_type": "category",
                "size_bytes": cat_bytes,
                "pct_of_total": round((cat_bytes / total_all_bytes * 100.0), 1) if total_all_bytes > 0 else 0.0,
                "pct_of_parent": round((cat_bytes / total_all_bytes * 100.0), 1) if total_all_bytes > 0 else 0.0,
                "item_count": len(server_nodes),
                "health": _worst_health(cat_healths),
                "children": server_nodes,
            })

        return tree

    def to_dict(self) -> dict:
        databases = self.get_database_catalog()
        servers = self.get_server_catalog()

        total_dbs = len(databases)
        healthy_dbs = sum(1 for d in databases if d["health"] == "PASS")
        warning_dbs = sum(1 for d in databases if d["health"] == "WARNING")
        failed_dbs = sum(1 for d in databases if d["health"] == "FAIL")
        total_actual_bytes = sum(d["actual_size_bytes"] for d in databases)

        restore_serialized = []
        for r in (self.restore_results or []):
            if hasattr(r, "to_dict"):
                restore_serialized.append(r.to_dict())
            elif isinstance(r, dict):
                restore_serialized.append(r)

        return {
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "summary": {
                # Distinct database-level stats (primary operational metrics)
                "total_databases": total_dbs,
                "healthy_databases": healthy_dbs,
                "warning_databases": warning_dbs,
                "failed_databases": failed_dbs,
                "total_servers": len(servers),
                "total_storage_bytes": total_actual_bytes,
                # Low-level raw folder metrics (backwards compatibility)
                "total_groups": self.total_groups,
                "healthy": self.healthy_count,
                "warning": self.warning_count,
                "failed": self.failed_count,
                "unknown": self.unknown_count,
            },
            "system_storage": self.system_storage or {
                "total_bytes": 0,
                "used_bytes": 0,
                "free_bytes": 0,
                "percent_used": 0.0,
                "mount_point": "",
            },
            "system_files": self.system_files or {
                "total_count": 0,
                "total_bytes": 0,
                "scripts_count": 0,
                "logs_count": 0,
                "sample_files": [],
            },
            "restore_results": restore_serialized,
            "df_mounts": self.df_mounts or [],
            "storage_projection": self.storage_projection or {
                "source": "none",
                "total_bytes": 0,
                "used_bytes": 0,
                "free_bytes": 0,
                "daily_growth_bytes": 0,
                "days_until_full": None,
                "days_until_80_pct": None,
                "projected_full_date": None,
                "status": "stable",
                "history": [],
                "forecast": [],
            },
            "folder_tree": self.get_folder_tree(),
            "databases": databases,
            "servers": servers,
            "groups": [g.to_dict() for g in self.groups],
            "retention_candidates": [r.to_dict() for r in self.retention_candidates],
        }
