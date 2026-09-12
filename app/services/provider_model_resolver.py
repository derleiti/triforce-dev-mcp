"""Canonical provider-model selection backed by the live ModelRegistry.

Consumer services must not maintain their own stale model inventories.  This
module expresses only preference order; availability and existence come from
the shared registry/availability services.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.services.model_availability import availability_service
from app.services.model_registry import registry

PREFERRED_CHAT_MODELS: dict[str, tuple[str, ...]] = {
    "gemini": (
        "gemini/gemini-3.8-flash",
        "gemini/gemini-3.7-flash",
        "gemini/gemini-3.6-flash",
        "gemini/gemini-2.5-flash",
    ),
    "mistral": (
        "mistral/mistral-medium-latest",
        "mistral/mistral-small-latest",
        "mistral/magistral-medium-latest",
    ),
    "groq": (
        "groq/qwen/qwen3.8-27b",
        "groq/openai/gpt-oss-120b",
        "groq/qwen/qwen3.6-27b",
        "groq/openai/gpt-oss-20b",
    ),
    "cerebras": (
        "cerebras/qwen-3.8-27b",
        "cerebras/gpt-oss-120b",
        "cerebras/gemma-4-31b",
    ),
    "openrouter": (
        "openrouter/google/gemini-3.8-flash:free",
        "openrouter/openai/gpt-oss-120b:free",
        "openrouter/nex-agi/nex-n2.5-pro:free",
    ),
}


def _usable(model) -> bool:
    caps = set(model.capabilities or ())
    return bool(caps & {"chat", "reasoning", "code"}) and availability_service.is_available(model.id)


async def resolve_provider_model(
    provider: str,
    *,
    preferred: Iterable[str] | None = None,
    force_refresh: bool = False,
) -> str:
    """Return an available canonical model id for *provider* from ModelRegistry."""
    provider = str(provider or "").strip().lower()
    if not provider:
        raise ValueError("provider is required")
    models = await registry.list_models(force_refresh=force_refresh)
    provider_models = [m for m in models if m.provider == provider and _usable(m)]
    if not provider_models:
        raise RuntimeError(f"no available chat/reasoning model discovered for provider {provider}")
    by_id = {m.id: m for m in provider_models}
    for model_id in tuple(preferred or PREFERRED_CHAT_MODELS.get(provider, ())):
        if model_id in by_id:
            return model_id
    provider_models.sort(
        key=lambda m: (
            0 if "reasoning" in set(m.capabilities or ()) else 1,
            0 if "chat" in set(m.capabilities or ()) else 1,
            m.id.lower(),
        )
    )
    return provider_models[0].id


def provider_api_model(model_id: str, provider: str) -> str:
    """Strip exactly one canonical provider prefix for direct provider HTTP APIs."""
    prefix = f"{provider.strip().lower()}/"
    return model_id.removeprefix(prefix)
