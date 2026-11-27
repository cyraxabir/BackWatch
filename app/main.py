"""Main application entry point for Backup Health Monitor.

Runs the scan loop, exposes Prometheus metrics, and generates reports.
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Event

from app.server import start_integrated_server

from app.ai.ollama import summarize_scan
from app.checks.anomaly import check_size_anomaly
from app.checks.discovery import (
    discover_all,
    discover_df_mounts,
    discover_system_files,
    discover_system_storage,
)
from app.checks.filesystem import check_group_health
from app.checks.integrity import check_group_integrity
from app.checks.restore import run_restore_tests
from app.checks.retention import execute_retention, find_retention_candidates
from app.config import AppConfig, load_config
from app.database import HistoryDB, compute_storage_projection
from app.metrics import (
    backup_failures_total,
    backup_monitor_info,
    backup_restore_test_failure,
    backup_restore_test_success,
    backup_retention_candidates,
    backup_scans_total,
    update_group_metrics,
    update_summary_metrics,
)
from app.models import HealthStatus, ScanResult
from app.notifications.alerts import send_notifications

# ── Logging setup ─────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s — %(message)s"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format=LOG_FORMAT,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backup-monitor")

# ── Graceful shutdown ─────────────────────────────────────────────────────
shutdown_event = Event()


def _signal_handler(signum, frame):
    logger.info("Received signal %d, shutting down…", signum)
    shutdown_event.set()


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def run_scan(config: AppConfig, db: HistoryDB) -> ScanResult:
    """Execute a complete backup scan cycle."""
    scan_time = datetime.now()
    logger.info("=" * 60)
    logger.info("Starting backup scan at %s", scan_time.isoformat())
    logger.info("=" * 60)

    # 1. Discovery
    logger.info("Phase 1: Discovery")
    groups = discover_all(config)
    storage_info = discover_system_storage(config.backup_root)
    sys_files_info = discover_system_files(config.backup_root)
    df_mounts = discover_df_mounts(config.backup_root, config.state_dir)
    logger.info("Discovered %d backup groups, %d system files, %d mounts (Storage: %.1f%% used)",
                len(groups), sys_files_info["total_count"], len(df_mounts), storage_info["percent_used"])

    # 2. Health checks (filesystem + freshness)
    logger.info("Phase 2: Health checks")
    for group in groups:
        check_group_health(group, config)

    # Immediately write preliminary report & metrics so Web UI & Prometheus update in seconds
    prelim_projection = compute_storage_projection(
        db.get_storage_history(days=30),
        storage_info,
        groups=groups,
    )
    prelim_result = ScanResult(
        timestamp=scan_time,
        groups=groups,
        system_storage=storage_info,
        system_files=sys_files_info,
        df_mounts=df_mounts,
        storage_projection=prelim_projection,
    )
    _update_metrics(prelim_result)
    _write_report(prelim_result, config)

    # 3. Integrity checks
    logger.info("Phase 3: Integrity checks")
    for group in groups:
        if group.files:
            check_group_integrity(group)

    # 4. Size anomaly detection
    logger.info("Phase 4: Size anomaly detection")
    for group in groups:
        check_size_anomaly(group, config, db)

    # 5. Record to history database
    logger.info("Phase 5: Recording history")
    for group in groups:
        newest = group.newest_file
        db.record_scan(
            scan_timestamp=scan_time,
            server=group.server,
            backup_group=group.group_name,
            file_count=group.file_count,
            total_bytes=group.total_bytes,
            latest_mtime=newest.mtime if newest else None,
            health=group.health.value,
            integrity=group.integrity.value,
            size_anomaly=group.size_anomaly,
        )

    total_backup_bytes = sum(g.total_bytes for g in groups)
    db.record_storage_snapshot(
        timestamp=scan_time,
        mount_point=storage_info.get("mount_point", config.backup_root),
        total_bytes=storage_info.get("total_bytes", 0),
        used_bytes=storage_info.get("used_bytes", 0),
        free_bytes=storage_info.get("free_bytes", 0),
        backup_bytes=total_backup_bytes,
    )
    storage_projection = compute_storage_projection(
        db.get_storage_history(days=30),
        storage_info,
        groups=groups,
    )

    # 6. Retention
    logger.info("Phase 6: Retention analysis")
    retention_candidates = find_retention_candidates(config, groups, db)
    retention_results = execute_retention(retention_candidates, config, db)
    logger.info(
        "Retention: %d candidates, %d safe to delete",
        len(retention_candidates),
        sum(1 for c in retention_candidates if c.safe_to_delete),
    )

    # 7. Restore testing (if configured)
    restore_results = []
    if config.restore_test.enabled:
        logger.info("Phase 7: Restore testing")
        restore_results = run_restore_tests(groups, config)
        success_count = sum(1 for r in restore_results if r.success)
        fail_count = sum(1 for r in restore_results if not r.success)
        backup_restore_test_success.set(success_count)
        backup_restore_test_failure.set(fail_count)

    # 8. Build result
    result = ScanResult(
        timestamp=scan_time,
        groups=groups,
        retention_candidates=retention_candidates,
        restore_results=restore_results,
        system_storage=storage_info,
        system_files=sys_files_info,
        df_mounts=df_mounts,
        storage_projection=storage_projection,
    )

    # 9. Update Prometheus metrics
    _update_metrics(result)

    # 10. Write report
    _write_report(result, config)

    # 11. Notifications
    if config.notifications.webhook or config.notifications.alertmanager_url:
        logger.info("Phase 8: Sending notifications")
        send_notifications(
            result,
            webhook_url=config.notifications.webhook,
            alertmanager_url=config.notifications.alertmanager_url,
        )

    # 12. AI summary (optional)
    if config.ai.enabled:
        summary = summarize_scan(result, config.ai)
        if summary:
            logger.info("AI Summary: %s", summary)
            # Include in report
            report_path = Path(config.state_dir) / "ai-summary.txt"
            try:
                report_path.write_text(summary, encoding="utf-8")
            except OSError as e:
                logger.warning("Failed to write AI summary: %s", e)

    # Log summary
    logger.info("-" * 60)
    logger.info("Scan complete:")
    logger.info("  Total groups: %d", result.total_groups)
    logger.info("  Healthy: %d", result.healthy_count)
    logger.info("  Warning: %d", result.warning_count)
    logger.info("  Failed: %d", result.failed_count)
    logger.info("  Retention candidates: %d", len(retention_candidates))
    logger.info("-" * 60)

    return result


def _update_metrics(result: ScanResult) -> None:
    """Update all Prometheus metrics from scan result."""
    backup_scans_total.inc()

    for group in result.groups:
        age = group.age_hours if group.age_hours is not None else -1
        update_group_metrics(
            server=group.server,
            group=group.group_name,
            health=group.health.value,
            age_hours=age,
            file_count=group.file_count,
            total_bytes=group.total_bytes,
            size_anomaly=group.size_anomaly,
        )
        if group.health == HealthStatus.FAIL:
            backup_failures_total.inc()

    update_summary_metrics(
        total=result.total_groups,
        healthy=result.healthy_count,
        warning=result.warning_count,
        failed=result.failed_count,
    )

    backup_retention_candidates.set(len(result.retention_candidates))


def _write_report(result: ScanResult, config: AppConfig) -> None:
    """Write JSON report to state directory."""
    state_dir = Path(config.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    report = result.to_dict()

    # Latest report
    latest_path = state_dir / "latest-report.json"
    try:
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info("Report written to %s", latest_path)
    except OSError as e:
        logger.error("Failed to write report: %s", e)

    # Daily report (append date)
    date_str = result.timestamp.strftime("%Y-%m-%d")
    daily_path = state_dir / f"daily-report-{date_str}.json"
    try:
        with open(daily_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
    except OSError as e:
        logger.error("Failed to write daily report: %s", e)


def main() -> None:
    """Main entry point."""
    logger.info("Backwatch v1.0.0 starting")

    # Load configuration
    config = load_config()

    # Initialize history database
    db = HistoryDB(config.db_path)

    # Set build info
    backup_monitor_info.info({
        "version": "1.0.0",
        "backup_root": config.backup_root,
    })

    # Start Integrated Web UI and Prometheus metrics server
    web_host = os.environ.get("HTTP_HOST", "0.0.0.0")
    metrics_port = int(os.environ.get("METRICS_PORT", "9108"))
    logger.info("Starting Web UI & Prometheus server on %s:%d", web_host, metrics_port)
    server = start_integrated_server(
        host=web_host,
        port=metrics_port,
        state_dir=config.state_dir,
        trigger_scan_fn=lambda: run_scan(config, db),
        ai_config=config.ai,
        auth_config=config.auth,
    )

    # Initial scan
    try:
        run_scan(config, db)
    except Exception:
        logger.exception("Initial scan failed")

    # Periodic cleanup
    db.cleanup_old_records(days=90)

    # Scan loop
    while not shutdown_event.is_set():
        logger.info(
            "Next scan in %d seconds", config.scan_interval_seconds
        )
        if shutdown_event.wait(timeout=config.scan_interval_seconds):
            break

        try:
            run_scan(config, db)
        except Exception:
            logger.exception("Scan failed")

    logger.info("Shutting down…")
    db.close()
    logger.info("Goodbye.")


if __name__ == "__main__":
    main()
