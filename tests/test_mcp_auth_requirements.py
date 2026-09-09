import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.utils import mcp_auth


def _build_request(
    path: str,
    client_host: str,
    headers: dict[str, str] | None = None,
) -> Request:
    raw_headers = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": raw_headers,
        "client": (client_host, 12345),
        "server": ("testserver", 80),
        "state": {},
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_require_mcp_auth_rejects_external_request_without_credentials(monkeypatch):
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_USER", "user")
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_PASS", "pass")

    request = _build_request(
        "/v1/mcp",
        "203.0.113.10",
        {"X-Forwarded-Port": "9100"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await mcp_auth.require_mcp_auth(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"


@pytest.mark.asyncio
async def test_require_mcp_auth_rejects_docker_internal_without_credentials(monkeypatch):
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_USER", "user")
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_PASS", "pass")
    monkeypatch.setattr(mcp_auth, "MCP_ALLOW_UNAUTHENTICATED_LOCAL", False)

    request = _build_request("/v1/mcp", "172.18.0.5")

    with pytest.raises(HTTPException) as exc_info:
        await mcp_auth.require_mcp_auth(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"


@pytest.mark.asyncio
async def test_require_mcp_auth_does_not_bypass_forwarded_internal_requests(monkeypatch):
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_USER", "user")
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_PASS", "pass")

    request = _build_request(
        "/v1/mcp",
        "172.18.0.5",
        {"X-Forwarded-For": "198.51.100.42"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await mcp_auth.require_mcp_auth(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"


@pytest.mark.asyncio
async def test_bearer_authentication_without_write_scopes_is_not_full_access(monkeypatch):
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_USER", "user")
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_PASS", "pass")
    monkeypatch.setattr(mcp_auth, "is_valid_token", lambda token: token == "read-token")
    monkeypatch.setattr(mcp_auth, "_token_has_full_mcp_access", lambda token: False)

    request = _build_request(
        "/v1/mcp",
        "172.18.0.5",
        {"X-Forwarded-Port": "9100", "Authorization": "Bearer read-token"},
    )

    assert await mcp_auth.require_mcp_auth(request) == "oauth_client"
    assert request.state.mcp_auth_method == "bearer"
    assert request.state.mcp_auth_full_access is False


@pytest.mark.asyncio
async def test_bearer_with_explicit_write_and_tool_scopes_gets_full_access(monkeypatch):
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_USER", "user")
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_PASS", "pass")
    monkeypatch.setattr(mcp_auth, "is_valid_token", lambda token: token == "admin-token")
    monkeypatch.setattr(mcp_auth, "_token_has_full_mcp_access", lambda token: True)

    request = _build_request(
        "/v1/mcp",
        "172.18.0.5",
        {"X-Forwarded-Port": "9100", "Authorization": "Bearer admin-token"},
    )

    assert await mcp_auth.require_mcp_auth(request) == "oauth_client"
    assert request.state.mcp_auth_method == "bearer"
    assert request.state.mcp_auth_full_access is True
