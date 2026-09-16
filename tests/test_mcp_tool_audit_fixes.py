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
