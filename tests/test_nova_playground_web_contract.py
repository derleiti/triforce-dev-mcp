from app.routes.nova_playground import PlaygroundRequest, _choose_mode


def test_auto_plain_chat_does_not_search():
    assert _choose_mode("Erklaere mir systemd", None, "auto", ["web_search", "crawl_url"])[0] == "chat"


def test_auto_search_on_explicit_research_intent():
    assert _choose_mode("Recherchiere aktuelle WordPress Updates", None, "auto", ["web_search", "crawl_url"])[0] == "search"


def test_auto_crawl_on_url():
    mode, url = _choose_mode("Lies https://example.com/test und erklaere es", None, "auto", ["web_search", "crawl_url"])
    assert mode == "fetch"
    assert url == "https://example.com/test"


def test_tools_can_disable_web_access():
    assert _choose_mode("Suche im Web nach Linux", None, "auto", [])[0] == "chat"


def test_request_defaults_are_isolated():
    a = PlaygroundRequest(message="a")
    b = PlaygroundRequest(message="b")
    a.history.append({"role": "user", "content": "x"})
    assert b.history == []


def test_max_results_is_bounded():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlaygroundRequest(message="x", max_results=0)
    with pytest.raises(ValidationError):
        PlaygroundRequest(message="x", max_results=9)
    assert PlaygroundRequest(message="x", max_results=8).max_results == 8


def test_response_activity_defaults_are_isolated():
    from app.routes.nova_playground import PlaygroundResponse

    a = PlaygroundResponse(ok=True, mode="chat", answer="a")
    b = PlaygroundResponse(ok=True, mode="chat", answer="b")
    a.activity.append({"type": "model", "name": "x", "status": "completed"})
    assert b.activity == []


async def test_private_crawl_targets_are_rejected():
    from app.routes.nova_playground import _is_safe_public_url

    assert await _is_safe_public_url("http://127.0.0.1/") is False
    assert await _is_safe_public_url("http://localhost/") is False
    assert await _is_safe_public_url("file:///etc/passwd") is False


async def test_search_response_reports_verified_activity(monkeypatch):
    from app.routes import nova_playground as mod

    async def fake_search(*_args, **_kwargs):
        return [{"title": "Example", "url": "https://example.com/", "snippet": "result"}]

    async def fake_llm(*_args, **_kwargs):
        return "answer", "mistral/test"

    monkeypatch.setattr(mod, "_web_search", fake_search)
    monkeypatch.setattr(mod, "_llm_answer", fake_llm)
    result = await mod.nova_playground(PlaygroundRequest(message="search the web for example", mode="search"))
    assert result["mode"] == "search"
    assert result["activity"][0] == {"type": "tool", "name": "web_search", "status": "completed", "result_count": 1}
    assert result["activity"][1]["name"] == "mistral/test"


async def test_fetch_response_reports_verified_activity(monkeypatch):
    from app.routes import nova_playground as mod

    async def fake_fetch(_url):
        return "page text"

    async def fake_llm(*_args, **_kwargs):
        return "answer", "mistral/test"

    monkeypatch.setattr(mod, "_fetch_url", fake_fetch)
    monkeypatch.setattr(mod, "_llm_answer", fake_llm)
    result = await mod.nova_playground(PlaygroundRequest(message="Read https://example.com/", mode="fetch"))
    assert result["mode"] == "fetch"
    assert result["activity"][0]["name"] == "crawl_url"
    assert result["activity"][0]["status"] == "completed"
