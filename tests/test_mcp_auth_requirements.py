import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.utils import mcp_auth


def _build_request(path: str, client_host: str, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [(k.lower().encode('latin-1'), v.encode('latin-1')) for k, v in (headers or {}).items()]
    return Request({
        'type': 'http', 'http_version': '1.1', 'method': 'POST', 'scheme': 'https',
        'path': path, 'raw_path': path.encode('ascii'), 'query_string': b'',
        'headers': raw_headers, 'client': (client_host, 12345), 'server': ('testserver', 443), 'state': {},
    })


@pytest.mark.asyncio
async def test_require_mcp_auth_allows_public_guest_on_canonical_mcp_without_credentials(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_USER', 'user')
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_PASS', 'pass')
    request = _build_request('/v1/mcp', '203.0.113.10', {'X-Forwarded-Port': '9100'})
    assert await mcp_auth.require_mcp_auth(request) == 'public_guest'
    assert request.state.mcp_auth_method == 'public_guest'
    assert request.state.mcp_auth_full_access is False


@pytest.mark.asyncio
async def test_public_guest_does_not_depend_on_proxy_or_docker_source(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_USER', 'user')
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_PASS', 'pass')
    monkeypatch.setattr(mcp_auth, 'MCP_ALLOW_UNAUTHENTICATED_LOCAL', False)
    request = _build_request('/v1/mcp', '172.18.0.5')
    assert await mcp_auth.require_mcp_auth(request) == 'public_guest'
    assert request.state.mcp_auth_full_access is False


@pytest.mark.asyncio
async def test_non_mcp_protected_route_still_rejects_without_credentials(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_USER', 'user')
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_PASS', 'pass')
    request = _build_request('/v1/private-admin', '203.0.113.10', {'X-Forwarded-Port': '9100'})
    with pytest.raises(HTTPException) as exc_info:
        await mcp_auth.require_mcp_auth(request)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_invalid_bearer_never_falls_back_to_public_guest(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_USER', 'user')
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_PASS', 'pass')
    monkeypatch.setattr(mcp_auth, 'is_valid_token', lambda _token: False)
    monkeypatch.setattr(mcp_auth, '_validate_jwt', lambda _token: None)
    request = _build_request('/v1/mcp', '203.0.113.10', {'Authorization': 'Bearer wrong'})
    with pytest.raises(HTTPException) as exc_info:
        await mcp_auth.require_mcp_auth(request)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_bearer_authentication_without_write_scopes_is_not_full_access(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_USER', 'user')
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_PASS', 'pass')
    monkeypatch.setattr(mcp_auth, 'is_valid_token', lambda token: token == 'read-token')
    monkeypatch.setattr(mcp_auth, '_token_has_full_mcp_access', lambda _token: False)
    request = _build_request('/v1/mcp', '172.18.0.5', {'Authorization': 'Bearer read-token'})
    assert await mcp_auth.require_mcp_auth(request) == 'oauth_client'
    assert request.state.mcp_auth_method == 'bearer'
    assert request.state.mcp_auth_full_access is False


@pytest.mark.asyncio
async def test_existing_admin_bearer_still_gets_full_access(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_USER', 'user')
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_PASS', 'pass')
    monkeypatch.setattr(mcp_auth, 'is_valid_token', lambda token: token == 'admin-token')
    monkeypatch.setattr(mcp_auth, '_token_has_full_mcp_access', lambda _token: True)
    request = _build_request('/v1/mcp', '172.18.0.5', {'Authorization': 'Bearer admin-token'})
    assert await mcp_auth.require_mcp_auth(request) == 'oauth_client'
    assert request.state.mcp_auth_method == 'bearer'
    assert request.state.mcp_auth_full_access is True
