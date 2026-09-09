from __future__ import annotations

import ipaddress
import json
import re
import os
import asyncio
import socket
from typing import AsyncGenerator, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException
from pydantic import AnyHttpUrl

from ..utils.http_client import HttpClient

from ..config import get_settings
from ..services.model_registry import ModelInfo
from ..utils.errors import api_error
from ..utils.http import extract_http_error
from ..utils.model_helpers import strip_provider_prefix
from . import web_search
# Crawler manager is imported lazily where needed to avoid optional dependency on playwright

logger = __import__("logging").getLogger("ailinux.chat")

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


NOVA_FALLBACK_CASCADE = [
    "gpt-oss:120b-cloud",
    "gpt-oss:20b-cloud",
    "openai/gpt-oss-120b:free",
]

async def _fallback_to_ollama(
    messages: List[dict[str, str]],
    temperature: Optional[float],
    timeout: float,
    original_provider: str,
    original_error: Exception,
    fallback_model: str | None = None,
) -> AsyncGenerator[str, None]:
    """Unified fallback logic - streams from Ollama when primary provider fails."""
    settings = get_settings()
    model = fallback_model or settings.ollama_fallback_model
    logger.warning("%s failed (%s), falling back to %s", original_provider, original_error, model)
    async for chunk in _stream_ollama(
        model, messages, temperature=temperature, stream=True, timeout=timeout
    ):
        yield chunk

STRUCTURE_PROMPT_MARKER = "nova_format_guideline"
STRUCTURE_PROMPT = (
    "nova_format_guideline: Answer every request in clean, well-structured Markdown. "
    "Start with a brief overview sentence, follow with bullet lists for key actions or findings, "
    "and group related details under short bolded labels when useful. Keep paragraphs short, "
    "add blank lines between sections, and prefer numbered lists for ordered steps."
)

MISTRAL_MODEL_ALIASES = {
    # Legacy aliases
    "mistral/open-mixtral-8x7b": "open-mixtral-8x7b",
    "mistral/mixtral-8x7b": "open-mixtral-8x7b",
    # Current generation (map to latest versions)
    "mistral/large": "mistral-medium-3.5",
    "mistral/medium": "mistral-medium-latest",
    "mistral/small": "mistral-small-latest",
    "mistral/tiny": "ministral-3b-latest",
    # Shorthand aliases
    "large": "mistral-medium-3.5",
    "medium": "mistral-medium-latest",
    "small": "mistral-small-latest",
    "tiny": "ministral-3b-latest",
    # Specialist models
    "mistral/codestral": "codestral-latest",
    "codestral": "codestral-latest",
    # Ministral models
    "mistral/ministral-8b": "ministral-8b-latest",
    "mistral/ministral-3b": "ministral-3b-latest",
    "ministral-8b": "ministral-8b-latest",
    "ministral-3b": "ministral-3b-latest",
}

GEMINI_MODEL_ALIASES = {
    # Gemini 3.x current generation
    "gemini/gemini-3.5-flash": "gemini-3.5-flash",
    "gemini-3.5-flash": "gemini-3.5-flash",
    "gemini/gemini-3.1-pro": "gemini-3.1-pro-preview",
    "gemini/gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
    "gemini-3.1-pro": "gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
    "gemini/gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
    "gemini/gemini-3-pro": "gemini-3.1-pro-preview",
    "gemini-3-pro": "gemini-3.1-pro-preview",
    # Gemini 2.5 Models (use simple names, no preview suffix needed)
    "gemini/gemini-2.5-pro": "gemini-2.5-pro",
    "gemini/gemini-2.5-flash": "gemini-2.5-flash",
    "gemini/gemini-2.5-flash-lite": "gemini-2.5-flash-lite",
    # Gemini 2.0 Models (use stable, not -exp)
    "gemini/gemini-2.0-flash": "gemini-2.0-flash",
    "gemini/gemini-2.0-flash-exp": "gemini-2.0-flash",  # Map exp to stable
    "gemini-2.0-flash-exp": "gemini-2.0-flash",  # Map exp to stable
    "gemini/gemini-2.0-flash-lite": "gemini-2.0-flash-lite",
    # Gemini 1.5 Models
    "gemini/gemini-1.5-flash": "gemini-1.5-flash",
    "gemini/gemini-1.5-flash-8b": "gemini-1.5-flash-8b",
    "gemini/gemini-1.5-pro": "gemini-1.5-pro",
    # Legacy aliases (map to current stable Gemini 3.5 Flash)
    "gemini/gemini-pro": "gemini-3.5-flash",
    "gemini/pro": "gemini-3.5-flash",
    "gemini/gemini-pro-vision": "gemini-3.5-flash",
    "gemini-pro": "gemini-3.5-flash",
    "pro": "gemini-3.5-flash",
}

OLLAMA_MODEL_ALIASES = {
    "gpt-oss:cloud/120b": "gpt-oss:120b-cloud",
    "gpt-oss:cloud/20b": "gpt-oss:20b-cloud",
    "llama3.2:latest": "gpt-oss:20b-cloud",
    "llama3.2": "gpt-oss:20b-cloud",
}

ANTHROPIC_MODEL_ALIASES = {
    # Claude 4 Series (Latest - use model IDs from Anthropic API)
    "anthropic/claude-sonnet-4": "claude-sonnet-4-6",
    "anthropic/claude-opus-4": "claude-opus-4-8",
    "claude-sonnet-4": "claude-sonnet-4-6",
    "claude-opus-4": "claude-opus-4-8",
    # Claude 3.5 Series
    "anthropic/claude-3.5-sonnet": "claude-sonnet-4-6",
    "anthropic/claude-3.5-haiku": "claude-3-5-haiku-20241022",
    "claude-3.5-sonnet": "claude-sonnet-4-6",
    "claude-3.5-haiku": "claude-3-5-haiku-20241022",
    "claude-3-5-sonnet": "claude-sonnet-4-6",
    "claude-3-5-haiku": "claude-3-5-haiku-20241022",
    # Claude 3 Series
    "anthropic/claude-3-opus": "claude-3-opus-20240229",
    "anthropic/claude-3-sonnet": "claude-3-sonnet-20240229",
    "anthropic/claude-3-haiku": "claude-3-haiku-20240307",
    "claude-3-opus": "claude-3-opus-20240229",
    "claude-3-sonnet": "claude-3-sonnet-20240229",
    "claude-3-haiku": "claude-3-haiku-20240307",
    # Legacy aliases (defaults to latest Sonnet)
    "anthropic/claude": "claude-sonnet-4-6",
    "claude": "claude-sonnet-4-6",
}

# OpenAI-compatible providers (Groq, Cerebras, Together, Fireworks, OpenRouter)
# These all use the same API format but different base URLs
OPENAI_COMPATIBLE_PROVIDERS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_setting": "openai_api_key",
        "api_key_env": "OPENAI_API_KEY",
        "timeout_setting": "openai_timeout_ms",
        "headers": {},
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_setting": "groq_api_key",
        "timeout_setting": "groq_timeout_ms",
        "headers": {},
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "api_key_setting": "cerebras_api_key",
        "timeout_setting": "cerebras_timeout_ms",
        "headers": {},
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_setting": "nvidia_api_key",
        "api_key_env": "NVIDIA_API_KEY",
        "timeout_setting": "nvidia_timeout_ms",
        "headers": {},
    },
    "kimi": {
        "base_url": "https://api.moonshot.ai/v1",
        "api_key_setting": "kimi_api_key",
        "api_key_env": "KIMI_API_KEY",
        "timeout_setting": "kimi_timeout_ms",
        "timeout_ms": 120000,
        "headers": {},
    },
    "huggingface": {
        "base_url": "https://router.huggingface.co/v1",
        "api_key_setting": "huggingface_api_key",
        "api_key_env": "HUGGINGFACE_API_KEY",
        "timeout_setting": "huggingface_timeout_ms",
        "timeout_ms": 120000,
        "headers": {},
    },
    "github": {
        "base_url": "https://models.github.ai/inference",
        "api_key_setting": "github_token",
        "api_key_env": "GITHUB_TOKEN",
        "timeout_setting": "github_models_timeout_ms",
        "headers": {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        },
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "api_key_setting": "together_api_key",
        "timeout_setting": "together_timeout_ms",
        "headers": {},
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_setting": "fireworks_api_key",
        "timeout_setting": "fireworks_timeout_ms",
        "headers": {},
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_setting": "openrouter_api_key",
        "timeout_setting": "openrouter_timeout_ms",
        "headers": {
            "HTTP-Referer": "https://api.ailinux.me",
            "X-Title": "AILinux TriForce",
        },
    },
}

UNCERTAINTY_PHRASES = [
    "i don't know",
    "i am not sure",
    "i cannot answer",
    "i can't answer",
    "i do not have information",
    "i don't have information",
]

CRAWLER_PHRASES = [
    "crawl this",
    "crawl the",
    "crawl url",
    "crawl website",
    "crawl page",
    "analyze this website",
    "analyze the website",
    "explore this website",
    "explore the website",
    "scrape this",
    "scrape the",
    "fetch website",
    "fetch this page",
]

# SSRF Protection: Blocked IP ranges (RFC 1918, loopback, link-local, etc.)
BLOCKED_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),        # Private Class A
    ipaddress.ip_network("172.16.0.0/12"),     # Private Class B
    ipaddress.ip_network("192.168.0.0/16"),    # Private Class C
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback
    ipaddress.ip_network("169.254.0.0/16"),    # Link-local
    ipaddress.ip_network("0.0.0.0/8"),         # Current network
    ipaddress.ip_network("100.64.0.0/10"),     # Shared address space
    ipaddress.ip_network("192.0.0.0/24"),      # IETF Protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),      # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),   # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),    # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),       # Multicast
    ipaddress.ip_network("240.0.0.0/4"),       # Reserved
    ipaddress.ip_network("255.255.255.255/32"), # Broadcast
    # IPv6 blocked ranges
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]


def _is_ssrf_safe(url: str) -> bool:
    """Check if URL is safe from SSRF attacks.

    Returns True if URL points to a public internet address,
    False if it could access internal resources.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname

        if not hostname:
            return False

        # Block common internal hostnames
        blocked_hostnames = {
            "localhost", "127.0.0.1", "::1", "0.0.0.0",
            "metadata.google.internal", "169.254.169.254",  # Cloud metadata
            "metadata", "internal", "local", "intranet",
        }
        if hostname.lower() in blocked_hostnames:
            logger.warning(f"SSRF blocked: hostname '{hostname}' is blocked")
            return False

        # Block .local, .internal, .localhost TLDs
        if any(hostname.lower().endswith(tld) for tld in [".local", ".internal", ".localhost", ".lan"]):
            logger.warning(f"SSRF blocked: hostname '{hostname}' has blocked TLD")
            return False

        # Resolve hostname and check IP
        try:
            ip_str = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(ip_str)

            for blocked_range in BLOCKED_IP_RANGES:
                if ip in blocked_range:
                    logger.warning(f"SSRF blocked: IP {ip_str} for '{hostname}' is in blocked range {blocked_range}")
                    return False

        except socket.gaierror:
            # DNS resolution failed - could be internal or non-existent
            logger.warning(f"SSRF blocked: DNS resolution failed for '{hostname}'")
            return False

        return True

    except Exception as e:
        logger.warning(f"SSRF check failed for URL '{url}': {e}")
        return False


def _extract_safe_urls(text: str) -> List[str]:
    """Extract URLs from text and filter out SSRF-unsafe ones."""
    raw_urls = re.findall(r"(https?://[^\s]+)", text)
    safe_urls = []

    for url in raw_urls:
        # Clean URL (remove trailing punctuation)
        url = url.rstrip(".,;:!?\"')")

        if _is_ssrf_safe(url):
            safe_urls.append(url)
        else:
            logger.info(f"Filtered unsafe URL from user input: {url[:50]}...")

    return safe_urls


def _format_messages(messages: Iterable[dict[str, str]]) -> List[dict[str, str]]:
    formatted: List[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if not role or content is None:
            continue
        formatted.append({"role": role, "content": content})
    if not formatted:
        raise api_error("Messages cannot be empty", status_code=422, code="missing_messages")
    return formatted


def _ensure_structure_prompt(messages: List[dict[str, str]]) -> List[dict[str, str]]:
    for message in messages:
        if message["role"] == "system" and STRUCTURE_PROMPT_MARKER in message["content"]:
            return messages
    return [{"role": "system", "content": STRUCTURE_PROMPT}] + messages


async def _get_initial_response(
    model: ModelInfo,
    request_model: str,
    messages: List[dict[str, str]],
    temperature: Optional[float],
    settings,
) -> str:
    chunks = []
    if model.provider == "ollama":
        async for chunk in _stream_ollama(
            request_model,
            messages,
            temperature=temperature,
            stream=True,
            timeout=int(settings.request_timeout),
        ):
            chunks.append(chunk)
    elif model.provider == "mistral":
        if not settings.mistral_api_key:
            raise api_error("Mistral support is not configured", status_code=503, code="mistral_unavailable")
        try:
            async for chunk in _stream_mistral(
                request_model,
                messages,
                api_key=settings.mistral_api_key,
                organisation_id=settings.mistral_organisation_id,
                temperature=temperature,
                stream=True,
                timeout=int(settings.request_timeout),
            ):
                chunks.append(chunk)
        except Exception as exc:
            async for chunk in _fallback_to_ollama(messages, temperature, settings.request_timeout, "Mistral", exc):
                chunks.append(chunk)
    elif model.provider == "gemini":
        if not settings.gemini_api_key:
            raise api_error("Gemini support is not configured", status_code=503, code="gemini_unavailable")
        try:
            async for chunk in _stream_gemini(
                request_model,
                messages,
                api_key=settings.gemini_api_key,
                temperature=temperature,
                stream=True,
                timeout=int(settings.request_timeout),
            ):
                chunks.append(chunk)
        except Exception as exc:
            async for chunk in _fallback_to_ollama(messages, temperature, settings.request_timeout, "Gemini", exc):
                chunks.append(chunk)
    elif model.provider == "gpt-oss":
        if not settings.gpt_oss_api_key or not settings.gpt_oss_base_url:
            raise api_error("GPT-OSS support is not configured (missing API key or base URL)", status_code=503, code="gpt_oss_unavailable")
        try:
            async for chunk in _stream_gpt_oss(
                model.id,
                messages,
                api_key=settings.gpt_oss_api_key,
                base_url=settings.gpt_oss_base_url,
                temperature=temperature,
                stream=True,
                timeout=int(settings.request_timeout),
            ):
                chunks.append(chunk)
        except Exception as exc:
            async for chunk in _fallback_to_ollama(messages, temperature, settings.request_timeout, "GPT-OSS", exc):
                chunks.append(chunk)
    elif model.provider == "anthropic":
        if not settings.anthropic_api_key:
            raise api_error("Anthropic Claude support is not configured", status_code=503, code="anthropic_unavailable")
        try:
            async for chunk in _stream_anthropic(
                request_model,
                messages,
                api_key=settings.anthropic_api_key,
                temperature=temperature,
                stream=True,
                timeout=settings.anthropic_timeout_ms / 1000.0,
                max_tokens=settings.anthropic_max_tokens,
            ):
                chunks.append(chunk)
        except Exception as exc:
            async for chunk in _fallback_to_ollama(messages, temperature, settings.request_timeout, "Anthropic", exc):
                chunks.append(chunk)
    elif model.provider in OPENAI_COMPATIBLE_PROVIDERS:
        # Handle Groq, Cerebras, Together, Fireworks, OpenRouter
        provider_config = OPENAI_COMPATIBLE_PROVIDERS[model.provider]
        api_key = (
            getattr(settings, provider_config["api_key_setting"], None)
            or os.getenv(provider_config.get("api_key_env", ""))
        )
        if not api_key:
            raise api_error(f"{model.provider.title()} support is not configured", status_code=503, code=f"{model.provider}_unavailable")
        timeout_ms = provider_config.get("timeout_ms") or getattr(
            settings, provider_config["timeout_setting"], 30000
        )
        try:
            async for chunk in _stream_openai_compatible(
                request_model,
                messages,
                api_key=api_key,
                base_url=provider_config["base_url"],
                extra_headers=provider_config.get("headers", {}),
                temperature=temperature,
                stream=True,
                timeout=timeout_ms / 1000.0,
                provider=model.provider,
            ):
                chunks.append(chunk)
        except Exception as exc:
            async for chunk in _fallback_to_ollama(messages, temperature, settings.request_timeout, model.provider.title(), exc):
                chunks.append(chunk)
    elif model.provider == "cohere":
        if not settings.cohere_api_key:
            raise api_error("Cohere support is not configured", status_code=503, code="cohere_unavailable")
        try:
            async for chunk in _stream_cohere(
                request_model,
                messages,
                api_key=settings.cohere_api_key,
                temperature=temperature,
                stream=True,
                timeout=settings.cohere_timeout_ms / 1000.0,
            ):
                chunks.append(chunk)
        except Exception as exc:
            async for chunk in _fallback_to_ollama(messages, temperature, settings.request_timeout, "Cohere", exc):
                chunks.append(chunk)
    elif model.provider == "cloudflare":
        if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
            raise api_error("Cloudflare Workers AI support is not configured", status_code=503, code="cloudflare_unavailable")
        try:
            async for chunk in _stream_cloudflare(
                request_model,
                messages,
                account_id=settings.cloudflare_account_id,
                api_token=settings.cloudflare_api_token,
                temperature=temperature,
                stream=True,
                timeout=30.0,
            ):
                chunks.append(chunk)
        except Exception as exc:
            async for chunk in _fallback_to_ollama(messages, temperature, settings.request_timeout, "Cloudflare", exc):
                chunks.append(chunk)
    else:
        raise api_error("Unsupported provider", status_code=400, code="unsupported_provider")

    if not chunks:
        logger.warning("Provider stream was empty for model %s", request_model)
        raise api_error("Provider returned an empty response", status_code=502, code="empty_provider_response")

    return "".join(chunks)


# =============================================================================
# WRAPPER FUNCTIONS - For backwards compatibility
# =============================================================================

async def handle_chat(params: dict) -> dict:
    """
    Wrapper for handle_chat_smart from chat_router.
    Provides backwards compatibility for old imports.
    """
    from app.services.chat_router import handle_chat_smart
    return await handle_chat_smart(params)


async def generate_response(
    message: str,
    model: str = "gemini-2.0-flash-001",
    temperature: float = 0.7,
    max_tokens: int = 2048,
    **kwargs
) -> str:
    """
    Generate a simple text response using the chat system.
    Wrapper around stream_chat for synchronous-style usage.

    Returns the complete response text.
    """
    messages = [{"role": "user", "content": message}]

    full_response = ""
    async for chunk in stream_chat(messages, model=model, temperature=temperature, max_tokens=max_tokens, **kwargs):
        if isinstance(chunk, dict) and "content" in chunk:
            full_response += chunk["content"]

    return full_response


async def stream_chat(
    model: ModelInfo,
    request_model: str,
    messages: Iterable[dict[str, str]],
    *,
    stream: bool,
    temperature: Optional[float] = None,
) -> AsyncGenerator[str, None]:
    settings = get_settings()
    formatted_messages = _ensure_structure_prompt(_format_messages(messages))
    user_query = formatted_messages[-1]["content"]

    # =========================================================================
    # MCP Command Filter - Execute API commands transparently
    # =========================================================================
    try:
        from .mcp_filter import mcp_filter
        processed_query, mcp_results = await mcp_filter.process_message(
            user_query, execute=True, inject_results=False
        )

        # If MCP commands were executed, inject results as context
        if mcp_results:
            mcp_context = "\n\n[System API Results - use this data to answer the user's question]\n"
            for result in mcp_results:
                if result.get('success'):
                    tool = result.get('tool', 'unknown')
                    data = result.get('result', {})
                    mcp_context += f"\n### {tool} Response:\n```json\n{json.dumps(data, indent=2, ensure_ascii=False)[:2000]}\n```\n"

            # Add MCP results as system context
            formatted_messages = formatted_messages[:-1] + [
                {"role": "system", "content": mcp_context},
                formatted_messages[-1]  # Keep user message last
            ]
            logger.info(f"MCP Filter: Executed {len(mcp_results)} commands, injected context")
    except Exception as mcp_exc:
        logger.warning(f"MCP Filter error (non-fatal): {mcp_exc}")

    # Get initial response
    initial_response = await _get_initial_response(model, request_model, formatted_messages, temperature, settings)

    # Check for uncertainty (web search)
    if any(phrase in initial_response.lower() for phrase in UNCERTAINTY_PHRASES):
        yield "Ich bin mir nicht sicher, aber ich werde im Web danach suchen...\n\n"
        
        search_results = await web_search.search_web(user_query)
        
        if not search_results:
            yield "Ich konnte keine relevanten Informationen online finden."
            return

        context = "Web search results:\n"
        for res in search_results:
            context += f"- Title: {res['title']}\n"
            context += f"  URL: {res['url']}\n"
            context += f"  Snippet: {res['snippet']}\n\n"

        augmented_messages = formatted_messages + [
            {"role": "system", "content": "Here is some context from a web search:"},
            {"role": "system", "content": context},
            {"role": "user", "content": f"Based on the web search results, please answer my original question: {user_query}"}
        ]
        logger.debug("Web search augmented messages length: %d", len(json.dumps(augmented_messages)))

        try:
            if model.provider == "ollama":
                async for chunk in _stream_ollama(
                    request_model, augmented_messages, temperature=temperature, stream=stream, timeout=settings.request_timeout
                ):
                    yield chunk
            elif model.provider == "mistral":
                async for chunk in _stream_mistral(
                    request_model, augmented_messages, api_key=settings.mistral_api_key, organisation_id=settings.mistral_organisation_id, temperature=temperature, stream=stream, timeout=settings.request_timeout
                ):
                    yield chunk
            elif model.provider == "gemini":
                async for chunk in _stream_gemini(
                    request_model, augmented_messages, api_key=settings.gemini_api_key, temperature=temperature, stream=stream, timeout=settings.request_timeout
                ):
                    yield chunk
            elif model.provider == "gpt-oss":
                if not settings.gpt_oss_api_key:
                    raise api_error("GPT-OSS support is not configured", status_code=503, code="gpt_oss_unavailable")
                async for chunk in _stream_gpt_oss(
                    model.id,
                    augmented_messages,
                    api_key=settings.gpt_oss_api_key,
                    base_url=settings.gpt_oss_base_url,
                    temperature=temperature,
                    stream=stream,
                    timeout=int(settings.request_timeout),
                ):
                    yield chunk
            elif model.provider == "anthropic":
                if not settings.anthropic_api_key:
                    raise api_error("Anthropic Claude support is not configured", status_code=503, code="anthropic_unavailable")
                async for chunk in _stream_anthropic(
                    request_model,
                    augmented_messages,
                    api_key=settings.anthropic_api_key,
                    temperature=temperature,
                    stream=stream,
                    timeout=settings.anthropic_timeout_ms / 1000.0,
                    max_tokens=settings.anthropic_max_tokens,
                ):
                    yield chunk
        except Exception as exc:
            logger.exception("Error during web search augmented chat streaming: %s", exc)
            raise
    # Check for crawler phrases
    elif any(phrase in user_query.lower() for phrase in CRAWLER_PHRASES):
        yield "Okay, ich werde versuchen, die angeforderten Informationen zu crawlen...\n\n"

        # Extract potential URLs from the user query with SSRF protection
        urls = _extract_safe_urls(user_query)
        if not urls:
            yield "Ich konnte keine sicheren Links in Ihrer Anfrage finden, die ich crawlen könnte."
            return

        # Extract keywords from the user query (excluding URLs and crawler phrases)
        keywords = [word for word in user_query.lower().split() if word not in CRAWLER_PHRASES and not word.startswith("http")]
        if not keywords:
            keywords = ["information"] # Default keyword if none provided

        try:
                from .crawler.manager import crawler_manager as _crawler_manager
                job = await _crawler_manager.create_job(
                    keywords=keywords,
                    seeds=urls,
                    max_depth=5,
                    max_pages=50,
                    allow_external=True,
                    requested_by="chat_tool",
                    user_context=user_query,
                    ollama_assisted=True,
                    ollama_query=user_query,
                    priority="high",  # <<< ensure AI-requested crawls are high priority
                )
                yield f"Crawl job {job.id} gestartet. Status: {job.status}. Bitte warten Sie, während ich die Ergebnisse sammle.\n\n"

                    # Poll job status until completed or failed
                job_status = job.status
                while job_status in ["queued", "running"]:
                    await asyncio.sleep(5) # Poll every 5 seconds
                    updated_job = await _crawler_manager.get_job(job.id)
                    if updated_job:
                        job_status = updated_job.status
                        yield f"Crawl job {job.id} Status: {job_status}. Seiten gecrawlt: {updated_job.pages_crawled}.\n"
                    else:
                        yield f"Fehler: Crawl job {job.id} nicht gefunden.\n"
                        break
                
                if job_status == "completed" and updated_job and updated_job.results:
                    yield "Crawling abgeschlossen. Ich analysiere die Ergebnisse...\n\n"
                    
                    crawl_results_context = "Gecrawlte Ergebnisse:\n"
                    for result_id in updated_job.results[:3]: # Limit context to top 3 results
                        result = await _crawler_manager.get_result(result_id)
                        if result:
                            title = result.title or "Kein Titel"
                            url = result.url or "Keine URL"
                            content_snippet = ""
                            if result.extracted_content_ollama: # Prioritize Ollama extracted content
                                content_snippet = result.extracted_content_ollama[:500] + "..." if len(result.extracted_content_ollama) > 500 else result.extracted_content_ollama
                                crawl_results_context += f"- Titel: {title}\n"
                                crawl_results_context += f"  URL: {url}\n"
                                crawl_results_context += f"  Extrahierter Inhalt (Ollama): {content_snippet}\n\n"
                            elif result.summary:
                                content_snippet = result.summary[:500] + "..." if len(result.summary) > 500 else result.summary
                                crawl_results_context += f"- Titel: {title}\n"
                                crawl_results_context += f"  URL: {url}\n"
                                crawl_results_context += f"  Zusammenfassung: {content_snippet}\n\n"
                            elif result.excerpt:
                                content_snippet = result.excerpt[:500] + "..." if len(result.excerpt) > 500 else result.excerpt
                                crawl_results_context += f"- Titel: {title}\n"
                                crawl_results_context += f"  URL: {url}\n"
                                crawl_results_context += f"  Auszug: {content_snippet}\n\n"

                    augmented_messages = formatted_messages + [
                        {"role": "system", "content": "Hier ist Kontext aus einem Crawl-Job:"},
                        {"role": "system", "content": crawl_results_context},
                        {"role": "user", "content": f"Basierend auf den gecrawlten Ergebnissen, beantworten Sie bitte meine ursprüngliche Frage: {user_query}"}
                    ]
                    logger.debug("Crawler augmented messages length: %d", len(json.dumps(augmented_messages)))

                    try:
                        if model.provider == "ollama":
                            async for chunk in _stream_ollama(
                                request_model, augmented_messages, temperature=temperature, stream=stream, timeout=settings.request_timeout
                            ):
                                yield chunk
                        elif model.provider == "mistral":
                            async for chunk in _stream_mistral(
                                request_model, augmented_messages, api_key=settings.mistral_api_key, organisation_id=settings.mistral_organisation_id, temperature=temperature, stream=stream, timeout=settings.request_timeout
                            ):
                                yield chunk
                        elif model.provider == "gemini":
                            async for chunk in _stream_gemini(
                                request_model, augmented_messages, api_key=settings.gemini_api_key, temperature=temperature, stream=stream, timeout=settings.request_timeout
                            ):
                                yield chunk
                        elif model.provider == "gpt-oss":
                            if not settings.gpt_oss_api_key:
                                raise api_error("GPT-OSS support is not configured", status_code=503, code="gpt_oss_unavailable")
                            async for chunk in _stream_gpt_oss(
                                model.id,
                                augmented_messages,
                                api_key=settings.gpt_oss_api_key,
                                base_url=settings.gpt_oss_base_url,
                                temperature=temperature,
                                stream=stream,
                                timeout=int(settings.request_timeout),
                            ):
                                yield chunk
                        elif model.provider == "anthropic":
                            if not settings.anthropic_api_key:
                                raise api_error("Anthropic Claude support is not configured", status_code=503, code="anthropic_unavailable")
                            async for chunk in _stream_anthropic(
                                request_model,
                                augmented_messages,
                                api_key=settings.anthropic_api_key,
                                temperature=temperature,
                                stream=stream,
                                timeout=settings.anthropic_timeout_ms / 1000.0,
                                max_tokens=settings.anthropic_max_tokens,
                            ):
                                yield chunk
                    except Exception as exc:
                        logger.error("Error during web search augmented chat streaming: %s", exc)
                        raise
                    # Successfully streamed augmented response, exit early
                    return
                elif job_status == "failed":
                    yield f"Crawl job {job.id} fehlgeschlagen: {updated_job.error or 'Unbekannter Fehler'}.\n"
                    return
                else:
                    # Provide more context when no relevant results are found
                    if updated_job and updated_job.pages_crawled > 0:
                        yield f"Crawl job {job.id} abgeschlossen. Es wurden {updated_job.pages_crawled} Seiten gecrawlt, aber keine Ergebnisse, die direkt auf Ihre Anfrage passen, wurden gefunden. Versuchen Sie, Ihre Suchanfrage zu präzisieren oder andere Keywords zu verwenden.\n"
                    else:
                        yield f"Crawl job {job.id} abgeschlossen, aber es wurden keine Seiten gecrawlt oder relevante Ergebnisse gefunden. Die angegebenen URLs waren möglicherweise nicht erreichbar oder enthielten keine durchsuchbaren Inhalte. Versuchen Sie, Ihre Anfrage zu überprüfen oder andere Links anzugeben.\n"
                    return
        except Exception as exc:
            logger.exception("Crawler tool failed: %s", exc)
            yield f"Entschuldigung, beim Starten des Crawl-Tools ist ein Fehler aufgetreten: {exc}.\n"
            return

    # Only yield initial response if crawler wasn't triggered
    yield initial_response


async def _stream_ollama(
    model: str,
    messages: List[dict[str, str]],
    *,
    temperature: Optional[float],
    stream: bool,
    timeout: float,
) -> AsyncGenerator[str, None]:
    settings = get_settings()
    # Map model aliases for Ollama
    target_model = OLLAMA_MODEL_ALIASES.get(model)
    if not target_model:
        target_model = model
    
    # Strip provider prefix for Ollama API (generic)
    target_model = strip_provider_prefix(target_model)

    url = str(httpx.URL(str(settings.ollama_base)).join("/api/chat"))
    payload: dict[str, object] = {
        "model": target_model,
        "messages": messages,
        "stream": stream,
    }
    if temperature is not None:
        payload["options"] = {"temperature": max(0.0, min(temperature, 2.0))}

    headers = {}
    if settings.ollama_bearer_auth_enabled and settings.ollama_bearer_token and settings.ollama_bearer_token.strip():
        headers["Authorization"] = f"Bearer {settings.ollama_bearer_token}"

    # Don't pass api_key to HttpClient - we handle Authorization header manually
    client = HttpClient(base_url=str(settings.ollama_base))
    if stream:
        try:
            async with client.stream(
                "POST",
                str(url),
                headers=headers,
                json=payload,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    # Read the response content for error handling
                    content = await response.aread()
                    try:
                        error_data = json.loads(content)
                        if response.status_code == 401 and error_data.get("error") == "unauthorized" and "signin_url" in error_data:
                            signin_url = error_data.get("signin_url", "")
                            if "-cloud" in model:
                                raise api_error(
                                    f"This cloud model ({model}) requires authentication with Ollama.com. "
                                    f"Please visit {signin_url} to sign in, then try again. "
                                    f"Alternatively, consider using a local Ollama model.",
                                    status_code=401,
                                    code="ollama_auth_required",
                                )
                    except json.JSONDecodeError:
                        pass

                    message, code = extract_http_error(
                        response,
                        default_message="Ollama returned an error",
                        default_code="ollama_error",
                    )
                    raise api_error(message, status_code=response.status_code, code=code)

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("done"):
                        break
                    message = data.get("message") or {}
                    content = _extract_ollama_text(message.get("content"))
                    if not content:
                        content = _extract_ollama_text(data.get("response"))
                    if content:
                        yield content
        except Exception as exc:
            logger.exception("Failed to reach Ollama backend: %s", exc)
            raise api_error(
                f"Failed to reach Ollama backend: {exc}",
                status_code=502,
                code="ollama_unreachable",
            ) from exc
    else:
        try:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            # Check if this is an authentication error for cloud models
            if isinstance(exc, httpx.HTTPStatusError) and hasattr(exc, 'response') and exc.response:
                try:
                    error_data = exc.response.json()
                    if error_data.get("error") == "unauthorized" and "signin_url" in error_data:
                        signin_url = error_data.get("signin_url", "")
                        if "-cloud" in model:
                            raise api_error(
                                f"This cloud model ({model}) requires authentication with Ollama.com. "
                                f"Please visit {signin_url} to sign in, then try again. "
                                f"Alternatively, consider using a local Ollama model.",
                                status_code=401,
                                code="ollama_auth_required",
                            ) from exc
                except (json.JSONDecodeError, KeyError):
                    pass  # Not the expected error format, continue with normal error handling

            message, code = extract_http_error(
                getattr(exc, "response", None),
                default_message="Ollama returned an error",
                default_code="ollama_error",
            )
            raise api_error(message, status_code=getattr(exc, "response", httpx.Response(500)).status_code, code=code) from exc

        data = response.json()
        if "message" in data and data["message"].get("content"):
            text = _extract_ollama_text(data["message"]["content"])
            if text:
                yield text
        elif data.get("response"):
            text = _extract_ollama_text(data.get("response"))
            if text:
                yield text


async def _stream_mistral(
    model: str,
    messages: List[dict[str, str]],
    *,
    api_key: Optional[str],
    organisation_id: Optional[str],
    temperature: Optional[float],
    stream: bool,
    timeout: float,
) -> AsyncGenerator[str, None]:
    target_model = MISTRAL_MODEL_ALIASES.get(model)
    if not target_model:
        target_model = strip_provider_prefix(model)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if organisation_id:
        headers["X-Organization"] = organisation_id

    body: dict[str, object] = {
        "model": target_model,
        "messages": messages,
    }
    if temperature is not None:
        body["temperature"] = max(0.0, min(temperature, 2.0))

    url = "https://api.mistral.ai/v1/chat/completions"
    client = HttpClient(base_url="https://api.mistral.ai")
    if stream:
        body["stream"] = True
        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line.split("data:", 1)[1].strip()
                    if payload in ( "", "[DONE]"):
                        if payload == "[DONE]":
                            break
                        continue
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices")
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        # Handle Magistral-style structured content (list with thinking/text blocks)
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text = block.get("text", "")
                                    if text:
                                        yield text
                        else:
                            yield content
        except Exception as exc:
            message, code = extract_http_error(
                getattr(exc, "response", None),
                default_message="Mistral API responded with an error",
                default_code="mistral_error",
            )
            raise api_error(message, status_code=getattr(exc, "response", httpx.Response(500)).status_code, code=code) from exc
    else:
        try:
            response = await client.post(
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            message, code = extract_http_error(
                getattr(exc, "response", None),
                default_message="Mistral API responded with an error",
                default_code="mistral_error",
            )
            raise api_error(message, status_code=getattr(exc, "response", httpx.Response(500)).status_code, code=code) from exc

        data = response.json()
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if content:
                # Handle Magistral-style structured content (list with thinking/text blocks)
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                    yield "".join(text_parts)
                else:
                    yield content


async def _stream_gemini(
    model: str,
    messages: List[dict[str, str]],
    *,
    api_key: Optional[str],
    temperature: Optional[float],
    stream: bool,
    timeout: float,
) -> AsyncGenerator[str, None]:
    from google.genai import types
    from .google_genai import create_client, text_contents

    target_model = GEMINI_MODEL_ALIASES.get(model) or strip_provider_prefix(model)
    system_instruction, contents = text_contents(messages)
    if not contents:
        raise api_error("Messages cannot be empty", status_code=422, code="missing_messages")

    config = types.GenerateContentConfig(
        temperature=temperature,
        system_instruction=system_instruction,
    )
    try:
        client = create_client(api_key)
        if stream:
            async for chunk in await client.aio.models.generate_content_stream(
                model=target_model, contents=contents, config=config
            ):
                if chunk.text:
                    yield chunk.text
        else:
            response = await client.aio.models.generate_content(
                model=target_model, contents=contents, config=config
            )
            if response.text:
                yield response.text
    except Exception as exc:
        logger.exception("Error during Gemini request: %s", exc)
        raise api_error(f"Failed to get response from Gemini: {exc}", status_code=502, code="gemini_error") from exc


async def _stream_gpt_oss(
    model: str,
    messages: List[dict[str, str]],
    *,
    api_key: Optional[str],
    base_url: Optional[AnyHttpUrl],
    temperature: Optional[float],
    stream: bool,
    timeout: float,
) -> AsyncGenerator[str, None]:
    if base_url:
        base = str(base_url)
        url = urljoin(base if base.endswith("/") else f"{base}/", "v1/chat/completions")
    else:
        url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    body: dict[str, object] = {
        "model": model,
        "messages": messages,
    }
    if temperature is not None:
        body["temperature"] = max(0.0, min(temperature, 2.0))

    client = HttpClient(base_url=base if base.endswith("/") else f"{base}/" if base_url else "https://api.openai.com/")
    if stream:
        body["stream"] = True
        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line.split("data:", 1)[1].strip()
                    if payload in ( "", "[DONE]"):
                                if payload == "[DONE]":
                                    break
                                continue
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices")
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
        except Exception as exc:
            message, code = extract_http_error(
                getattr(exc, "response", None),
                default_message="GPT-OSS API responded with an error",
                default_code="gpt_oss_error",
            )
            raise api_error(message, status_code=getattr(exc, "response", httpx.Response(500)).status_code, code=code) from exc
    else:
        try:
            response = await client.post(
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            message, code = extract_http_error(
                getattr(exc, "response", None),
                default_message="GPT-OSS API responded with an error",
                default_code="gpt_oss_error",
            )
            raise api_error(message, status_code=getattr(exc, "response", httpx.Response(500)).status_code, code=code) from exc

        data = response.json()
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if content:
                yield content


def _extract_ollama_text(content: Optional[object]) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        fragments: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text_value = item.get("text") or item.get("content")
                if text_value:
                    fragments.append(str(text_value))
            elif isinstance(item, str):
                fragments.append(item)
        return "".join(fragments)
    if isinstance(content, dict):
        text_value = content.get("text") or content.get("content")
        if isinstance(text_value, str):
            return text_value
    return str(content)


async def _stream_anthropic(
    model: str,
    messages: List[dict[str, str]],
    *,
    api_key: str,
    temperature: Optional[float],
    stream: bool,
    timeout: float,
    max_tokens: int = 8192,
) -> AsyncGenerator[str, None]:
    """Stream chat completions from Anthropic Claude API.

    Supports both streaming and non-streaming responses.
    Handles system messages by extracting them into the dedicated system parameter.
    """
    # Map model aliases to actual Anthropic model IDs
    target_model = ANTHROPIC_MODEL_ALIASES.get(model)
    if not target_model:
        # Try without prefix
        stripped = strip_provider_prefix(model)
        target_model = ANTHROPIC_MODEL_ALIASES.get(stripped, stripped)

    # Anthropic API requires system messages to be passed separately
    system_content: List[str] = []
    api_messages: List[dict[str, object]] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system_content.append(content)
        elif role == "assistant":
            api_messages.append({"role": "assistant", "content": content})
        else:
            # Treat everything else as user
            api_messages.append({"role": "user", "content": content})

    # Anthropic requires alternating user/assistant messages
    # Merge consecutive messages of the same role
    merged_messages: List[dict[str, object]] = []
    for msg in api_messages:
        if merged_messages and merged_messages[-1]["role"] == msg["role"]:
            # Merge with previous message
            prev_content = merged_messages[-1]["content"]
            merged_messages[-1]["content"] = f"{prev_content}\n\n{msg['content']}"
        else:
            merged_messages.append(msg)

    # Ensure first message is from user (Anthropic requirement)
    if merged_messages and merged_messages[0]["role"] != "user":
        merged_messages.insert(0, {"role": "user", "content": "Hello"})

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    body: dict[str, object] = {
        "model": target_model,
        "messages": merged_messages,
        "max_tokens": max_tokens,
    }

    if system_content:
        body["system"] = "\n\n".join(system_content)

    if temperature is not None:
        body["temperature"] = max(0.0, min(temperature, 1.0))  # Anthropic uses 0-1 range

    url = "https://api.anthropic.com/v1/messages"
    client = HttpClient(base_url="https://api.anthropic.com")

    if stream:
        body["stream"] = True
        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    content = await response.aread()
                    try:
                        error_data = json.loads(content)
                        error_msg = error_data.get("error", {}).get("message", "Anthropic API error")
                    except json.JSONDecodeError:
                        error_msg = f"Anthropic API returned status {response.status_code}"
                    raise api_error(error_msg, status_code=response.status_code, code="anthropic_error")

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line.split("data:", 1)[1].strip()
                    if not payload or payload == "[DONE]":
                        continue

                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    event_type = data.get("type")

                    # Handle content block delta events
                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield text

                    # Handle message stop
                    elif event_type == "message_stop":
                        break

                    # Handle errors
                    elif event_type == "error":
                        error = data.get("error", {})
                        raise api_error(
                            error.get("message", "Anthropic streaming error"),
                            status_code=500,
                            code="anthropic_stream_error",
                        )
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            logger.exception("Anthropic streaming failed: %s", exc)
            raise api_error(
                f"Failed to stream from Anthropic: {exc}",
                status_code=502,
                code="anthropic_unreachable",
            ) from exc
    else:
        try:
            response = await client.post(
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    error_data = exc.response.json()
                    error_msg = error_data.get("error", {}).get("message", "Anthropic API error")
                except (json.JSONDecodeError, AttributeError):
                    error_msg = f"Anthropic API returned status {exc.response.status_code}"
                raise api_error(error_msg, status_code=exc.response.status_code, code="anthropic_error") from exc
            raise api_error(
                f"Failed to reach Anthropic API: {exc}",
                status_code=502,
                code="anthropic_unreachable",
            ) from exc

        data = response.json()
        content_blocks = data.get("content", [])
        for block in content_blocks:
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    yield text


async def _stream_openai_compatible(
    model: str,
    messages: List[dict[str, str]],
    *,
    api_key: str,
    base_url: str,
    extra_headers: dict[str, str],
    temperature: Optional[float],
    stream: bool,
    timeout: float,
    provider: str,
) -> AsyncGenerator[str, None]:
    """Stream chat completions from OpenAI-compatible APIs.

    Supports: Groq, Cerebras, Together, Fireworks, OpenRouter
    """
    # Strip provider prefix from model name
    target_model = strip_provider_prefix(model)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **extra_headers,
    }

    body: dict[str, object] = {
        "model": target_model,
        "messages": messages,
    }
    if temperature is not None:
        body["temperature"] = max(0.0, min(temperature, 2.0))

    url = f"{base_url.rstrip('/')}/chat/completions"
    client = HttpClient(base_url=base_url)

    if stream:
        body["stream"] = True
        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    content = await response.aread()
                    try:
                        error_data = json.loads(content)
                        error_msg = error_data.get("error", {}).get("message", f"{provider} API error")
                    except json.JSONDecodeError:
                        error_msg = f"{provider} API returned status {response.status_code}"
                    raise api_error(error_msg, status_code=response.status_code, code=f"{provider}_error")

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line.split("data:", 1)[1].strip()
                    if payload in ("", "[DONE]"):
                        if payload == "[DONE]":
                            break
                        continue

                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    choices = data.get("choices")
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            logger.exception("%s streaming failed: %s", provider.title(), exc)
            raise api_error(
                f"Failed to stream from {provider}: {exc}",
                status_code=502,
                code=f"{provider}_unreachable",
            ) from exc
    else:
        try:
            response = await client.post(
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    error_data = exc.response.json()
                    error_msg = error_data.get("error", {}).get("message", f"{provider} API error")
                except (json.JSONDecodeError, AttributeError):
                    error_msg = f"{provider} API returned status {exc.response.status_code}"
                raise api_error(error_msg, status_code=exc.response.status_code, code=f"{provider}_error") from exc
            raise api_error(
                f"Failed to reach {provider} API: {exc}",
                status_code=502,
                code=f"{provider}_unreachable",
            ) from exc

        data = response.json()
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if content:
                yield content


async def _stream_cohere(
    model: str,
    messages: List[dict[str, str]],
    *,
    api_key: str,
    temperature: Optional[float],
    stream: bool,
    timeout: float,
) -> AsyncGenerator[str, None]:
    """Stream chat completions from Cohere API.

    Cohere has its own API format different from OpenAI.
    """
    # Strip provider prefix from model name
    target_model = strip_provider_prefix(model)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Convert messages to Cohere format
    chat_history = []
    user_message = ""
    preamble = ""

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            preamble = content
        elif role == "user":
            user_message = content
        elif role == "assistant":
            # Add to chat history
            if user_message:
                chat_history.append({"role": "USER", "message": user_message})
            chat_history.append({"role": "CHATBOT", "message": content})
            user_message = ""

    # Use the last user message as the query
    if not user_message:
        user_message = "Hello"

    body: dict[str, object] = {
        "model": target_model,
        "message": user_message,
    }

    if chat_history:
        body["chat_history"] = chat_history
    if preamble:
        body["preamble"] = preamble
    if temperature is not None:
        body["temperature"] = max(0.0, min(temperature, 2.0))

    url = "https://api.cohere.ai/v1/chat"
    client = HttpClient(base_url="https://api.cohere.ai")

    if stream:
        body["stream"] = True
        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    content = await response.aread()
                    try:
                        error_data = json.loads(content)
                        error_msg = error_data.get("message", "Cohere API error")
                    except json.JSONDecodeError:
                        error_msg = f"Cohere API returned status {response.status_code}"
                    raise api_error(error_msg, status_code=response.status_code, code="cohere_error")

                async for line in response.aiter_lines():
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Cohere streaming format
                    event_type = data.get("event_type")
                    if event_type == "text-generation":
                        text = data.get("text", "")
                        if text:
                            yield text
                    elif event_type == "stream-end":
                        break
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            logger.exception("Cohere streaming failed: %s", exc)
            raise api_error(
                f"Failed to stream from Cohere: {exc}",
                status_code=502,
                code="cohere_unreachable",
            ) from exc
    else:
        try:
            response = await client.post(
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    error_data = exc.response.json()
                    error_msg = error_data.get("message", "Cohere API error")
                except (json.JSONDecodeError, AttributeError):
                    error_msg = f"Cohere API returned status {exc.response.status_code}"
                raise api_error(error_msg, status_code=exc.response.status_code, code="cohere_error") from exc
            raise api_error(
                f"Failed to reach Cohere API: {exc}",
                status_code=502,
                code="cohere_unreachable",
            ) from exc

        data = response.json()
        text = data.get("text", "")
        if text:
            yield text


async def _stream_cloudflare(
    model: str,
    messages: List[dict[str, str]],
    *,
    account_id: str,
    api_token: str,
    temperature: Optional[float],
    stream: bool,
    timeout: float,
) -> AsyncGenerator[str, None]:
    """Stream chat completions from Cloudflare Workers AI.

    Cloudflare Workers AI has its own API format.
    """
    # Strip provider prefix from model name (e.g., "cloudflare/@cf/meta/llama..." -> "@cf/meta/llama...")
    target_model = strip_provider_prefix(model)

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    body: dict[str, object] = {
        "messages": messages,
    }
    if temperature is not None:
        body["temperature"] = max(0.0, min(temperature, 2.0))

    # Cloudflare Workers AI URL format
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{target_model}"
    client = HttpClient(base_url="https://api.cloudflare.com")

    if stream:
        body["stream"] = True
        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    content = await response.aread()
                    try:
                        error_data = json.loads(content)
                        error_msg = error_data.get("errors", [{}])[0].get("message", "Cloudflare API error")
                    except (json.JSONDecodeError, IndexError):
                        error_msg = f"Cloudflare API returned status {response.status_code}"
                    raise api_error(error_msg, status_code=response.status_code, code="cloudflare_error")

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line.split("data:", 1)[1].strip()
                    if payload in ("", "[DONE]"):
                        if payload == "[DONE]":
                            break
                        continue

                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    # Cloudflare streaming format
                    response_text = data.get("response", "")
                    if response_text:
                        yield response_text
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            logger.exception("Cloudflare Workers AI streaming failed: %s", exc)
            raise api_error(
                f"Failed to stream from Cloudflare Workers AI: {exc}",
                status_code=502,
                code="cloudflare_unreachable",
            ) from exc
    else:
        try:
            response = await client.post(
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    error_data = exc.response.json()
                    error_msg = error_data.get("errors", [{}])[0].get("message", "Cloudflare API error")
                except (json.JSONDecodeError, IndexError, AttributeError):
                    error_msg = f"Cloudflare API returned status {exc.response.status_code}"
                raise api_error(error_msg, status_code=exc.response.status_code, code="cloudflare_error") from exc
            raise api_error(
                f"Failed to reach Cloudflare Workers AI: {exc}",
                status_code=502,
                code="cloudflare_unreachable",
            ) from exc

        data = response.json()
        # Cloudflare wraps response in result object
        result = data.get("result", data)
        text = result.get("response", "")
        if text:
            yield text