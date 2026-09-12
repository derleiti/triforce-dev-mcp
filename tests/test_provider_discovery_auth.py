from types import SimpleNamespace

import httpx
import pytest

from app.services import model_registry


class _AuthRejectResponse:
    def __init__(self, status_code: int = 401):
        self.status_code = status_code
        self.request = httpx.Request("GET", "https://provider.example/v1/models")

    def raise_for_status(self):
        response = httpx.Response(self.status_code, request=self.request)
        raise httpx.HTTPStatusError("rejected", request=self.request, response=response)

    def json(self):
        return {}


class _RejectClient:
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        type(self).calls += 1
        return _AuthRejectResponse(401)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["cerebras", "cohere"])
async def test_auth_rejection_marks_provider_invalid_and_returns_no_fake_models(monkeypatch, provider):
    _RejectClient.calls = 0
    monkeypatch.setattr(model_registry.httpx, "AsyncClient", _RejectClient)
    registry = model_registry.ModelRegistry()
    registry._settings = SimpleNamespace(
        cerebras_api_key="configured",
        cerebras_base_url="https://provider.example/v1",
        cohere_api_key="configured",
    )

    discover = getattr(registry, f"_discover_{provider}")
    assert await discover() == []
    state = registry.provider_status()[provider]
    assert state["status"] == "invalid_credentials"
    assert state["error"] == "HTTP 401"
    assert state["retry_after"] is not None

    # Backoff suppresses repeated network probes/log spam.
    assert await discover() == []
    assert _RejectClient.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["cerebras", "cohere"])
async def test_missing_credentials_skip_discovery_without_network(monkeypatch, provider):
    class _MustNotRun:
        def __init__(self, *args, **kwargs):
            raise AssertionError("network client must not be created")

    monkeypatch.setattr(model_registry.httpx, "AsyncClient", _MustNotRun)
    registry = model_registry.ModelRegistry()
    registry._settings = SimpleNamespace(
        cerebras_api_key=None,
        cerebras_base_url="https://provider.example/v1",
        cohere_api_key=None,
    )

    discover = getattr(registry, f"_discover_{provider}")
    assert await discover() == []
    assert registry.provider_status()[provider]["status"] == "not_configured"


def test_model_catalog_exposes_provider_status():
    registry = model_registry.ModelRegistry()
    registry._set_provider_status("cohere", "invalid_credentials", error="HTTP 401", retry_seconds=60)
    catalog = registry.model_catalog([])
    assert catalog["provider_status"]["cohere"]["status"] == "invalid_credentials"
