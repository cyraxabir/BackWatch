"""Filesystem-level backup health checks.

Checks based on actual file metadata:
- File existence
- File freshness (based on mtime, NOT folder date)
- Zero-byte detection
- Minimum file size
"""

import logging
from datetime import datetime

from app.config import AppConfig
from app.models import BackupGroup, HealthStatus

logger = logging.getLogger(__name__)


def check_freshness(group: BackupGroup, config: AppConfig) -> None:
    """Check backup group freshness based on actual file mtime.

    IMPORTANT: Uses actual filesystem mtime, NOT the YYYY/MM/DD folder date.
    A file under 2026/11/22/ with mtime 2026-11-19 is 4 days old, NOT 1 day.

    For special directories, uses the per-directory configured interval if set.
    """
    if not group.files:
        group.errors.append("NO_BACKUP_FILES: No valid backup files found")
        group.health = HealthStatus.FAIL
        logger.error(
            "Server %s group %s FAIL: no backup files", group.server, group.group_name
        )
        return

    newest = group.newest_file
    if newest is None:
        group.errors.append("NO_NEWEST_FILE: Cannot determine newest file")
        group.health = HealthStatus.FAIL
        return

    # Determine expected interval
    if group.is_special and group.server in (config.special or {}):
        expected_hours = config.special[group.server].expected_interval_hours
    else:
        expected_hours = config.health.expected_interval_hours

    age_hours = newest.age_hours
    if age_hours > expected_hours:
        group.errors.append(
            f"STALE: Newest backup is {age_hours:.1f}h old "
            f"(expected within {expected_hours:.1f}h). "
            f"File: {newest.filename}, mtime: {newest.mtime.isoformat()}"
        )
        if group.health != HealthStatus.FAIL:
            group.health = HealthStatus.FAIL
        logger.error(
            "Server %s group %s STALE: age=%.1fh, expected=%.1fh, "
            "file=%s, mtime=%s, folder_date=%s",
            group.server,
            group.group_name,
            age_hours,
            expected_hours,
            newest.filename,
            newest.mtime.isoformat(),
            group.folder_date,
        )
    else:
        logger.debug(
            "Server %s group %s freshness OK: age=%.1fh",
            group.server,
            group.group_name,
            age_hours,
        )


def check_file_sizes(group: BackupGroup, config: AppConfig) -> None:
    """Check for zero-byte or suspiciously small backup files."""
    min_size = config.health.minimum_file_size_bytes

    for f in group.files:
        if f.size_bytes == 0:
            group.errors.append(
                f"ZERO_BYTE: {f.filename} is 0 bytes"
            )
            group.health = HealthStatus.FAIL
            logger.error(
                "Server %s group %s ZERO_BYTE: %s",
                group.server,
                group.group_name,
                f.filename,
            )
        elif f.size_bytes < min_size:
            group.warnings.append(
                f"SMALL_FILE: {f.filename} is {f.size_bytes} bytes "
                f"(minimum: {min_size})"
            )
            if group.health not in (HealthStatus.FAIL,):
                group.health = HealthStatus.WARNING
            logger.warning(
                "Server %s group %s SMALL_FILE: %s (%d bytes)",
                group.server,
                group.group_name,
                f.filename,
                f.size_bytes,
            )


def check_group_health(group: BackupGroup, config: AppConfig) -> None:
    """Run all filesystem health checks on a backup group.

    Sets health to PASS if all checks pass, unless already marked FAIL/WARNING.
    """
    # Start optimistic (will be overridden by check functions if issues found)
    if group.health == HealthStatus.UNKNOWN:
        group.health = HealthStatus.PASS

    check_freshness(group, config)
    check_file_sizes(group, config)

    # Final status
    if not group.errors and not group.warnings:
        group.health = HealthStatus.PASS
    elif group.errors:
        group.health = HealthStatus.FAIL
    elif group.warnings and group.health != HealthStatus.FAIL:
        group.health = HealthStatus.WARNING

    logger.info(
        "Server %s group %s %s (files=%d, size=%d, age=%.1fh)",
        group.server,
        group.group_name,
        group.health.value,
        group.file_count,
        group.total_bytes,
        group.age_hours if group.age_hours is not None else -1,
    )
