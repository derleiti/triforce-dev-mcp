from types import SimpleNamespace

from starlette.requests import Request

from app.routes import mcp
from app.utils.mcp_security import is_internal_full_request, is_tool_allowed


def _request(client_host: str = "172.18.0.10") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/v1/mcp/messages/",
        "raw_path": b"/v1/mcp/messages/",
        "query_string": b"",
        "headers": [],
        "client": (client_host, 12345),
        "server": ("api.ailinux.me", 443),
        "state": {},
    }
    return Request(scope)


def test_sse_session_restores_full_owner_authorization():
    handshake = _request()
    handshake.state.mcp_auth_user = "oauth_client"
    handshake.state.mcp_auth_method = "bearer"
    handshake.state.mcp_auth_full_access = True

    session = {
        "authenticated": True,
        "auth_user": handshake.state.mcp_auth_user,
        "auth_method": handshake.state.mcp_auth_method,
        "auth_full_access": handshake.state.mcp_auth_full_access,
    }
    message = _request("104.23.223.53")

    mcp._restore_session_request_state(session, message)
    assert message.state.mcp_auth_user == "oauth_client"
    assert message.state.mcp_auth_method == "bearer"
    assert message.state.mcp_auth_full_access is True
    assert is_internal_full_request(message) is True
    assert is_tool_allowed("shell", message, {"command": "true"}) is True


def test_sse_session_preserves_public_default_deny():
    handshake = _request()
    handshake.state.mcp_auth_user = "oauth_client"
    handshake.state.mcp_auth_method = "bearer"
    handshake.state.mcp_auth_full_access = False

    session = {
        "authenticated": True,
        "auth_user": handshake.state.mcp_auth_user,
        "auth_method": handshake.state.mcp_auth_method,
        "auth_full_access": handshake.state.mcp_auth_full_access,
    }
    message = _request("104.23.223.53")

    mcp._restore_session_request_state(session, message)
    assert message.state.mcp_auth_full_access is False
    assert is_internal_full_request(message) is False
    assert is_tool_allowed("shell", message, {"command": "true"}) is False
    assert is_tool_allowed("status", message, {}) is True


def test_incomplete_legacy_session_does_not_restore_or_grant_access():
    message = _request("104.23.223.53")
    mcp._restore_session_request_state({"authenticated": True}, message)
    assert is_internal_full_request(message) is False
    assert is_tool_allowed("shell", message, {}) is False


def test_legacy_sse_polls_disconnect_inside_graceful_shutdown_window():
    import inspect
    from app.routes.mcp import mcp_sse_connect

    source = inspect.getsource(mcp_sse_connect)
    assert "await request.is_disconnected()" in source
    assert "wait_timeout = min(1.0" in source
    assert "next_ping = loop.time() + 15.0" in source


def _delete_request(*, session_id: str = "", client_id: str = "client-A") -> Request:
    headers = []
    if session_id:
        headers.append((b"mcp-session-id", session_id.encode("ascii")))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "DELETE",
        "scheme": "https",
        "path": "/v1/mcp/sse",
        "raw_path": b"/v1/mcp/sse",
        "query_string": b"",
        "headers": headers,
        "client": ("172.18.0.10", 12345),
        "server": ("api.ailinux.me", 443),
        "state": {},
    }
    request = Request(scope)
    request.state.mcp_auth_client_id = client_id
    request.state.mcp_auth_user = "oauth_client"
    return request


def test_close_legacy_sse_session_wakes_queue_and_preserves_detached_transport(monkeypatch):
    session_id = "legacy-close-regression"
    previous = mcp._mcp_sessions.pop(session_id, None)
    detached = []
    try:
        session = mcp._get_session(session_id)
        monkeypatch.setattr(
            "app.services.mcp_workspace_sessions.mark_transport_detached",
            lambda sid: detached.append(sid) or None,
        )
        assert mcp._close_legacy_sse_session(session_id) is True
        assert session_id not in mcp._mcp_sessions
        assert session["queue"].get_nowait() is mcp._LEGACY_SSE_CLOSE
        assert detached == [session_id]
        assert mcp._close_legacy_sse_session(session_id) is False
    finally:
        mcp._mcp_sessions.pop(session_id, None)
        if previous is not None:
            mcp._mcp_sessions[session_id] = previous


def test_legacy_sse_identity_match_requires_stored_identity():
    request = _delete_request(client_id="client-A")
    assert mcp._legacy_sse_session_matches_request(
        {"auth_client_id": "client-A", "auth_user": "oauth_client"}, request
    ) is True
    assert mcp._legacy_sse_session_matches_request(
        {"auth_client_id": "client-B", "auth_user": "oauth_client"}, request
    ) is False
    assert mcp._legacy_sse_session_matches_request({}, request) is False


def test_legacy_sse_connect_exposes_session_header_and_delete_route_exists():
    import inspect

    connect_source = inspect.getsource(mcp.mcp_sse_connect)
    delete_source = inspect.getsource(mcp.mcp_sse_delete)
    assert '"Mcp-Session-Id": session_id' in connect_source
    assert "_close_legacy_sse_session(session_id)" in delete_source
    assert "Multiple legacy SSE sessions match" in delete_source


def test_legacy_sse_delete_with_session_header_closes_transport(monkeypatch):
    import asyncio

    session_id = "legacy-delete-header"
    previous = mcp._mcp_sessions.pop(session_id, None)

    async def allow(_request):
        return None

    monkeypatch.setattr(mcp, "require_mcp_auth", allow)
    try:
        session = mcp._get_session(session_id)
        session["auth_client_id"] = "client-A"
        session["auth_user"] = "oauth_client"
        response = asyncio.run(mcp.mcp_sse_delete(_delete_request(session_id=session_id)))
        assert response.status_code == 204
        assert response.headers["mcp-session-id"] == session_id
        assert session_id not in mcp._mcp_sessions
        assert session["queue"].get_nowait() is mcp._LEGACY_SSE_CLOSE
    finally:
        mcp._mcp_sessions.pop(session_id, None)
        if previous is not None:
            mcp._mcp_sessions[session_id] = previous


def test_legacy_sse_delete_without_id_only_closes_unique_identity_match(monkeypatch):
    import asyncio

    previous = dict(mcp._mcp_sessions)

    async def allow(_request):
        return None

    monkeypatch.setattr(mcp, "require_mcp_auth", allow)
    try:
        mcp._mcp_sessions.clear()
        own = mcp._get_session("own-session")
        own.update({"auth_client_id": "client-A", "auth_user": "oauth_client"})
        other = mcp._get_session("other-session")
        other.update({"auth_client_id": "client-B", "auth_user": "oauth_client"})

        response = asyncio.run(mcp.mcp_sse_delete(_delete_request(client_id="client-A")))
        assert response.status_code == 204
        assert response.headers["mcp-session-id"] == "own-session"
        assert "own-session" not in mcp._mcp_sessions
        assert "other-session" in mcp._mcp_sessions
    finally:
        mcp._mcp_sessions.clear()
        mcp._mcp_sessions.update(previous)


def test_legacy_sse_delete_rejects_ambiguous_identity_match(monkeypatch):
    import asyncio

    previous = dict(mcp._mcp_sessions)

    async def allow(_request):
        return None

    monkeypatch.setattr(mcp, "require_mcp_auth", allow)
    try:
        mcp._mcp_sessions.clear()
        for session_id in ("first-session", "second-session"):
            session = mcp._get_session(session_id)
            session.update({"auth_client_id": "client-A", "auth_user": "oauth_client"})

        response = asyncio.run(mcp.mcp_sse_delete(_delete_request(client_id="client-A")))
        assert response.status_code == 409
        assert "first-session" in mcp._mcp_sessions
        assert "second-session" in mcp._mcp_sessions
    finally:
        mcp._mcp_sessions.clear()
        mcp._mcp_sessions.update(previous)
