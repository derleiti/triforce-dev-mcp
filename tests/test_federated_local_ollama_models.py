"""Federated Ollama local-model routing and catalog safety tests."""
from __future__ import annotations

import pytest
import httpx

from app.services.ollama_node_router import (
    LOCAL_OLLAMA_PLACEMENT,
    local_model_endpoint,
    ollama_candidates,
)
from app.services.model_registry import ModelRegistry
from app.services.user_tiers import LOCAL_OLLAMA_MODELS, UserTier, tier_service
from app.routes.client_chat import is_guest_free_model, is_registered_free_model


@pytest.mark.parametrize("model,node,base", [
    ("openbmb/minicpm-v4.7:35b-a3b", "hetzner", "http://127.0.0.1:11434"),
    ("openbmb/minicpm-v4.7:1b", "backup", "http://10.10.0.3:11434"),
    ("gemma4:12b", "zombie-pc", "http://10.10.0.2:11434"),
])
def test_pinned_model_only_routes_to_its_owner(monkeypatch, model, node, base):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("TRIFORCE_OLLAMA_BACKUP_URL", "http://10.10.0.3:11434")
    monkeypatch.setenv("TRIFORCE_OLLAMA_ZOMBIE_URL", "http://10.10.0.2:11434")
    for request_model in (model, "ollama/" + model):
        endpoint = local_model_endpoint(request_model)
        assert endpoint is not None
        assert endpoint.node_id == node
        candidates = ollama_candidates(request_model)
        assert [(e.node_id, e.base_url) for e in candidates] == [(node, base)]


def test_pinned_models_are_not_cloud_catalog_entries():
    from app.services.user_tiers import OLLAMA_MODELS
    assert set("ollama/" + name for name in LOCAL_OLLAMA_PLACEMENT) == set(LOCAL_OLLAMA_MODELS)
    assert not set(LOCAL_OLLAMA_MODELS) & set(OLLAMA_MODELS)


def test_guest_cannot_use_local_compute_but_registered_can(monkeypatch):
    for model in LOCAL_OLLAMA_MODELS:
        assert not is_guest_free_model(model)
        assert is_registered_free_model(model)
    monkeypatch.setattr(tier_service, "get_user_tier", lambda uid: UserTier.GUEST)
    assert all(mid not in tier_service.get_allowed_models("anonymous") for mid in LOCAL_OLLAMA_MODELS)
    monkeypatch.setattr(tier_service, "get_user_tier", lambda uid: UserTier.REGISTERED)
    assert all(mid in tier_service.get_allowed_models("registered") for mid in LOCAL_OLLAMA_MODELS)


@pytest.mark.asyncio
async def test_discovery_only_announces_live_allowlisted_models(monkeypatch):
    from app.services import model_registry
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    tags = {
        "http://127.0.0.1:11434": [
            {"name": "openbmb/minicpm-v4.7:35b-a3b",
             "capabilities": ["completion", "vision", "tools", "thinking"]},
            {"name": "untrusted/model:latest", "capabilities": ["completion"]},
        ],
        "http://10.10.0.3:11434": [
            {"name": "openbmb/minicpm-v4.7:1b",
             "capabilities": ["completion", "vision", "tools"]},
        ],
        "http://10.10.0.2:11434": [
            {"name": "gemma4:12b",
             "capabilities": ["completion", "vision", "tools", "audio", "thinking"]},
        ],
    }

    def handler(request):
        url = str(request.url).removesuffix("/api/tags")
        if url not in tags:
            return httpx.Response(404)
        return httpx.Response(200, json={"models": tags[url]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        model_registry.httpx, "AsyncClient",
        lambda *a, **k: original_client(transport=httpx.MockTransport(handler)),
    )
    registry = ModelRegistry()
    discovered = await registry._discover_ollama()
    local = [m for m in discovered if m.id in LOCAL_OLLAMA_MODELS]
    assert {m.id for m in local} == set(LOCAL_OLLAMA_MODELS)
    assert all(m.availability == "ok" for m in local)
    assert all(m.free_status == "catalog_free" for m in local)
    assert all(m.capability_source == "declared" for m in local)
    assert not any("untrusted" in m.id for m in discovered)
    assert "vision" in next(m for m in local if m.id.endswith("35b-a3b")).capabilities
    assert "code" in next(m for m in local if m.id.endswith("gemma4:12b")).capabilities


@pytest.mark.asyncio
async def test_discovery_does_not_publish_offline_or_missing_models(monkeypatch):
    from app.services import model_registry
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    def handler(request):
        url = str(request.url)
        if "10.10.0.2" in url:
            raise httpx.ConnectError("offline", request=request)
        if "10.10.0.3" in url:
            return httpx.Response(200, json={"models": [{"name": "other", "capabilities": ["completion"]}]})
        return httpx.Response(200, json={"models": [
            {"name": "openbmb/minicpm-v4.7:35b-a3b", "capabilities": ["completion", "vision"]}
        ]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        model_registry.httpx, "AsyncClient",
        lambda *a, **k: original_client(transport=httpx.MockTransport(handler)),
    )
    models = await ModelRegistry()._discover_ollama()
    found = {m.id for m in models}
    assert "ollama/openbmb/minicpm-v4.7:35b-a3b" in found
    assert "ollama/openbmb/minicpm-v4.7:1b" not in found
    assert "ollama/gemma4:12b" not in found


@pytest.mark.asyncio
async def test_stale_capability_store_cannot_override_local_live_tag(monkeypatch):
    from app.services import model_registry
    from app.services.model_capabilities import CapabilityRecord

    class FakeStore:
        def get(self, _):
            return CapabilityRecord(
                model_id="ollama/gemma4:12b", provider="ollama",
                capabilities=[], source="probed", availability="not_found",
            )

    monkeypatch.setattr("app.services.model_capabilities.get_store", lambda: FakeStore())
    entry = model_registry.ModelInfo(
        id="ollama/gemma4:12b", provider="ollama",
        capabilities=["chat", "vision"], availability="ok",
    )
    model_registry.ModelRegistry._apply_capability_store({entry.id: entry})
    assert entry.availability == "ok"
    assert entry.capabilities == ["chat", "vision"]


@pytest.mark.asyncio
async def test_registered_chat_uses_pinned_ollama_worker(monkeypatch):
    from app.routes import client_chat

    called = []
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"message": {"content": "Gemma alive"}, "done_reason": "stop",
                    "eval_count": 4, "prompt_eval_count": 8}

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            called.append((url, kwargs["json"]["model"]))
            return FakeResponse()

    monkeypatch.setattr(client_chat.httpx, "AsyncClient", FakeClient)
    answer = await client_chat.call_ollama(
        "ollama/gemma4:12b", [{"role": "user", "content": "ping"}],
        is_fallback=True,
    )
    assert called == [("http://10.10.0.2:11434/api/chat", "gemma4:12b")]
    assert answer["choices"][0]["message"]["content"] == "Gemma alive"
    assert answer["provider_diagnostics"]["ollama_node"] == "zombie-pc"


@pytest.mark.asyncio
async def test_openai_compat_blocks_untrusted_public_access(monkeypatch):
    from fastapi import HTTPException
    from app.routes import openai_compat
    from app.services.openai_compat import OpenAIChatCompletionRequest, OpenAIChatMessage

    payload = OpenAIChatCompletionRequest(
        model="ollama/gemma4:12b",
        messages=[OpenAIChatMessage(role="user", content="ping")],
    )
    cloud = payload.model_copy(update={"model": "ollama/gemma4:cloud"})
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    # All ordinary cloud models retain their old routing behavior.
    await openai_compat.require_pinned_ollama_auth(
        cloud, authorization=None, x_internal_key=None,
    )
    with pytest.raises(HTTPException) as denied:
        await openai_compat.require_pinned_ollama_auth(
            payload, authorization=None, x_internal_key=None,
        )
    assert denied.value.status_code == 403

    # X-User-ID by itself is never a substitute for a JWT.
    from app.services.user_tiers import UserTier
    monkeypatch.setattr("app.routes.client_chat.get_user_and_tier_from_headers",
                        lambda token: ("anonymous", UserTier.GUEST))
    with pytest.raises(HTTPException):
        await openai_compat.require_pinned_ollama_auth(
            payload, authorization="untrusted", x_internal_key=None,
        )
    monkeypatch.setattr("app.routes.client_chat.get_user_and_tier_from_headers",
                        lambda token: ("verified-user", UserTier.REGISTERED))
    await openai_compat.require_pinned_ollama_auth(
        payload, authorization="verified-jwt", x_internal_key=None,
    )

    monkeypatch.setenv("INTERNAL_API_KEY", "test-server-secret")
    await openai_compat.require_pinned_ollama_auth(
        payload, authorization=None, x_internal_key="test-server-secret",
    )


def test_openai_compat_protects_both_chat_routes():
    from app.routes import openai_compat
    protected_paths = {route.path for route in openai_compat.router.routes
                       if hasattr(route, "dependencies") and
                       any(x.dependency is openai_compat.require_pinned_ollama_auth
                           for x in route.dependencies)}
    assert {"/chat/completions", "/"} <= protected_paths


@pytest.mark.asyncio
async def test_pinned_model_failure_never_silently_changes_to_cloud(monkeypatch):
    from app.routes import client_chat
    from fastapi import HTTPException

    urls = []
    class FailedResponse:
        status_code = 503
        text = "offline"

    class OfflineClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            urls.append(url)
            return FailedResponse()

    monkeypatch.setattr(client_chat.httpx, "AsyncClient", OfflineClient)
    with pytest.raises(HTTPException) as error:
        await client_chat.call_ollama(
            "ollama/gemma4:12b",
            [{"role": "user", "content": "ping"}],
        )
    assert error.value.status_code == 503
    assert urls == ["http://10.10.0.2:11434/api/chat"]


@pytest.mark.asyncio
async def test_openai_compatible_stream_chats_on_assigned_node(monkeypatch):
    from app.services import chat

    captured = []
    class GoodResponse:
        def raise_for_status(self): return None
        def json(self):
            return {"message": {"content": "from backup"}}

    class FakeHttpClient:
        def __init__(self, base_url):
            captured.append(("base", base_url))
        async def post(self, url, **kwargs):
            captured.append(("post", str(url), kwargs["json"]["model"]))
            return GoodResponse()

    monkeypatch.setattr(chat, "HttpClient", FakeHttpClient)
    chunks = [
        item async for item in chat._stream_ollama(
            "ollama/openbmb/minicpm-v4.7:1b",
            [{"role": "user", "content": "hi"}],
            temperature=0.1,
            stream=False,
            timeout=8.0,
        )
    ]
    assert chunks == ["from backup"]
    assert captured[0] == ("base", "http://10.10.0.3:11434")
    assert captured[1][2] == "openbmb/minicpm-v4.7:1b"


def test_v1_chat_routes_also_guard_private_local_workers():
    from app.routes import chat
    protected = {
        route.path for route in chat.router.routes
        if hasattr(route, "dependencies")
        and any(x.dependency is chat.require_local_chat_auth
                for x in route.dependencies)
    }
    assert {"/chat", "/chat/completions"} <= protected


@pytest.mark.asyncio
async def test_anonymous_playground_cannot_route_to_private_worker():
    from app.routes.nova_playground import (
        PlaygroundRequest, AgentRequest, nova_playground, nova_playground_agent,
    )
    request = PlaygroundRequest(model="ollama/gemma4:12b", message="hello", mode="chat")
    response = await nova_playground(request)
    assert response["ok"] is False
    assert response["mode"] == "error"
    agent = await nova_playground_agent(AgentRequest(topic="hello", model_id="ollama/gemma4:12b"))
    assert agent["ok"] is False
