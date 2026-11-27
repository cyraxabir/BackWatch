"""Tests for OpenWebUI / OpenAI-compatible AI integration with authentication."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.ai.ollama import _resolve_endpoints, _call_ai_api, summarize_scan
from app.config import AIConfig
from app.models import HealthStatus, ScanResult, BackupGroup


class TestOpenWebUIResolution:
    """Test resolution of OpenWebUI and OpenAI-compatible endpoints."""

    def test_explicit_openai_type(self):
        cfg = AIConfig(url="http://openwebui:8080/api", api_type="openai")
        endpoint, call_type = _resolve_endpoints(cfg)
        assert call_type == "openai"
        assert endpoint == "http://openwebui:8080/api/v1/chat/completions"

    def test_explicit_openai_v1(self):
        cfg = AIConfig(url="http://openwebui:8080/v1", api_type="openai")
        endpoint, call_type = _resolve_endpoints(cfg)
        assert call_type == "openai"
        assert endpoint == "http://openwebui:8080/v1/chat/completions"

    def test_auto_detect_with_api_key(self):
        """When API key is present in auto mode, default to OpenAI-compatible format."""
        cfg = AIConfig(url="http://openwebui:8080", api_key="sk-test-token-12345", api_type="auto")
        endpoint, call_type = _resolve_endpoints(cfg)
        assert call_type == "openai"
        assert "chat/completions" in endpoint

    def test_native_ollama_fallback(self):
        cfg = AIConfig(url="http://ollama:11434", api_key="", api_type="ollama")
        endpoint, call_type = _resolve_endpoints(cfg)
        assert call_type == "ollama"
        assert endpoint == "http://ollama:11434/api/generate"


class TestOpenWebUICall:
    """Test API call with Authorization Bearer header and OpenAI format."""

    @patch("app.ai.ollama.urlopen")
    def test_openai_format_with_bearer_token(self, mock_urlopen):
        cfg = AIConfig(
            enabled=True,
            url="http://openwebui:8080/v1",
            api_key="sk-test-secret-key",
            api_type="openai",
            model="qwen2.5:7b",
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "All backups on 192.0.2.10 are healthy and within SLA.",
                    }
                }
            ]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        summary = _call_ai_api("Summarize backup status", cfg)
        assert summary == "All backups on 192.0.2.10 are healthy and within SLA."

        # Verify request details
        req = mock_urlopen.call_args[0][0]
        assert req.headers["Authorization"] == "Bearer sk-test-secret-key"
        assert req.headers["Content-type"] == "application/json"

        body = json.loads(req.data.decode("utf-8"))
        assert body["model"] == "qwen2.5:7b"
        assert len(body["messages"]) == 2

    @patch("app.ai.ollama.urlopen")
    def test_summarize_scan_disabled(self, mock_urlopen):
        cfg = AIConfig(enabled=False)
        result = ScanResult(timestamp=None, groups=[], retention_candidates=[])
        summary = summarize_scan(result, cfg)
        assert summary is None
        mock_urlopen.assert_not_called()
