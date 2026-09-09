from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.utils import mcp_auth


def _request(client: str = "127.0.0.1", headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/v1/mcp",
        "headers": headers or [],
        "query_string": b"",
        "client": (client, 12345),
        "server": ("127.0.0.1", 9100),
        "scheme": "http",
    })


def test_local_mcp_requires_auth_by_default(monkeypatch):
    monkeypatch.setattr(mcp_auth, "MCP_ALLOW_UNAUTHENTICATED_LOCAL", False)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(mcp_auth.require_mcp_auth(_request()))
    assert caught.value.status_code == 401


def test_local_bypass_requires_explicit_switch_and_loopback(monkeypatch):
    monkeypatch.setattr(mcp_auth, "MCP_ALLOW_UNAUTHENTICATED_LOCAL", True)
    req = _request()
    assert asyncio.run(mcp_auth.require_mcp_auth(req)) == "internal"
    assert req.state.mcp_auth_method == "explicit-local-bypass"


def test_explicit_local_bypass_never_trusts_forwarded_request(monkeypatch):
    monkeypatch.setattr(mcp_auth, "MCP_ALLOW_UNAUTHENTICATED_LOCAL", True)
    req = _request(headers=[(b"x-forwarded-for", b"203.0.113.10")])
    with pytest.raises(HTTPException) as caught:
        asyncio.run(mcp_auth.require_mcp_auth(req))
    assert caught.value.status_code == 401
