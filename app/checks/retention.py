"""Backup retention management.

Manages retention of old backup directories following strict safety rules:
- Only targets canonical IP/YYYY/MM/DD directories
- Never deletes special directories, scripts, or non-backup content
- Enforces minimum-good-backup protection
- Supports dry-run mode (default)
- Validates paths before any destructive operation

IMPORTANT: Destructive deletion is DISABLED by default (dry_run=true).
"""

import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.config import AppConfig
from app.database import HistoryDB
from app.models import BackupGroup, HealthStatus, RetentionCandidate

logger = logging.getLogger(__name__)

# Strict pattern for IP/YYYY/MM/DD path validation
# Must match: <backup_root>/<IP>/YYYY/MM/DD
RETENTION_PATH_PATTERN = re.compile(
    r"^.+/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{4}/\d{1,2}/\d{1,2}$"
)


def _is_valid_retention_path(path: str, backup_root: str) -> bool:
    """Validate that a path is a legitimate retention candidate.

    Path must:
    1. Be under the backup root
    2. Match the IP/YYYY/MM/DD pattern
    3. Not contain path traversal
    """
    # Normalize paths
    norm_path = os.path.normpath(os.path.abspath(path))
    norm_root = os.path.normpath(os.path.abspath(backup_root))

    # Must be under backup root
    if not norm_path.startswith(norm_root + os.sep):
        logger.error("Retention path %s is not under backup root %s", path, backup_root)
        return False

    # Check for path traversal
    if ".." in path:
        logger.error("Path traversal detected in retention path: %s", path)
        return False

    # Must match IP/YYYY/MM/DD pattern
    # Use forward slashes for pattern matching
    check_path = norm_path.replace("\\", "/")
    if not RETENTION_PATH_PATTERN.match(check_path):
        logger.warning(
            "Retention path does not match IP/YYYY/MM/DD pattern: %s", path
        )
        return False

    return True


def find_retention_candidates(
    config: AppConfig,
    groups: List[BackupGroup],
    db: Optional[HistoryDB] = None,
) -> List[RetentionCandidate]:
    """Find date directories eligible for retention-based deletion.

    Only considers canonical IP/YYYY/MM/DD/ directories.
    Never considers special directories.
    """
    if not config.retention.enabled:
        return []

    from app.checks.discovery import discover_date_directories

    candidates: List[RetentionCandidate] = []
    cutoff = datetime.now() - timedelta(days=config.retention.days)

    # Group the already-discovered backup groups by server
    groups_by_server: Dict[str, Dict[str, List[BackupGroup]]] = {}
    for g in groups:
        if g.is_special:
            continue
        if g.server not in groups_by_server:
            groups_by_server[g.server] = {}
        date_key = g.folder_date or "unknown"
        if date_key not in groups_by_server[g.server]:
            groups_by_server[g.server][date_key] = []
        groups_by_server[g.server][date_key].append(g)

    # Discover all date directories
    date_dirs = discover_date_directories(config)

    for date_path, server, folder_date in date_dirs:
        try:
            dir_date = datetime.strptime(folder_date, "%Y-%m-%d")
        except ValueError:
            continue

        if dir_date >= cutoff:
            continue

        # This is a retention candidate based on date
        age_days = (datetime.now() - dir_date).total_seconds() / 86400.0

        # Collect backup group names in this directory
        group_names = []
        try:
            for entry in os.listdir(date_path):
                if os.path.isdir(os.path.join(date_path, entry)):
                    group_names.append(entry)
        except OSError:
            pass

        candidate = RetentionCandidate(
            path=date_path,
            server=server,
            folder_date=folder_date,
            age_days=age_days,
            backup_groups=group_names,
        )

        # Safety checks
        safe, reason = _check_retention_safety(
            candidate, config, groups_by_server, db
        )
        candidate.safe_to_delete = safe
        candidate.block_reason = reason

        candidates.append(candidate)

    return candidates


def _check_retention_safety(
    candidate: RetentionCandidate,
    config: AppConfig,
    groups_by_server: Dict[str, Dict[str, List[BackupGroup]]],
    db: Optional[HistoryDB] = None,
) -> tuple:
    """Perform safety checks before allowing retention deletion.

    Returns (safe_to_delete, block_reason_or_None).
    """
    # Validate path
    if not _is_valid_retention_path(candidate.path, config.backup_root):
        return False, "Invalid retention path"

    # Check minimum-good-backup protection
    min_good = config.retention.minimum_good_backups
    server_groups = groups_by_server.get(candidate.server, {})

    for group_name in candidate.backup_groups:
        # Count how many good date directories exist for this server+group
        good_dates: Set[str] = set()
        for date_key, group_list in server_groups.items():
            for g in group_list:
                if g.group_name == group_name and g.health == HealthStatus.PASS:
                    good_dates.add(date_key)

        # Also check DB history
        if db:
            healthy_dates = db.get_healthy_dates(candidate.server, group_name)
            good_dates.update(healthy_dates)

        # Remove the candidate date from good dates
        remaining = good_dates - {candidate.folder_date}

        if len(remaining) < min_good:
            reason = (
                f"Minimum good backups protection: {group_name} has "
                f"{len(remaining)} good backup(s) remaining "
                f"(minimum: {min_good})"
            )
            logger.warning(
                "Retention blocked for %s: %s", candidate.path, reason
            )
            return False, reason

    # Check if there's an active backup failure for this server
    latest_dates = sorted(server_groups.keys(), reverse=True)
    if latest_dates:
        latest_date = latest_dates[0]
        latest_groups = server_groups.get(latest_date, [])
        for g in latest_groups:
            if g.health == HealthStatus.FAIL:
                reason = (
                    f"Active backup failure: {g.group_name} on "
                    f"{candidate.server} ({latest_date}) is in FAIL state"
                )
                logger.warning(
                    "Retention blocked for %s: %s", candidate.path, reason
                )
                return False, reason

    return True, None


def execute_retention(
    candidates: List[RetentionCandidate],
    config: AppConfig,
    db: Optional[HistoryDB] = None,
) -> List[dict]:
    """Execute retention on candidates.

    In dry-run mode, only logs what would be deleted.
    With dry_run=false, actually deletes validated directories.
    """
    results = []

    for candidate in candidates:
        if not candidate.safe_to_delete:
            action = "BLOCKED"
            detail = candidate.block_reason or "Safety check failed"
            logger.warning(
                "Retention BLOCKED: %s — %s", candidate.path, detail
            )
            if db:
                db.record_retention_action(
                    server=candidate.server,
                    folder_date=candidate.folder_date,
                    path=candidate.path,
                    action=action,
                    dry_run=config.retention.dry_run,
                    details=detail,
                )
            results.append({
                "path": candidate.path,
                "action": action,
                "reason": detail,
            })
            continue

        if config.retention.dry_run:
            action = "DRY_RUN"
            detail = f"Would delete {candidate.path} (age: {candidate.age_days:.1f} days)"
            logger.info("DRY RUN: %s", detail)
            if db:
                db.record_retention_action(
                    server=candidate.server,
                    folder_date=candidate.folder_date,
                    path=candidate.path,
                    action=action,
                    dry_run=True,
                    details=detail,
                )
            results.append({
                "path": candidate.path,
                "action": action,
                "detail": detail,
            })
        else:
            # DESTRUCTIVE DELETION
            # Final validation
            if not _is_valid_retention_path(candidate.path, config.backup_root):
                logger.error(
                    "SAFETY: Refusing to delete invalid path: %s",
                    candidate.path,
                )
                continue

            try:
                shutil.rmtree(candidate.path)
                action = "DELETED"
                detail = (
                    f"Deleted {candidate.path} "
                    f"(age: {candidate.age_days:.1f} days)"
                )
                logger.info("DELETED: %s", detail)
            except OSError as e:
                action = "DELETE_FAILED"
                detail = f"Failed to delete {candidate.path}: {e}"
                logger.error("DELETE FAILED: %s", detail)

            if db:
                db.record_retention_action(
                    server=candidate.server,
                    folder_date=candidate.folder_date,
                    path=candidate.path,
                    action=action,
                    dry_run=False,
                    details=detail,
                )
            results.append({
                "path": candidate.path,
                "action": action,
                "detail": detail,
            })

    return results
