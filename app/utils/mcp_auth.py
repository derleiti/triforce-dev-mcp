"""
MCP Authentication - OAuth 2.0 + Basic Auth + Bearer Token
==========================================================

Unified authentication for ALL MCP routes supporting:
1. Bearer Token (from OAuth 2.0 flow)
2. Basic Auth (username:password from .env)
3. OAuth 2.0 Password Grant
4. OAuth 2.0 Authorization Code with PKCE

Compatible with:
- Claude Code CLI, Codex CLI, Gemini CLI, OpenCode CLI
- Claude.ai, ChatGPT web interfaces
- Any OAuth 2.0 compliant client

Credentials from .env:
- MCP_OAUTH_USER: Username
- MCP_OAUTH_PASS: Password
"""

from __future__ import annotations

import os
import logging
import secrets
import hashlib
import base64
import json
from pathlib import Path

from app.paths import LOG_DIR
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Set, Tuple

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from ..config import get_settings

# Logger
logger = logging.getLogger("ailinux.auth")

# Log directory
_LOG_DIR = LOG_DIR
_LOG_DIR.mkdir(parents=True, exist_ok=True)

# File handler for auth log
_auth_log_file = _LOG_DIR / "auth.log"
if not any(isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', '').endswith('auth.log') for h in logger.handlers):
    _file_handler = logging.FileHandler(_auth_log_file)
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(_file_handler)

# Export
AUTH_ENABLED = True

# Verifizierte/Autorisierte Domains für OAuth und Protected Resources
VERIFIED_DOMAINS = [
    # Externe Domains
    "api.ailinux.me",
    "ailinux.me",
    "search.ailinux.me",
    "repo.ailinux.me",
    # Local Development
    "localhost",
    "127.0.0.1",
    # Docker Internal
    "host.docker.internal",
    "172.17.0.1",   # Docker Bridge Gateway
    "172.19.0.1",   # WordPress Network Gateway
]

# Standard OAuth Issuer URL (extern)
DEFAULT_ISSUER = "https://api.ailinux.me"

# Settings
_settings = get_settings()

# OAuth credentials from .env
MCP_AUTH_USER = _settings.mcp_oauth_user or os.getenv("MCP_OAUTH_USER", "")
MCP_AUTH_PASS = _settings.mcp_oauth_pass or os.getenv("MCP_OAUTH_PASS", "")
MCP_ALLOW_UNAUTHENTICATED_LOCAL = _settings.mcp_allow_unauthenticated_local

# Token Storage (file-based for multi-worker support)
_AUTH_DIR = Path("/var/tristar/auth")
_TOKEN_FILE = _AUTH_DIR / "tokens.json"
_AUTH_CODES_FILE = _AUTH_DIR / "auth_codes.json"
_ACTIVE_TOKENS: Set[str] = set()
_PERSISTENT_TOKENS: Dict[str, Dict[str, Any]] = {}
_AUTH_CODES: Dict[str, Dict[str, Any]] = {}
_AUTH_CODE_TTL = 300  # 5 minutes

# Token expiration (default 365 days for long-lived tokens)
_DEFAULT_TOKEN_EXPIRY_DAYS = 365


# Fingerprint (mtime_ns, size) of the token file as of the last successful load.
# Used to skip re-reading an unchanged file on every auth check.
_TOKEN_FILE_STAMP: Optional[tuple] = None


def _load_persistent_tokens(force: bool = False):
    """Load tokens from disk.

    Called on every auth check, so an unchanged file is not re-read and
    re-parsed each time. Any write to the file changes (mtime_ns, size)
    and is picked up on the very next call, so a revoked token still takes
    effect immediately. Pass force=True to bypass the check.
    """
    global _PERSISTENT_TOKENS, _TOKEN_FILE_STAMP
    try:
        if not _TOKEN_FILE.exists():
            return
        st = _TOKEN_FILE.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        if not force and stamp == _TOKEN_FILE_STAMP:
            return
        _PERSISTENT_TOKENS = json.loads(_TOKEN_FILE.read_text())
        _TOKEN_FILE_STAMP = stamp
        logger.debug(f"Loaded {len(_PERSISTENT_TOKENS)} persistent tokens")
    except Exception as e:
        _TOKEN_FILE_STAMP = None
        logger.warning(f"Could not load tokens: {e}")


def _load_auth_codes():
    """Load auth codes from disk (multi-worker safe)."""
    global _AUTH_CODES
    try:
        if _AUTH_CODES_FILE.exists():
            data = json.loads(_AUTH_CODES_FILE.read_text())
            # Filter expired codes and merge with current
            now = datetime.now(timezone.utc)
            for k, v in data.items():
                try:
                    exp = datetime.fromisoformat(v.get("expires_at", "2000-01-01T00:00:00+00:00").replace("Z", "+00:00"))
                    if exp > now:
                        _AUTH_CODES[k] = v
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Could not load auth codes: {e}")


def _save_auth_codes():
    """Save auth codes to disk (multi-worker safe with file locking)."""
    import fcntl
    try:
        _AUTH_DIR.mkdir(parents=True, exist_ok=True)
        # Load existing codes first to merge
        existing = {}
        if _AUTH_CODES_FILE.exists():
            try:
                existing = json.loads(_AUTH_CODES_FILE.read_text())
            except Exception:
                pass
        # Merge with current (current overwrites existing)
        merged = {**existing, **_AUTH_CODES}
        # Filter expired
        now = datetime.now(timezone.utc)
        merged = {
            k: v for k, v in merged.items()
            if datetime.fromisoformat(v.get("expires_at", "2000-01-01T00:00:00+00:00").replace("Z", "+00:00")) > now
        }
        # Write atomically
        with open(_AUTH_CODES_FILE, 'w') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(merged, f, indent=2)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        logger.warning(f"Could not save auth codes: {e}")


def _save_persistent_tokens():
    """Save tokens to disk."""
    try:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(json.dumps(_PERSISTENT_TOKENS, indent=2))
    except Exception as e:
        logger.warning(f"Could not save tokens: {e}")


def add_token(token: str, metadata: Optional[Dict] = None):
    """Add a new token to storage."""
    _ACTIVE_TOKENS.add(token)
    if metadata:
        _PERSISTENT_TOKENS[token] = metadata
        _save_persistent_tokens()


def is_valid_token(token: str) -> bool:
    """Check if a bearer token is valid."""
    # Check in-memory tokens first
    if token in _ACTIVE_TOKENS:
        return True

    # Check persistent tokens
    _load_persistent_tokens()
    if token in _PERSISTENT_TOKENS:
        data = _PERSISTENT_TOKENS[token]
        expires = data.get("expires_at")
        if expires:
            try:
                if datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                    logger.debug(f"Token expired: {token[:8]}...")
                    return False
            except Exception:
                pass
        return True
    return False


def get_active_tokens() -> Set[str]:
    """Get all active session tokens."""
    return _ACTIVE_TOKENS


def get_persistent_tokens() -> Dict[str, Dict[str, Any]]:
    """Get all persistent tokens."""
    _load_persistent_tokens()
    return _PERSISTENT_TOKENS


# Canonical MCP scopes advertised via discovery metadata (RFC 8414/9728).
# "mcp" is the always-granted base scope; the granular scopes gate privileged
# tool visibility (see _token_has_full_mcp_access).
SUPPORTED_MCP_SCOPES = ["mcp", "mcp:read", "mcp:write", "mcp:tools"]


def filter_scopes(requested: str) -> str:
    """Validate a requested scope string against SUPPORTED_MCP_SCOPES.

    Keeps only recognised scopes (order per SUPPORTED_MCP_SCOPES), always
    includes the "mcp" base scope, and never invents scopes the client did not
    ask for. Unknown scopes are dropped rather than granting anything implicit.
    """
    asked = {part.strip().lower() for part in (requested or "").split() if part.strip()}
    granted = [s for s in SUPPORTED_MCP_SCOPES if s == "mcp" or s in asked]
    return " ".join(granted)


def get_token_scope(token: str) -> str:
    """Return the scope actually stored on a token (for token-response echoing)."""
    if not token:
        return "mcp"
    _load_persistent_tokens()
    metadata = _PERSISTENT_TOKENS.get(token) or {}
    return str(metadata.get("scope") or "mcp")


def _token_has_full_mcp_access(token: str) -> bool:
    """Authorize privileged MCP tools only for tokens carrying explicit scopes."""
    if not token:
        return False
    _load_persistent_tokens()
    metadata = _PERSISTENT_TOKENS.get(token) or {}
    raw_scope = str(metadata.get("scope") or "")
    scopes = {part.strip().lower() for part in raw_scope.split() if part.strip()}
    return {"mcp:write", "mcp:tools"}.issubset(scopes)


def _safe_compare(a: str, b: str) -> bool:
    """Constant-time string comparison."""
    if not a or not b:
        return False
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _validate_credentials(username: str, password: str) -> bool:
    """
    Validate OAuth username/password or static client_id/client_secret
    against MCP_OAUTH_USER / MCP_OAUTH_PASS from the live environment.
    Reads os.environ at call time so systemd/env rotations work after restart.
    """
    import os
    import secrets
    import logging

    expected_user = os.getenv("MCP_OAUTH_USER", "").strip()
    expected_pass = os.getenv("MCP_OAUTH_PASS", "").strip()

    username = (username or "").strip()
    password = (password or "").strip()

    if not expected_user or not expected_pass:
        logging.getLogger("ailinux.auth").error(
            "AUTH_ERROR | MCP_OAUTH_USER/PASS not configured"
        )
        return False

    ok = (
        secrets.compare_digest(username, expected_user)
        and secrets.compare_digest(password, expected_pass)
    )

    if not ok:
        logging.getLogger("ailinux.auth").warning(
            "AUTH_FAIL | user_len=%s expected_user_len=%s pass_len=%s expected_pass_len=%s",
            len(username), len(expected_user), len(password), len(expected_pass)
        )

    return ok


def _extract_basic_auth(request: Request) -> Tuple[str, str]:
    """Extract username and password from Basic Auth header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        try:
            creds = base64.b64decode(auth_header[6:]).decode("utf-8")
            if ":" in creds:
                return tuple(creds.split(":", 1))
        except Exception:
            pass
    return ("", "")


def _verify_pkce(verifier: str, challenge: str, method: str = "S256") -> bool:
    """Verify PKCE code verifier against challenge."""
    if method == "plain":
        return _safe_compare(verifier, challenge)
    elif method == "S256":
        computed = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        return _safe_compare(computed, challenge)
    return False


def _unauthorized(detail: str, www_auth: str = "Bearer") -> HTTPException:
    """Return 401 Unauthorized with appropriate WWW-Authenticate header."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": f'{www_auth} realm="AILinux MCP"'},
    )


def create_token(user: str = "oauth_client", client_id: str = None,
                 scope: str = "mcp", expires_days: int = _DEFAULT_TOKEN_EXPIRY_DAYS) -> str:
    """Create a new bearer token."""
    token = secrets.token_urlsafe(32)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat(),
        "user": user,
        "client_id": client_id,
        "scope": scope,
    }
    add_token(token, metadata)
    logger.info(f"TOKEN_CREATED | User: {user} | Client: {client_id} | Expires: {expires_days}d")
    return token


def store_auth_code(code: str, client_id: str, redirect_uri: str,
                    code_challenge: str = None, code_challenge_method: str = "S256",
                    scope: str = "mcp", user: str = None):
    """Store an authorization code for later exchange (file-based for multi-worker)."""
    _load_auth_codes()  # Load latest from disk
    _AUTH_CODES[code] = {
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=_AUTH_CODE_TTL)).isoformat(),
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method or "S256",
        "scope": scope,
        "user": user or MCP_AUTH_USER,
    }
    _save_auth_codes()  # Persist to disk


def exchange_auth_code(code: str, code_verifier: str = None) -> Optional[str]:
    """Exchange authorization code for access token (file-based for multi-worker)."""
    _load_auth_codes()  # Load latest from disk

    if not code or code not in _AUTH_CODES:
        logger.warning(f"AUTH_CODE_NOT_FOUND | code={code[:8] if code else 'none'}...")
        return None

    auth_data = _AUTH_CODES.pop(code)
    _save_auth_codes()  # Remove used code from disk

    # Check expiration
    expires_at = auth_data.get("expires_at", "")
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))

    if expires_at < datetime.now(timezone.utc):
        logger.warning(f"AUTH_CODE_EXPIRED | code={code[:8]}...")
        return None

    # PKCE verification (if challenge was provided)
    if auth_data.get("code_challenge"):
        if not code_verifier:
            logger.warning("PKCE_FAIL | code_verifier required but not provided")
            return None
        if not _verify_pkce(code_verifier, auth_data["code_challenge"],
                          auth_data.get("code_challenge_method", "S256")):
            logger.warning("PKCE_FAIL | Invalid code_verifier")
            return None

    # Create token
    token = create_token(
        user=auth_data.get("user", "oauth_client"),
        client_id=auth_data.get("client_id"),
        scope=auth_data.get("scope", "mcp"),
    )

    logger.info(f"AUTH_CODE_EXCHANGED | client={auth_data.get('client_id')}")
    return token


def _validate_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    Validate JWT-Token vom /v1/client/login als alternativen MCP-Bearer.
    Returns payload dict bei valid, None bei invalid/expired.
    Lazy-Import um Circular-Imports zu vermeiden.
    """
    try:
        import jwt as _jwt
        from ..routes.client_auth import JWT_SECRET, JWT_ALGORITHM
        return _jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


def _jwt_authority(payload: Dict[str, Any]) -> tuple[str, int, str]:
    """Resolve current organizational authority for an AILinux JWT.

    Subscription/product roles are intentionally ignored. For known human
    accounts we consult current server-side account state so stale JWT claims do
    not silently retain WordPress-derived administrative access.
    """
    user_id = str(payload.get("email") or payload.get("sub") or "").strip().lower()
    try:
        from ..routes.client_auth import USER_REGISTRY, resolve_user_authority
        role, level, source = resolve_user_authority(
            user_id, USER_REGISTRY.get(user_id, {}),
            payload_role=str(payload.get("authority_role") or ""),
        )
        return role.value, int(level), source
    except Exception:
        return "human_member", 20, "fallback"


def _jwt_has_full_mcp_access(payload: Dict[str, Any]) -> bool:
    """Full MCP is reserved for current human owner/admin authority."""
    authority_role, _level, _source = _jwt_authority(payload)
    return authority_role in {"human_owner", "human_admin"}


PUBLIC_GUEST_MCP_PATHS = {
    "/v1/mcp", "/v1/mcp/",
    "/v1/mcp/sse", "/v1/mcp/sse/",
    "/v1/mcp/messages", "/v1/mcp/messages/",
    "/v1/mcp/stream", "/v1/mcp/stream/",
    "/v1/sse", "/v1/sse/", "/v1/messages", "/v1/messages/",
    "/mcp", "/mcp/", "/mcp/sse", "/mcp/sse/",
}


def _is_public_guest_mcp_path(path: str) -> bool:
    return str(path or "") in PUBLIC_GUEST_MCP_PATHS


async def require_mcp_auth(request: Request) -> str:
    """
    Unified MCP authentication.

    Existing credentials retain their normal authorization semantics. Exact MCP
    transport endpoints also permit a credential-less ``public_guest`` profile;
    this profile never receives ``internal_full`` and remains subject to the
    external MCP allowlist plus session-scoped local-workspace routing.
    """
    client_ip = request.client.host if request.client else "unknown"
    auth_header = request.headers.get("Authorization", "")
    query_token = ""
    if os.environ.get("ALLOW_QUERY_TOKEN_AUTH") == "1":
        query_token = (
            request.query_params.get("access_token")
            or request.query_params.get("mcp_token")
            or request.query_params.get("token")
            or ""
        ).strip()
    
    forwarded_port = request.headers.get("X-Forwarded-Port", "")
    forwarded_for = request.headers.get("X-Forwarded-For", "")

    if (
        MCP_ALLOW_UNAUTHENTICATED_LOCAL
        and client_ip in {"127.0.0.1", "::1"}
        and not forwarded_for
        and not forwarded_port
    ):
        request.state.mcp_auth_user = "internal"
        request.state.mcp_auth_method = "explicit-local-bypass"
        request.state.mcp_auth_full_access = True
        logger.warning("AUTH_LOCAL_BYPASS | IP: %s | explicitly enabled", client_ip)
        return "internal"

    logger.debug(
        "AUTH_CHECK | IP: %s | X-Fwd-Port: %s | X-Fwd-For: %s",
        client_ip, forwarded_port or "none", forwarded_for or "none",
    )

    # No credentials on the canonical MCP transport means public guest. Never
    # downgrade invalid/partial credentials to guest: if a credential was sent,
    # the normal authentication branches below must validate it.
    x_mcp_token = request.headers.get("X-MCP-Token", "").strip()
    has_credentials = bool(auth_header.strip() or query_token or x_mcp_token)
    if not has_credentials and _is_public_guest_mcp_path(request.url.path):
        request.state.mcp_auth_user = "public_guest"
        request.state.mcp_auth_method = "public_guest"
        request.state.mcp_auth_full_access = False
        request.state.mcp_auth_client_id = None
        logger.debug("AUTH_OK | IP: %s | Method: public_guest", client_ip)
        return "public_guest"
    
    if not MCP_AUTH_USER or not MCP_AUTH_PASS:
        logger.error("AUTH_ERROR | MCP_OAUTH_USER/PASS not configured")
        raise _unauthorized("Server authentication not configured")
    
    # Method 0: query parameter fallback for MCP clients that drop headers
    if query_token:
        if is_valid_token(query_token):
            request.state.mcp_auth_user = "oauth_client"
            request.state.mcp_auth_method = "query"
            request.state.mcp_auth_full_access = _token_has_full_mcp_access(query_token)
            logger.debug(
                "AUTH_OK | IP: %s | Method: query-param | FullAccess: %s",
                client_ip, request.state.mcp_auth_full_access,
            )
            return "oauth_client"
        logger.warning(f"AUTH_FAIL | IP: {client_ip} | Reason: invalid_query_param")
        raise _unauthorized("Invalid query credential")

    # Method 1: Bearer Token
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if is_valid_token(token):
            request.state.mcp_auth_user = "oauth_client"
            request.state.mcp_auth_method = "bearer"
            request.state.mcp_auth_full_access = _token_has_full_mcp_access(token)
            logger.debug(
                "AUTH_OK | IP: %s | Method: bearer | FullAccess: %s",
                client_ip, request.state.mcp_auth_full_access,
            )
            return "oauth_client"
        # JWT Bridge: Akzeptiere JWTs vom /v1/client/login als gültige MCP-Bearer
        jwt_payload = _validate_jwt(token)
        if jwt_payload:
            user = jwt_payload.get("email") or jwt_payload.get("sub") or "jwt_user"
            request.state.mcp_auth_user = user
            request.state.mcp_auth_method = "jwt"
            authority_role, authority_level, authority_source = _jwt_authority(jwt_payload)
            request.state.mcp_authority_role = authority_role
            request.state.mcp_authority_level = authority_level
            request.state.mcp_authority_source = authority_source
            request.state.mcp_auth_full_access = authority_role in {"human_owner", "human_admin"}
            request.state.mcp_auth_client_id = jwt_payload.get("client_id")
            logger.debug(
                "AUTH_OK | IP: %s | Method: jwt | User: %s | FullAccess: %s",
                client_ip, user, request.state.mcp_auth_full_access,
            )
            return user
        logger.warning(f"AUTH_FAIL | IP: {client_ip} | Reason: invalid_bearer")
        raise _unauthorized("Invalid bearer token")
    
    # Method 2: Basic Auth
    if auth_header.lower().startswith("basic "):
        username, password = _extract_basic_auth(request)
        if _validate_credentials(username, password):
            request.state.mcp_auth_user = username
            request.state.mcp_auth_method = "basic"
            logger.debug(f"AUTH_OK | IP: {client_ip} | Method: basic | User: {username}")
            return username
        else:
            logger.warning(f"AUTH_FAIL | IP: {client_ip} | Reason: invalid_basic")
            raise _unauthorized("Invalid credentials", "Basic")
    
    # No auth provided
    logger.warning(f"AUTH_FAIL | IP: {client_ip} | Reason: no_credentials")
    raise _unauthorized("Authentication required")


async def issue_token(form_data: OAuth2PasswordRequestForm = Depends()) -> dict:
    """
    Issue a bearer token via OAuth 2.0 Password Grant.

    Usage:
        POST /token
        Content-Type: application/x-www-form-urlencoded

        grant_type=password&username=user&password=pass
    """
    username = form_data.username or ""
    password = form_data.password or ""

    logger.info(f"TOKEN_REQUEST | User: {username} | Grant: password")

    if not _validate_credentials(username, password):
        logger.warning(f"TOKEN_FAIL | User: {username} | Reason: invalid_credentials")
        raise _unauthorized("Invalid credentials")

    token = create_token(user=username, scope="mcp")
    return {"access_token": token, "token_type": "bearer", "scope": "mcp"}


# ============================================================================
# OAuth 2.0 Discovery Helpers
# ============================================================================

def get_oauth_metadata(issuer: str) -> dict:
    """Get OAuth 2.0 Authorization Server Metadata (RFC 8414)."""
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "response_types_supported": ["code", "token"],
        "grant_types_supported": ["authorization_code", "password", "client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "scopes_supported": ["mcp", "mcp:read", "mcp:write", "mcp:tools"],
        "code_challenge_methods_supported": ["S256", "plain"],
    }


def get_protected_resource_metadata(issuer: str, resource: str = "/") -> dict:
    """Get OAuth 2.0 Protected Resource Metadata (RFC 9728 / RFC 8707).

    ``resource`` must be a canonical absolute URI. A bare "/" (the well-known
    root request) maps to the canonical MCP resource so Resource Indicators
    line up with the actual protected endpoint.
    """
    issuer = (issuer or DEFAULT_ISSUER).rstrip("/")
    path = resource or "/"
    if path in ("", "/"):
        canonical = f"{issuer}/v1/mcp/sse"
    elif path.startswith("http://") or path.startswith("https://"):
        canonical = path
    else:
        canonical = f"{issuer}/{path.lstrip('/')}"
    return {
        "resource": canonical,
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": list(SUPPORTED_MCP_SCOPES),
    }


# ============================================================================
# Initialize on module load
# ============================================================================

_load_persistent_tokens()
logger.info(f"MCP Auth initialized | User: {MCP_AUTH_USER or '(not set)'} | Tokens loaded: {len(_PERSISTENT_TOKENS)}")
