"""Test webhook notifications including Discord webhook format."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.models import HealthStatus, ScanResult, BackupGroup
from app.notifications.alerts import send_webhook


class TestNotifications:
    """Test webhook alert formatting and sending."""

    @patch("app.notifications.alerts.urlopen")
    def test_discord_webhook_formatting(self, mock_urlopen):
        """Verify discord webhook payload contains embeds and content."""
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        discord_url = "https://discord.com/api/webhooks/123456789/abcdef"

        group = BackupGroup(
            server="192.0.2.10",
            group_name="postgres_prod",
            folder_date="2026-11-22",
            path="/fake",
            files=[],
            health=HealthStatus.FAIL,
            errors=["NO_BACKUP_FILES: No valid backup files found"],
        )

        result = ScanResult(
            timestamp=datetime.now(),
            groups=[group],
            retention_candidates=[],
        )

        ok = send_webhook(discord_url, result)
        assert ok is True

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == discord_url
        data = json.loads(req.data.decode("utf-8"))

        assert "content" in data
        assert "embeds" in data
        assert len(data["embeds"]) == 1
        assert data["embeds"][0]["title"].startswith("🚨")
        assert "192.0.2.10" in data["embeds"][0]["title"]
