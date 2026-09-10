from __future__ import annotations

import asyncio
from starlette.requests import Request

from app.utils import mcp_auth


def _request(client: str = '127.0.0.1', headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({
        'type': 'http', 'method': 'POST', 'path': '/v1/mcp', 'headers': headers or [],
        'query_string': b'', 'client': (client, 12345), 'server': ('127.0.0.1', 9100), 'scheme': 'http', 'state': {},
    })


def test_local_mcp_is_public_guest_by_default_when_no_credentials(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_ALLOW_UNAUTHENTICATED_LOCAL', False)
    req = _request()
    assert asyncio.run(mcp_auth.require_mcp_auth(req)) == 'public_guest'
    assert req.state.mcp_auth_full_access is False


def test_local_bypass_requires_explicit_switch_and_loopback(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_ALLOW_UNAUTHENTICATED_LOCAL', True)
    req = _request()
    assert asyncio.run(mcp_auth.require_mcp_auth(req)) == 'internal'
    assert req.state.mcp_auth_method == 'explicit-local-bypass'


def test_forwarded_loopback_request_never_gets_internal_bypass(monkeypatch):
    monkeypatch.setattr(mcp_auth, 'MCP_ALLOW_UNAUTHENTICATED_LOCAL', True)
    req = _request(headers=[(b'x-forwarded-for', b'203.0.113.10')])
    assert asyncio.run(mcp_auth.require_mcp_auth(req)) == 'public_guest'
    assert req.state.mcp_auth_full_access is False
