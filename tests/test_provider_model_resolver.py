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


def test_nvidia_gpt_oss_defaults_to_low_reasoning_effort(monkeypatch):
    from app.services import provider_chat

    monkeypatch.setenv("NVIDIA_API_KEY", "configured")
    monkeypatch.delenv("NVIDIA_REASONING_EFFORT", raising=False)
    seen = {}

    async def fake_post(provider, url, headers, payload, timeout):
        seen.update(provider=provider, url=url, payload=payload)
        return {
            "model": "openai/gpt-oss-20b",
            "choices": [{"message": {"content": "nvidia-ok", "tool_calls": []}}],
            "usage": {"total_tokens": 4},
        }

    monkeypatch.setattr(provider_chat, "_post", fake_post)
    result = asyncio.run(provider_chat.chat_completion(
        SimpleNamespace(provider="nvidia"),
        "nvidia/openai/gpt-oss-20b",
        [{"role": "user", "content": "hi"}],
        max_tokens=16,
    ))

    assert seen["provider"] == "nvidia"
    assert seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert seen["payload"]["model"] == "openai/gpt-oss-20b"
    assert seen["payload"]["reasoning_effort"] == "low"
    assert result["content"] == "nvidia-ok"


def test_cloudflare_caps_requested_output_tokens(monkeypatch):
    from app.services import provider_chat

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "configured")
    monkeypatch.delenv("CLOUDFLARE_MAX_TOKENS", raising=False)
    seen = {}

    async def fake_post(provider, url, headers, payload, timeout):
        seen.update(provider=provider, url=url, payload=payload)
        return {
            "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            "choices": [{"message": {"content": "cloudflare-ok", "tool_calls": []}}],
            "usage": {"total_tokens": 4},
        }

    monkeypatch.setattr(provider_chat, "_post", fake_post)
    result = asyncio.run(provider_chat.chat_completion(
        SimpleNamespace(provider="cloudflare"),
        "cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        [{"role": "user", "content": "hi"}],
        max_tokens=16384,
    ))

    assert seen["provider"] == "cloudflare"
    assert seen["payload"]["max_tokens"] == 8192
    assert result["content"] == "cloudflare-ok"
