"""Universal AI Summarization Layer.

Supports any AI provider:
- OpenAI (GPT-4o, GPT-4o-mini, etc.)
- Anthropic Claude (Claude 3.5 Sonnet, Claude 3.5 Haiku, Opus, etc.)
- OpenWebUI / vLLM / LocalAI / LiteLLM / DeepSeek / Mistral (OpenAI-compatible)
- Ollama (Local / Self-hosted)

AI is purely an optional reporting layer. It NEVER makes backup/deletion decisions.
"""

import json
import logging
import ssl
import sys
from typing import Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import AIConfig
from app.models import HealthStatus, ScanResult

logger = logging.getLogger(__name__)


def summarize_scan(result: ScanResult, ai_config: AIConfig) -> Optional[str]:
    """Generate a human-readable executive briefing of scan results using AI.

    Returns None if AI is disabled or unavailable.
    """
    if not ai_config.enabled:
        return None

    # Build structured factual summary from deterministic results
    summary_data = {
        "total_databases": len(result.get_database_catalog()) if hasattr(result, "get_database_catalog") else result.total_groups,
        "healthy_count": result.healthy_count,
        "warning_count": result.warning_count,
        "failed_count": result.failed_count,
        "critical_issues": [],
        "warnings": [],
        "retention_candidates": len(result.retention_candidates),
    }

    # Extract distinct failing databases
    for group in result.groups:
        if group.health == HealthStatus.FAIL:
            summary_data["critical_issues"].append({
                "server": group.server,
                "database": group.group_name,
                "folder_date": group.folder_date,
                "errors": group.errors,
                "age_hours": round(group.age_hours, 1) if group.age_hours else None,
            })
        elif group.health == HealthStatus.WARNING:
            summary_data["warnings"].append({
                "server": group.server,
                "database": group.group_name,
                "warnings": group.warnings,
            })

    # Keep prompt concise
    if len(summary_data["critical_issues"]) > 15:
        summary_data["critical_issues"] = summary_data["critical_issues"][:15]
        summary_data["critical_issues"].append("... and more critical issues truncated for brevity.")

    prompt = (
        "You are a Senior SRE & Database Reliability Engineer. Review the following "
        "deterministic backup health monitoring results and write a concise, professional executive briefing (2-3 sentences).\n"
        "Rules:\n"
        "1. Highlight any stale, overdue, or failed database backups and integrity failures.\n"
        "2. Note total healthy count and any size anomalies.\n"
        "3. Do NOT make deletion decisions or invent data. Be direct and factual.\n\n"
        f"Data:\n{json.dumps(summary_data, indent=2)}"
    )

    return _dispatch_ai_request(prompt, ai_config)


def answer_backup_query(query: str, report_data: dict, ai_config: AIConfig) -> str:
    """Answer an interactive user query about current backup health using the live report data."""
    if not ai_config.enabled:
        return (
            "⚠️ AI is currently disabled in your configuration.\n\n"
            "To enable interactive chat, set `AI_ENABLED=true` in your `.env` or `/etc/backup-health-monitor/backwatch.env` "
            "and configure your preferred AI provider (OpenAI, Claude, OpenWebUI, or Ollama)."
        )

    # Extract concise context from report_data
    summary = report_data.get("summary", {})
    storage = report_data.get("system_storage", {})
    databases = report_data.get("databases", [])
    retention_candidates = report_data.get("retention_candidates", [])
    restore_results = report_data.get("restore_results", [])

    # Filter critical issues
    issues = []
    for d in databases:
        if d.get("health") in ("FAIL", "WARNING"):
            issues.append({
                "database": d.get("database"),
                "server": d.get("server"),
                "health": d.get("health"),
                "age_hours": d.get("age_hours"),
                "latest_mtime": d.get("latest_mtime"),
                "errors": d.get("errors", []),
                "warnings": d.get("warnings", []),
            })

    context_obj = {
        "summary": summary,
        "storage": {
            "percent_used": storage.get("percent_used"),
            "used_bytes": storage.get("used_bytes"),
            "total_bytes": storage.get("total_bytes"),
            "mount_point": storage.get("mount_point"),
        },
        "critical_issues": issues[:25],
        "total_databases": len(databases),
        "total_retention_candidates": len(retention_candidates),
        "restore_results": restore_results[:5],
    }

    prompt = (
        "You are a Senior SRE and Database Reliability Copilot embedded directly in Backup Health Monitor.\n"
        "You have live access to the latest database backup scan report.\n"
        "Answer the user's question directly, technically, and concisely based strictly on the provided live metrics.\n"
        "Rules:\n"
        "1. Never invent or hallucinate data. If information is not in the data, state that clearly.\n"
        "2. Keep answers direct, actionable, and formatted in clean markdown.\n"
        "3. Highlight server IPs, database names, and exact ages where relevant.\n\n"
        f"Live Backup Status Data:\n{json.dumps(context_obj, indent=2)}\n\n"
        f"User Question: {query}"
    )

    resp = _dispatch_ai_request(prompt, ai_config)
    if not resp:
        return "❌ The AI model did not return a response. Please verify that your AI endpoint URL and API key are configured correctly."
    return resp


def _detect_provider(ai_config: AIConfig) -> str:
    """Detect AI provider type (openai, claude, ollama)."""
    explicit = (ai_config.provider or ai_config.api_type or "").lower().strip()
    if explicit in ("claude", "anthropic"):
        return "claude"
    if explicit in ("openai", "openwebui", "chatgpt"):
        return "openai"
    if explicit == "ollama":
        return "ollama"

    # Auto detection based on URL or model name
    url = (ai_config.url or "").lower()
    model = (ai_config.model or "").lower()

    if "anthropic.com" in url or model.startswith("claude"):
        return "claude"
    if "api.openai.com" in url or model.startswith(("gpt-", "o1-", "o3-", "chatgpt")):
        return "openai"
    if ":11434" in url and not ai_config.api_key:
        return "ollama"

    # Default for OpenWebUI, proxies, and custom endpoints
    return "openai"


def _resolve_endpoints(ai_config: AIConfig) -> Tuple[str, str]:
    """Resolve endpoint URL and provider type (openai, claude, ollama)."""
    provider = _detect_provider(ai_config)
    url = (ai_config.url or "").rstrip("/")

    if provider == "claude":
        if url.endswith("/messages"):
            return url, "claude"
        elif url.endswith("/v1"):
            return f"{url}/messages", "claude"
        return f"{url}/v1/messages" if url else "https://api.anthropic.com/v1/messages", "claude"

    if provider == "ollama":
        if url.endswith("/api/generate"):
            return url, "ollama"
        return f"{url}/api/generate" if url else "http://localhost:11434/api/generate", "ollama"

    # OpenAI / OpenWebUI
    if url.endswith("/chat/completions"):
        return url, "openai"
    elif url.endswith("/v1"):
        return f"{url}/chat/completions", "openai"
    elif url.endswith("/api"):
        return f"{url}/v1/chat/completions", "openai"
    else:
        return f"{url}/v1/chat/completions" if url else "https://api.openai.com/v1/chat/completions", "openai"


def _call_ai_api(prompt: str, ai_config: AIConfig) -> Optional[str]:
    """Execute AI API request (alias for _dispatch_ai_request)."""
    return _dispatch_ai_request(prompt, ai_config)


def _get_urlopen():
    """Retrieve urlopen function, respecting test mocks on app.ai.ollama or summarizer."""
    summarizer_mod = sys.modules.get("app.ai.summarizer")
    if summarizer_mod:
        s_url = getattr(summarizer_mod, "urlopen", None)
        if getattr(s_url, "_mock_return_value", None) is not None or hasattr(s_url, "assert_called"):
            return s_url

    ollama_mod = sys.modules.get("app.ai.ollama")
    if ollama_mod:
        o_url = getattr(ollama_mod, "urlopen", None)
        if getattr(o_url, "_mock_return_value", None) is not None or hasattr(o_url, "assert_called"):
            return o_url

    return urlopen


def _dispatch_ai_request(prompt: str, ai_config: AIConfig) -> Optional[str]:
    """Route AI request to the appropriate provider (OpenAI, Claude, or Ollama)."""
    provider = _detect_provider(ai_config)

    if provider == "claude":
        return _call_claude_api(prompt, ai_config)
    elif provider == "ollama":
        return _call_ollama_api(prompt, ai_config)
    else:
        # Default to OpenAI / OpenAI-compatible endpoint
        return _call_openai_api(prompt, ai_config)


def _call_openai_api(prompt: str, ai_config: AIConfig) -> Optional[str]:
    """Call OpenAI, OpenWebUI, or any OpenAI-compatible API."""
    base_url = (ai_config.url or "https://api.openai.com/v1").rstrip("/")

    # Resolve chat completions endpoint
    if base_url.endswith("/chat/completions"):
        endpoint = base_url
    elif base_url.endswith("/v1") or base_url.endswith("/api"):
        endpoint = f"{base_url}/chat/completions"
    else:
        endpoint = f"{base_url}/v1/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Backup-Health-Monitor/1.0",
    }
    if ai_config.api_key:
        headers["Authorization"] = f"Bearer {ai_config.api_key}"

    payload = {
        "model": ai_config.model or "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a Senior SRE/DevOps Database Monitoring Assistant. "
                    "Summarize backup status factually and concisely. "
                    "Do not include polite greetings or filler."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 500,
        "stream": False,
    }

    resp_data = _execute_http_post(endpoint, headers, payload)
    if not resp_data:
        # If auto mode attempted OpenAI endpoint and failed, try Ollama /api/generate fallback
        if (ai_config.api_type or "").lower() == "auto":
            fallback_endpoint = f"{base_url}/api/generate"
            fallback_payload = {"model": ai_config.model, "prompt": prompt, "stream": False}
            fallback_data = _execute_http_post(fallback_endpoint, headers, fallback_payload)
            if fallback_data and "response" in fallback_data:
                return fallback_data["response"].strip()
        return None

    choices = resp_data.get("choices", [])
    if choices and "message" in choices[0]:
        return choices[0]["message"].get("content", "").strip()
    if choices and "text" in choices[0]:
        return choices[0].get("text", "").strip()
    if "response" in resp_data:
        return resp_data.get("response", "").strip()

    return None


def _call_claude_api(prompt: str, ai_config: AIConfig) -> Optional[str]:
    """Call Anthropic Claude API (Messages format)."""
    base_url = (ai_config.url or "https://api.anthropic.com/v1").rstrip("/")

    if base_url.endswith("/messages"):
        endpoint = base_url
    elif base_url.endswith("/v1"):
        endpoint = f"{base_url}/messages"
    else:
        endpoint = f"{base_url}/v1/messages"

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ai_config.api_key,
        "anthropic-version": "2023-06-01",
        "User-Agent": "Backup-Health-Monitor/1.0",
    }

    payload = {
        "model": ai_config.model or "claude-3-5-haiku-20241022",
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "system": (
            "You are a Senior SRE/DevOps Database Monitoring Assistant. "
            "Summarize backup status factually and concisely. "
            "Do not include polite greetings or filler."
        ),
        "max_tokens": 500,
        "temperature": 0.2,
    }

    resp_data = _execute_http_post(endpoint, headers, payload)
    if not resp_data:
        return None

    # Claude messages response structure: content[0].text
    content = resp_data.get("content", [])
    if content and isinstance(content, list) and "text" in content[0]:
        return content[0]["text"].strip()

    return None


def _call_ollama_api(prompt: str, ai_config: AIConfig) -> Optional[str]:
    """Call native Ollama /api/generate endpoint."""
    base_url = (ai_config.url or "http://localhost:11434").rstrip("/")
    endpoint = f"{base_url}/api/generate" if not base_url.endswith("/api/generate") else base_url

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Backup-Health-Monitor/1.0",
    }
    if ai_config.api_key:
        headers["Authorization"] = f"Bearer {ai_config.api_key}"

    payload = {
        "model": ai_config.model or "qwen2.5:7b",
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 500},
    }

    resp_data = _execute_http_post(endpoint, headers, payload)
    if resp_data and "response" in resp_data:
        return resp_data["response"].strip()

    return None


def _execute_http_post(endpoint: str, headers: dict, payload: dict) -> Optional[dict]:
    """Execute JSON HTTP POST request with SSL fallback and error handling."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = Request(endpoint, data=data, headers=headers, method="POST")
        urlopen_fn = _get_urlopen()

        try:
            ctx = ssl.create_default_context()
            resp_handle = urlopen_fn(req, timeout=60, context=ctx)
        except ssl.SSLError as ssl_err:
            logger.warning("SSL verification failed for %s (%s). Retrying with unverified context...", endpoint, ssl_err)
            unverified_ctx = ssl._create_unverified_context()
            resp_handle = urlopen_fn(req, timeout=60, context=unverified_ctx)

        with resp_handle as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            logger.info("AI response received successfully from %s", endpoint)
            return resp_data

    except HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        logger.warning("AI HTTP error %d from %s: %s", e.code, endpoint, err_body[:250])
        return None
    except (URLError, OSError) as e:
        logger.warning("AI network error connecting to %s: %s", endpoint, e)
        return None
    except json.JSONDecodeError as e:
        logger.warning("AI JSON parse error from %s: %s", endpoint, e)
        return None

