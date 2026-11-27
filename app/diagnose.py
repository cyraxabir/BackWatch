"""Quick diagnostic tool to test Discord Webhook and AI API connectivity."""

import json
import logging
import os
import sys
from datetime import datetime

from app.config import load_config
from app.models import BackupGroup, HealthStatus, ScanResult
from app.notifications.alerts import send_webhook
from app.ai.summarizer import summarize_scan

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger("diagnose")


def test_discord(config):
    webhook_url = config.notifications.webhook
    print("\n" + "=" * 60)
    print("1. TESTING DISCORD WEBHOOK")
    print("=" * 60)
    if not webhook_url:
        print("❌ WEBHOOK_URL is not configured.")
        return

    print(f"Target Webhook: {webhook_url[:50]}...")

    # Create dummy test result
    dummy_group = BackupGroup(
        server="192.0.2.20",
        group_name="diagnostic_test_db",
        folder_date=datetime.now().strftime("%Y-%m-%d"),
        path="/test/path",
        health=HealthStatus.FAIL,
        errors=["TEST_ALERT: Backup Health Monitor diagnostic test message."],
    )
    test_result = ScanResult(
        timestamp=datetime.now(),
        groups=[dummy_group],
        retention_candidates=[],
    )

    ok = send_webhook(webhook_url, test_result)
    if ok:
        print("✅ SUCCESS: Test alert sent! Check your Discord channel.")
    else:
        print("❌ FAILED: Webhook delivery failed. Check logs above.")


def test_ai(config):
    print("\n" + "=" * 60)
    print("2. TESTING AI API (OpenAI / Claude / OpenWebUI / Ollama)")
    print("=" * 60)
    if not config.ai.enabled:
        print("⚠️  AI is marked disabled (ai.enabled=false).")
        print("   To test anyway, we will temporarily enable it for this test.")
        config.ai.enabled = True

    provider = getattr(config.ai, "provider", config.ai.api_type)
    print(f"Provider:  {provider}")
    print(f"AI URL:    {config.ai.url}")
    print(f"AI Model:  {config.ai.model}")
    print(f"API Key:   {'*' * 8 + config.ai.api_key[-4:] if config.ai.api_key else '(None)'}")

    dummy_group = BackupGroup(
        server="192.0.2.20",
        group_name="app_prod_db",
        folder_date="2026-11-22",
        path="/test",
        health=HealthStatus.PASS,
    )
    test_result = ScanResult(
        timestamp=datetime.now(),
        groups=[dummy_group],
        retention_candidates=[],
    )

    print("\nSending prompt to AI endpoint...")
    summary = summarize_scan(test_result, config.ai)
    if summary:
        print("✅ SUCCESS: AI response received!")
        print("-" * 50)
        print(summary)
        print("-" * 50)
    else:
        print("❌ FAILED: AI did not return a response. Check endpoint URL and API key.")


def main():
    print("Running Backup Health Monitor Connectivity Diagnostics...")
    config = load_config()
    test_discord(config)
    test_ai(config)
    print("\nDiagnostics complete.\n")


if __name__ == "__main__":
    main()
