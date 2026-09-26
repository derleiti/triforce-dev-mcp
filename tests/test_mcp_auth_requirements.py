import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.utils import mcp_auth


def _build_request(path: str, client_host: str, headers: dict[str, str] | None = None, *, method: str = 'POST') -> Request:
    raw_headers = [(k.lower().encode('latin-1'), v.encode('latin-1')) for k, v in (headers or {}).items()]
    return Request({
        'type': 'http', 'http_version': '1.1', 'method': method, 'scheme': 'https',
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
async def test_public_guest_can_delete_canonical_mcp_transport(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_USER', 'user')
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_PASS', 'pass')
    request = _build_request('/v1/mcp', '203.0.113.10', method='DELETE')
    assert await mcp_auth.require_mcp_auth(request) == 'public_guest'
    assert request.state.mcp_auth_method == 'public_guest'


@pytest.mark.asyncio
async def test_public_guest_can_delete_legacy_sse_transport(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_USER', 'user')
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_PASS', 'pass')
    request = _build_request('/v1/mcp/sse', '203.0.113.10', method='DELETE')
    assert await mcp_auth.require_mcp_auth(request) == 'public_guest'


@pytest.mark.asyncio
async def test_public_guest_can_still_get_legacy_sse_transport(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_USER', 'user')
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_PASS', 'pass')
    request = _build_request('/v1/mcp/sse', '203.0.113.10', method='GET')
    assert await mcp_auth.require_mcp_auth(request) == 'public_guest'



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


@pytest.mark.asyncio
async def test_bearer_workspace_subject_comes_only_from_trusted_token_metadata(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_USER', 'user')
    monkeypatch.setattr(mcp_auth, 'MCP_AUTH_PASS', 'pass')
    monkeypatch.setattr(mcp_auth, 'is_valid_token', lambda token: token == 'bridge-token')
    monkeypatch.setattr(mcp_auth, '_token_has_full_mcp_access', lambda _token: False)
    monkeypatch.setattr(mcp_auth, 'get_token_metadata', lambda _token: {
        'user': 'nova-telegram-bridge',
        'client_id': 'nova-telegram-bridge-mcp',
        'workspace_subject': 'server-issued-stable-subject',
    })
    request = _build_request('/v1/mcp', '172.18.0.5', {
        'Authorization': 'Bearer bridge-token',
        'X-TriForce-Workspace-Subject': 'attacker-controlled-value',
    })
    assert await mcp_auth.require_mcp_auth(request) == 'oauth_client'
    assert request.state.mcp_auth_user == 'nova-telegram-bridge'
    assert request.state.mcp_auth_client_id == 'nova-telegram-bridge-mcp'
    assert request.state.mcp_workspace_subject == 'server-issued-stable-subject'
    assert request.state.mcp_auth_full_access is False


def test_service_mcp_alias_is_not_public_guest():
    from app.utils.mcp_auth import _is_public_guest_mcp_path
    assert _is_public_guest_mcp_path("/v1/mcp") is True
    assert _is_public_guest_mcp_path("/v1/mcp/service") is False

def test_auth_middleware_defers_mcp_bearer_to_dual_auth_dependency(monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from app.utils.auth_middleware import AuthMiddleware

    monkeypatch.setattr(mcp_auth, "MCP_AUTH_USER", "user")
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_PASS", "pass")
    monkeypatch.setattr(mcp_auth, "is_valid_token", lambda _token: False)
    monkeypatch.setattr(
        mcp_auth,
        "_validate_jwt",
        lambda token: {
            "email": "admin@example.test",
            "client_id": "client-admin",
            "authority_role": "human_owner",
        } if token == "valid-aicoder-jwt" else None,
    )
    monkeypatch.setattr(
        mcp_auth,
        "_jwt_authority",
        lambda _payload: ("human_owner", 100, "test"),
    )

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.post("/v1/mcp")
    async def mcp_probe(request: Request):
        user = await mcp_auth.require_mcp_auth(request)
        return {
            "user": user,
            "method": request.state.mcp_auth_method,
            "full": request.state.mcp_auth_full_access,
        }

    client = TestClient(app)
    response = client.post(
        "/v1/mcp",
        headers={
            "Authorization": "Bearer valid-aicoder-jwt",
            "X-Forwarded-Port": "9100",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "user": "admin@example.test",
        "method": "jwt",
        "full": True,
    }


def test_auth_middleware_mcp_deferral_does_not_accept_invalid_bearer(monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from app.utils.auth_middleware import AuthMiddleware

    monkeypatch.setattr(mcp_auth, "MCP_AUTH_USER", "user")
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_PASS", "pass")
    monkeypatch.setattr(mcp_auth, "is_valid_token", lambda _token: False)
    monkeypatch.setattr(mcp_auth, "_validate_jwt", lambda _token: None)

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.post("/v1/mcp")
    async def mcp_probe(request: Request):
        await mcp_auth.require_mcp_auth(request)
        return {"ok": True}

    client = TestClient(app)
    response = client.post(
        "/v1/mcp",
        headers={
            "Authorization": "Bearer invalid-token",
            "X-Forwarded-Port": "9100",
        },
    )
    assert response.status_code == 401


def test_auth_middleware_defers_notify_network_prefix_to_route_jwt_auth():
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient
    from app.utils.auth_middleware import AuthMiddleware

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/v1/notify-network/directory")
    async def directory_probe(authorization: str | None = Header(None)):
        if authorization != "Bearer valid-aicoder-jwt":
            raise HTTPException(401, "Invalid token")
        return {"ok": True}

    client = TestClient(app)
    response = client.get(
        "/v1/notify-network/directory",
        headers={
            "Authorization": "Bearer valid-aicoder-jwt",
            "X-Forwarded-Port": "9100",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_notify_network_prefix_deferral_remains_route_authenticated():
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient
    from app.utils.auth_middleware import AuthMiddleware

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.post("/v1/notify-network/presence")
    async def presence_probe(authorization: str | None = Header(None)):
        if authorization != "Bearer valid-aicoder-jwt":
            raise HTTPException(401, "Invalid token")
        return {"ok": True}

    client = TestClient(app)
    missing = client.post(
        "/v1/notify-network/presence",
        headers={"X-Forwarded-Port": "9100"},
    )
    invalid = client.post(
        "/v1/notify-network/presence",
        headers={
            "Authorization": "Bearer wrong",
            "X-Forwarded-Port": "9100",
        },
    )
    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_notify_network_deferral_is_prefix_boundary_aware():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.utils.auth_middleware import AuthMiddleware

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/v1/notify-network-evil")
    async def similarly_named_route():
        return {"ok": True}

    response = TestClient(app).get(
        "/v1/notify-network-evil",
        headers={"X-Forwarded-Port": "9100"},
    )
    assert response.status_code == 401
