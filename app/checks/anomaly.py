"""Backup size anomaly detection.

Uses deterministic calculations against historical baselines.
No AI is used for anomaly detection.
"""

import logging
import statistics
from typing import List, Optional, Tuple

from app.config import AppConfig
from app.database import HistoryDB
from app.models import BackupGroup, HealthStatus

logger = logging.getLogger(__name__)


def calculate_baseline(
    history: List[Tuple[str, int]],
) -> Optional[float]:
    """Calculate the median total_bytes from historical data.

    Uses median rather than mean to be robust against outliers.
    Returns None if insufficient history.
    """
    if len(history) < 2:
        return None

    sizes = [size for _, size in history if size > 0]
    if len(sizes) < 2:
        return None

    return statistics.median(sizes)


def check_size_anomaly(
    group: BackupGroup,
    config: AppConfig,
    db: Optional[HistoryDB] = None,
) -> None:
    """Check for backup size anomalies against historical baselines.

    Detects:
    - Sudden size decrease beyond threshold
    - Sudden size increase beyond threshold
    - Abnormal total backup size

    Uses deterministic percentage comparison, not AI.
    """
    if db is None:
        return

    current_size = group.total_bytes
    if current_size <= 0:
        # Zero-byte already caught by filesystem checks
        return

    threshold_pct = config.health.size_anomaly_threshold_percent

    # Try 7-day baseline first, fall back to 30-day
    history_7d = db.get_size_history(group.server, group.group_name, days=7)
    history_30d = db.get_size_history(group.server, group.group_name, days=30)

    baseline = calculate_baseline(history_7d)
    baseline_period = "7-day"

    if baseline is None:
        baseline = calculate_baseline(history_30d)
        baseline_period = "30-day"

    if baseline is None:
        logger.debug(
            "Server %s group %s: insufficient history for size baseline",
            group.server,
            group.group_name,
        )
        return

    if baseline <= 0:
        return

    # Calculate percentage change
    change_pct = ((current_size - baseline) / baseline) * 100.0

    if abs(change_pct) >= threshold_pct:
        group.size_anomaly = True
        direction = "decrease" if change_pct < 0 else "increase"
        detail = (
            f"SIZE_ANOMALY: {direction} of {abs(change_pct):.1f}% detected. "
            f"Expected (~{baseline_period} baseline): {_human_size(baseline)}, "
            f"Actual: {_human_size(current_size)}, "
            f"Change: {change_pct:+.1f}%"
        )
        group.size_anomaly_detail = detail
        group.warnings.append(detail)

        if group.health not in (HealthStatus.FAIL,):
            group.health = HealthStatus.WARNING

        logger.warning(
            "Server %s group %s %s",
            group.server,
            group.group_name,
            detail,
        )
    else:
        logger.debug(
            "Server %s group %s size OK: current=%s, baseline=%s, change=%.1f%%",
            group.server,
            group.group_name,
            _human_size(current_size),
            _human_size(baseline),
            change_pct,
        )


def _human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable size string."""
    if size_bytes < 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"
