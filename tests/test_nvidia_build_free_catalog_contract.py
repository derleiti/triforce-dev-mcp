"""Fail-closed NVIDIA Build metadata and client catalog contract."""
import pytest
from app.services.model_registry import ModelInfo, ModelRegistry
from app.services.nvidia_free_endpoints import (
    NVIDIA_BUILD_FREE_ENDPOINT_IDS, is_nvidia_build_free_endpoint,
)


def test_build_reference_has_44_unique_ids():
    assert len(NVIDIA_BUILD_FREE_ENDPOINT_IDS) == 44
    assert is_nvidia_build_free_endpoint('nvidia/nemotron-3.5-lightning-30b-a3b')
    assert not is_nvidia_build_free_endpoint('nvidia/retired-unknown-model')


def test_free_endpoint_does_not_grant_free_billing():
    model = ModelInfo(
        id='nvidia/nvidia/nemotron-3.5-lightning-30b-a3b',
        provider='nvidia', capabilities=['chat'], free_endpoint=True,
    )
    payload = model.to_dict()
    assert payload['free_endpoint'] is True
    assert payload['free_status'] == 'unknown'
    assert ModelRegistry()._normalize_entry(model).free_status == 'unknown'


@pytest.mark.asyncio
async def test_nvidia_discovery_marks_label_but_not_entitlement(monkeypatch):
    from app.services import model_registry as mod
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'data': [{'id':'nvidia/nemotron-3.5-lightning-30b-a3b'},
                             {'id':'nvidia/other-model'}]}
    class FakeClient:
        def __init__(self,*a,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def get(self,*a,**kw):return Response()
    monkeypatch.setattr(mod.httpx,'AsyncClient',FakeClient)
    registry=mod.ModelRegistry()
    from types import SimpleNamespace
    registry._settings=SimpleNamespace(nvidia_api_key='test-not-real',nvidia_base_url='https://invalid.example/v1')
    models=await registry._discover_nvidia()
    assert len(models)==2
    assert models[0].free_endpoint is True
    assert models[1].free_endpoint is False
    assert all(m.free_status=='unknown' for m in models)


def test_nvidia_public_free_requires_explicit_entitlement(monkeypatch):
    from app.services.nvidia_public_access import is_public_free_nvidia_model
    model = 'nvidia/nvidia/nemotron-3.5-lightning-30b-a3b'
    monkeypatch.delenv('NVIDIA_PUBLIC_FREE_ENABLED', raising=False)
    monkeypatch.setenv('NVIDIA_PUBLIC_FREE_MODEL_IDS', model)
    assert not is_public_free_nvidia_model(model)
    monkeypatch.setenv('NVIDIA_PUBLIC_FREE_ENABLED', '1')
    monkeypatch.delenv('NVIDIA_PUBLIC_FREE_MODEL_IDS', raising=False)
    assert not is_public_free_nvidia_model(model)
    monkeypatch.setenv('NVIDIA_PUBLIC_FREE_MODEL_IDS', model)
    assert not is_public_free_nvidia_model('nvidia/nvidia/not-listed')
