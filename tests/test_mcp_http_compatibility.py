"""Wire-level regression gate for deployed MCP clients, without starting agents."""
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import VERSION
from app.mcp.compatibility import (
    HANDSHAKE_PROTOCOL_VERSIONS,
    MODERN_PROTOCOL_VERSION,
    SERVER_INFO_META_KEY,
)
from app.routes import mcp, mcp_remote
from app.utils import mcp_auth
from app.utils.triforce_logging import multi_logger


@pytest.fixture
def mcp_app(monkeypatch):
    monkeypatch.setattr(mcp, "_mcp_sessions", {})
    monkeypatch.setattr(mcp_auth, "MCP_ALLOW_UNAUTHENTICATED_LOCAL", False)
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_USER", "test-user")
    monkeypatch.setattr(mcp_auth, "MCP_AUTH_PASS", "test-password")
    monkeypatch.setenv("MCP_OAUTH_USER", "test-user")
    monkeypatch.setenv("MCP_OAUTH_PASS", "test-password")
    monkeypatch.setattr(multi_logger, "log_mcp", AsyncMock())
    app = FastAPI()
    # Match production router precedence, without lifespan/external services.
    app.include_router(mcp.public_router, prefix="/v1")
    app.include_router(mcp.router, prefix="/v1")
    app.include_router(mcp_remote.router)
    return app


def transport(app):
    return ASGITransport(app=app, client=("203.0.113.20", 12345))


def rpc(method, params=None):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


def assert_guest_catalog(response):
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"chat", "models", "aihelper_pair", "file_read"} <= names
    assert {"shell", "remote_exec", "service_control"}.isdisjoint(names)
    assert len(names) == len(tools)
    by_name = {tool["name"]: tool for tool in tools}
    assert by_name["file_read"]["x_requires_workspace"] is True


@pytest.mark.parametrize("version", HANDSHAKE_PROTOCOL_VERSIONS)
async def test_canonical_http_initialize_list_delete(mcp_app, version):
    async with AsyncClient(transport=transport(mcp_app), base_url="https://test.invalid") as client:
        reply = await client.post("/v1/mcp", json=rpc("initialize", {
            "protocolVersion": version,
            "clientInfo": {"name": "AILinux Helper", "version": "legacy-compatible"},
            "capabilities": {},
        }))
        assert reply.status_code == 200
        result = reply.json()["result"]
        assert result["protocolVersion"] == version
        assert result["serverInfo"]["version"] == VERSION
        session = reply.headers["Mcp-Session-Id"]
        headers = {"Mcp-Session-Id": session, "MCP-Protocol-Version": version}
        try:
            assert_guest_catalog(await client.post("/v1/mcp", headers=headers, json=rpc("tools/list")))
        finally:
            assert (await client.delete("/v1/mcp", headers=headers)).status_code == 200
        assert session not in mcp._mcp_sessions


async def test_modern_stateless_discovery_and_list(mcp_app):
    headers = {"MCP-Protocol-Version": MODERN_PROTOCOL_VERSION}
    async with AsyncClient(transport=transport(mcp_app), base_url="https://test.invalid", headers=headers) as client:
        reply = await client.post("/v1/mcp", json=rpc("server/discover"))
        assert reply.status_code == 200
        result = reply.json()["result"]
        assert set(result["supportedVersions"]) == set((*HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSION))
        assert result["_meta"][SERVER_INFO_META_KEY]["version"] == VERSION
        listing = await client.post("/v1/mcp", json=rpc("tools/list"))
        assert_guest_catalog(listing)
        assert listing.json()["result"]["_meta"][SERVER_INFO_META_KEY]["version"] == VERSION


@pytest.mark.parametrize("path", ["/v1/mcp/sse", "/v1/sse", "/v1/mcp/sse/"])
async def test_legacy_sse_post_and_messages_alias(mcp_app, path):
    async with AsyncClient(transport=transport(mcp_app), base_url="https://test.invalid") as client:
        reply = await client.post(path, headers={"Accept": "text/event-stream"}, json=rpc(
            "initialize", {"protocolVersion": "2024-11-05"}
        ))
        assert reply.status_code == 200
        assert "text/event-stream" in reply.headers["content-type"]
        assert "event: message" in reply.text
        assert "2024-11-05" in reply.text
        sid = reply.headers["Mcp-Session-Id"]
        try:
            for messages in ("/v1/mcp/messages", "/v1/messages"):
                assert_guest_catalog(await client.post(messages, params={"session_id": sid}, json=rpc("tools/list")))
        finally:
            assert (await client.delete("/v1/mcp", headers={"Mcp-Session-Id": sid})).status_code == 200


@pytest.mark.parametrize("path", ["/v1/mcp/service", "/mcp/claude", "/mcp/codex", "/mcp/gemini"])
async def test_protected_endpoints_reject_public_requests(mcp_app, path):
    async with AsyncClient(transport=transport(mcp_app), base_url="https://test.invalid") as client:
        reply = await client.post(path, json=rpc("initialize"))
        assert reply.status_code == 401


@pytest.mark.parametrize("provider", ["claude", "codex", "gemini"])
async def test_direct_agent_legacy_init_keeps_wire_contract(mcp_app, monkeypatch, provider):
    from app.services.tristar.agent_controller import agent_controller

    monkeypatch.setattr(agent_controller, "get_agent", AsyncMock(return_value={"name": provider}))
    async with AsyncClient(
        transport=transport(mcp_app), base_url="https://test.invalid",
        auth=("test-user", "test-password"),
    ) as client:
        reply = await client.post("/mcp/" + provider, json=rpc("initialize", {
            "protocolVersion": "2024-11-05",
        }))
        assert reply.status_code == 200
        result = reply.json()["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["version"] == VERSION
        assert result["capabilities"] == {"tools": {"listChanged": False}}
        listing = await client.post("/mcp/" + provider, json=rpc("tools/list"))
        assert listing.status_code == 200
        assert {"agent/status", "agent/call"} <= {
            row["name"] for row in listing.json()["result"]["tools"]
        }


async def test_helper_metadata_get_and_head(mcp_app, monkeypatch):
    manifest = {"schema_version": 1, "helper_version": "2.90.40", "artifacts": {}}
    catalog = {"latest_version": "2.90.40", "manifest_schema": 1}
    monkeypatch.setattr(mcp, "_helper_release_manifest", lambda: manifest)
    monkeypatch.setattr(mcp, "_helper_release_catalog", lambda: catalog)
    async with AsyncClient(transport=transport(mcp_app), base_url="https://test.invalid") as client:
        for path, expected in (("release.json", manifest), ("releases", catalog)):
            url = "/v1/mcp/helper/" + path
            reply = await client.get(url)
            assert reply.json() == expected
            head = await client.head(url)
            assert head.status_code == 200
            assert head.content == b""
            assert head.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("role,expected_full", [("human_owner", True), ("human_member", False)])
async def test_aicoder_jwt_discovery_respects_authority(mcp_app, monkeypatch, role, expected_full):
    from app.utils.auth_middleware import AuthMiddleware

    mcp_app.add_middleware(AuthMiddleware)
    monkeypatch.setattr(mcp_auth, "is_valid_token", lambda token: False)
    monkeypatch.setattr(
        mcp_auth, "_validate_jwt",
        lambda token: {"email": "test@example.invalid", "client_id": "aicoder-test"}
        if token == "aicoder-test-jwt" else None,
    )
    monkeypatch.setattr(mcp_auth, "_jwt_authority", lambda payload: (role, 100 if expected_full else 20, "test"))
    headers = {
        "Authorization": "Bearer aicoder-test-jwt",
        "X-Client-Profile": "ai-coder",
        "X-Forwarded-Port": "9100",
    }
    async with AsyncClient(transport=transport(mcp_app), base_url="https://test.invalid", headers=headers) as client:
        reply = await client.post("/v1/mcp", json=rpc("initialize", {"protocolVersion": "2025-11-25"}))
        assert reply.status_code == 200
        session = reply.headers["Mcp-Session-Id"]
        try:
            listing = await client.post("/v1/mcp", headers={"Mcp-Session-Id": session}, json=rpc("tools/list"))
            assert listing.status_code == 200
            names = {tool["name"] for tool in listing.json()["result"]["tools"]}
            assert {"chat", "models", "aihelper_pair"} <= names
            assert ("shell" in names) is expected_full
        finally:
            assert (await client.delete("/v1/mcp", headers={"Mcp-Session-Id": session})).status_code == 200
        invalid = await client.post("/v1/mcp", headers={"Authorization": "Bearer invalid"}, json=rpc("tools/list"))
        assert invalid.status_code == 401


async def test_authenticated_service_bridge_preserves_session_lifecycle(mcp_app):
    async with AsyncClient(transport=transport(mcp_app), base_url="https://test.invalid",
                           auth=("test-user", "test-password")) as client:
        reply = await client.post("/v1/mcp/service", json=rpc("initialize", {"protocolVersion": "2025-11-25"}))
        assert reply.status_code == 200
        assert reply.json()["result"]["serverInfo"]["version"] == VERSION
        session = reply.headers["Mcp-Session-Id"]
        try:
            listing = await client.post("/v1/mcp/service", headers={"Mcp-Session-Id": session}, json=rpc("tools/list"))
            assert listing.status_code == 200
            assert listing.json()["result"]["tools"]
        finally:
            assert (await client.delete("/v1/mcp/service", headers={"Mcp-Session-Id": session})).status_code == 200
