import pytest

from app.services.mistral_agent import MistralAgentService


@pytest.mark.asyncio
async def test_start_pins_agent_version(monkeypatch):
    service = MistralAgentService()
    captured = {}

    class Settings:
        mistral_api_key = "key"
        mistral_agent_id = "agent-1"
        nova_mistral_agent_id = None
        mistral_agent_version = 5
        mistral_agent_base_url = "https://api.mistral.ai"
        mistral_agent_timeout_seconds = 180
        mistral_agent_store = True

    monkeypatch.setattr("app.services.mistral_agent.get_settings", lambda: Settings())

    async def fake_request(method, path, *, payload=None, timeout=None):
        captured.update({"method": method, "path": path, "payload": payload})
        return {"conversation_id": "conv-1", "outputs": [{"content": "ok"}]}

    monkeypatch.setattr(service, "_request", fake_request)
    result = await service.start("hello")
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/conversations"
    assert captured["payload"]["agent_id"] == "agent-1"
    assert captured["payload"]["agent_version"] == 5
    assert captured["payload"]["handoff_execution"] == "server"
    assert result["response"] == "ok"


@pytest.mark.asyncio
async def test_append_uses_existing_conversation(monkeypatch):
    service = MistralAgentService()

    class Settings:
        mistral_api_key = "key"
        mistral_agent_id = "agent-1"
        nova_mistral_agent_id = None
        mistral_agent_version = 5
        mistral_agent_base_url = "https://api.mistral.ai"
        mistral_agent_timeout_seconds = 180
        mistral_agent_store = True

    monkeypatch.setattr("app.services.mistral_agent.get_settings", lambda: Settings())
    calls = []

    async def fake_request(method, path, *, payload=None, timeout=None):
        calls.append((method, path, payload))
        return {"conversation_id": "conv-1", "outputs": [{"content": [{"text": "continued"}]}]}

    monkeypatch.setattr(service, "_request", fake_request)
    result = await service.append("conv-1", "next")
    assert calls[0][1] == "/v1/conversations/conv-1"
    assert result["response"] == "continued"


@pytest.mark.asyncio
async def test_review_detects_verdict(monkeypatch):
    service = MistralAgentService()

    async def fake_start(*args, **kwargs):
        return {"conversation_id": "conv-r", "response": "Looks good\nVERDICT: PASS"}

    monkeypatch.setattr(service, "start", fake_start)
    result = await service.review("Implement X", diff="+done", tests="3 passed")
    assert result["verdict"] == "PASS"
