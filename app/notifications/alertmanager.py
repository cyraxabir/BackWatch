"""Alertmanager configuration helper.

Provides Alertmanager rule generation and integration utilities.
This module supplements the main alerts module with Alertmanager-specific concerns.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Prometheus alerting rules for backup monitoring
ALERT_RULES = [
    {
        "alert": "BackupMissing",
        "expr": 'backup_group_healthy == 0 and backup_group_file_count == 0',
        "for": "5m",
        "labels": {"severity": "critical"},
        "annotations": {
            "summary": 'Backup missing for {{ $labels.server }}/{{ $labels.backup_group }}',
            "description": 'No valid backup files found for {{ $labels.backup_group }} on {{ $labels.server }}.',
        },
    },
    {
        "alert": "BackupStale",
        "expr": f'backup_group_latest_age_hours > 26',
        "for": "10m",
        "labels": {"severity": "critical"},
        "annotations": {
            "summary": 'Stale backup: {{ $labels.server }}/{{ $labels.backup_group }}',
            "description": 'Backup age is {{ $value }}h (expected < 26h).',
        },
    },
    {
        "alert": "BackupSizeAnomaly",
        "expr": "backup_group_size_anomaly == 1",
        "for": "5m",
        "labels": {"severity": "warning"},
        "annotations": {
            "summary": 'Size anomaly: {{ $labels.server }}/{{ $labels.backup_group }}',
            "description": 'Backup size anomaly detected for {{ $labels.backup_group }} on {{ $labels.server }}.',
        },
    },
    {
        "alert": "BackupCorrupt",
        "expr": 'backup_group_healthy == 0 and backup_group_file_count > 0',
        "for": "5m",
        "labels": {"severity": "critical"},
        "annotations": {
            "summary": 'Corrupt backup: {{ $labels.server }}/{{ $labels.backup_group }}',
            "description": 'Integrity check failed for {{ $labels.backup_group }} on {{ $labels.server }}.',
        },
    },
    {
        "alert": "RestoreTestFailed",
        "expr": "backup_restore_test_failure > 0",
        "for": "5m",
        "labels": {"severity": "critical"},
        "annotations": {
            "summary": "Backup restore test failed",
            "description": "{{ $value }} restore test(s) failed.",
        },
    },
]


def get_prometheus_alert_rules() -> List[Dict[str, Any]]:
    """Return Prometheus alert rules for backup monitoring."""
    return ALERT_RULES
