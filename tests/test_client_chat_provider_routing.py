from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routes import client_chat
from app.services import provider_chat
from app.services.provider_chat import normalize_tools


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_id",
    [
        "groq/llama-3.3-70b-versatile",
        "gemini/gemini-2.5-flash",
        "cerebras/llama3.1-8b",
        "nvidia/meta/llama-3.3-70b-instruct",
        "cloudflare/@cf/meta/llama-3.1-8b-instruct",
        "anthropic/claude-sonnet-4-6",
        "openai/gpt-5.4",
        "mistral/mistral-code-latest",
    ],
)
async def test_registered_chat_models_share_one_provider_router(monkeypatch, model_id):
    seen = {}

    async def fake_get_model(requested):
        seen["registry_model"] = requested
        return SimpleNamespace(
            provider=requested.split("/", 1)[0],
            capabilities={"chat"},
        )

    async def fake_chat_completion(
        model_info, requested, messages, *,
        temperature, max_tokens, tools, tool_choice,
    ):
        seen["adapter_model"] = requested
        seen["messages"] = list(messages)
        seen["tools"] = tools
        return {
            "content": "provider-ok",
            "tool_calls": [{"function": {"name": "health", "arguments": "{}"}}],
            "usage_total": 7,
            "model_used": requested,
        }

    monkeypatch.setattr(client_chat.registry, "get_model", fake_get_model)
    monkeypatch.setattr(client_chat, "chat_completion", fake_chat_completion)

    tool = {"name": "health", "inputSchema": {"type": "object"}}
    result = await client_chat.call_registered_model(
        model_id,
        [{"role": "user", "content": "ping"}],
        temperature=0.1,
        max_tokens=8,
        tools=[tool],
    )

    assert seen["registry_model"] == model_id
    assert seen["adapter_model"] == model_id
    assert seen["tools"] == [tool]
    assert result["choices"][0]["message"]["content"] == "provider-ok"
    assert result["choices"][0]["message"]["tool_calls"]


@pytest.mark.asyncio
async def test_openrouter_http_200_embedded_error_is_not_normalized_as_empty(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "configured")

    class FakeResponse:
        status_code = 200
        def json(self):
            return {
                "id": "gen-test",
                "error": {
                    "code": 429,
                    "message": "Provider rate limited this request",
                    "metadata": {"provider_name": "NVIDIA"},
                },
            }
        @property
        def text(self):
            return "embedded provider error"

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return False
        async def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(provider_chat.httpx, "AsyncClient", FakeClient)
    with pytest.raises(HTTPException) as exc:
        await provider_chat.chat_completion(
            SimpleNamespace(provider="openrouter"),
            "openrouter/nvidia/nemotron-test",
            [{"role": "user", "content": "hi"}],
            max_tokens=16,
        )
    assert exc.value.status_code == 429
    assert exc.value.detail["error"] == "provider_embedded_error"
    assert exc.value.detail["provider_code"] == 429
    assert exc.value.detail["provider_message"] == "Provider rate limited this request"
    assert exc.value.detail["provider_metadata"] == {"provider_name": "NVIDIA"}
    assert exc.value.detail["retryable"] is True
    assert exc.value.detail["retry_after"] == 30


@pytest.mark.asyncio
async def test_openrouter_embedded_overload_gets_short_retry_hint(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "configured")

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"error": {"code": 502, "message": "Upstream error from Nvidia: Service temporarily overloaded"}}
        @property
        def text(self): return "embedded overload"

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return False
        async def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(provider_chat.httpx, "AsyncClient", FakeClient)
    with pytest.raises(HTTPException) as exc:
        await provider_chat.chat_completion(
            SimpleNamespace(provider="openrouter"),
            "openrouter/nvidia/nemotron-test",
            [{"role": "user", "content": "hi"}], max_tokens=16,
        )
    assert exc.value.status_code == 502
    assert exc.value.detail["retry_after"] == 5
    assert "temporarily overloaded" in exc.value.detail["provider_message"]


@pytest.mark.asyncio
async def test_registered_chat_turns_empty_provider_response_into_retryable_502(monkeypatch):
    async def fake_get_model(requested):
        return SimpleNamespace(provider="openrouter", capabilities={"chat"})

    async def fake_chat_completion(*args, **kwargs):
        return {
            "content": "",
            "tool_calls": [],
            "usage_total": 17,
            "model_used": "nvidia/nemotron-test",
            "tool_transport": "native",
            "provider_diagnostics": {
                "finish_reason": "stop",
                "reasoning_chars": 321,
                "content_chars": 0,
                "tool_call_count": 0,
            },
        }

    monkeypatch.setattr(client_chat.registry, "get_model", fake_get_model)
    monkeypatch.setattr(client_chat, "chat_completion", fake_chat_completion)
    with pytest.raises(HTTPException) as exc:
        await client_chat.call_registered_model(
            "openrouter/nvidia/nemotron-test",
            [{"role": "user", "content": "ping"}],
            tools=[{"name": "health", "inputSchema": {"type": "object"}}],
        )
    assert exc.value.status_code == 502
    assert exc.value.detail["error"] == "empty_provider_response"
    assert exc.value.detail["retryable"] is True
    assert exc.value.detail["provider_diagnostics"]["finish_reason"] == "stop"
    assert exc.value.detail["provider_diagnostics"]["reasoning_chars"] == 321


@pytest.mark.asyncio
async def test_registered_chat_rejects_non_chat_model(monkeypatch):
    async def fake_get_model(_requested):
        return SimpleNamespace(capabilities={"embedding"})

    monkeypatch.setattr(client_chat.registry, "get_model", fake_get_model)

    with pytest.raises(HTTPException) as exc:
        await client_chat.call_registered_model(
            "provider/embed-only",
            [{"role": "user", "content": "ping"}],
        )

    assert exc.value.status_code == 404


def test_default_ollama_model_is_current_catalog_entry():
    assert client_chat.LOCAL_FALLBACK_MODEL == "ollama/gemma4:cloud"
    assert client_chat.LOCAL_FALLBACK_MODEL in client_chat.FREE_MODELS_OLLAMA


def test_default_route_uses_current_ollama_model():
    assert client_chat.get_default_model(client_chat.UserTier.REGISTERED) == client_chat.LOCAL_FALLBACK_MODEL
    assert client_chat.get_default_ollama_model() == "gemma4:cloud"


def test_model_catalog_exposes_only_chat_capable_registry_entries():
    models = [
        SimpleNamespace(id="ollama/chat", provider="ollama", capabilities={"chat"}),
        SimpleNamespace(id="ollama/embed", provider="ollama", capabilities={"embedding"}),
        SimpleNamespace(id="ollama/image", provider="ollama", capabilities={"image"}),
    ]
    assert client_chat.chat_capable_model_ids(models) == ["ollama/chat"]


def test_registered_free_policy(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "configured")
    monkeypatch.setenv("GROQ_API_KEY", "configured")
    monkeypatch.setenv("NVIDIA_API_KEY", "configured")
    monkeypatch.setenv("OPENROUTER_API_KEY", "configured")
    assert client_chat.is_registered_free_model("ollama/qwen3.5:cloud")
    assert client_chat.is_registered_free_model("mistral/mistral-large-latest")
    assert client_chat.is_registered_free_model("mistral/mistral-code-latest")
    assert client_chat.is_registered_free_model("groq/llama-3.3-70b-versatile")
    assert not client_chat.is_registered_free_model("nvidia/meta/llama-3.3-70b-instruct")  # no public entitlement
    assert client_chat.is_registered_free_model("openrouter/vendor/model:free")
    assert client_chat.is_registered_free_model("openrouter/openrouter/free")
    assert not client_chat.is_registered_free_model("openrouter/vendor/paid")


def test_mistral_free_policy_requires_configured_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert not client_chat.is_registered_free_model("mistral/mistral-large-latest")
    assert not client_chat.is_guest_free_model("mistral/mistral-large-latest")

    monkeypatch.setenv("MISTRAL_API_KEY", "configured")
    assert client_chat.is_registered_free_model("mistral/mistral-large-latest")
    assert client_chat.is_guest_free_model("mistral/mistral-large-latest")


def test_free_ollama_catalog_matches_current_cloud_generation_set():
    assert "ollama/glm-5.3-flash:cloud" in client_chat.FREE_MODELS_OLLAMA
    assert "ollama/qwen3.5:cloud" in client_chat.FREE_MODELS_OLLAMA
    assert "ollama/qwen3.5:397b-cloud" in client_chat.FREE_MODELS_OLLAMA
    assert "ollama/minimax-m3:cloud" in client_chat.FREE_MODELS_OLLAMA
    assert "ollama/glm-4.7:cloud" not in client_chat.FREE_MODELS_OLLAMA
    assert "ollama/qwen3-coder-next:cloud" not in client_chat.FREE_MODELS_OLLAMA
    assert "ollama/minimax-m2.5:cloud" not in client_chat.FREE_MODELS_OLLAMA
    assert "ollama/llama3.2:latest" not in client_chat.FREE_MODELS_OLLAMA


def test_free_ollama_policy_rejects_retired_or_arbitrary_tags():
    assert client_chat.is_guest_free_model("ollama/qwen3.5:cloud")
    assert client_chat.is_registered_free_model("ollama/qwen3.5:cloud")
    assert not client_chat.is_guest_free_model("ollama/retired-model:cloud")
    assert not client_chat.is_registered_free_model("ollama/retired-model:cloud")


def test_mcp_schema_is_normalized_for_provider_tool_calling():
    assert normalize_tools([{
        "name": "code_read",
        "description": "Read a file",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }]) == [{
        "type": "function",
        "function": {
            "name": "code_read",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }]


@pytest.mark.asyncio
async def test_cohere_v2_chat_normalizes_text_and_tool_calls(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "configured")
    seen = {}

    async def fake_post(provider, url, headers, payload, timeout):
        seen.update(provider=provider, url=url, payload=payload)
        return {
            "message": {
                "content": [{"type": "text", "text": "cohere-ok"}],
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "health", "arguments": "{}"},
                }],
            },
            "usage": {"tokens": {"input_tokens": 2, "output_tokens": 3}},
        }

    monkeypatch.setattr(provider_chat, "_post", fake_post)
    result = await provider_chat.chat_completion(
        SimpleNamespace(provider="cohere"),
        "cohere/command-a-plus-05-2026",
        [{"role": "user", "content": "use health"}],
        max_tokens=16,
        tools=[{"name": "health", "inputSchema": {"type": "object"}}],
    )

    assert seen["provider"] == "cohere"
    assert seen["url"] == "https://api.cohere.com/v2/chat"
    assert seen["payload"]["tools"][0]["function"]["name"] == "health"
    assert result["content"] == "cohere-ok"
    assert result["tool_calls"][0]["function"]["name"] == "health"
    assert result["usage_total"] == 5


@pytest.mark.asyncio
async def test_nvidia_uses_openai_compatible_chat_endpoint(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "configured")
    seen = {}

    async def fake_post(provider, url, headers, payload, timeout):
        seen.update(provider=provider, url=url, headers=headers, payload=payload, timeout=timeout)
        return {
            "model": "meta/llama-3.3-70b-instruct",
            "choices": [{"message": {"content": "nvidia-ok", "tool_calls": []}}],
            "usage": {"total_tokens": 4},
        }

    monkeypatch.setattr(provider_chat, "_post", fake_post)
    result = await provider_chat.chat_completion(
        SimpleNamespace(provider="nvidia"),
        "nvidia/meta/llama-3.3-70b-instruct",
        [{"role": "user", "content": "hi"}],
        max_tokens=16,
    )

    assert seen["provider"] == "nvidia"
    assert seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert seen["payload"]["model"] == "meta/llama-3.3-70b-instruct"
    assert "reasoning_effort" not in seen["payload"]
    assert result["content"] == "nvidia-ok"


@pytest.mark.asyncio
async def test_nvidia_gpt_oss_uses_low_reasoning_effort_by_default(monkeypatch):
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
    result = await provider_chat.chat_completion(
        SimpleNamespace(provider="nvidia"),
        "nvidia/openai/gpt-oss-20b",
        [{"role": "user", "content": "hi"}],
        max_tokens=16,
    )

    assert seen["provider"] == "nvidia"
    assert seen["payload"]["model"] == "openai/gpt-oss-20b"
    assert seen["payload"]["reasoning_effort"] == "low"
    assert result["content"] == "nvidia-ok"


def test_unconfigured_provider_is_hidden_from_pro_catalog(monkeypatch):
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    models = [
        SimpleNamespace(id="cohere/command-a", provider="cohere", capabilities={"chat"}),
        SimpleNamespace(id="ollama/llama3.2:latest", provider="ollama", capabilities={"chat"}),
    ]
    assert client_chat.chat_capable_model_ids(models) == ["ollama/llama3.2:latest"]


def test_anthropic_sampling_policy_matches_current_model_contract():
    assert provider_chat._anthropic_supports_temperature("claude-sonnet-4-6")
    assert not provider_chat._anthropic_supports_temperature("claude-sonnet-5")
    assert not provider_chat._anthropic_supports_temperature("claude-opus-4-7")
    assert not provider_chat._anthropic_supports_temperature("claude-opus-4-8")


def test_sampling_parameter_rejection_is_detected():
    request = provider_chat.httpx.Request("POST", "https://api.example.test/v1/messages")
    response = provider_chat.httpx.Response(
        400,
        request=request,
        json={"error": {"message": "temperature is deprecated for this model."}},
    )
    assert provider_chat._sampling_parameters_rejected(response)


@pytest.mark.asyncio
async def test_anthropic_sonnet5_omits_deprecated_temperature(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured")
    seen = {}

    async def fake_post(provider, url, headers, payload, timeout):
        seen.update(provider=provider, payload=payload)
        return {
            "model": "claude-sonnet-5",
            "content": [{"type": "text", "text": "anthropic-ok"}],
            "usage": {"input_tokens": 2, "output_tokens": 2},
        }

    monkeypatch.setattr(provider_chat, "_post", fake_post)
    result = await provider_chat.chat_completion(
        SimpleNamespace(provider="anthropic"),
        "anthropic/claude-sonnet-5",
        [{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=16,
    )

    assert seen["provider"] == "anthropic"
    assert "temperature" not in seen["payload"]
    assert result["content"] == "anthropic-ok"

@pytest.mark.asyncio
async def test_gemini_model_not_found_does_not_fallback_to_openrouter(monkeypatch):
    monkeypatch.setenv("GOOGLE_AI_STUDIO_KEY", "configured")
    monkeypatch.setenv("OPENROUTER_API_KEY", "configured")

    class FakeResponse:
        status_code = 404
        def json(self):
            return {"error": {"message": "model is no longer available"}}
        @property
        def text(self):
            return "model is no longer available"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def post(self, *args, **kwargs):
            return FakeResponse()

    async def unexpected_openrouter(*args, **kwargs):
        raise AssertionError("Gemini model 404 must not be rerouted to OpenRouter")

    monkeypatch.setattr(provider_chat.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(provider_chat, "_compatible", unexpected_openrouter)

    with pytest.raises(provider_chat.HTTPException) as exc:
        await provider_chat.chat_completion(
            SimpleNamespace(provider="gemini"),
            "gemini/gemini-2.5-pro",
            [{"role": "user", "content": "hello"}],
            max_tokens=16,
        )
    assert exc.value.status_code == 404


DATA_URL = "data:image/png;base64,QUJD"
MULTIMODAL_MESSAGES = [
    {"role": "system", "content": "system"},
    {"role": "user", "content": [
        {"type": "text", "text": "inspect"},
        {"type": "image_url", "image_url": {"url": DATA_URL}},
    ]},
]


def test_chat_message_accepts_multimodal_content():
    message = client_chat.ChatMessage(role="user", content=MULTIMODAL_MESSAGES[1]["content"])
    assert isinstance(message.content, list)
    assert client_chat._messages_have_images(MULTIMODAL_MESSAGES)


def test_chat_content_rejects_remote_image_url():
    with pytest.raises(HTTPException) as exc:
        client_chat._validate_chat_content([
            {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
        ])
    assert exc.value.status_code == 400


def test_provider_multimodal_transforms_preserve_image_bytes():
    openai = provider_chat._openai_response_messages(MULTIMODAL_MESSAGES)
    assert openai[1]["content"][0] == {"type": "input_text", "text": "inspect"}
    assert openai[1]["content"][1] == {"type": "input_image", "image_url": DATA_URL}

    system, anthropic = provider_chat._anthropic_messages(MULTIMODAL_MESSAGES)
    assert system == "system"
    assert anthropic[0]["content"][1]["source"] == {
        "type": "base64", "media_type": "image/png", "data": "QUJD",
    }

    system, gemini = provider_chat._gemini_contents(MULTIMODAL_MESSAGES)
    assert system == "system"
    assert gemini[0]["parts"][1]["inlineData"] == {
        "mimeType": "image/png", "data": "QUJD",
    }

    ollama = client_chat._ollama_multimodal_messages(MULTIMODAL_MESSAGES)
    assert ollama[1]["content"] == "inspect"
    assert ollama[1]["images"] == ["QUJD"]


@pytest.mark.asyncio
async def test_non_vision_registered_model_is_rejected_before_provider_call(monkeypatch):
    async def fake_get_model(_requested):
        return SimpleNamespace(provider="example", capabilities={"chat"})

    async def fail_chat(*args, **kwargs):
        raise AssertionError("provider must not be called for a non-vision model")

    monkeypatch.setattr(client_chat.registry, "get_model", fake_get_model)
    monkeypatch.setattr(client_chat, "chat_completion", fail_chat)

    with pytest.raises(HTTPException) as exc:
        await client_chat.call_registered_model("example/text-only", MULTIMODAL_MESSAGES)
    assert exc.value.status_code == 400
    assert "does not support vision" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_vision_registered_model_receives_multimodal_messages(monkeypatch):
    seen = {}

    async def fake_get_model(_requested):
        return SimpleNamespace(provider="example", capabilities={"chat", "vision"})

    async def fake_chat(model_info, requested, messages, **kwargs):
        seen["messages"] = messages
        return {"content": "vision-ok", "tool_calls": [], "usage_total": 4, "model_used": requested}

    monkeypatch.setattr(client_chat.registry, "get_model", fake_get_model)
    monkeypatch.setattr(client_chat, "chat_completion", fake_chat)

    result = await client_chat.call_registered_model("example/vision", MULTIMODAL_MESSAGES)
    assert seen["messages"][1]["content"][1]["image_url"]["url"] == DATA_URL
    assert result["choices"][0]["message"]["content"] == "vision-ok"


@pytest.mark.asyncio
async def test_openrouter_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "configured")
    monkeypatch.setenv("OPENROUTER_CHAT_TIMEOUT", "420")
    seen = {}

    async def fake_post(provider, url, headers, payload, timeout):
        seen["timeout"] = timeout
        return {
            "model": "qwen/qwen3.8-27b",
            "choices": [{"message": {"content": "ok", "tool_calls": []}}],
            "usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(provider_chat, "_post", fake_post)
    result = await provider_chat.chat_completion(
        SimpleNamespace(provider="openrouter"),
        "openrouter/qwen/qwen3.8-27b",
        [{"role": "user", "content": "hi"}],
        max_tokens=8,
    )
    assert seen["timeout"] == 420.0
    assert result["content"] == "ok"


@pytest.mark.asyncio
async def test_client_chat_keepalive_stream_ends_as_valid_json(monkeypatch):
    async def fake_call_registered_model(*args, **kwargs):
        return {
            "choices": [{"message": {"role": "assistant", "content": "stream-ok", "tool_calls": []}}],
            "usage": {"total_tokens": 2},
            "model_used": "openrouter/qwen/qwen3.8-27b",
            "is_fallback": False,
            "tool_transport": "none",
        }

    monkeypatch.setattr(client_chat, "get_user_and_tier_from_headers", lambda *args: ("u", client_chat.UserTier.ENTERPRISE))
    monkeypatch.setattr(client_chat, "call_registered_model", fake_call_registered_model)
    request = client_chat.ChatRequest(message="hi", model="openrouter/qwen/qwen3.8-27b", max_tokens=8)
    response = await client_chat.client_chat(request, x_aicoder_keepalive="json")
    assert response.media_type == "application/json"
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    import json
    payload = json.loads(b"".join(chunks).decode("utf-8"))
    assert payload["response"] == "stream-ok"
    assert payload["model"] == "openrouter/qwen/qwen3.8-27b"


@pytest.mark.asyncio
async def test_client_chat_exposes_provider_diagnostics_on_success(monkeypatch):
    async def fake_call_registered_model(*args, **kwargs):
        return {
            "choices": [{
                "message": {"role": "assistant", "content": "ok", "tool_calls": []},
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 3},
            "model_used": "openrouter/nvidia/test",
            "is_fallback": False,
            "tool_transport": "none",
            "provider_diagnostics": {"finish_reason": "stop", "reasoning_chars": 42},
        }

    monkeypatch.setattr(client_chat, "get_user_and_tier_from_headers", lambda *args: ("u", client_chat.UserTier.ENTERPRISE))
    monkeypatch.setattr(client_chat, "call_registered_model", fake_call_registered_model)
    result = await client_chat.client_chat(
        client_chat.ChatRequest(message="hi", model="openrouter/nvidia/test", max_tokens=8)
    )
    assert result.finish_reason == "stop"
    assert result.provider_diagnostics["reasoning_chars"] == 42


@pytest.mark.asyncio
async def test_keepalive_preserves_provider_retry_after(monkeypatch):
    async def fail_registered_model(*args, **kwargs):
        raise HTTPException(502, {
            "error": "empty_provider_response",
            "retryable": True,
            "retry_after": 2,
            "provider_diagnostics": {"finish_reason": "stop"},
        })

    monkeypatch.setattr(client_chat, "get_user_and_tier_from_headers", lambda *args: ("u", client_chat.UserTier.ENTERPRISE))
    monkeypatch.setattr(client_chat, "call_registered_model", fail_registered_model)
    request = client_chat.ChatRequest(message="hi", model="openrouter/nvidia/test", max_tokens=8)
    response = await client_chat.client_chat(request, x_aicoder_keepalive="json")
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    import json
    payload = json.loads(b"".join(chunks).decode("utf-8"))
    assert payload["error"]["status"] == 502
    assert payload["error"]["retry_after"] == 2
    assert payload["error"]["detail"]["error"] == "empty_provider_response"


@pytest.mark.asyncio
async def test_keepalive_uses_short_status_fallback_when_provider_has_no_hint(monkeypatch):
    async def fail_registered_model(*args, **kwargs):
        raise HTTPException(502, {
            "error": "provider_embedded_error",
            "provider_message": "temporarily overloaded",
            "retryable": True,
        })

    monkeypatch.setattr(client_chat, "get_user_and_tier_from_headers", lambda *args: ("u", client_chat.UserTier.ENTERPRISE))
    monkeypatch.setattr(client_chat, "call_registered_model", fail_registered_model)
    request = client_chat.ChatRequest(message="hi", model="openrouter/nvidia/test", max_tokens=8)
    response = await client_chat.client_chat(request, x_aicoder_keepalive="json")
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    import json
    payload = json.loads(b"".join(chunks).decode("utf-8"))
    assert payload["error"]["retry_after"] == 5


@pytest.mark.asyncio
async def test_client_chat_keepalive_serializes_immediate_provider_http_error(monkeypatch):
    async def fail_registered_model(*args, **kwargs):
        raise HTTPException(402, "OpenRouter API 402: insufficient credits")

    monkeypatch.setattr(client_chat, "get_user_and_tier_from_headers", lambda *args: ("u", client_chat.UserTier.ENTERPRISE))
    monkeypatch.setattr(client_chat, "call_registered_model", fail_registered_model)
    request = client_chat.ChatRequest(message="hi", model="openrouter/openai/gpt-6-astra", max_tokens=8)
    response = await client_chat.client_chat(request, x_aicoder_keepalive="json")
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    import json
    payload = json.loads(b"".join(chunks).decode("utf-8"))
    assert payload["error"]["status"] == 402
    assert payload["error"]["retryable"] is False
    assert "insufficient credits" in str(payload["error"]["detail"])


def test_invalid_authorization_header_never_downgrades_to_guest():
    with pytest.raises(HTTPException) as exc:
        client_chat.get_user_and_tier_from_headers("Bearer definitely-not-a-jwt", None)
    assert exc.value.status_code == 401


def test_x_user_id_without_jwt_cannot_impersonate_paid_user(monkeypatch):
    monkeypatch.setattr(
        client_chat.tier_service, "get_user_tier",
        lambda _user: client_chat.UserTier.ENTERPRISE,
    )
    user_id, tier = client_chat.get_user_and_tier_from_headers(None, "admin@example.invalid")
    assert user_id == "anonymous"
    assert tier == client_chat.UserTier.GUEST


@pytest.mark.asyncio
async def test_ollama_length_with_empty_content_is_reported_as_truncation(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = ""
        def json(self):
            return {
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "length",
                "prompt_eval_count": 4,
                "eval_count": 8,
            }

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(client_chat.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())
    with pytest.raises(HTTPException) as exc:
        await client_chat.call_ollama(
            "ollama/qwen3.5:cloud",
            [{"role": "user", "content": "ping"}],
            max_tokens=8,
        )
    assert exc.value.status_code == 502
    assert exc.value.detail["error"] == "ollama_response_truncated"
    assert exc.value.detail["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_ollama_preserves_finish_reason(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = ""
        def json(self):
            return {
                "message": {"role": "assistant", "content": "OK"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 2,
                "eval_count": 1,
            }

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(client_chat.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())
    result = await client_chat.call_ollama(
        "ollama/qwen3.5:cloud",
        [{"role": "user", "content": "ping"}],
        max_tokens=32,
    )
    assert result["choices"][0]["message"]["content"] == "OK"
    assert result["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_client_chat_preserves_native_tool_history_fields(monkeypatch):
    seen = {}

    async def fake_call_registered_model(model, messages, **kwargs):
        seen["model"] = model
        seen["messages"] = messages
        seen["tools"] = kwargs.get("tools")
        return {
            "choices": [{"message": {"role": "assistant", "content": "done", "tool_calls": []}}],
            "usage": {"total_tokens": 3},
            "model_used": model,
            "is_fallback": False,
            "tool_transport": "native",
        }

    monkeypatch.setattr(
        client_chat,
        "get_user_and_tier_from_headers",
        lambda *args: ("u", client_chat.UserTier.ENTERPRISE),
    )
    monkeypatch.setattr(client_chat, "call_registered_model", fake_call_registered_model)

    tool_call = {
        "id": "call_abc",
        "type": "function",
        "function": {"name": "file_tree", "arguments": '{"path":"."}'},
    }
    request = client_chat.ChatRequest(
        model="openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
        messages=[
            client_chat.ChatMessage(role="system", content="system"),
            client_chat.ChatMessage(role="user", content="inspect"),
            client_chat.ChatMessage(role="assistant", content="", tool_calls=[tool_call]),
            client_chat.ChatMessage(
                role="tool", content="tree result", tool_call_id="call_abc", name="file_tree"
            ),
        ],
        tools=[{
            "name": "file_tree",
            "description": "List files",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }],
    )

    result = await client_chat.client_chat(request)

    assert result.response == "done"
    assert seen["messages"][2]["tool_calls"] == [tool_call]
    assert seen["messages"][3]["tool_call_id"] == "call_abc"
    assert seen["messages"][3]["name"] == "file_tree"
    assert seen["messages"][3]["role"] == "tool"
