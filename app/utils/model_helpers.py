"""Model utility functions for consistent model ID handling across services."""

from __future__ import annotations

# All known provider prefixes
PROVIDER_PREFIXES = (
    "ollama/", "gemini/", "mistral/", "anthropic/", "openai/", "groq/",
    "cerebras/", "nvidia/", "cohere/", "kimi/", "huggingface/", "github/",
    "openrouter/", "together/", "fireworks/", "cloudflare/", "gpt-oss/",
)

# Default fallback model
DEFAULT_FALLBACK_MODEL = "gpt-oss:20b-cloud"


def strip_provider_prefix(model_id: str) -> str:
    """Remove provider prefix from model ID for API calls.
    
    Examples:
        ollama/kimi-k2:1t-cloud -> kimi-k2:1t-cloud
        gemini/gemini-2.5-flash -> gemini-2.5-flash
        mistral/mistral-medium-latest -> mistral-medium-latest
        cloudflare/@cf/meta/llama -> @cf/meta/llama
    """
    for prefix in PROVIDER_PREFIXES:
        if model_id.startswith(prefix):
            return model_id[len(prefix):]
    return model_id


def get_provider_from_model_id(model_id: str) -> str | None:
    """Extract provider name from prefixed model ID.
    
    Examples:
        ollama/kimi-k2:1t-cloud -> ollama
        gemini/gemini-2.5-flash -> gemini
    """
    for prefix in PROVIDER_PREFIXES:
        if model_id.startswith(prefix):
            return prefix.rstrip("/")
    return None
