"""Backward-compatibility shim for app.ai.ollama -> app.ai.summarizer."""

from urllib.request import urlopen
from app.ai.summarizer import (
    summarize_scan,
    _call_openai_api,
    _call_claude_api,
    _call_ollama_api,
    _resolve_endpoints,
    _call_ai_api,
    _dispatch_ai_request,
    _detect_provider,
    answer_backup_query,
)

__all__ = [
    "summarize_scan",
    "answer_backup_query",
    "_call_openai_api",
    "_call_claude_api",
    "_call_ollama_api",
    "_resolve_endpoints",
    "_call_ai_api",
    "_dispatch_ai_request",
    "_detect_provider",
    "urlopen",
]
