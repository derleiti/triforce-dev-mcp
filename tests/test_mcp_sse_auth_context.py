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
