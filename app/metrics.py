"""Prometheus metrics for backup health monitoring."""

from prometheus_client import Counter, Gauge, Info

# ── Gauges (per backup group) ──────────────────────────────────────────────
backup_group_healthy = Gauge(
    "backup_group_healthy",
    "Whether the backup group is healthy (1=PASS, 0=FAIL, -1=WARNING, -2=UNKNOWN)",
    ["server", "backup_group"],
)

backup_group_latest_age_hours = Gauge(
    "backup_group_latest_age_hours",
    "Age of the newest backup file in hours (based on mtime)",
    ["server", "backup_group"],
)

backup_group_file_count = Gauge(
    "backup_group_file_count",
    "Number of valid backup files in the group",
    ["server", "backup_group"],
)

backup_group_total_bytes = Gauge(
    "backup_group_total_bytes",
    "Total size of backup files in bytes",
    ["server", "backup_group"],
)

backup_group_size_anomaly = Gauge(
    "backup_group_size_anomaly",
    "Whether a size anomaly was detected (1=anomaly, 0=normal)",
    ["server", "backup_group"],
)

# ── Global counters/gauges ────────────────────────────────────────────────
backup_scans_total = Counter(
    "backup_scans_total",
    "Total number of backup scans performed",
)

backup_failures_total = Counter(
    "backup_failures_total",
    "Total number of backup group failures detected",
)

backup_retention_candidates = Gauge(
    "backup_retention_candidates",
    "Number of retention deletion candidates",
)

backup_restore_test_success = Gauge(
    "backup_restore_test_success",
    "Number of successful restore tests",
)

backup_restore_test_failure = Gauge(
    "backup_restore_test_failure",
    "Number of failed restore tests",
)

# ── Summary gauges ────────────────────────────────────────────────────────
backup_total_groups = Gauge(
    "backup_total_groups",
    "Total number of discovered backup groups",
)

backup_healthy_groups = Gauge(
    "backup_healthy_groups",
    "Number of healthy backup groups",
)

backup_warning_groups = Gauge(
    "backup_warning_groups",
    "Number of warning backup groups",
)

backup_failed_groups = Gauge(
    "backup_failed_groups",
    "Number of failed backup groups",
)

# ── Build info ────────────────────────────────────────────────────────────
backup_monitor_info = Info(
    "backup_monitor",
    "Backup monitor build information",
)


HEALTH_VALUE_MAP = {
    "PASS": 1,
    "WARNING": -1,
    "FAIL": 0,
    "UNKNOWN": -2,
}


def update_group_metrics(server: str, group: str, health: str,
                         age_hours: float, file_count: int,
                         total_bytes: int, size_anomaly: bool) -> None:
    """Update all per-group metrics atomically."""
    labels = {"server": server, "backup_group": group}
    backup_group_healthy.labels(**labels).set(HEALTH_VALUE_MAP.get(health, -2))
    backup_group_latest_age_hours.labels(**labels).set(age_hours)
    backup_group_file_count.labels(**labels).set(file_count)
    backup_group_total_bytes.labels(**labels).set(total_bytes)
    backup_group_size_anomaly.labels(**labels).set(1 if size_anomaly else 0)


def update_summary_metrics(total: int, healthy: int, warning: int, failed: int) -> None:
    """Update summary-level metrics."""
    backup_total_groups.set(total)
    backup_healthy_groups.set(healthy)
    backup_warning_groups.set(warning)
    backup_failed_groups.set(failed)
