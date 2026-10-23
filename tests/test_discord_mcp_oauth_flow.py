"""End-to-end isolated OAuth DCR/PKCE and MCP handshake tests."""
import base64
import hashlib
import re
from urllib.parse import urlencode, urlparse, parse_qs
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routes import discord_mcp_oauth as auth
from app.routes import discord_mcp_transport as mcp
from app.services.discord_mcp_oauth_store import DiscordMcpOAuthStore, RESOURCE
from app.services.discord_mcp_registry import DiscordRegistry


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    path = tmp_path / "oauth.sqlite3"
    original = DiscordRegistry(path)
    tokens = DiscordMcpOAuthStore(str(path))
    monkeypatch.setattr(auth, "registry", lambda: original)
    monkeypatch.setattr(auth, "store", lambda: tokens)
    monkeypatch.setattr(auth, "_ailinux_account", lambda request: "alice")
    monkeypatch.setattr(mcp, "registry", lambda: original)
    monkeypatch.setattr(mcp, "USER_REGISTRY", {"alice": {"tier": "user"}})
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(mcp.router)
    with TestClient(app, base_url="https://discord.ailinux.me", follow_redirects=False) as client:
        yield client, original, tokens


def _client_registration(client):
    r = client.post("/v1/discord-mcp/oauth2/register", json={
        "client_name": "ChatGPT local test",
        "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
        "token_endpoint_auth_method": "none",
    })
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _start(client, client_id):
    verifier = "A" * 60
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {
        "response_type": "code", "client_id": client_id,
        "redirect_uri": "https://chatgpt.com/connector_platform_oauth_redirect",
        "scope": "discord:read", "state": "strongrandomstateabcdefghijklmnopqrstuvwxyz",
        "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": RESOURCE,
    }
    r = client.get("/v1/discord-mcp/oauth2/authorize", params=q)
    assert r.status_code == 200, r.text
    assert "ChatGPT local test" in r.text
    assert r.headers["referrer-policy"] == "same-origin"
    assert '<meta name="referrer" content="same-origin">' in r.text
    assert "login.ailinux.me" in r.text
    match = re.search(r'name="csrf" value="([^"]+)"', r.text)
    assert match is not None
    assert len(match.group(1)) >= 32
    # No browser-wide consent cookie: concurrent authorizations must not clash.
    assert "dmcp_consent" not in client.cookies
    return verifier, q, match.group(1)


def test_dcr_authorize_token_refresh_and_mcp(scenario):
    client, _, _ = scenario
    client_id = _client_registration(client)
    verifier, q, csrf = _start(client, client_id)
    approved = client.post(
        "/v1/discord-mcp/oauth2/authorize/approve",
        data={"csrf": csrf, "allow": "yes"},
        headers={"Origin": "https://discord.ailinux.me"},
    )
    assert approved.status_code == 303, approved.text
    redirect = urlparse(approved.headers["location"])
    assert redirect.hostname == "chatgpt.com"
    args = parse_qs(redirect.query)
    assert args["state"][0] == q["state"]
    assert "code" in args
    exchange = client.post("/v1/discord-mcp/oauth2/token", data={
        "grant_type": "authorization_code", "client_id": client_id,
        "redirect_uri": q["redirect_uri"], "code": args["code"][0],
        "code_verifier": verifier, "resource": RESOURCE,
    })
    assert exchange.status_code == 200, exchange.text
    t = exchange.json()
    assert t["scope"] == "discord:read" and t["refresh_token"]
    replay = client.post("/v1/discord-mcp/oauth2/token", data={
        "grant_type": "authorization_code", "client_id": client_id,
        "redirect_uri": q["redirect_uri"], "code": args["code"][0], "code_verifier": verifier,
    })
    assert replay.status_code == 400
    headers = {"Authorization": "Bearer " + t["access_token"]}
    initialized = client.post("/v1/discord-mcp/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "tester", "version": "1"}},
    })
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "ailinux-discord-mcp"
    listed = client.post("/v1/discord-mcp/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    assert listed.status_code == 200
    assert len(listed.json()["result"]["tools"]) == 3
    status = client.post("/v1/discord-mcp/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "discord_connection_status", "arguments": {}},
    })
    assert status.status_code == 200
    assert status.json()["result"]["structuredContent"]["discord_linked"] is False
    refreshed = client.post("/v1/discord-mcp/oauth2/token", data={
        "grant_type": "refresh_token", "client_id": client_id,
        "refresh_token": t["refresh_token"], "resource": RESOURCE,
    })
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] != t["access_token"]


def test_mcp_discovery_and_auth_boundary(scenario):
    client, _, _ = scenario
    metadata = client.get("/v1/discord-mcp/oauth2/.well-known/oauth-protected-resource/mcp")
    assert metadata.status_code == 200
    assert metadata.json()["resource"] == RESOURCE
    oauth = client.get("/v1/discord-mcp/oauth2/.well-known/oauth-authorization-server")
    assert oauth.status_code == 200
    assert oauth.json()["registration_endpoint"] == "https://discord.ailinux.me/register"
    head = client.head("/v1/discord-mcp/mcp")
    assert head.status_code == 401
    assert "resource_metadata=" in head.headers.get("WWW-Authenticate", "")
    unauth = client.post("/v1/discord-mcp/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
    assert unauth.status_code == 401
    assert "resource_metadata=" in unauth.headers.get("WWW-Authenticate", "")
    origin = client.post("/v1/discord-mcp/mcp", headers={"Origin": "https://untrusted.example"},
                         json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
    assert origin.status_code == 403


def test_google_ailinux_sign_in_redirect_for_unauthenticated_user(scenario, monkeypatch):
    client, _, _ = scenario
    # One AILinux session per person; no Gmail credential is sent to ChatGPT.
    monkeypatch.setattr(auth, "_ailinux_account", lambda request: None)
    client_id = _client_registration(client)
    verifier = "A" * 60
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    query = {
        "response_type": "code", "client_id": client_id,
        "redirect_uri": "https://chatgpt.com/connector_platform_oauth_redirect",
        "scope": "discord:read", "state": "strongrandomstateabcdefghijklmnopqrstuvwxyz",
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": RESOURCE,
    }
    res = client.get("/v1/discord-mcp/oauth2/authorize", params=query)
    assert res.status_code == 302
    dest = urlparse(res.headers["location"])
    assert (dest.scheme, dest.netloc) == ("https", "login.ailinux.me")
    parameters = parse_qs(dest.query)
    assert parameters["google"] == ["1"]
    redirect = urlparse(parameters["redirect"][0])
    assert redirect.netloc == "discord.ailinux.me"
    assert redirect.path == "/authorize"
    assert "state=" in redirect.query


def test_null_origin_is_still_rejected(scenario):
    client, _, _ = scenario
    client_id = _client_registration(client)
    _, _, csrf = _start(client, client_id)
    blocked = client.post(
        "/v1/discord-mcp/oauth2/authorize/approve",
        data={"csrf": csrf, "allow": "yes"},
        headers={"Origin": "null"},
    )
    assert blocked.status_code == 403
    assert "Invalid request origin" in blocked.text
    # The invalid POST must not consume the authorization attempt.
    approved = client.post(
        "/v1/discord-mcp/oauth2/authorize/approve",
        data={"csrf": csrf, "allow": "yes"},
        headers={"Origin": "https://discord.ailinux.me"},
    )
    assert approved.status_code == 303


def test_redirect_integrity_and_dcr_validation(scenario):
    client, _, _ = scenario
    rejected = client.post("/v1/discord-mcp/oauth2/register", json={
        "client_name": "bad", "redirect_uris": ["https://chatgpt.com.evil.example/callback#anchor"],
    })
    assert rejected.status_code == 400
    client_id = _client_registration(client)
    invalid = client.get("/v1/discord-mcp/oauth2/authorize", params={
        "response_type": "code", "client_id": client_id,
        "redirect_uri": "https://evil.example/cb", "scope": "discord:read",
        "state": "a" * 25, "code_challenge": "c" * 43, "code_challenge_method": "S256",
    })
    assert invalid.status_code == 400


def test_consent_refuses_csrf_and_one_time(scenario):
    client, _, _ = scenario
    client_id = _client_registration(client)
    _, _, csrf = _start(client, client_id)
    rejected = client.post("/v1/discord-mcp/oauth2/authorize/approve",
                           data={"csrf": csrf, "allow": "yes"},
                           headers={"Origin": "https://evil.example"})
    assert rejected.status_code == 403
    first = client.post("/v1/discord-mcp/oauth2/authorize/approve", data={"csrf": csrf, "allow": "no"})
    assert first.status_code == 303
    assert "access_denied" in first.headers["location"]
    second = client.post("/v1/discord-mcp/oauth2/authorize/approve", data={"csrf": csrf, "allow": "yes"})
    assert second.status_code == 403


def test_two_ailinux_google_accounts_get_independent_mcp_tokens(scenario, monkeypatch):
    """After an AILinux login, OAuth tokens preserve the verified user identity."""
    client, db, tokens = scenario
    discord_alice = "123456789012345678"
    discord_bob = "223456789012345678"
    guild_alice = "323456789012345678"
    db.link("alice", discord_alice)
    db.link("bob", discord_bob)
    db.grant("alice", discord_alice, guild_alice, {"mcp:read"})
    monkeypatch.setattr(mcp, "USER_REGISTRY", {
        "alice": {"tier": "free"}, "bob": {"tier": "free"},
    })
    monkeypatch.setattr(
        auth, "_ailinux_account", lambda request: request.cookies.get("test_ailinux_identity"),
    )

    token_for = {}
    for account in ("alice", "bob"):
        client.cookies.set("test_ailinux_identity", account, domain="discord.ailinux.me")
        client_id = _client_registration(client)
        verifier, params, csrf = _start(client, client_id)
        redirect = client.post(
            "/v1/discord-mcp/oauth2/authorize/approve",
            data={"csrf": csrf, "allow": "yes"},
            headers={"Origin": "https://discord.ailinux.me"},
        )
        assert redirect.status_code == 303
        code = parse_qs(urlparse(redirect.headers["location"]).query)["code"][0]
        exchange = client.post("/v1/discord-mcp/oauth2/token", data={
            "grant_type": "authorization_code", "client_id": client_id,
            "redirect_uri": params["redirect_uri"], "code": code,
            "code_verifier": verifier,
            "resource": RESOURCE,
        })
        assert exchange.status_code == 200
        token_for[account] = exchange.json()["access_token"]
        claims = tokens.verify(token_for[account])
        assert claims["account"] == account
        assert claims["resource"] == RESOURCE

    assert token_for["alice"] != token_for["bob"]
    for account in ("alice", "bob"):
        response = client.post(
            "/v1/discord-mcp/mcp",
            headers={"Authorization": "Bearer " + token_for[account]},
            json={"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                  "params": {"name": "discord_authorized_guilds", "arguments": {}}},
        )
        assert response.status_code == 200
        servers = response.json()["result"]["structuredContent"]["servers"]
        if account == "alice":
            assert len(servers) == 1 and servers[0]["guild_id"] == guild_alice
        else:
            assert servers == []

    tokens.revoke_account("alice")
    revoked = client.post(
        "/v1/discord-mcp/mcp",
        headers={"Authorization": "Bearer " + token_for["alice"]},
        json={"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
    )
    assert revoked.status_code == 401
    still_authorized = client.post(
        "/v1/discord-mcp/mcp",
        headers={"Authorization": "Bearer " + token_for["bob"]},
        json={"jsonrpc": "2.0", "id": 12, "method": "tools/list"},
    )
    assert still_authorized.status_code == 200



def test_parallel_oauth_consent_tabs_do_not_share_mutable_cookie(scenario):
    client, _, _ = scenario
    client_id = _client_registration(client)
    _, _, first_csrf = _start(client, client_id)
    _, _, second_csrf = _start(client, client_id)
    assert first_csrf != second_csrf
    for csrf in (first_csrf, second_csrf):
        approved = client.post(
            "/v1/discord-mcp/oauth2/authorize/approve",
            data={"csrf": csrf, "allow": "yes"},
            headers={"Origin": "https://discord.ailinux.me", "Sec-Fetch-Site": "same-origin"},
        )
        assert approved.status_code == 303
        assert "code=" in approved.headers["location"]
    assert "dmcp_consent" not in client.cookies


def test_stale_legacy_consent_cookie_does_not_block_valid_page(scenario):
    client, _, _ = scenario
    # Earlier deployments used a single cookie for all OAuth tabs and
    # even set different path attributes; Chrome can retain both.
    client.cookies.set("dmcp_consent", "old-nonce", domain="discord.ailinux.me", path="/authorize")
    client.cookies.set("dmcp_consent", "another-old-nonce", domain="discord.ailinux.me", path="/")
    client_id = _client_registration(client)
    _, _, csrf = _start(client, client_id)
    approved = client.post(
        "/v1/discord-mcp/oauth2/authorize/approve",
        data={"csrf": csrf, "allow": "yes"},
        headers={"Origin": "https://discord.ailinux.me"},
    )
    assert approved.status_code == 303
    assert "code=" in approved.headers["location"]


def test_mcp_consent_nonce_required_one_time_and_account_bound(scenario, monkeypatch):
    client, _, _ = scenario
    client_id = _client_registration(client)
    _, _, csrf = _start(client, client_id)
    url = "/v1/discord-mcp/oauth2/authorize/approve"
    headers = {"Origin": "https://discord.ailinux.me"}
    missing = client.post(url, data={"allow": "yes"}, headers=headers)
    assert missing.status_code == 403
    random_nonce = client.post(url, data={"csrf": "z" * 43, "allow": "yes"}, headers=headers)
    assert random_nonce.status_code == 403
    wrong_site = client.post(url, data={"csrf": csrf, "allow": "yes"},
                             headers={**headers, "Sec-Fetch-Site": "cross-site"})
    assert wrong_site.status_code == 403
    # A stolen nonce cannot be approved under a different account.
    monkeypatch.setattr(auth, "_ailinux_account", lambda request: "bob")
    other_user = client.post(url, data={"csrf": csrf, "allow": "yes"}, headers=headers)
    assert other_user.status_code == 403
    monkeypatch.setattr(auth, "_ailinux_account", lambda request: "alice")
    approved = client.post(url, data={"csrf": csrf, "allow": "yes"}, headers=headers)
    assert approved.status_code == 303
    replay = client.post(url, data={"csrf": csrf, "allow": "yes"}, headers=headers)
    assert replay.status_code == 403
