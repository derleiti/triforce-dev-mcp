import asyncio
from types import SimpleNamespace

import app.services.provider_model_resolver as resolver


def _model(mid, provider, caps):
    return SimpleNamespace(id=mid, provider=provider, capabilities=caps)


def test_resolver_prefers_current_registry_model(monkeypatch):
    models = [
        _model("groq/openai/gpt-oss-120b", "groq", ["chat", "reasoning"]),
        _model("groq/qwen/qwen3.8-27b", "groq", ["chat", "reasoning"]),
    ]
    async def fake_list_models(force_refresh=False):
        return models
    monkeypatch.setattr(resolver.registry, "list_models", fake_list_models)
    monkeypatch.setattr(resolver.availability_service, "is_available", lambda _m: True)
    assert asyncio.run(resolver.resolve_provider_model("groq")) == "groq/qwen/qwen3.8-27b"


def test_resolver_skips_unavailable_preference(monkeypatch):
    models = [
        _model("groq/qwen/qwen3.8-27b", "groq", ["chat"]),
        _model("groq/openai/gpt-oss-120b", "groq", ["chat", "reasoning"]),
    ]
    async def fake_list_models(force_refresh=False):
        return models
    monkeypatch.setattr(resolver.registry, "list_models", fake_list_models)
    monkeypatch.setattr(
        resolver.availability_service,
        "is_available",
        lambda mid: mid != "groq/qwen/qwen3.8-27b",
    )
    assert asyncio.run(resolver.resolve_provider_model("groq")) == "groq/openai/gpt-oss-120b"


def test_provider_api_model_strips_one_prefix():
    assert resolver.provider_api_model("groq/qwen/qwen3.8-27b", "groq") == "qwen/qwen3.8-27b"
