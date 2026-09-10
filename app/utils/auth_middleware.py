"""
Authentication Middleware for /v1 and /v1/mcp
=============================================

Port-based authentication:
- X-Forwarded-Port: 9100 → Auth required (external via Apache)
- No X-Forwarded-Port → No auth (internal/direct/public endpoints)
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .mcp_auth import (
    is_valid_token,
    _validate_credentials,
    _extract_basic_auth,
    MCP_AUTH_USER,
    MCP_AUTH_PASS,
    _is_public_guest_mcp_path,
)

logger = logging.getLogger("ailinux.auth.middleware")

# Protected path prefixes
PROTECTED_PREFIXES = ["/v1/", "/v1/mcp", "/mcp"]

# Public paths (no auth required)
PUBLIC_PATHS = [
    "/.well-known/",
    "/authorize",
    "/token",
    "/auth/",
    "/v1/auth/",  # Client auth endpoints (login, register)
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/robots.txt",
    "/tristar/login",
    "/tristar/logout",
    "/static/",
    "/v1/distributed",
    "/v1/mcp/node/support",  # Support-Calls (KI-Support für alle)
    "/v1/mcp/workspace/pair-ticket",  # Browser-only one-time workspace ticket
    "/v1/client/",  # Client-API (Free-Tier ohne Auth)
]

# Port that requires authentication (set by Apache for external requests)
AUTH_REQUIRED_PORT = 9100


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Port-based authentication middleware.
    
    - X-Forwarded-Port: 9100 → require auth (external via Apache proxy)
    - No X-Forwarded-Port or other port → bypass (internal/public)
    """

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path

        # Skip auth for public paths
        for public in PUBLIC_PATHS:
            if path.startswith(public) or path == public.rstrip("/"):
                logger.info(f"AUTH_SKIP | Path: {path} | Matched: {public}")
                return await call_next(request)

        # Check if path needs protection
        needs_auth = False
        for prefix in PROTECTED_PREFIXES:
            if path.startswith(prefix) or path == prefix.rstrip("/"):
                needs_auth = True
                break

        if not needs_auth:
            return await call_next(request)

        # === Port-based auth decision ===
        # Apache sets X-Forwarded-Port: 9100 for EXTERNAL requests
        # Public endpoints (like /api/public/search) don't have this header
        forwarded_port_str = request.headers.get("X-Forwarded-Port", "")
        client_ip = request.client.host if request.client else "unknown"
        
        # Parse forwarded port
        forwarded_port = None
        if forwarded_port_str:
            try:
                forwarded_port = int(forwarded_port_str)
            except ValueError:
                pass

        # Only require auth if X-Forwarded-Port is 9100 (external)
        if forwarded_port != AUTH_REQUIRED_PORT:
            logger.debug(f"AUTH_OK | IP: {client_ip} | X-Fwd-Port: {forwarded_port_str or 'none'} | Method: port_bypass")
            return await call_next(request)

        # External request (port 9100) → requires authentication
        logger.debug(f"AUTH_CHECK | IP: {client_ip} | X-Fwd-Port: {forwarded_port} | Path: {path}")
        
        auth_header = request.headers.get("Authorization", "")
        x_mcp_token = request.headers.get("X-MCP-Token", "").strip()
        query_token = ""
        if os.environ.get("ALLOW_QUERY_TOKEN_AUTH") == "1":
            query_token = (
                request.query_params.get("access_token")
                or request.query_params.get("mcp_token")
                or request.query_params.get("token")
                or ""
            ).strip()

        # Exact MCP transport endpoints are publishable without credentials.
        # If any credential is supplied, keep the existing validation path so an
        # invalid admin/user credential can never silently become public_guest.
        if (
            _is_public_guest_mcp_path(path)
            and not auth_header.strip()
            and not x_mcp_token
            and not query_token
        ):
            logger.debug(f"AUTH_OK | IP: {client_ip} | X-Fwd-Port: {forwarded_port} | Method: public_guest_passthrough")
            return await call_next(request)

        # Check if auth is configured
        if not MCP_AUTH_USER or not MCP_AUTH_PASS:
            logger.error(f"AUTH_ERROR | IP: {client_ip} | Reason: auth_not_configured")
            return self._unauthorized_response(request, "Server authentication not configured")

        # Method 0a: X-MCP-Token header (avoids clients overwriting Authorization)
        if x_mcp_token:
            if is_valid_token(x_mcp_token):
                logger.debug(f"AUTH_OK | IP: {client_ip} | X-Fwd-Port: {forwarded_port} | Method: x-mcp-token")
                return await call_next(request)
            else:
                logger.warning(f"AUTH_FAIL | IP: {client_ip} | Reason: invalid_x_mcp_token")
                return self._unauthorized_response(request, "Invalid X-MCP-Token")

        # Method 0b: query token fallback for MCP clients that drop custom headers on SSE
        if query_token:
            if is_valid_token(query_token):
                logger.debug(f"AUTH_OK | IP: {client_ip} | X-Fwd-Port: {forwarded_port} | Method: query-token")
                return await call_next(request)
            else:
                logger.warning(f"AUTH_FAIL | IP: {client_ip} | Reason: invalid_query_token")
                return self._unauthorized_response(request, "Invalid query token")

        # Method 1: Bearer Token
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            if is_valid_token(token):
                logger.debug(f"AUTH_OK | IP: {client_ip} | X-Fwd-Port: {forwarded_port} | Method: bearer")
                return await call_next(request)
            else:
                logger.warning(f"AUTH_FAIL | IP: {client_ip} | Reason: invalid_bearer")
                return self._unauthorized_response(request, "Invalid bearer token")

        # Method 2: Basic Auth
        if auth_header.lower().startswith("basic "):
            username, password = _extract_basic_auth(request)
            if _validate_credentials(username, password):
                logger.debug(f"AUTH_OK | IP: {client_ip} | X-Fwd-Port: {forwarded_port} | Method: basic | User: {username}")
                return await call_next(request)
            else:
                logger.warning(f"AUTH_FAIL | IP: {client_ip} | Reason: invalid_basic")
                return self._unauthorized_response(request, "Invalid credentials")

        # No auth provided
        logger.warning(f"AUTH_FAIL | IP: {client_ip} | X-Fwd-Port: {forwarded_port} | Reason: no_credentials | Path: {path}")
        return self._unauthorized_response(request, "Authentication required")

    def _unauthorized_response(self, request: Request, detail: str) -> JSONResponse:
        """Return 401 with proper WWW-Authenticate header."""
        base_url = str(request.base_url).rstrip("/")
        auth_server = f"{base_url}/v1" + "/.well-known/oauth-authorization-server"

        return JSONResponse(
            status_code=401,
            content={"detail": detail},
            headers={
                "WWW-Authenticate": f'Bearer realm="mcp", authorization_server="{auth_server}"'
            }
        )
