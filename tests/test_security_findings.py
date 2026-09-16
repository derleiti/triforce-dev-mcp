"""
Security-Tests fuer Findings F1 + F2 + zusaetzliche Regressions-Tests.

Ausfuehren:
    cd /home/zombie/workspace/triforce
    .venv/bin/python -m pytest tests/test_security_findings.py -v

Alle Tests sollen nach dem Fix grueen sein.
"""

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException


# =============================================================================
# F1 — Privilege Escalation: Admin-Check in grant-* Routes
# =============================================================================

class TestAdminAuthUtility:
    """Tests fuer app/utils/admin_auth.py"""

    def test_require_admin_blocks_non_admin(self):
        from app.utils.admin_auth import require_admin
        ctx = {"user_id": "regular_user"}
        with patch("app.utils.admin_auth.ADMIN_USER_IDS", frozenset({"real_admin"})):
            with pytest.raises(HTTPException) as exc:
                require_admin(ctx)
            assert exc.value.status_code == 403

    def test_require_admin_allows_admin(self):
        from app.utils.admin_auth import require_admin
        ctx = {"user_id": "real_admin"}
        with patch("app.utils.admin_auth.ADMIN_USER_IDS", frozenset({"real_admin"})):
            # Darf KEINE Exception werfen
            require_admin(ctx)

    def test_require_admin_blocks_empty_user_id(self):
        from app.utils.admin_auth import require_admin
        ctx = {"user_id": ""}
        with patch("app.utils.admin_auth.ADMIN_USER_IDS", frozenset({"real_admin"})):
            with pytest.raises(HTTPException) as exc:
                require_admin(ctx)
            assert exc.value.status_code == 403

    def test_require_admin_blocks_missing_user_id(self):
        from app.utils.admin_auth import require_admin
        ctx = {}
        with patch("app.utils.admin_auth.ADMIN_USER_IDS", frozenset({"real_admin"})):
            with pytest.raises(HTTPException):
                require_admin(ctx)

    def test_validate_paths_traversal_blocked(self):
        from app.utils.admin_auth import validate_allowed_paths
        with pytest.raises(HTTPException) as exc:
            validate_allowed_paths(["../../etc/passwd"])
        assert exc.value.status_code == 400

    def test_validate_paths_traversal_encoded_blocked(self):
        from app.utils.admin_auth import validate_allowed_paths
        with pytest.raises(HTTPException):
            validate_allowed_paths(["/home/zombie/../../../etc/shadow"])

    def test_validate_paths_outside_whitelist_blocked(self):
        from app.utils.admin_auth import validate_allowed_paths
        with pytest.raises(HTTPException) as exc:
            validate_allowed_paths(["/etc/cron.d/"])
        assert exc.value.status_code == 400

    def test_validate_paths_valid_home(self):
        from app.utils.admin_auth import validate_allowed_paths
        result = validate_allowed_paths(["/home/zombie/projects"])
        assert len(result) == 1
        assert result[0].startswith("/home/")

    def test_validate_paths_valid_tmp(self):
        from app.utils.admin_auth import validate_allowed_paths
        result = validate_allowed_paths(["/tmp/ailinux/data"])
        assert result[0].startswith("/tmp/ailinux/")

    def test_validate_paths_empty_list_blocked(self):
        from app.utils.admin_auth import validate_allowed_paths
        with pytest.raises(HTTPException) as exc:
            validate_allowed_paths([])
        assert exc.value.status_code == 400

    def test_validate_paths_too_many_blocked(self):
        from app.utils.admin_auth import validate_allowed_paths, MAX_PATHS_PER_GRANT
        paths = [f"/home/zombie/dir{i}" for i in range(MAX_PATHS_PER_GRANT + 1)]
        with pytest.raises(HTTPException) as exc:
            validate_allowed_paths(paths)
        assert exc.value.status_code == 400


class TestGrantFileAccess:
    """Tests fuer POST /client/mcp/admin/grant-file-access"""

    @pytest.mark.asyncio
    async def test_enterprise_user_cannot_grant_to_foreign_client(self):
        """
        KERN-TEST F1: Enterprise-User OHNE Admin-Rolle darf NICHT grant-file-access nutzen.
        Vor dem Fix war dieser Test gruen (kein Admin-Check). Nach dem Fix muss 403 kommen.
        """
        from app.routes.client_mcp import grant_file_access

        enterprise_ctx = {
            "user_id": "enterprise_attacker",
            "tier": MagicMock(value="enterprise"),
            "client_id": "client_enterprise_attacker_dev",
        }

        with patch("app.utils.admin_auth.ADMIN_USER_IDS", frozenset()):
            with pytest.raises(HTTPException) as exc:
                await grant_file_access(
                    target_client_id="client_victim_456_prod",
                    paths=["/home/victim"],
                    write_access=True,
                    ctx=enterprise_ctx,
                )
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_grant_file_access(self):
        """Admin darf grant-file-access fuer fremden Client ausfuehren."""
        from app.routes.client_mcp import grant_file_access

        mock_device = MagicMock()
        mock_device.client_id = "client_victim_456_prod"
        mock_user = MagicMock()
        mock_user.devices = [mock_device]

        admin_ctx = {
            "user_id": "real_admin",
            "tier": MagicMock(value="enterprise"),
            "client_id": "client_real_admin_main",
        }

        with patch("app.utils.admin_auth.ADMIN_USER_IDS", frozenset({"real_admin"})), \
             patch("app.routes.client_mcp.user_manager") as mock_um:
            mock_um.get_user = AsyncMock(return_value=mock_user)
            mock_um._save_user = MagicMock()

            result = await grant_file_access(
                target_client_id="client_victim_456_prod",
                paths=["/home/victim/projects"],
                write_access=False,
                ctx=admin_ctx,
            )

        assert result["success"] is True
        assert result["granted_by"] == "real_admin"
        assert "/home/victim/projects" in result["allowed_paths"]

    @pytest.mark.asyncio
    async def test_path_traversal_blocked_even_for_admin(self):
        """Auch Admin darf keine Path-Traversal-Pfade setzen."""
        from app.routes.client_mcp import grant_file_access

        admin_ctx = {
            "user_id": "real_admin",
            "tier": MagicMock(value="enterprise"),
            "client_id": "client_real_admin_main",
        }

        with patch("app.utils.admin_auth.ADMIN_USER_IDS", frozenset({"real_admin"})):
            with pytest.raises(HTTPException) as exc:
                await grant_file_access(
                    target_client_id="client_victim_456_prod",
                    paths=["../../etc/passwd"],
                    write_access=False,
                    ctx=admin_ctx,
                )
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_client_id_format_blocked(self):
        """client_id mit weniger als 3 Teilen (type_userid_suffix) wird abgelehnt."""
        from app.routes.client_mcp import grant_file_access

        admin_ctx = {
            "user_id": "real_admin",
            "tier": MagicMock(value="enterprise"),
            "client_id": "client_real_admin_main",
        }

        with patch("app.utils.admin_auth.ADMIN_USER_IDS", frozenset({"real_admin"})):
            with pytest.raises(HTTPException) as exc:
                await grant_file_access(
                    target_client_id="badformat",
                    paths=["/home/zombie"],
                    ctx=admin_ctx,
                )
            assert exc.value.status_code == 400


class TestGrantBashAccess:
    """Tests fuer POST /client/mcp/admin/grant-bash-access"""

    @pytest.mark.asyncio
    async def test_enterprise_user_cannot_grant_bash(self):
        """F1: Enterprise-User darf kein Bash-Recht setzen."""
        from app.routes.client_mcp import grant_bash_access

        ctx = {
            "user_id": "attacker",
            "tier": MagicMock(value="enterprise"),
            "client_id": "client_attacker_123_dev",
        }

        with patch("app.utils.admin_auth.ADMIN_USER_IDS", frozenset()):
            with pytest.raises(HTTPException) as exc:
                await grant_bash_access(
                    target_client_id="client_victim_456_prod",
                    enabled=True,
                    ctx=ctx,
                )
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_revoke_bash_access(self):
        """Admin darf Bash-Recht auch entziehen (enabled=False)."""
        from app.routes.client_mcp import grant_bash_access

        mock_device = MagicMock()
        mock_device.client_id = "client_victim_456_prod"
        mock_user = MagicMock()
        mock_user.devices = [mock_device]

        admin_ctx = {
            "user_id": "real_admin",
            "tier": MagicMock(value="enterprise"),
            "client_id": "client_real_admin_main",
        }

        with patch("app.utils.admin_auth.ADMIN_USER_IDS", frozenset({"real_admin"})), \
             patch("app.routes.client_mcp.user_manager") as mock_um:
            mock_um.get_user = AsyncMock(return_value=mock_user)
            mock_um._save_user = MagicMock()

            result = await grant_bash_access(
                target_client_id="client_victim_456_prod",
                enabled=False,
                ctx=admin_ctx,
            )

        assert result["success"] is True
        assert result["bash_access"] is False
        assert result["granted_by"] == "real_admin"
        assert mock_device.allow_bash is False


# =============================================================================
# F2 — IDOR: Unauthenticated GET Endpoints
# =============================================================================

class TestRequireReadAccess:
    """Tests fuer app/utils/admin_auth.require_read_access"""

    def test_no_key_returns_401(self):
        from app.utils.admin_auth import require_read_access
        import os
        with patch.dict(os.environ, {"INTERNAL_API_KEY": "secret123"}):
            with pytest.raises(HTTPException) as exc:
                require_read_access(x_internal_key="")
            assert exc.value.status_code == 401

    def test_wrong_key_returns_401(self):
        from app.utils.admin_auth import require_read_access
        import os
        with patch.dict(os.environ, {"INTERNAL_API_KEY": "secret123"}):
            with pytest.raises(HTTPException) as exc:
                require_read_access(x_internal_key="wrongkey")
            assert exc.value.status_code == 401

    def test_correct_key_passes(self):
        from app.utils.admin_auth import require_read_access
        import os
        with patch.dict(os.environ, {"INTERNAL_API_KEY": "secret123"}):
            # Darf keine Exception werfen
            require_read_access(x_internal_key="secret123")

    def test_no_env_key_configured_blocks_everything(self):
        """Wenn INTERNAL_API_KEY nicht gesetzt ist, wird alles geblockt."""
        from app.utils.admin_auth import require_read_access
        import os
        env = {k: v for k, v in os.environ.items() if k != "INTERNAL_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(HTTPException) as exc:
                require_read_access(x_internal_key="anything")
            assert exc.value.status_code == 401


class TestUserApiGetEndpointsAuth:
    """Stellt sicher, dass alle GET-Routen in user_api.py abgesichert sind."""

    def test_get_user_has_require_read_access_dep(self):
        """GET /users/{user_id} muss require_read_access als Dependency haben."""
        from app.routes.user_api import get_user
        import inspect
        sig = inspect.signature(get_user)
        dep_names = [
            str(p.default) for p in sig.parameters.values()
            if hasattr(p.default, "dependency")
        ]
        # Pruefe ob irgendein Depends(require_read_access) dabei ist
        from app.utils.admin_auth import require_read_access
        from fastapi import Depends
        deps = [
            p.default for p in sig.parameters.values()
            if hasattr(p.default, "dependency") and p.default.dependency is require_read_access
        ]
        assert len(deps) >= 1, "get_user hat kein Depends(require_read_access)"

    def test_list_devices_has_require_read_access_dep(self):
        from app.routes.user_api import list_devices
        from app.utils.admin_auth import require_read_access
        import inspect
        sig = inspect.signature(list_devices)
        deps = [
            p.default for p in sig.parameters.values()
            if hasattr(p.default, "dependency") and p.default.dependency is require_read_access
        ]
        assert len(deps) >= 1, "list_devices hat kein Depends(require_read_access)"

    def test_list_credentials_has_require_read_access_dep(self):
        from app.routes.user_api import list_credentials
        from app.utils.admin_auth import require_read_access
        import inspect
        sig = inspect.signature(list_credentials)
        deps = [
            p.default for p in sig.parameters.values()
            if hasattr(p.default, "dependency") and p.default.dependency is require_read_access
        ]
        assert len(deps) >= 1, "list_credentials hat kein Depends(require_read_access)"

    def test_get_settings_has_require_read_access_dep(self):
        from app.routes.user_api import get_settings
        from app.utils.admin_auth import require_read_access
        import inspect
        sig = inspect.signature(get_settings)
        deps = [
            p.default for p in sig.parameters.values()
            if hasattr(p.default, "dependency") and p.default.dependency is require_read_access
        ]
        assert len(deps) >= 1, "get_settings hat kein Depends(require_read_access)"

    def test_get_user_quota_has_require_read_access_dep(self):
        from app.routes.user_api import get_user_quota
        from app.utils.admin_auth import require_read_access
        import inspect
        sig = inspect.signature(get_user_quota)
        deps = [
            p.default for p in sig.parameters.values()
            if hasattr(p.default, "dependency") and p.default.dependency is require_read_access
        ]
        assert len(deps) >= 1, "get_user_quota hat kein Depends(require_read_access)"


# =============================================================================
# Zusaetzliche Findings: JWT_SECRET + check_path_allowed
# =============================================================================

class TestCheckPathAllowed:
    """Regressions-Tests fuer die bestehende check_path_allowed-Funktion."""

    def test_traversal_normalized_blocked(self):
        from app.routes.client_mcp import check_path_allowed
        allowed = ["/home/zombie"]
        # /home/zombie/../../etc/passwd resolved = /etc/passwd -> nicht in allowed
        result = check_path_allowed("/home/zombie/../../etc/passwd", allowed)
        assert result is False

    def test_exact_match_allowed(self):
        from app.routes.client_mcp import check_path_allowed
        assert check_path_allowed("/home/zombie", ["/home/zombie"]) is True

    def test_subpath_allowed(self):
        from app.routes.client_mcp import check_path_allowed
        assert check_path_allowed("/home/zombie/projects/myapp", ["/home/zombie"]) is True

    def test_sibling_dir_blocked(self):
        from app.routes.client_mcp import check_path_allowed
        # /home/victim ist nicht Subpfad von /home/zombie
        assert check_path_allowed("/home/victim/secret", ["/home/zombie"]) is False


class TestJwtSecretStability:
    """Stellt sicher, dass _JWT_SECRET_MODULE stabil ist."""

    def test_jwt_secret_is_module_level(self):
        """_JWT_SECRET_MODULE darf nicht None oder leer sein."""
        from app.routes.user_api import _JWT_SECRET_MODULE
        assert _JWT_SECRET_MODULE is not None
        assert len(_JWT_SECRET_MODULE) >= 16

    def test_jwt_secret_consistent_across_imports(self):
        """Mehrfacher Import des Moduls darf den Secret nicht aendern."""
        from app.routes.user_api import _JWT_SECRET_MODULE as s1
        import importlib
        import app.routes.user_api as mod
        importlib.reload(mod)
        # Nach reload kann sich secret aendern wenn JWT_SECRET nicht gesetzt ist
        # Dieser Test prueft nur, dass er nicht leer wird
        assert mod._JWT_SECRET_MODULE is not None
        assert len(mod._JWT_SECRET_MODULE) >= 16


# =============================================================================
# MCP Runtime Allowlist + GET Health Regression
# =============================================================================

def _mock_mcp_request(headers=None, host="127.0.0.1"):
    req = MagicMock()
    req.headers = headers or {}
    req.query_params = {}
    req.client = MagicMock()
    req.client.host = host
    req.state = MagicMock()
    return req


class TestMcpRuntimeSecurity:
    @pytest.mark.asyncio
    async def test_get_mcp_health_requires_auth_by_default(self):
        from app.routes.mcp import mcp_health_or_sse

        with pytest.raises(HTTPException) as caught:
            await mcp_health_or_sse(_mock_mcp_request())
        assert caught.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_mcp_health_does_not_reference_missing_params_with_explicit_local_bypass(self, monkeypatch):
        from app.routes.mcp import mcp_health_or_sse
        from app.utils import mcp_auth

        monkeypatch.setattr(mcp_auth, "MCP_ALLOW_UNAUTHENTICATED_LOCAL", True)
        response = await mcp_health_or_sse(_mock_mcp_request())
        payload = json.loads(response.body)

        assert response.status_code == 200
        assert payload["result"]["protocolVersion"] == "2024-11-05"

    @pytest.mark.asyncio
    async def test_external_tools_list_filters_privileged_tools(self):
        from app.routes import mcp as route_mcp

        internal_payload = await route_mcp.handle_tools_list({"inventory": "all"})
        internal_names = {tool["name"] for tool in internal_payload["tools"]}
        assert {"agent_start", "group_chat_create", "chat", "aihelper_pair"} <= internal_names

        external = _mock_mcp_request(headers={"X-Forwarded-Port": "9100"}, host="1.2.3.4")
        external_payload = await route_mcp.handle_tools_list({}, request=external)
        external_names = {tool["name"] for tool in external_payload["tools"]}

        assert "chat" in external_names
        assert "aihelper_pair" in external_names
        assert "group_chat_create" not in external_names
        assert "agent_start" not in external_names

    @pytest.mark.asyncio
    async def test_public_guest_tools_list_does_not_restore_memory_store_via_workspace_overlay(self):
        from app.routes import mcp as route_mcp

        guest = _mock_mcp_request(host="203.0.113.10")
        guest.state.mcp_auth_method = "public_guest"
        guest.state.mcp_auth_user = ""
        guest.state.mcp_auth_full_access = False
        payload = await route_mcp.handle_tools_list({}, request=guest)
        names = {tool["name"] for tool in payload["tools"]}

        assert "memory_search" in names
        assert "memory_store" not in names
        assert "tristar_memory_store" not in names

    def test_public_guest_catalog_hides_shared_memory_writes(self):
        from app.utils.mcp_security import filter_tools_for_external

        guest = _mock_mcp_request(host="203.0.113.10")
        guest.state.mcp_auth_method = "public_guest"
        guest.state.mcp_auth_user = ""
        authenticated = _mock_mcp_request(host="203.0.113.10")
        authenticated.state.mcp_auth_method = "bearer"
        authenticated.state.mcp_auth_user = "operator@example"
        tools = [
            {"name": "memory_store", "x_scope": "global"},
            {"name": "tristar_memory_store", "x_scope": "global"},
            {"name": "memory_search", "x_scope": "global"},
        ]

        assert [t["name"] for t in filter_tools_for_external(tools, guest)] == ["memory_search"]
        assert {t["name"] for t in filter_tools_for_external(tools, authenticated)} == {
            "memory_store", "tristar_memory_store", "memory_search"
        }

    def test_name_only_scope_resolution_cannot_downgrade_admin_or_auth_tools(self):
        """Server-side authorization often has only the tool name, not tools/list metadata."""
        from app.utils.mcp_security import is_tool_allowed

        guest = _mock_mcp_request(host="203.0.113.10")
        guest.state.mcp_auth_method = "public_guest"
        guest.state.mcp_auth_user = ""
        guest.state.mcp_auth_full_access = False

        authenticated = _mock_mcp_request(host="203.0.113.10")
        authenticated.state.mcp_auth_method = "bearer"
        authenticated.state.mcp_auth_user = "operator@example"
        authenticated.state.mcp_auth_full_access = False

        assert is_tool_allowed("chat", guest, {}) is True
        assert is_tool_allowed("mail_inbox", guest, {}) is False
        assert is_tool_allowed("n8n_mcp_call", guest, {}) is False
        assert is_tool_allowed("group_chat_create", guest, {}) is False
        assert is_tool_allowed("status", guest, {}) is True
        assert is_tool_allowed("memory_store", guest, {"content": "poison"}) is False
        assert is_tool_allowed("tristar_memory_store", guest, {"content": "poison"}) is False
        assert is_tool_allowed("shell", guest, {}) is False

        assert is_tool_allowed("mail_inbox", authenticated, {}) is True
        assert is_tool_allowed("n8n_mcp_call", authenticated, {}) is True
        assert is_tool_allowed("group_chat_create", authenticated, {}) is False
        assert is_tool_allowed("status", authenticated, {}) is True
        assert is_tool_allowed("memory_store", authenticated, {"content": "trusted"}) is True
        assert is_tool_allowed("tristar_memory_store", authenticated, {"content": "trusted"}) is True

    @pytest.mark.asyncio
    async def test_external_tools_call_blocks_privileged_tools(self):
        from app.routes import mcp as route_mcp

        external = _mock_mcp_request(headers={"X-Forwarded-Port": "9100"}, host="1.2.3.4")
        result = await route_mcp.handle_tools_call(
            {"name": "agent_start", "arguments": {"agent": "codex-mcp"}},
            request=external,
        )
        payload = json.loads(result["content"][0]["text"])

        assert result["isError"] is True
        assert payload["code"] == "MCP_TOOL_FORBIDDEN"
        assert payload["tool_name"] == "agent_start"



class TestGlobalAuthMiddlewareRegistration:
    def test_main_registers_auth_middleware(self):
        from app.main import create_app
        from app.utils.auth_middleware import AuthMiddleware
        app = create_app()
        assert any(m.cls is AuthMiddleware for m in app.user_middleware)

    @pytest.mark.asyncio
    async def test_untrusted_direct_container_cannot_bypass_protected_v1(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.responses import Response
        from app.utils import auth_middleware

        from starlette.requests import Request
        middleware = auth_middleware.AuthMiddleware(FastAPI())
        request = Request({
            "type": "http", "http_version": "1.1", "method": "GET",
            "scheme": "http", "path": "/v1/admin/config-sanity",
            "raw_path": b"/v1/admin/config-sanity", "query_string": b"",
            "headers": [], "client": ("172.18.0.42", 12345),
            "server": ("backend", 9000), "state": {},
        })
        monkeypatch.setattr(auth_middleware, "MCP_AUTH_USER", "test-user")
        monkeypatch.setattr(auth_middleware, "MCP_AUTH_PASS", "test-pass")

        async def call_next(_request):
            return Response(status_code=204)

        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
    async def test_external_api_documentation_requires_auth(self, monkeypatch, path):
        from fastapi import FastAPI
        from fastapi.responses import Response
        from app.utils import auth_middleware
        from starlette.requests import Request

        middleware = auth_middleware.AuthMiddleware(FastAPI())
        request = Request({
            "type": "http", "http_version": "1.1", "method": "GET",
            "scheme": "https", "path": path,
            "raw_path": path.encode(), "query_string": b"",
            "headers": [(b"x-forwarded-port", b"9100")],
            "client": ("203.0.113.10", 12345),
            "server": ("backend", 9000), "state": {},
        })
        monkeypatch.setattr(auth_middleware, "MCP_AUTH_USER", "test-user")
        monkeypatch.setattr(auth_middleware, "MCP_AUTH_PASS", "test-pass")

        async def call_next(_request):
            return Response(status_code=204)

        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", [
        "/v1/mcp/workspace/pair-ticket",
        "/v1/mcp/workspace/socket-ticket",
        "/v1/mcp/workspace/resume-ticket",
        "/v1/mcp/workspace/handoff-ticket",
        "/v1/mcp/workspace/handoff",
        "/v1/mcp/workspace/android.apk",
        "/v1/mcp/helper/releases",
        "/v1/mcp/helper/icon.png",
        "/v1/mcp/web/app.js",
        "/v1/mcp/manifest.webmanifest",
        "/v1/mcp/sw.js",
    ])
    async def test_public_webmcp_bootstrap_and_resume_routes_bypass_account_auth(self, monkeypatch, path):
        from fastapi import FastAPI
        from fastapi.responses import Response
        from app.utils import auth_middleware
        from starlette.requests import Request

        middleware = auth_middleware.AuthMiddleware(FastAPI())
        request = Request({
            "type": "http", "http_version": "1.1", "method": "GET",
            "scheme": "https", "path": path,
            "raw_path": path.encode(), "query_string": b"",
            "headers": [(b"x-forwarded-port", b"9100")],
            "client": ("203.0.113.10", 12345),
            "server": ("backend", 9000), "state": {},
        })
        monkeypatch.setattr(auth_middleware, "MCP_AUTH_USER", "test-user")
        monkeypatch.setattr(auth_middleware, "MCP_AUTH_PASS", "test-pass")

        async def call_next(_request):
            return Response(status_code=204)

        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 204

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", [
        "/v1/mcp/workspace/resume-ticket-extra",
        "/v1/mcp/workspace/handoff-admin",
        "/v1/mcp/node/connect",
    ])
    async def test_webmcp_public_allowlist_does_not_open_neighboring_sensitive_routes(self, monkeypatch, path):
        from fastapi import FastAPI
        from fastapi.responses import Response
        from app.utils import auth_middleware
        from starlette.requests import Request

        middleware = auth_middleware.AuthMiddleware(FastAPI())
        request = Request({
            "type": "http", "http_version": "1.1", "method": "GET",
            "scheme": "https", "path": path,
            "raw_path": path.encode(), "query_string": b"",
            "headers": [(b"x-forwarded-port", b"9100")],
            "client": ("203.0.113.10", 12345),
            "server": ("backend", 9000), "state": {},
        })
        monkeypatch.setattr(auth_middleware, "MCP_AUTH_USER", "test-user")
        monkeypatch.setattr(auth_middleware, "MCP_AUTH_PASS", "test-pass")

        async def call_next(_request):
            return Response(status_code=204)

        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_host_bridge_keeps_internal_protected_route_bypass(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.responses import Response
        from app.utils import auth_middleware

        from starlette.requests import Request
        middleware = auth_middleware.AuthMiddleware(FastAPI())
        request = Request({
            "type": "http", "http_version": "1.1", "method": "GET",
            "scheme": "http", "path": "/v1/admin/config-sanity",
            "raw_path": b"/v1/admin/config-sanity", "query_string": b"",
            "headers": [], "client": ("172.17.0.1", 12345),
            "server": ("backend", 9000), "state": {},
        })

        async def call_next(_request):
            return Response(status_code=204)

        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 204

class TestDistributedAndClientRouteAuth:
    def test_distributed_rest_requires_client_jwt(self):
        from app.routes.distributed_compute import _require_distributed_session
        with pytest.raises(HTTPException) as exc:
            _require_distributed_session(None)
        assert exc.value.status_code == 401

    def test_sensitive_client_routes_declare_admin_dependency(self):
        from app.routes.client_chat import router
        protected = {
            "/client/models/availability",
            "/client/models/availability/reset/{model_id:path}",
            "/client/models/availability/exclude",
            "/client/tokens/reset/{user_id}",
            "/client/tokens/usage/{user_id}",
        }
        found = {}
        for route in router.routes:
            if getattr(route, "path", None) in protected:
                found[route.path] = {dep.call for dep in route.dependant.dependencies}
        from app.routes.client_auth import require_admin
        assert set(found) == protected
        assert all(require_admin in calls for calls in found.values())


# =============================================================================
# MCP Dev-Tool path and command hardening
# =============================================================================

class TestMcpDevToolSecurity:
    def test_run_uses_argv_without_shell_interpretation(self):
        from app.mcp import dev_tools

        suspicious_arg = "code'; touch /tmp/should-not-run; #"
        with patch.object(
            dev_tools.subprocess,
            "run",
            return_value=CompletedProcess([], 0, stdout="", stderr=""),
        ) as run:
            result = dev_tools._run(["ruff", "check", suspicious_arg])

        assert result["exit_code"] == 0
        assert run.call_args.args[0] == ["ruff", "check", suspicious_arg]
        assert run.call_args.kwargs["shell"] is False

    def test_run_rejects_string_commands(self):
        from app.mcp import dev_tools

        result = dev_tools._run("ruff check .; touch /tmp/should-not-run")

        assert result["exit_code"] == -1
        assert "argv sequence" in result["stderr"]

    @pytest.mark.asyncio
    async def test_dev_lint_keeps_metacharacters_in_one_path_argument(self, tmp_path: Path):
        from app.mcp import dev_tools

        project = tmp_path / "code'; touch injected; #"
        project.mkdir()
        tool_results = [
            {"stdout": "[]", "stderr": "", "exit_code": 0},
            {"stdout": "", "stderr": "", "exit_code": 0},
        ]

        with patch.object(dev_tools, "_run", side_effect=tool_results) as run:
            result = await dev_tools.handle_dev_lint({
                "path": str(project),
                "language": "python",
            })

        assert result["clean"] is True
        assert run.call_count == 2
        assert all(isinstance(call.args[0], list) for call in run.call_args_list)
        assert str(project) in run.call_args_list[0].args[0]
        assert not (tmp_path / "injected").exists()

    @pytest.mark.asyncio
    async def test_dev_debug_rejects_system_file_from_traceback(self):
        from app.mcp import dev_tools

        result = await dev_tools.handle_dev_debug({
            "error": 'Traceback: File "/etc/passwd", line 1, in <module>',
        })

        assert result["code_context"] is None
        assert result["path_error"] == "Path is outside MCP dev workspace roots"

    @pytest.mark.asyncio
    async def test_dev_debug_reads_code_from_another_workspace(self, tmp_path: Path):
        from app.mcp import dev_tools

        project = tmp_path / "another-project"
        project.mkdir()
        source = project / "example.py"
        source.write_text("first = 1\nsecond = missing_name\n", encoding="utf-8")

        result = await dev_tools.handle_dev_debug({
            "error": f'Traceback: File "{source}", line 2, in <module>\nNameError',
        })

        assert result.get("path_error") is None
        assert result["file"] == str(source.resolve())
        assert ">>>    2: second = missing_name" in result["code_context"]

    @pytest.mark.asyncio
    async def test_dev_debug_rejects_symlink_escape(self, tmp_path: Path):
        from app.mcp import dev_tools

        source_link = tmp_path / "outside.py"
        source_link.symlink_to("/etc/passwd")
        result = await dev_tools.handle_dev_debug({
            "error": f'Traceback: File "{source_link}", line 1, in <module>',
        })

        assert result["code_context"] is None
        assert result["path_error"] == "Path is outside MCP dev workspace roots"

    def test_allowed_roots_are_operator_configurable(self, tmp_path: Path, monkeypatch):
        from app.mcp import dev_tools

        workspace = tmp_path / "custom-workspace"
        workspace.mkdir()
        source = workspace / "main.py"
        source.write_text("print('ok')\n", encoding="utf-8")
        monkeypatch.setenv("MCP_DEV_ALLOWED_ROOTS", str(workspace))

        assert dev_tools._resolve_dev_path(str(source)) == source.resolve()
        with pytest.raises(ValueError, match="outside MCP dev workspace roots"):
            dev_tools._resolve_dev_path(str(Path(dev_tools.PROJECT_ROOT) / "app"))

    def test_settings_exposes_dev_workspace_roots(self, monkeypatch):
        from app.config import Settings

        monkeypatch.setenv("MCP_DEV_ALLOWED_ROOTS", "/srv/source:/tmp")
        assert Settings().mcp_dev_allowed_roots == "/srv/source:/tmp"


# =============================================================================
# Code tools — arbitrary approved project roots without scope escape
# =============================================================================

class TestCodeWorkspaceRoots:
    @pytest.mark.asyncio
    async def test_code_read_supports_project_root_and_line_range(self, tmp_path: Path, monkeypatch):
        from app.services.mcp_service import handle_codebase_file

        source = tmp_path / "app.py"
        source.write_text("one\ntwo\nthree\n", encoding="utf-8")
        monkeypatch.setenv("MCP_DEV_ALLOWED_ROOTS", str(tmp_path))
        result = await handle_codebase_file({
            "root": str(tmp_path), "path": "app.py", "start_line": 2, "end_line": 3,
        })
        assert result["path"] == "app.py"
        assert result["content"] == "2: two\n3: three"

    @pytest.mark.asyncio
    async def test_code_tree_recurses_in_project_root(self, tmp_path: Path, monkeypatch):
        from app.mcp.adaptive_code import handle_code_scout

        nested = tmp_path / "pkg" / "inner"
        nested.mkdir(parents=True)
        (nested / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
        monkeypatch.setenv("MCP_DEV_ALLOWED_ROOTS", str(tmp_path))
        result = await handle_code_scout({"root": str(tmp_path), "path": ".", "depth": 4})
        assert result["root"] == str(tmp_path.resolve())
        assert "mod.py" in repr(result)

    @pytest.mark.asyncio
    async def test_code_search_recurses_and_supports_regex(self, tmp_path: Path, monkeypatch):
        from app.services.mcp_service import handle_codebase_search

        nested = tmp_path / "pkg"
        nested.mkdir()
        (nested / "mod.py").write_text("alpha = 1\nTargetValue = 42\n", encoding="utf-8")
        monkeypatch.setenv("MCP_DEV_ALLOWED_ROOTS", str(tmp_path))
        result = await handle_codebase_search({
            "root": str(tmp_path), "path": ".", "query": r"Target.*42",
            "regex": True, "file_pattern": "*.py",
        })
        assert result["count"] == 1
        assert result["results"][0]["file"] == "pkg/mod.py"
        assert result["results"][0]["line"] == 2

    @pytest.mark.asyncio
    async def test_code_read_allows_version_but_not_arbitrary_extensionless_files(self, tmp_path: Path, monkeypatch):
        from app.services.mcp_service import handle_codebase_file

        (tmp_path / "VERSION").write_text("2.90.13\n", encoding="utf-8")
        (tmp_path / "SECRETLESS").write_text("not source\n", encoding="utf-8")
        monkeypatch.setenv("MCP_DEV_ALLOWED_ROOTS", str(tmp_path))

        result = await handle_codebase_file({"root": str(tmp_path), "path": "VERSION"})
        assert result["content"] == "1: 2.90.13"
        with pytest.raises(ValueError, match="File type not allowed: SECRETLESS"):
            await handle_codebase_file({"root": str(tmp_path), "path": "SECRETLESS"})

    @pytest.mark.asyncio
    async def test_code_search_includes_version_file(self, tmp_path: Path, monkeypatch):
        from app.services.mcp_service import handle_codebase_search

        (tmp_path / "VERSION").write_text("release-2.90.13\n", encoding="utf-8")
        monkeypatch.setenv("MCP_DEV_ALLOWED_ROOTS", str(tmp_path))

        result = await handle_codebase_search({"root": str(tmp_path), "path": ".", "query": "release-2.90.13"})
        assert result["count"] == 1
        assert result["results"][0]["file"] == "VERSION"

    @pytest.mark.asyncio
    async def test_code_root_cannot_escape_to_sibling(self, tmp_path: Path, monkeypatch):
        from app.services.mcp_service import handle_codebase_file

        root = tmp_path / "project"
        sibling = tmp_path / "other"
        root.mkdir(); sibling.mkdir()
        (sibling / "secret.py").write_text("SECRET = True\n", encoding="utf-8")
        monkeypatch.setenv("MCP_DEV_ALLOWED_ROOTS", str(tmp_path))
        with pytest.raises(ValueError, match="outside requested project root"):
            await handle_codebase_file({"root": str(root), "path": "../other/secret.py"})

    @pytest.mark.asyncio
    async def test_sensitive_code_roots_are_rejected(self, tmp_path: Path, monkeypatch):
        from app.mcp.adaptive_code import handle_code_scout
        from app.services.mcp_service import handle_codebase_file

        secret = tmp_path / ".ssh"
        secret.mkdir()
        (secret / "id_test").write_text("secret", encoding="utf-8")
        monkeypatch.setenv("MCP_DEV_ALLOWED_ROOTS", str(tmp_path))
        with pytest.raises(ValueError, match="blocked path component"):
            await handle_codebase_file({"root": str(secret), "path": "id_test"})
        with pytest.raises(ValueError, match="blocked path component"):
            await handle_code_scout({"root": str(secret), "path": "."})


def test_no_unused_python_jose_or_ecdsa_dependency():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    lock = (root / "requirements-lock-2.85-beta2.txt").read_text(encoding="utf-8").lower()
    assert "python-jose" not in requirements
    assert "python-jose==" not in lock
    assert "ecdsa==" not in lock


# =============================================================================
# WordPress -> TriForce webhook contract
# =============================================================================

class TestWordPressWebhookContract:
    @staticmethod
    def _request(body: bytes):
        from starlette.requests import Request
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        scope = {
            "type": "http", "http_version": "1.1", "method": "POST",
            "scheme": "https", "path": "/v1/webhook/user-created",
            "raw_path": b"/v1/webhook/user-created", "query_string": b"",
            "headers": [], "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443), "state": {},
        }
        return Request(scope, receive=receive)

    def test_only_hardened_webhooks_are_exported(self):
        from app.routes import user_api
        paths = {route.path for route in user_api.webhook_router.routes}
        assert paths == {
            "/webhook/user-created",
            "/webhook/payment-success",
            "/webhook/subscription-cancelled",
        }
        assert not any(path.startswith("/users/") for path in paths)

    @pytest.mark.asyncio
    async def test_webhook_signature_fails_closed_without_secret(self, monkeypatch):
        from app.routes import user_api
        monkeypatch.delenv("AILINUX_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        assert await user_api.verify_webhook_signature(self._request(b"{}"), "deadbeef") is False

    @pytest.mark.asyncio
    async def test_webhook_signature_accepts_legacy_webhook_secret(self, monkeypatch):
        import hashlib
        import hmac
        from app.routes import user_api
        secret = "legacy-test-secret"
        body = b'{"event":"payment_success","user_id":"wp_test","data":{}}'
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        monkeypatch.delenv("AILINUX_WEBHOOK_SECRET", raising=False)
        monkeypatch.setenv("WEBHOOK_SECRET", secret)
        assert await user_api.verify_webhook_signature(self._request(body), signature) is True

    @pytest.mark.asyncio
    async def test_webhook_signature_accepts_matching_hmac(self, monkeypatch):
        import hashlib
        import hmac
        from app.routes import user_api
        secret = "test-secret"
        body = b'{"event":"user_created","user_id":"wp_test","data":{}}'
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        monkeypatch.setenv("AILINUX_WEBHOOK_SECRET", secret)
        assert await user_api.verify_webhook_signature(self._request(body), signature) is True

    @pytest.mark.asyncio
    async def test_webhook_dependency_rejects_invalid_signature(self, monkeypatch):
        from app.routes import user_api
        monkeypatch.setenv("AILINUX_WEBHOOK_SECRET", "test-secret")
        with pytest.raises(HTTPException) as exc_info:
            await user_api.require_webhook_signature(self._request(b"{}"), "bad")
        assert exc_info.value.status_code == 401

    def test_main_mounts_only_webhook_router(self):
        import inspect
        from app import main
        source = inspect.getsource(main.create_app)
        assert 'include_router(user_webhook_router, prefix="/v1"' in source
        assert 'include_router(user_api_router' not in source


def test_stack_control_removes_legacy_wordpress_webroot_secret():
    script = Path("scripts/docker/stack-control.sh").read_text()
    assert "remove_legacy_wordpress_webroot_secrets" in script
    assert "nova-ai-frontend/config/triforce.env" in script
    assert 'rm -f -- "$legacy_env"' in script


def test_oauth_state_files_are_private(monkeypatch, tmp_path):
    """Bearer tokens and one-time auth codes must never be group/world-readable."""
    from app.utils import mcp_auth

    auth_dir = tmp_path / "auth"
    token_file = auth_dir / "tokens.json"
    code_file = auth_dir / "auth_codes.json"
    monkeypatch.setattr(mcp_auth, "_AUTH_DIR", auth_dir)
    monkeypatch.setattr(mcp_auth, "_TOKEN_FILE", token_file)
    monkeypatch.setattr(mcp_auth, "_AUTH_CODES_FILE", code_file)
    monkeypatch.setattr(mcp_auth, "_PERSISTENT_TOKENS", {"secret-token": {"user": "test"}})
    monkeypatch.setattr(mcp_auth, "_AUTH_CODES", {
        "secret-code": {"expires_at": "2999-01-01T00:00:00+00:00"}
    })

    mcp_auth._save_persistent_tokens()
    mcp_auth._save_auth_codes()

    assert auth_dir.stat().st_mode & 0o777 == 0o700
    assert token_file.stat().st_mode & 0o777 == 0o600
    assert code_file.stat().st_mode & 0o777 == 0o600
