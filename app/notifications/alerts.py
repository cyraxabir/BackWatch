"""Alert notifications — webhook and Alertmanager integration.

Supports:
- Generic webhook (POST JSON)
- Alertmanager API
- Structured for easy extension (Telegram, Teams, email)

Credentials are sourced from environment variables, never hardcoded.
"""

import json
import logging
import ssl
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.models import BackupGroup, HealthStatus, RetentionCandidate, ScanResult

logger = logging.getLogger(__name__)

# Alert severity levels
SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


def _build_alerts(result: ScanResult) -> List[Dict[str, Any]]:
    """Build alert payloads from scan results."""
    alerts: List[Dict[str, Any]] = []

    for group in result.groups:
        labels = {
            "server": group.server,
            "backup_group": group.group_name,
        }

        for error in group.errors:
            if "NO_BACKUP_FILES" in error:
                alerts.append({
                    "alertname": "BackupMissing",
                    "severity": SEVERITY_CRITICAL,
                    "labels": labels,
                    "message": error,
                })
            elif "STALE" in error:
                alerts.append({
                    "alertname": "BackupStale",
                    "severity": SEVERITY_CRITICAL,
                    "labels": labels,
                    "message": error,
                })
            elif "INTEGRITY_FAIL" in error:
                alerts.append({
                    "alertname": "BackupCorrupt",
                    "severity": SEVERITY_CRITICAL,
                    "labels": labels,
                    "message": error,
                })
            elif "ZERO_BYTE" in error:
                alerts.append({
                    "alertname": "ZeroByteBackup",
                    "severity": SEVERITY_CRITICAL,
                    "labels": labels,
                    "message": error,
                })

        for warning in group.warnings:
            if "SIZE_ANOMALY" in warning:
                alerts.append({
                    "alertname": "BackupSizeAnomaly",
                    "severity": SEVERITY_WARNING,
                    "labels": labels,
                    "message": warning,
                })
            elif "SMALL_FILE" in warning or "TOO_FEW" in warning:
                alerts.append({
                    "alertname": "TooFewBackupFiles",
                    "severity": SEVERITY_WARNING,
                    "labels": labels,
                    "message": warning,
                })

    # Retention safety alerts
    for candidate in result.retention_candidates:
        if not candidate.safe_to_delete and candidate.block_reason:
            alerts.append({
                "alertname": "RetentionSafetyBlocked",
                "severity": SEVERITY_WARNING,
                "labels": {
                    "server": candidate.server,
                    "folder_date": candidate.folder_date,
                },
                "message": candidate.block_reason,
            })

    return alerts


def send_webhook(webhook_url: str, result: ScanResult) -> bool:
    """Send scan results to a generic webhook endpoint or Discord webhook."""
    if not webhook_url:
        return False

    alerts = _build_alerts(result)
    if not alerts:
        logger.debug("No alerts to send via webhook")
        return True

    # Detect Discord Webhooks and format with Discord embeds
    if "discord.com/api/webhooks" in webhook_url:
        embeds = []
        for alert in alerts[:10]:  # Discord limit: max 10 embeds per message
            color = 15158332 if alert.get("severity") == SEVERITY_CRITICAL else 16776960  # Red or Gold
            labels = alert.get("labels", {})
            server_info = f"{labels.get('server', 'N/A')}/{labels.get('backup_group', '')}".strip('/')
            embeds.append({
                "title": f"🚨 {alert.get('alertname', 'Backup Alert')} — {server_info}",
                "description": alert.get("message", "No description"),
                "color": color,
                "fields": [
                    {"name": "Severity", "value": alert.get("severity", "unknown").upper(), "inline": True},
                    {"name": "Server", "value": labels.get("server", "N/A"), "inline": True},
                ],
                "footer": {"text": "Backup Health Monitor"},
                "timestamp": result.timestamp.isoformat(),
            })

        summary_text = (
            f"🛡️ **Backup Health Alert Summary**\n"
            f"• **Total Groups**: {result.total_groups} | "
            f"• **Healthy**: {result.healthy_count} | "
            f"• **Warnings**: {result.warning_count} | "
            f"• **Failures**: {result.failed_count}\n"
            f"Showing top {len(embeds)} of {len(alerts)} alerts:"
        )

        payload = {
            "content": summary_text,
            "embeds": embeds,
        }
    else:
        # Standard generic webhook format
        payload = {
            "timestamp": result.timestamp.isoformat(),
            "summary": {
                "total_groups": result.total_groups,
                "healthy": result.healthy_count,
                "warning": result.warning_count,
                "failed": result.failed_count,
            },
            "alerts": alerts,
        }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = Request(
            webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Backup-Health-Monitor/1.0",
            },
            method="POST",
        )
        try:
            ctx = ssl.create_default_context()
            resp_handle = urlopen(req, timeout=30, context=ctx)
        except ssl.SSLError as ssl_err:
            logger.warning("SSL verification failed for webhook %s (%s). Retrying with unverified context...", webhook_url, ssl_err)
            unverified_ctx = ssl._create_unverified_context()
            resp_handle = urlopen(req, timeout=30, context=unverified_ctx)

        with resp_handle as resp:
            logger.info(
                "Webhook sent: %d alerts, status=%d", len(alerts), resp.status
            )
            return resp.status < 400
    except (URLError, OSError) as e:
        logger.error("Webhook failed: %s", e)
        return False


def send_alertmanager(alertmanager_url: str, result: ScanResult) -> bool:
    """Send alerts to Alertmanager API.

    Uses the Alertmanager v2 API: POST /api/v2/alerts
    """
    if not alertmanager_url:
        return False

    alerts = _build_alerts(result)
    if not alerts:
        logger.debug("No alerts to send to Alertmanager")
        return True

    # Convert to Alertmanager format
    am_alerts = []
    for alert in alerts:
        am_alert = {
            "labels": {
                "alertname": alert["alertname"],
                "severity": alert["severity"],
                **alert["labels"],
            },
            "annotations": {
                "summary": alert["message"],
            },
        }
        am_alerts.append(am_alert)

    api_url = alertmanager_url.rstrip("/") + "/api/v2/alerts"

    try:
        data = json.dumps(am_alerts).encode("utf-8")
        req = Request(
            api_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            logger.info(
                "Alertmanager: sent %d alerts, status=%d",
                len(am_alerts),
                resp.status,
            )
            return resp.status < 400
    except (URLError, OSError) as e:
        logger.error("Alertmanager failed: %s", e)
        return False


def send_notifications(result: ScanResult, webhook_url: str = "",
                       alertmanager_url: str = "") -> None:
    """Send all configured notifications."""
    if webhook_url:
        send_webhook(webhook_url, result)
    if alertmanager_url:
        send_alertmanager(alertmanager_url, result)
