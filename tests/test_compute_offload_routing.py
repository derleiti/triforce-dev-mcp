from __future__ import annotations

import pytest

from app.services.mesh_brain_v2 import MeshBrainV2, OllamaNode, ProviderType, Strategy
from app.services.server_federation import FederationNode, LoadBalancerIntegration, NodeRole, NodeStatus, ServerFederation


@pytest.mark.asyncio
async def test_initialize_prefers_backup_and_registers_zombie_pc(monkeypatch):
    brain = MeshBrainV2()

    async def fake_health(node):
        node.healthy = True
        if node.provider_id in {"backup", "hetzner"}:
            node.models = ["qwen3.5:cloud"]
        elif node.provider_id == "zombie-pc":
            node.models = ["hf-gemma-4-e4b-uncensored-hauhaucs-aggressive:latest"]

    monkeypatch.setattr(brain, "_check_ollama_health", fake_health)
    await brain.initialize()

    assert set(brain.ollama_nodes) == {"backup", "zombie-pc", "hetzner"}
    selected = brain.select_provider(
        Strategy.FALLBACK, prefer_local=True, provider_type=ProviderType.OLLAMA
    )
    assert selected.provider_id == "backup"


def test_model_specific_routing_uses_node_inventory():
    brain = MeshBrainV2()
    brain.ollama_nodes = {
        "backup": OllamaNode("backup", ProviderType.OLLAMA, priority=12, host="10.10.0.3", models=["qwen3.5:cloud"]),
        "zombie-pc": OllamaNode("zombie-pc", ProviderType.OLLAMA, priority=11, host="10.10.0.2", models=["gemma-local"]),
        "hetzner": OllamaNode("hetzner", ProviderType.OLLAMA, priority=7, host="127.0.0.1", models=["qwen3.5:cloud"]),
    }
    assert brain.select_provider(
        Strategy.FALLBACK, provider_type=ProviderType.OLLAMA, model="gemma-local"
    ).provider_id == "zombie-pc"
    assert brain.select_provider(
        Strategy.FALLBACK, provider_type=ProviderType.OLLAMA, model="qwen3.5:cloud"
    ).provider_id == "backup"


@pytest.mark.asyncio
async def test_failed_backup_retries_on_hetzner_for_same_ollama_model(monkeypatch):
    brain = MeshBrainV2()
    brain._initialized = True
    brain.ollama_nodes = {
        "backup": OllamaNode("backup", ProviderType.OLLAMA, priority=12, host="10.10.0.3", models=["qwen3.5:cloud"]),
        "zombie-pc": OllamaNode("zombie-pc", ProviderType.OLLAMA, priority=11, host="10.10.0.2", models=["gemma-local"]),
        "hetzner": OllamaNode("hetzner", ProviderType.OLLAMA, priority=7, host="127.0.0.1", models=["qwen3.5:cloud"]),
    }
    brain.providers = {}
    calls = []

    async def fake_chat(node, message, model, system):
        calls.append(node.provider_id)
        if node.provider_id == "backup":
            return {"error": "temporary failure"}
        return {"response": "ok", "model": model}

    monkeypatch.setattr(brain, "_chat_ollama", fake_chat)
    result = await brain.chat("hello", model="qwen3.5:cloud", max_retries=3)
    assert calls == ["backup", "hetzner"]
    assert result["_provider"] == "hetzner"


def test_federation_weight_favors_compute_node_over_hub_at_equal_load():
    federation = ServerFederation()
    lb = LoadBalancerIntegration(federation)
    hub = FederationNode(
        node_id="hetzner", role=NodeRole.HUB, base_url="http://10.10.0.1:9100",
        status=NodeStatus.HEALTHY, max_concurrent=10, current_load=0
    )
    node = FederationNode(
        node_id="backup", role=NodeRole.NODE, base_url="http://10.10.0.3:9100",
        status=NodeStatus.HEALTHY, max_concurrent=10, current_load=0
    )
    assert lb._calculate_weight(node) > lb._calculate_weight(hub)

def test_static_ollama_router_prefers_backup_and_uses_zombie_for_its_model(monkeypatch):
    from app.services.ollama_node_router import ollama_candidates

    monkeypatch.setenv("TRIFORCE_OLLAMA_BACKUP_URL", "http://backup.test:11434")
    monkeypatch.setenv("TRIFORCE_OLLAMA_ZOMBIE_URL", "http://zombie.test:11434")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://hetzner.test:11434")

    normal = ollama_candidates("qwen3.5:cloud")
    assert [item.node_id for item in normal] == ["backup", "hetzner"]

    zombie = ollama_candidates("hf-gemma-4-e4b-uncensored-hauhaucs-aggressive:latest")
    assert [item.node_id for item in zombie] == ["zombie-pc", "backup", "hetzner"]


@pytest.mark.asyncio
async def test_client_chat_ollama_fails_over_from_backup_to_hetzner(monkeypatch):
    from app.routes import client_chat

    calls = []

    class FakeResponse:
        def __init__(self, status_code, text="", payload=None):
            self.status_code = status_code
            self.text = text
            self._payload = payload or {}
        def json(self):
            return self._payload

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            calls.append(url)
            if "10.10.0.3" in url:
                return FakeResponse(503, "backup unavailable")
            return FakeResponse(200, payload={
                "message": {"role": "assistant", "content": "ok"},
                "done_reason": "stop",
                "prompt_eval_count": 2,
                "eval_count": 1,
            })

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(client_chat.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())
    result = await client_chat.call_ollama(
        "ollama/qwen3.5:cloud",
        [{"role": "user", "content": "ping"}],
    )
    assert calls[0].startswith("http://10.10.0.3:11434/")
    assert calls[1].startswith("http://127.0.0.1:11434/")
    assert result["provider_diagnostics"]["ollama_node"] == "hetzner"
