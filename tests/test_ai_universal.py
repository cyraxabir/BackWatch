"""Tests for Universal AI Provider Integration (OpenAI, Claude, Ollama, OpenWebUI)."""

import json
from unittest.mock import MagicMock, patch
import pytest

from app.ai.summarizer import (
    _detect_provider,
    _resolve_endpoints,
    _call_claude_api,
    _call_openai_api,
    _call_ollama_api,
    _dispatch_ai_request,
    summarize_scan,
)
from app.config import AIConfig
from app.models import HealthStatus, ScanResult, BackupGroup


class TestProviderDetection:
    """Test intelligent AI provider detection."""

    def test_explicit_provider(self):
        assert _detect_provider(AIConfig(provider="claude")) == "claude"
        assert _detect_provider(AIConfig(provider="openai")) == "openai"
        assert _detect_provider(AIConfig(provider="ollama")) == "ollama"
        assert _detect_provider(AIConfig(provider="openwebui")) == "openai"

    def test_auto_detect_by_model(self):
        assert _detect_provider(AIConfig(model="claude-3-5-haiku-20241022")) == "claude"
        assert _detect_provider(AIConfig(model="gpt-4o-mini")) == "openai"
        assert _detect_provider(AIConfig(model="o1-preview")) == "openai"

    def test_auto_detect_by_url(self):
        assert _detect_provider(AIConfig(url="https://api.anthropic.com/v1")) == "claude"
        assert _detect_provider(AIConfig(url="https://api.openai.com/v1")) == "openai"
        assert _detect_provider(AIConfig(url="http://192.168.1.10:11434")) == "ollama"

    def test_endpoint_resolution_claude(self):
        cfg = AIConfig(provider="claude", url="https://api.anthropic.com")
        endpoint, ptype = _resolve_endpoints(cfg)
        assert ptype == "claude"
        assert endpoint == "https://api.anthropic.com/v1/messages"


class TestClaudeAPICall:
    """Test Anthropic Claude API calling and payload format."""

    @patch("app.ai.summarizer.urlopen")
    def test_claude_request_structure(self, mock_urlopen):
        cfg = AIConfig(
            enabled=True,
            provider="claude",
            url="https://api.anthropic.com/v1",
            api_key="sk-ant-test-key-12345",
            model="claude-3-5-haiku-20241022",
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "msg_01X",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "All 12 databases passed health and integrity checks."}
            ],
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = _call_claude_api("Summarize backup status", cfg)
        assert result == "All 12 databases passed health and integrity checks."

        req = mock_urlopen.call_args[0][0]
        assert req.headers["X-api-key"] == "sk-ant-test-key-12345"
        assert req.headers["Anthropic-version"] == "2023-06-01"
        assert req.headers["Content-type"] == "application/json"

        body = json.loads(req.data.decode("utf-8"))
        assert body["model"] == "claude-3-5-haiku-20241022"
        assert "messages" in body
        assert body["messages"][0]["content"] == "Summarize backup status"
        assert "system" in body


class TestUniversalDispatch:
    """Test end-to-end dispatch and summarize_scan behavior."""

    @patch("app.ai.summarizer.urlopen")
    def test_dispatch_openai_format(self, mock_urlopen):
        cfg = AIConfig(
            enabled=True,
            provider="openai",
            url="https://api.openai.com/v1",
            api_key="sk-openai-test-key",
            model="gpt-4o-mini",
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [
                {"message": {"role": "assistant", "content": "Database backups are up to date."}}
            ]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        summary = _dispatch_ai_request("Test prompt", cfg)
        assert summary == "Database backups are up to date."

        req = mock_urlopen.call_args[0][0]
        assert req.headers["Authorization"] == "Bearer sk-openai-test-key"
        body = json.loads(req.data.decode("utf-8"))
        assert body["model"] == "gpt-4o-mini"
