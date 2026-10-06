import json
from types import SimpleNamespace

import pytest

from app.mcp import handlers_wordpress
from app.services import mcp_service
from app.mcp.specialists import SpecialistCapability
from app.utils.http import extract_http_error
from httpx import Request, Response


@pytest.mark.asyncio
async def test_wp_delete_accepts_wordpress_deleted_shape(monkeypatch):
    async def fake_wp_api(method, path, data=None):
        assert method == "DELETE"
        return {"deleted": True, "previous": {"id": 123}}

    monkeypatch.setattr(handlers_wordpress, "_wp_api", fake_wp_api)
    result = json.loads(await handlers_wordpress.handle_wp_delete_post({"post_id": 123}))
    assert result == {"success": True, "deleted_id": 123}


@pytest.mark.asyncio
async def test_specialist_enum_falls_back_to_capability(monkeypatch):
    chosen = SimpleNamespace(
        id="mistral/test-math",
        system_prompt_template="",
        to_dict=lambda: {"id": "mistral/test-math"},
    )

    monkeypatch.setattr(mcp_service.specialist_router, "get_best_specialist", lambda *_a, **_k: None)

    def by_capability(capability, preferred_speed=None):
        assert capability is SpecialistCapability.MATH
        return chosen

    monkeypatch.setattr(mcp_service.specialist_router, "get_specialist_for_capability", by_capability)

    async def fake_invoke(params):
        assert params["model"] == "mistral/test-math"
        return {"response": "4"}

    monkeypatch.setattr(mcp_service, "handle_llm_invoke", fake_invoke)
    result = await mcp_service.handle_specialists_invoke({"task": "math", "message": "2+2"})
    assert result["response"] == "4"
    assert result["specialist"]["id"] == "mistral/test-math"


def test_extract_http_error_handles_unread_stream():
    class UnreadStream:
        def __iter__(self):
            return iter(())
        async def __aiter__(self):
            if False:
                yield b""

    response = Response(402, request=Request("GET", "https://example.invalid"), stream=UnreadStream())
    message, code = extract_http_error(response, default_message="fallback", default_code="upstream")
    assert message == "HTTP 402 Payment Required"
    assert code == "upstream"

@pytest.mark.asyncio
async def test_route_llm_default_uses_configurable_live_default(monkeypatch):
    from app.routes import mcp as mcp_route

    class Model:
        id = "groq/groq/compound-mini"
        provider = "groq"
        capabilities = ["chat"]

    async def fake_get_model(model_id):
        assert model_id == "groq/groq/compound-mini"
        return Model()

    async def fake_stream_chat(model, model_id, messages, **kwargs):
        assert model_id == "groq/groq/compound-mini"
        yield "OK"

    monkeypatch.delenv("TRIFORCE_DEFAULT_CHAT_MODEL", raising=False)
    monkeypatch.setattr(mcp_route.registry, "get_model", fake_get_model)
    monkeypatch.setattr(mcp_route.chat_service, "stream_chat", fake_stream_chat)
    result = await mcp_route.handle_llm_invoke({"message": "ping"})
    assert result["model"] == "groq/groq/compound-mini"
    assert result["response"] == "OK"

@pytest.mark.asyncio
async def test_specialist_provider_failure_falls_back_to_default(monkeypatch):
    chosen = SimpleNamespace(
        id="mistral/unavailable",
        system_prompt_template="Be precise.",
        to_dict=lambda: {"id": "mistral/unavailable"},
    )
    monkeypatch.setattr(mcp_service.specialist_router, "get_best_specialist", lambda *_a, **_k: chosen)
    calls = []

    async def fake_invoke(params):
        calls.append(params["model"])
        if len(calls) == 1:
            raise RuntimeError("provider unavailable")
        return {"response": "4", "model": params["model"]}

    monkeypatch.delenv("TRIFORCE_SPECIALIST_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("TRIFORCE_DEFAULT_CHAT_MODEL", raising=False)
    monkeypatch.setattr(mcp_service, "handle_llm_invoke", fake_invoke)
    result = await mcp_service.handle_specialists_invoke({"task": "math", "message": "2+2"})
    assert calls == ["mistral/unavailable", "groq/groq/compound-mini"]
    assert result["fallback_model"] == "groq/groq/compound-mini"
    assert result["specialist_model_attempted"] == "mistral/unavailable"
    assert result["response"] == "4"
