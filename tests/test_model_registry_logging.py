import pytest
import httpx

from app.services.model_registry import redact_url, safe_http_error


def test_redact_url_masks_sensitive_query_params():
    url = "https://generativelanguage.googleapis.com/v1beta/models?key=AIza_TEST_SECRET&foo=bar&token=abc123"

    redacted = redact_url(url)

    assert "AIza_TEST_SECRET" not in redacted
    assert "abc123" not in redacted
    assert "key=%5BREDACTED%5D" in redacted or "key=[REDACTED]" in redacted
    assert "token=%5BREDACTED%5D" in redacted or "token=[REDACTED]" in redacted
    assert "foo=bar" in redacted


def test_safe_http_error_does_not_leak_query_api_key():
    request = httpx.Request(
        "GET",
        "https://generativelanguage.googleapis.com/v1beta/models?key=AIza_TEST_SECRET",
    )
    response = httpx.Response(400, request=request)
    exc = httpx.HTTPStatusError("boom", request=request, response=response)

    formatted = safe_http_error(exc)

    assert "AIza_TEST_SECRET" not in formatted
    assert "key=" in formatted
    assert "%5BREDACTED%5D" in formatted or "[REDACTED]" in formatted
    assert "status=400" in formatted


def test_groq_orpheus_is_audio_not_chat():
    from app.services.model_registry import detect_capabilities
    capabilities, _, _ = detect_capabilities("canopylabs/orpheus-arabic-saudi")
    assert "audio" in capabilities
    assert "chat" not in capabilities


def test_specialized_models_do_not_default_to_chat():
    from app.services.model_registry import detect_capabilities
    cases = {
        "whisper-large-v3": "audio",
        "text-embedding-3-small": "embedding",
        "omni-moderation-latest": "moderation",
        "sora-2": "video_gen",
        "gpt-image-1": "image_gen",
    }
    for model_id, expected in cases.items():
        capabilities, _, _ = detect_capabilities(model_id)
        assert expected in capabilities
        assert "chat" not in capabilities


def test_static_catalog_is_not_injected():
    from app.services.model_registry import ModelRegistry
    assert list(ModelRegistry()._discover_static_hosted()) == []



def test_openai_compatible_provider_prefixes_are_stripped():
    from app.utils.model_helpers import strip_provider_prefix

    cases = {
        "openai/gpt-4.1-mini": "gpt-4.1-mini",
        "kimi/moonshot-v1-8k": "moonshot-v1-8k",
        "huggingface/allenai/Olmo-3-7B-Instruct:fastest": "allenai/Olmo-3-7B-Instruct:fastest",
        "github/openai/gpt-4.1-mini": "openai/gpt-4.1-mini",
        "nvidia/z-ai/glm-5.3": "z-ai/glm-5.3",
    }
    for model_id, expected in cases.items():
        assert strip_provider_prefix(model_id) == expected

@pytest.mark.asyncio
async def test_gemini_discovery_filters_provider_rejected_legacy_model(monkeypatch):
    from types import SimpleNamespace
    from app.services import model_registry

    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"models": [
                {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-3.1-pro-preview", "supportedGenerationMethods": ["generateContent"]},
            ]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(model_registry.httpx, "AsyncClient", FakeClient)
    registry = model_registry.ModelRegistry()
    registry._settings = SimpleNamespace(gemini_api_key="configured")
    models = await registry._discover_gemini()
    ids = {m.id for m in models}
    assert "gemini/gemini-2.5-pro" not in ids
    assert "gemini/gemini-3.1-pro-preview" in ids
