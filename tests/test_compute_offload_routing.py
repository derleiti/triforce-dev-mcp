from __future__ import annotations

import pytest
from datetime import datetime, timedelta

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
    now = datetime.now()
    hub = FederationNode(
        node_id="hetzner", role=NodeRole.HUB, base_url="http://10.10.0.1:9100",
        status=NodeStatus.HEALTHY, last_heartbeat=now, max_concurrent=10, current_load=0,
        placement_weight=0.72,
    )
    node = FederationNode(
        node_id="backup", role=NodeRole.NODE, base_url="http://10.10.0.3:9100",
        status=NodeStatus.HEALTHY, last_heartbeat=now, max_concurrent=10, current_load=0,
        placement_weight=1.18,
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


def _healthy_node(node_id: str, *, weight: float = 1.0, cpu: float = 10.0, memory: float = 20.0, load: float = 0.1):
    return FederationNode(
        node_id=node_id,
        role=NodeRole.NODE,
        base_url=f"http://{node_id}:9100",
        status=NodeStatus.HEALTHY,
        last_heartbeat=datetime.now(),
        placement_weight=weight,
        cpu_percent=cpu,
        memory_percent=memory,
        load_ratio=load,
        memory_available_mb=8192,
        swap_free_mb=4096,
        metrics_updated_at=datetime.now(),
    )


def test_resource_score_prefers_less_loaded_worker():
    idle = _healthy_node("backup", cpu=10, memory=20, load=0.1)
    busy = _healthy_node("zombie-pc", cpu=94, memory=91, load=1.4)
    assert idle.selection_score() > busy.selection_score()


def test_stale_or_draining_worker_is_not_schedulable():
    stale = _healthy_node("zombie-pc")
    stale.last_heartbeat = datetime.now() - timedelta(seconds=120)
    draining = _healthy_node("backup")
    draining.draining = True
    assert stale.selection_score(stale_after=60) == 0
    assert draining.selection_score(stale_after=60) == 0


def test_federation_scheduler_ignores_offline_zombie_and_keeps_backup():
    federation = ServerFederation()
    backup = _healthy_node("backup", weight=1.18)
    zombie = _healthy_node("zombie-pc", weight=1.08)
    zombie.status = NodeStatus.OFFLINE
    federation.nodes = {"backup": backup, "zombie-pc": zombie}
    ranked = federation.rank_available_nodes()
    assert [node.node_id for node in ranked] == ["backup"]


def test_federation_scheduler_readds_zombie_after_recovery():
    federation = ServerFederation()
    zombie = _healthy_node("zombie-pc", weight=1.08)
    zombie.status = NodeStatus.OFFLINE
    federation.nodes = {"zombie-pc": zombie}
    assert federation.get_available_node() is None
    zombie.status = NodeStatus.HEALTHY
    zombie.last_heartbeat = datetime.now()
    assert federation.get_available_node().node_id == "zombie-pc"


def test_ollama_router_omits_offline_zombie_but_retains_fallbacks(monkeypatch):
    import app.services.ollama_node_router as router

    def snapshot(node_id):
        if node_id == "zombie-pc":
            return {"status": "offline", "score": 0, "draining": False}
        if node_id == "backup":
            return {"status": "healthy", "score": 80, "draining": False}
        return None

    monkeypatch.setattr(router, "_federation_snapshot", snapshot)
    monkeypatch.setattr(router, "_local_capacity_score", lambda: 60)
    nodes = router.ollama_candidates("hf-gemma-4-e4b-uncensored-hauhaucs-aggressive:latest")
    assert [node.node_id for node in nodes] == ["backup", "hetzner"]


def test_ollama_router_uses_hub_when_backup_is_overloaded(monkeypatch):
    import app.services.ollama_node_router as router

    monkeypatch.setattr(
        router, "_federation_snapshot",
        lambda node_id: {"status": "healthy", "score": 20, "draining": False}
        if node_id == "backup" else None,
    )
    monkeypatch.setattr(router, "_local_capacity_score", lambda: 65)
    nodes = router.ollama_candidates("gemma4:cloud")
    assert [node.node_id for node in nodes][:2] == ["hetzner", "backup"]

@pytest.mark.asyncio
async def test_federation_health_probe_imports_resource_metrics(monkeypatch):
    import app.services.server_federation as sf

    class Response:
        status_code = 200
        def json(self):
            return {
                "node_metrics": {
                    "cpu_percent": 22.5,
                    "memory_percent": 31.0,
                    "load_ratio": 0.25,
                    "memory_available_mb": 12000,
                    "swap_free_mb": 8000,
                    "disk_free_gb": 400.0,
                }
            }

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, *args, **kwargs): return Response()

    monkeypatch.setattr(sf.httpx, "AsyncClient", lambda *args, **kwargs: Client())
    federation = ServerFederation()
    node = FederationNode("backup", NodeRole.NODE, "http://10.10.0.3:9100")
    await federation._check_node(node)
    assert node.status == NodeStatus.HEALTHY
    assert node.cpu_percent == 22.5
    assert node.memory_available_mb == 12000
    assert node.metrics_updated_at is not None


@pytest.mark.asyncio
async def test_heartbeat_checks_are_parallel_when_optional_node_is_slow(monkeypatch):
    import asyncio
    import time

    federation = ServerFederation()
    federation.nodes = {
        "backup": FederationNode("backup", NodeRole.NODE, "http://backup"),
        "zombie-pc": FederationNode("zombie-pc", NodeRole.NODE, "http://zombie"),
    }

    async def slow_probe(node):
        await asyncio.sleep(0.08)
        node.status = NodeStatus.HEALTHY
        node.last_heartbeat = datetime.now()

    monkeypatch.setattr(federation, "_check_node", slow_probe)
    started = time.monotonic()
    await federation._check_all_nodes()
    elapsed = time.monotonic() - started
    assert elapsed < 0.14


def test_public_health_metrics_have_capacity_fields():
    from app.routes.health import _compute_health_metrics
    metrics = _compute_health_metrics()
    assert metrics["node_id"] in {"hetzner", "backup", "zombie-pc"}
    assert metrics["cpu_count"] >= 1
    assert 0 <= metrics["memory_percent"] <= 100
    assert metrics["memory_available_mb"] >= 0
    assert metrics["swap_free_mb"] >= 0


@pytest.mark.asyncio
async def test_zombie_only_model_falls_back_to_general_model_when_node_disappears(monkeypatch):
    from app.routes import client_chat
    import app.services.ollama_node_router as router

    special = "hf-gemma-4-e4b-uncensored-hauhaucs-aggressive:latest"

    def candidates(model):
        if model == special:
            return [router.OllamaEndpoint("zombie-pc", "http://zombie.test:11434", 90, "test")]
        return [router.OllamaEndpoint("backup", "http://backup.test:11434", 80, "test")]

    class Response:
        def __init__(self, status, payload=None, text=""):
            self.status_code = status
            self._payload = payload or {}
            self.text = text
        def json(self): return self._payload

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            model = kwargs["json"]["model"]
            if model == special:
                return Response(503, text="zombie offline")
            return Response(200, {
                "message": {"role": "assistant", "content": "fallback ok"},
                "done_reason": "stop",
                "prompt_eval_count": 1,
                "eval_count": 1,
            })

    monkeypatch.setattr(router, "ollama_candidates", candidates)
    monkeypatch.setattr(client_chat.httpx, "AsyncClient", lambda *args, **kwargs: Client())
    result = await client_chat.call_ollama(
        f"ollama/{special}",
        [{"role": "user", "content": "ping"}],
    )
    assert result["provider_diagnostics"]["ollama_node"] == "backup"
    assert result["fallback_from"] == f"ollama/{special}"
    assert result["fallback_to"] == client_chat.LOCAL_FALLBACK_MODEL
    assert result["choices"][0]["message"]["content"] == "fallback ok"

def test_health_capacity_metrics_require_federation_key(monkeypatch):
    from starlette.requests import Request
    from app.routes.health import _federation_metrics_allowed

    monkeypatch.setenv("FEDERATION_SECRET", "test-federation-secret")

    def request(headers):
        return Request({
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": headers,
            "query_string": b"",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        })

    assert _federation_metrics_allowed(request([])) is False
    assert _federation_metrics_allowed(
        request([(b"x-federation-key", b"wrong")])
    ) is False
    assert _federation_metrics_allowed(
        request([(b"x-federation-key", b"test-federation-secret")])
    ) is True
