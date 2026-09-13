import os
import json
from pathlib import Path
# app/routes/client_auth.py
"""
Client Authentication Routes
Authentifizierung für Desktop-Clients und Mobile-Apps

Implementierung für TriForce Backend
Stand: 2025-12-13
"""

from fastapi import APIRouter, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import secrets
import base64
import time
import jwt
import logging
import urllib.request
import urllib.error

from ..services.shared_authority import (
    AuthorityRole,
    HUMAN_AUTHORITY_ROLES,
    authority_from_wordpress,
    authority_level,
    parse_authority_role,
)

logger = logging.getLogger(__name__)

# Pfad zur User-Datenbank
USERS_FILE_PATH = Path(__file__).parent.parent.parent / "config" / "users.json"

ALLOW_AUTO_REGISTER = os.environ.get("ALLOW_AUTO_REGISTER", "false").lower() in (
    "1", "true", "yes", "on"
)
if ALLOW_AUTO_REGISTER:
    logger.warning("ALLOW_AUTO_REGISTER is enabled - unknown login emails may create accounts")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo?id_token="

router = APIRouter(prefix="/auth", tags=["Client Auth"])

# JWT Secret (in Produktion aus ENV laden!)
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    import secrets
    JWT_SECRET = secrets.token_hex(32)
    logger.warning("JWT_SECRET not set, using random secret (sessions won't persist across restarts)")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


class ClientRole(str, Enum):
    ADMIN = "admin"          # Voller Zugriff (Markus)
    CLI_AGENT = "cli_agent"  # Server-Side Agents
    DESKTOP = "desktop"      # Desktop Clients
    MOBILE = "mobile"        # Mobile Clients (eingeschränkt)
    WEB = "web"              # Web Clients


# =============================================================================
# Client Registry
# =============================================================================

# In Produktion: Datenbank statt Dict
# Client Registry - dynamisch über API befüllt
# Keine hartcodierten Clients mehr - Registrierung erfolgt über /auth/register-client
CLIENT_REGISTRY: Dict[str, dict] = {}

# Default-Berechtigungen für neue Clients nach Rolle
DEFAULT_CLIENT_PERMISSIONS = {
    ClientRole.DESKTOP: {
        "allowed_tools": [
            "chat", "chat_smart", "weather", "current_time",
            "web_search", "smart_search", "multi_search",
            "client_*", "tristar_memory_*",
        ],
        "blocked_tools": [
            "codebase_*", "restart_*", "tristar_shell_exec", "vault_*",
        ]
    },
    ClientRole.CLI_AGENT: {
        "allowed_tools": ["chat", "chat_smart"],
        "blocked_tools": ["*"]  # Sehr eingeschränkt
    },
    ClientRole.WEB: {
        "allowed_tools": ["chat", "chat_smart", "web_search"],
        "blocked_tools": ["codebase_*", "restart_*", "tristar_shell_exec", "vault_*"]
    }
}

# Aktive Sessions
ACTIVE_SESSIONS: Dict[str, dict] = {}


def hash_secret(secret: str) -> str:
    """Secret hashen für Speicherung"""
    return hashlib.sha256(secret.encode()).hexdigest()


def verify_secret(secret: str, secret_hash: str) -> bool:
    """Secret gegen Hash prüfen"""
    return hash_secret(secret) == secret_hash


def generate_client_secret() -> str:
    """Neues Client-Secret generieren"""
    return secrets.token_urlsafe(32)


def normalize_tier(tier: Optional[str]) -> str:
    """Normalize legacy/client tier names to the server-side tier contract."""
    raw = (tier or "guest").strip().lower()
    aliases = {
        "free": "guest",
        "basic": "guest",
        "paid": "pro",
        "premium": "pro",
        "admin": "enterprise",
        "unlimited": "enterprise",
    }
    normalized = aliases.get(raw, raw)
    if normalized in {"guest", "registered", "pro", "enterprise"}:
        return normalized
    logger.warning("Unknown user tier %r, falling back to guest", tier)
    return "guest"


CANONICAL_ENTITLEMENTS = {
    "copa_ocr": "copa_ocr",
    "copa-ocr": "copa_ocr",
    "copa ocr": "copa_ocr",
    "copaocr": "copa_ocr",
    "Copa OCR": "copa_ocr",
    "970007": "copa_ocr",
    "1140151": "copa_ocr",
}


def normalize_entitlements(raw: Any) -> Dict[str, bool]:
    """Normalize all entitlement shapes to canonical keys.

    Canonical Copa key: copa_ocr.
    Only truthy values survive. False/null means no entitlement.
    """
    out: Dict[str, bool] = {}

    def canon(k: Any) -> str:
        text = str(k).strip()
        return CANONICAL_ENTITLEMENTS.get(text, CANONICAL_ENTITLEMENTS.get(text.lower(), text))

    if not raw:
        return out

    if isinstance(raw, dict):
        for k, v in raw.items():
            ck = canon(k)
            if ck and bool(v):
                out[ck] = True
        return out

    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            ck = canon(item)
            if ck:
                out[ck] = True
        return out

    if isinstance(raw, str):
        ck = canon(raw)
        if ck:
            out[ck] = True

    return out


def get_user_entitlements(user: Optional[dict]) -> Dict[str, Any]:
    """Return canonical product entitlements from current server user state.

    nova_entitlements is canonical.
    entitlements is a legacy mirror only if nova_entitlements is absent.
    """
    if not user:
        return {}

    if "nova_entitlements" in user:
        return normalize_entitlements(user.get("nova_entitlements") or {})

    if "entitlements" in user:
        return normalize_entitlements(user.get("entitlements") or {})

    return {}

def permissions_for_tier(tier: str) -> Tuple[ClientRole, List[str], List[str]]:
    """Map a user tier to client role and MCP tool permissions."""
    tier = normalize_tier(tier)
    if tier == "enterprise":
        return ClientRole.ADMIN, ["*"], []
    if tier == "pro":
        return (
            ClientRole.DESKTOP,
            [
                "chat", "chat_smart", "weather", "current_time",
                "web_search", "smart_search", "client_*", "tristar_memory_*",
            ],
            ["codebase_*", "restart_*", "vault_*", "tristar_shell_exec"],
        )
    if tier == "registered":
        return (
            ClientRole.DESKTOP,
            ["chat", "chat_smart", "weather", "current_time", "web_search", "client_*"],
            ["codebase_*", "restart_*", "vault_*", "tristar_shell_exec"],
        )
    return (
        ClientRole.DESKTOP,
        ["chat", "weather", "current_time", "web_search"],
        ["codebase_*", "restart_*", "vault_*", "tristar_*"],
    )


def create_jwt_token(
    client_id: str,
    role: str,
    email: str = None,
    entitlements: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
    expires_hours: int = JWT_EXPIRY_HOURS,
    authority_role: str = "",
    authority_level_value: int = 0,
) -> str:
    """JWT Token erstellen - enthält Email für Tier-Lookup"""
    client_roles = {item.value for item in ClientRole}
    is_client_role = role in client_roles
    role = role if is_client_role else normalize_tier(role)
    payload = {
        "client_id": client_id,
        "role": role,  # tier: guest, registered, pro, enterprise
        "exp": datetime.utcnow() + timedelta(hours=expires_hours),
        "iat": datetime.utcnow()
    }
    if not is_client_role:
        payload["tier"] = role
    # Email im Token speichern für Tier-Service
    if email:
        payload["email"] = email
        payload["sub"] = email  # Standard JWT subject claim
    if name:
        payload["name"] = name
    if authority_role:
        payload["authority_role"] = str(authority_role)
        payload["authority_level"] = int(authority_level_value or 0)
    # Do not embed entitlements in JWT. Token is auth only; license state is fetched live.
    
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt_token(token: str) -> dict:
    """JWT Token dekodieren"""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")


def decode_authorization_header(authorization: Optional[str]) -> dict:
    """Decode a Bearer Authorization header."""
    if not authorization:
        raise HTTPException(401, "Authorization header required")
    parts = authorization.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1]
    else:
        token = authorization.replace("Bearer ", "", 1).strip()
    if not token:
        raise HTTPException(401, "Authorization header required")
    return decode_jwt_token(token)


def resolve_user_authority(email: str, user: Optional[Dict[str, Any]] = None, *, payload_role: str = "") -> tuple[AuthorityRole, int, str]:
    """Resolve organizational authority independently from subscription tier.

    ADMIN_EMAIL is the root owner. WordPress-derived roles are trusted only when
    they were recently validated through the server-to-server auth bridge. A
    signed JWT claim is a fallback for non-registry sessions, not a source for
    privilege escalation.
    """
    email = str(email or "").strip().lower()
    user = user if isinstance(user, dict) else {}
    admin_email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    if admin_email and email and secrets.compare_digest(email, admin_email):
        role = AuthorityRole.HUMAN_OWNER
        return role, authority_level(role), "primary_owner"

    admin_ids = {
        value.strip().lower() for value in (os.environ.get("ADMIN_USER_IDS") or "").split(",") if value.strip()
    }
    if email and email in admin_ids:
        role = AuthorityRole.HUMAN_ADMIN
        return role, authority_level(role), "server_admin_list"

    explicit = str(user.get("authority_role") or "").strip().lower()
    wp_roles = user.get("wordpress_roles") or []
    wp_can_admin = bool(user.get("wordpress_can_admin", False))
    verified_at = str(user.get("wordpress_authority_verified_at") or "").strip()
    wp_fresh = False
    if verified_at:
        try:
            checked = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            max_age = max(300, min(int(os.environ.get("WP_AUTHORITY_MAX_AGE_SECONDS", "28800")), 86400))
            wp_fresh = (datetime.now(timezone.utc) - checked.astimezone(timezone.utc)).total_seconds() <= max_age
        except Exception:
            wp_fresh = False

    if wp_fresh:
        role = authority_from_wordpress(
            wp_roles=wp_roles, can_admin=wp_can_admin, explicit_role=explicit, is_owner=False,
        )
        return role, authority_level(role), "wordpress"

    # Server-managed non-WP role assignments may be used for lower-risk human
    # roles, but stale WordPress admin/security roles fail closed to member.
    if explicit:
        try:
            candidate = parse_authority_role(explicit)
        except PermissionError:
            candidate = AuthorityRole.HUMAN_MEMBER
        if candidate in HUMAN_AUTHORITY_ROLES and authority_level(candidate) <= authority_level(AuthorityRole.HUMAN_OPERATOR):
            return candidate, authority_level(candidate), "server_registry"

    if payload_role:
        try:
            candidate = parse_authority_role(payload_role)
        except PermissionError:
            candidate = AuthorityRole.HUMAN_MEMBER
        if candidate in HUMAN_AUTHORITY_ROLES and authority_level(candidate) <= authority_level(AuthorityRole.HUMAN_MEMBER):
            return candidate, authority_level(candidate), "signed_token_fallback"

    role = AuthorityRole.HUMAN_MEMBER
    return role, authority_level(role), "default"


def build_verified_session(payload: dict) -> Dict[str, Any]:
    """Build the public auth session shape used by ai-coder and Copa."""
    email = (payload.get("email") or payload.get("sub") or "").lower()
    user = USER_REGISTRY.get(email, {}) if email else {}
    tier_source = payload.get("tier") or user.get("tier")
    if not tier_source and payload.get("role") in {"guest", "registered", "pro", "enterprise", "free"}:
        tier_source = payload.get("role")
    tier = normalize_tier(tier_source)
    # Entitlements are always read from current server-side USER_REGISTRY.
    # JWT payload entitlements are stale and must never unlock Copa OCR.
    entitlements = get_user_entitlements(user)

    client_id = payload.get("client_id")
    authority_role, authority_level_value, authority_source = resolve_user_authority(
        email, user, payload_role=str(payload.get("authority_role") or "")
    )
    return {
        "valid": True,
        "user_id": email or client_id,
        "email": email,
        "client_id": client_id,
        "tier": tier,
        "role": tier,
        "authority_role": authority_role.value,
        "authority_level": authority_level_value,
        "authority_source": authority_source,
        "mcp_admin": authority_role in {AuthorityRole.HUMAN_OWNER, AuthorityRole.HUMAN_ADMIN},
        "name": payload.get("name") or user.get("name"),
        "nova_entitlements": entitlements,
        "entitlements": entitlements,
        "expires_at": payload.get("exp"),
        "issued_at": payload.get("iat"),
    }



def verify_wordpress_login(email: str, password: str) -> dict | None:
    """
    Validate login against WordPress, which is the source of truth for passwords.
    Returns WordPress user payload on success, otherwise None.
    """
    import urllib.request
    import urllib.error

    base = os.environ.get("WORDPRESS_AUTH_VALIDATE_URL", "https://ailinux.me/wp-json/nova-ai/v1/auth/validate")
    secret = (
        os.environ.get("NOVA_AI_INTERNAL_KEY")
        or os.environ.get("WEBHOOK_SECRET")
        or os.environ.get("TRIFORCE_ADMIN_SECRET")
        or ""
    )

    if not secret:
        logger.warning("WordPress auth fallback unavailable: no internal secret configured")
        return None

    body = json.dumps({"email": email, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        base,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Nova-Webhook-Secret": secret,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("WordPress auth fallback failed for %s: %s", email, exc)
        return None

    if not payload.get("ok"):
        return None

    user = payload.get("user") or {}
    if not isinstance(user, dict):
        return None

    return user


# =============================================================================
# User Registry (Simple User/Pass Auth)
# =============================================================================

# In Produktion: Datenbank!
# Tiers: guest (free), registered, pro, enterprise
# User Registry - dynamisch über Datenbank/API befüllt
# Keine hartcodierten User mehr - Authentifizierung erfolgt über /auth/login mit Client-Daten
# User-Daten werden vom Client bei Login mitgesendet und validiert
USER_REGISTRY: Dict[str, dict] = {}

def load_users_from_file() -> dict:
    """Load users while preserving persisted entitlements for the ENV admin."""
    users = {}

    # Load persisted account state first. Product entitlements are billing state
    # and must survive service restarts, including for ADMIN_EMAIL.
    if USERS_FILE_PATH.exists():
        try:
            with USERS_FILE_PATH.open("r", encoding="utf-8") as f:
                saved_users = json.load(f)
            if isinstance(saved_users, dict):
                users.update({email.lower(): data for email, data in saved_users.items()})
                logger.info("Loaded %d users from %s", len(saved_users), USERS_FILE_PATH)
        except Exception as exc:
            logger.error("Failed to load users: %s", exc)

    # ADMIN_PASSWORD remains authoritative for authentication and the admin tier,
    # but it must not erase entitlements written by WordPress/Lemon Squeezy.
    admin_email = (os.environ.get("ADMIN_EMAIL") or "").lower().strip()
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if admin_email and admin_password:
        current = users.get(admin_email)
        if not isinstance(current, dict):
            current = {}
        entitlements = get_user_entitlements(current)
        users[admin_email] = {
            **current,
            "password_hash": hash_secret(admin_password),
            "tier": "enterprise",
            "name": current.get("name") or "Admin",
            "billing": True,
            "nova_entitlements": entitlements,
            "entitlements": entitlements,
        }

    return users


def save_user_to_file(email: str, user_data: dict) -> bool:
    """Speichere neuen User in users.json"""
    try:
        # Lade existierende User
        users = {}
        if USERS_FILE_PATH.exists():
            with open(USERS_FILE_PATH, 'r') as f:
                users = json.load(f)
        
        # Füge neuen User hinzu
        users[email.lower()] = user_data
        
        # Speichere zurück
        USERS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(USERS_FILE_PATH, 'w') as f:
            json.dump(users, f, indent=2)
        
        logger.info(f"Saved user {email} to {USERS_FILE_PATH}")
        return True
    except Exception as e:
        logger.error(f"Failed to save user {email}: {e}")
        return False


def register_new_user(email: str, password: str, name: str = None, tier: str = "guest") -> dict:
    """Registriere einen neuen User und speichere in users.json"""
    email = email.lower().strip()
    
    # Prüfe ob User bereits existiert
    if email in USER_REGISTRY:
        return None
    
    # Erstelle User-Daten
    user_data = {
        "password_hash": hash_secret(password),
        "tier": normalize_tier(tier),
        "name": name or email.split("@")[0],
        "billing": False,
        "nova_entitlements": {},
        "created_at": datetime.now().isoformat(),
    }
    
    # Speichere in Datei
    if save_user_to_file(email, user_data):
        # Füge zu Registry hinzu
        USER_REGISTRY[email] = user_data
        return user_data
    
    return None


# Lade User beim Start
USER_REGISTRY.update(load_users_from_file())


# =============================================================================
# Request/Response Models
# =============================================================================

class UserLoginRequest(BaseModel):
    """User Login Request - email/password (auto-registers new users)"""
    email: str = Field(..., description="Email address")
    password: str = Field(..., description="Password")
    name: Optional[str] = Field(None, description="Display name (optional, for new users)")


class GoogleLoginRequest(BaseModel):
    """Sign in with Google credential from Google Identity Services."""
    credential: str = Field(..., description="Google ID token / credential JWT")


class AuthConfigResponse(BaseModel):
    google_client_id: str = ""
    google_enabled: bool = False


class BrowserCodeRequest(BaseModel):
    purpose: str = Field(..., pattern="^(wordpress|app)$")
    app_id: str = ""
    redirect_uri: str = ""
    code_challenge: str = ""
    code_challenge_method: str = "S256"
    state: str = ""


class BrowserCodeExchangeRequest(BaseModel):
    code: str
    purpose: str = Field(..., pattern="^(wordpress|app)$")
    app_id: str = ""
    redirect_uri: str = ""
    code_verifier: str = ""


class UserLoginResponse(BaseModel):
    """User Login Response"""
    user_id: str
    token: str
    token_type: str = "Bearer"
    expires_in: int = JWT_EXPIRY_HOURS * 3600
    tier: str
    account_role: str
    authority_role: str = AuthorityRole.HUMAN_MEMBER.value
    authority_level: int = 20
    authority_source: str = "default"
    mcp_admin: bool = False
    client_id: str  # Server-assigned per login
    email: str
    name: Optional[str] = None
    nova_entitlements: Dict[str, Any] = Field(default_factory=dict)
    entitlements: Dict[str, Any] = Field(default_factory=dict)



# User Register Models (für explizite Registrierung mit Beta-Code)
class UserRegisterRequest(BaseModel):
    """User Registration Request"""
    email: str = Field(..., description="Email address")
    password: str = Field(..., min_length=6, description="Password (min 6 chars)")
    name: Optional[str] = Field(None, description="Display name")
    beta_code: Optional[str] = Field(None, description="Beta code for tier upgrade")


class UserRegisterResponse(BaseModel):
    """User Registration Response"""
    success: bool
    message: str
    tier: str
    email: str


# Beta codes für Tier-Upgrades
BETA_CODES = {
    "AILINUX2026": "pro",
    "TRIFORCE": "pro",
    "MARKUS": "enterprise",
    "ADMIN": "enterprise",
}

class ClientAuthRequest(BaseModel):
    """Client Auth Request"""
    client_id: str = Field(..., description="Client-ID")
    client_secret: str = Field(..., description="Client-Secret")
    device_name: Optional[str] = Field(None, description="Gerätename")
    capabilities: Optional[List[str]] = Field(default_factory=list, description="Client-Fähigkeiten")


class ClientAuthResponse(BaseModel):
    """Client Auth Response"""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int  # Sekunden
    role: str
    client_id: str
    allowed_tools: List[str]


class ClientRegisterRequest(BaseModel):
    """Neuen Client registrieren (nur Admin)"""
    client_id: str
    name: str
    role: ClientRole = ClientRole.DESKTOP
    allowed_tools: List[str] = Field(default_factory=list)
    blocked_tools: List[str] = Field(default_factory=list)


class ClientRegisterResponse(BaseModel):
    """Response mit generiertem Secret"""
    client_id: str
    client_secret: str  # Nur einmal angezeigt!
    message: str


def issue_user_login_response(email: str, user: dict) -> UserLoginResponse:
    """Create a normal AILinux session response for any authenticated user."""
    email = email.lower().strip()
    email_prefix = email.split("@")[0][:10]
    client_id = f"client-{email_prefix}-{secrets.token_hex(8)}"

    tier = normalize_tier(user.get("tier"))
    entitlements = get_user_entitlements(user)
    role, allowed, blocked = permissions_for_tier(tier)
    authority_role, authority_level_value, authority_source = resolve_user_authority(email, user)

    token = create_jwt_token(
        client_id,
        tier,
        email=email,
        entitlements=entitlements,
        name=user.get("name"),
        authority_role=authority_role.value,
        authority_level_value=authority_level_value,
    )

    CLIENT_REGISTRY[client_id] = {
        "secret_hash": "",
        "name": f"{user.get('name') or email}'s Client",
        "role": role,
        "created_at": datetime.now().isoformat(),
        "email": email,
        "allowed_tools": allowed,
        "blocked_tools": blocked,
    }

    ACTIVE_SESSIONS[client_id] = {
        "email": email,
        "connected_at": datetime.now().isoformat(),
        "last_seen": datetime.now().isoformat(),
    }

    return UserLoginResponse(
        user_id=email,
        token=token,
        tier=tier,
        account_role=role.value,
        authority_role=authority_role.value,
        authority_level=authority_level_value,
        authority_source=authority_source,
        mcp_admin=authority_role in {AuthorityRole.HUMAN_OWNER, AuthorityRole.HUMAN_ADMIN},
        client_id=client_id,
        email=email,
        name=user.get("name"),
        nova_entitlements=entitlements,
        entitlements=entitlements,
    )


def verify_google_credential(credential: str) -> dict:
    """Validate a Google Identity Services ID token via Google's tokeninfo endpoint."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google login is not configured")
    if not credential or len(credential) < 40:
        raise HTTPException(400, "Missing Google credential")

    url = GOOGLE_TOKENINFO_URL + credential
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning("Google tokeninfo rejected credential: HTTP %s", e.code)
        raise HTTPException(401, "Invalid Google credential")
    except Exception as e:
        logger.error("Google tokeninfo verification failed: %s", e)
        raise HTTPException(502, "Google verification unavailable")

    if data.get("aud") != GOOGLE_CLIENT_ID:
        logger.warning("Google credential audience mismatch: %r", data.get("aud"))
        raise HTTPException(401, "Invalid Google audience")
    if data.get("email_verified") not in ("true", True):
        raise HTTPException(401, "Google email is not verified")
    if not data.get("email"):
        raise HTTPException(401, "Google credential has no email")

    return data


# =============================================================================
# Browser login one-time codes (WordPress bridge + native app PKCE)
# =============================================================================

_BROWSER_CODE_TTL = 90
_BROWSER_CODE_PREFIX = "ailinux:auth:browser-code:"
_BROWSER_CODE_FALLBACK: Dict[str, tuple[float, dict]] = {}
_BROWSER_APP_IDS = {
    "ailinux-ai-coder",
    "ailinux-copa",
    "ailinux-control-center",
    "ailinux-client",
}
_browser_redis = None


async def _get_browser_redis():
    global _browser_redis
    if _browser_redis is False:
        return None
    if _browser_redis is None:
        try:
            import redis.asyncio as aioredis
            _browser_redis = aioredis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                encoding="utf-8", decode_responses=True,
            )
            await _browser_redis.ping()
        except Exception as exc:
            logger.warning("Browser auth Redis unavailable, using process-local fallback: %s", exc)
            _browser_redis = False
            return None
    return _browser_redis


_BROWSER_APP_HTTPS_REDIRECTS = {
    app_id: f"https://api.ailinux.me/v1/auth/browser/app-callback/{app_id}"
    for app_id in _BROWSER_APP_IDS
}


def _validate_browser_app_redirect(app_id: str, redirect_uri: str) -> str:
    from urllib.parse import urlparse
    app_id = (app_id or "").strip()
    value = (redirect_uri or "").strip()
    if app_id not in _BROWSER_APP_IDS:
        raise HTTPException(400, "Unsupported AILinux app")
    parsed = urlparse(value)
    if parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise HTTPException(400, "Invalid app redirect_uri")

    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}:
        if not parsed.port or not (1024 <= int(parsed.port) <= 65535):
            raise HTTPException(400, "App redirect_uri requires a non-privileged loopback port")
        return value

    expected = _BROWSER_APP_HTTPS_REDIRECTS.get(app_id)
    if parsed.scheme == "https" and value == expected:
        return value

    raise HTTPException(400, "App redirect_uri must use loopback HTTP or the verified AILinux HTTPS App Link")


def _validate_pkce_challenge(challenge: str, method: str) -> str:
    challenge = (challenge or "").strip()
    if method != "S256" or not (43 <= len(challenge) <= 128):
        raise HTTPException(400, "PKCE S256 challenge required")
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in challenge):
        raise HTTPException(400, "Invalid PKCE challenge")
    return challenge


def _pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def _store_browser_code(payload: dict) -> str:
    code = secrets.token_urlsafe(32)
    key = _BROWSER_CODE_PREFIX + code
    redis_client = await _get_browser_redis()
    if redis_client is not None:
        await redis_client.set(key, json.dumps(payload, separators=(",", ":")), ex=_BROWSER_CODE_TTL, nx=True)
    else:
        now = time.monotonic()
        for old, (expires, _value) in list(_BROWSER_CODE_FALLBACK.items()):
            if expires <= now:
                _BROWSER_CODE_FALLBACK.pop(old, None)
        _BROWSER_CODE_FALLBACK[code] = (now + _BROWSER_CODE_TTL, payload)
    return code


async def _consume_browser_code(code: str) -> Optional[dict]:
    code = (code or "").strip()
    if not code:
        return None
    key = _BROWSER_CODE_PREFIX + code
    redis_client = await _get_browser_redis()
    if redis_client is not None:
        raw = await redis_client.getdel(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None
    item = _BROWSER_CODE_FALLBACK.pop(code, None)
    if not item or item[0] <= time.monotonic():
        return None
    return item[1]


# =============================================================================
# Auth Endpoints
# =============================================================================

@router.get("/config", response_model=AuthConfigResponse)
async def auth_config():
    """Public auth frontend configuration."""
    return AuthConfigResponse(
        google_client_id=GOOGLE_CLIENT_ID,
        google_enabled=bool(GOOGLE_CLIENT_ID),
    )


@router.post("/browser/code")
async def create_browser_code(request: BrowserCodeRequest, authorization: str = Header(None)):
    """Create a short-lived one-time code from an already authenticated browser session."""
    payload = decode_authorization_header(authorization)
    email = str(payload.get("email") or payload.get("sub") or "").lower().strip()
    if not email or email not in USER_REGISTRY:
        raise HTTPException(401, "Authenticated account not found")

    record = {
        "purpose": request.purpose,
        "email": email,
        "created_at": int(time.time()),
    }
    if request.purpose == "app":
        redirect_uri = _validate_browser_app_redirect(request.app_id, request.redirect_uri)
        challenge = _validate_pkce_challenge(request.code_challenge, request.code_challenge_method)
        if not request.state or len(request.state) < 16 or len(request.state) > 256:
            raise HTTPException(400, "Strong state value required")
        record.update({
            "app_id": request.app_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": request.state,
        })

    code = await _store_browser_code(record)
    response = {"ok": True, "code": code, "expires_in": _BROWSER_CODE_TTL, "state": record.get("state", "")}
    if request.purpose == "app":
        from urllib.parse import quote
        # Native HTTPS App Links carry the short-lived authorization code in the
        # fragment so a browser fallback/proxy never receives it. Desktop loopback
        # callbacks keep query parameters because a local HTTP listener cannot see
        # URL fragments.
        separator = "#" if redirect_uri.startswith("https://") else "?"
        response["handoff_url"] = (
            f"{redirect_uri}{separator}code={quote(code, safe='')}"
            f"&state={quote(str(record.get('state') or ''), safe='')}"
        )
    return response


@router.get("/browser/app-callback/{app_id}")
async def browser_app_callback_fallback(app_id: str):
    """Fallback when no installed app claims the verified HTTPS App Link."""
    if app_id not in _BROWSER_APP_IDS:
        raise HTTPException(404, "Unknown AILinux app")
    html = """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'><title>AILinux App Login</title></head><body><main><h1>AILinux app login handoff</h1><p>No verified AILinux app claimed this link. Return to the app and retry the login handoff.</p><p>The one-time login code remains only in the URL fragment and was not sent to this server.</p><button onclick='history.back()'>Back</button></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/browser/exchange", response_model=UserLoginResponse)
async def exchange_browser_code(request: BrowserCodeExchangeRequest):
    """Consume a one-time browser code and issue a fresh purpose-specific AILinux session."""
    record = await _consume_browser_code(request.code)
    if not record or record.get("purpose") != request.purpose:
        raise HTTPException(400, "Invalid or expired browser login code")

    if request.purpose == "app":
        redirect_uri = _validate_browser_app_redirect(request.app_id, request.redirect_uri)
        if request.app_id != record.get("app_id") or redirect_uri != record.get("redirect_uri"):
            raise HTTPException(400, "Browser login binding mismatch")
        verifier = (request.code_verifier or "").strip()
        if not (43 <= len(verifier) <= 128):
            raise HTTPException(400, "Invalid PKCE verifier")
        try:
            challenge = _pkce_s256(verifier)
        except (UnicodeEncodeError, ValueError):
            raise HTTPException(400, "Invalid PKCE verifier")
        if not secrets.compare_digest(challenge, str(record.get("code_challenge") or "")):
            raise HTTPException(400, "PKCE verification failed")

    email = str(record.get("email") or "").lower().strip()
    user = USER_REGISTRY.get(email)
    if not user:
        raise HTTPException(401, "Browser login account no longer exists")
    return issue_user_login_response(email, user)


@router.post("/google", response_model=UserLoginResponse)
async def google_login(request: GoogleLoginRequest):
    """Sign in or register with Google Identity Services."""
    profile = verify_google_credential(request.credential)
    email = profile["email"].lower().strip()
    name = profile.get("name") or email.split("@")[0]
    sub = profile.get("sub") or ""

    user = USER_REGISTRY.get(email)
    if not user:
        user = {
            "password_hash": hash_secret(secrets.token_urlsafe(32)),
            "tier": "pro",
            "name": name,
            "billing": False,
            "nova_entitlements": {},
            "auth_provider": "google",
            "google_sub": sub,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        if not save_user_to_file(email, user):
            raise HTTPException(500, "Failed to register Google user")
        USER_REGISTRY[email] = user
        logger.info("New Google user registered: %s", email)
    else:
        user["auth_provider"] = user.get("auth_provider") or "password+google"
        if sub:
            user["google_sub"] = sub
        if name and not user.get("name"):
            user["name"] = name
        user["updated_at"] = datetime.now().isoformat()
        save_user_to_file(email, user)

    response = issue_user_login_response(email, user)
    logger.info("Google user logged in: %s (%s) -> %s", email, response.tier, response.client_id)
    return response


@router.post("/auth/login", response_model=UserLoginResponse)  # Compatibility: ai-coder expects /v1/auth/login
@router.post("/login", response_model=UserLoginResponse)
async def user_login(request: UserLoginRequest):
    """
    User Login with email/password
    Server assigns a new client_id per login
    """
    email = request.email.lower().strip()
    user = USER_REGISTRY.get(email)
    wp_user = None

    logger.info(
        "COPA_LOGIN_DEBUG start email=%s local_user=%s has_hash=%s tier=%s ents=%s",
        email,
        bool(user),
        bool(user.get("password_hash")) if isinstance(user, dict) else False,
        user.get("tier") if isinstance(user, dict) else None,
        list((get_user_entitlements(user) or {}).keys()) if isinstance(user, dict) else [],
    )

    # WordPress is the source of truth for login/password.
    # TriForce users.json is only the client/entitlement mirror.
    needs_wp_auth = (not user) or (not isinstance(user, dict)) or (not user.get("password_hash"))
    logger.info("COPA_LOGIN_DEBUG needs_wp_auth email=%s needs_wp_auth=%s", email, needs_wp_auth)

    if needs_wp_auth:
        wp_user = verify_wordpress_login(email, request.password)
        if not wp_user:
            if not user and ALLOW_AUTO_REGISTER:
                logger.warning(f"Auto-registering unknown email via login because ALLOW_AUTO_REGISTER=true: {email}")
                user = register_new_user(
                    email=email,
                    password=request.password,
                    name=request.name if hasattr(request, 'name') and request.name else None,
                    tier="guest"
                )
                if not user:
                    logger.error(f"Failed to register new user: {email}")
                    raise HTTPException(500, "Failed to register user")
            else:
                logger.warning(f"WordPress login failed for: {email}")
                raise HTTPException(401, "Invalid email or password")
        else:
            existing = user if isinstance(user, dict) else {}
            wp_entitlements = normalize_entitlements(
                wp_user.get("nova_entitlements") if "nova_entitlements" in wp_user else wp_user.get("entitlements")
            )
            user = {
                **existing,
                "tier": wp_user.get("tier") or existing.get("tier") or "free",
                "name": wp_user.get("name") or existing.get("name") or email.split("@", 1)[0],
                "billing": existing.get("billing", False),
                "nova_entitlements": wp_entitlements,
                "entitlements": wp_entitlements,
                "auth_provider": "wordpress",
                "wordpress_roles": list(wp_user.get("wordpress_roles") or []),
                "wordpress_can_admin": bool(wp_user.get("wordpress_can_admin", False)),
                "authority_role": str(wp_user.get("authority_role") or ""),
                "wordpress_authority_verified_at": datetime.now(timezone.utc).isoformat(),
            }
            save_user_to_file(email, user)
            USER_REGISTRY[email] = user
            logger.info(f"WordPress-authenticated user: {email}")
    else:
        # Existing local TriForce password hash.
        local_hash_ok = verify_secret(request.password, user["password_hash"])
        logger.info("COPA_LOGIN_DEBUG local_hash email=%s ok=%s", email, local_hash_ok)
        if not local_hash_ok:
            logger.info("COPA_LOGIN_DEBUG wordpress_fallback_start email=%s", email)
            wp_user = verify_wordpress_login(email, request.password)
            logger.info(
                "COPA_LOGIN_DEBUG wordpress_fallback_result email=%s ok=%s wp_keys=%s",
                email,
                bool(wp_user),
                list(wp_user.keys()) if isinstance(wp_user, dict) else [],
            )
            if not wp_user:
                logger.warning(f"Invalid password for: {email}")
                raise HTTPException(401, "Invalid email or password")
            user["auth_provider"] = "wordpress"
            user["tier"] = wp_user.get("tier") or user.get("tier") or "free"
            user["name"] = wp_user.get("name") or user.get("name") or email.split("@", 1)[0]
            wp_entitlements = normalize_entitlements(
                wp_user.get("nova_entitlements") if "nova_entitlements" in wp_user else wp_user.get("entitlements")
            )
            user["nova_entitlements"] = wp_entitlements
            user["entitlements"] = wp_entitlements
            user["wordpress_roles"] = list(wp_user.get("wordpress_roles") or [])
            user["wordpress_can_admin"] = bool(wp_user.get("wordpress_can_admin", False))
            user["authority_role"] = str(wp_user.get("authority_role") or "")
            user["wordpress_authority_verified_at"] = datetime.now(timezone.utc).isoformat()
            save_user_to_file(email, user)
            USER_REGISTRY[email] = user

    response = issue_user_login_response(email, user)
    logger.info(
        "COPA_LOGIN_DEBUG success email=%s tier=%s ents=%s token_len=%s client_id=%s",
        email,
        response.tier,
        list((response.nova_entitlements or {}).keys()),
        len(response.token or ""),
        response.client_id,
    )
    logger.info(f"User logged in: {email} ({response.tier}) -> {response.client_id}")
    return response


@router.post("/refresh")
async def refresh_auth(authorization: str = Header(None)):
    """Renew a still-valid client JWT without accepting expired credentials."""
    payload = decode_authorization_header(authorization)
    client_id = str(payload.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(401, "Token has no client identity")

    email = str(payload.get("email") or payload.get("sub") or "").lower().strip() or None
    user = USER_REGISTRY.get(email, {}) if email else {}
    role = str(payload.get("role") or payload.get("tier") or "guest")
    if email and user:
        role = normalize_tier(user.get("tier") or role)
    name = user.get("name") if user else payload.get("name")

    authority_role, authority_level_value, _ = resolve_user_authority(
        email or "", user, payload_role=str(payload.get("authority_role") or "")
    )
    token = create_jwt_token(
        client_id, role, email=email, name=name,
        authority_role=authority_role.value, authority_level_value=authority_level_value,
    )
    if client_id in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS[client_id]["last_seen"] = datetime.now().isoformat()
    return {
        "token": token,
        "token_type": "Bearer",
        "expires_in": JWT_EXPIRY_HOURS * 3600,
    }


@router.get("/verify")
async def verify_auth(authorization: str = Header(None)):
    """
    Validate a user/client JWT and return the session contract used by desktop clients.
    """
    payload = decode_authorization_header(authorization)
    return build_verified_session(payload)


@router.get("/client/handshake")
async def client_handshake(authorization: str = Header(None)):
    """
    Return client capabilities and canonical endpoint paths after authentication.
    """
    payload = decode_authorization_header(authorization)
    session = build_verified_session(payload)
    tier = session["tier"]
    default_role, default_allowed, default_blocked = permissions_for_tier(tier)

    client_id = session.get("client_id")
    client = CLIENT_REGISTRY.get(client_id, {}) if client_id else {}
    allowed_tools = client.get("allowed_tools") or default_allowed
    blocked_tools = client.get("blocked_tools") or default_blocked
    role = client.get("role") or default_role
    role_value = role.value if isinstance(role, ClientRole) else role

    if client_id in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS[client_id]["last_seen"] = datetime.now().isoformat()

    return {
        "ok": True,
        "valid": True,
        "client_id": client_id,
        "user_id": session.get("user_id"),
        "email": session.get("email"),
        "tier": tier,
        "role": role_value,
        "allowed_tools": allowed_tools,
        "blocked_tools": blocked_tools,
        "capabilities": {
            "chat": True,
            "models": True,
            "mcp": True,
            "shared_notify": True,
            "presence": True,
            "ocr": bool(session.get("nova_entitlements", {}).get("copa_ocr")),
        },
        "endpoints": {
            "auth_verify": "/v1/auth/verify",
            "chat": "/v1/client/chat",
            "models": "/v1/client/models",
            "mcp": "/v1/mcp",
            "shared_notify": "/v1/notify-network",
            "ocr_mistral": "/v1/client/ocr/mistral",
            "ocr_status": "/v1/client/ocr/status",
        },
        "nova_entitlements": session.get("nova_entitlements", {}),
        "entitlements": session.get("entitlements", {}),
    }


@router.post("/register", response_model=UserRegisterResponse)
async def user_register(request: UserRegisterRequest):
    """
    Register a new user account.
    Beta phase: All accounts get PRO tier automatically!
    """
    email = request.email.lower().strip()
    
    # Prüfe ob User bereits existiert
    if email in USER_REGISTRY:
        raise HTTPException(400, "Email already registered. Please login instead.")
    
    # Bestimme Tier basierend auf Beta-Code
    tier = "pro"  # Beta: Alle bekommen PRO!
    if request.beta_code:
        beta_tier = BETA_CODES.get(request.beta_code.upper())
        if beta_tier:
            tier = beta_tier
            logger.info(f"Beta code used: {request.beta_code} -> {tier}")
    
    # Registriere neuen User
    user = register_new_user(
        email=email,
        password=request.password,
        name=request.name,
        tier=tier
    )
    
    if not user:
        raise HTTPException(500, "Failed to register user")
    
    logger.info(f"New user registered: {email} (tier: {tier})")
    
    return UserRegisterResponse(
        success=True,
        message=f"Account created! Tier: {tier.upper()}",
        tier=tier,
        email=email
    )



@router.post("/client", response_model=ClientAuthResponse)
async def client_auth(request: ClientAuthRequest):
    """
    Client authentifizieren
    
    Gibt JWT Token zurück für weitere API-Calls
    """
    client = CLIENT_REGISTRY.get(request.client_id)
    
    if not client:
        logger.warning(f"Unknown client: {request.client_id}")
        raise HTTPException(401, "Unknown client")
    
    # Secret prüfen (falls bereits gesetzt)
    if client.get("secret_hash"):
        if not verify_secret(request.client_secret, client["secret_hash"]):
            logger.warning(f"Invalid secret for client: {request.client_id}")
            raise HTTPException(401, "Invalid credentials")
    else:
        # Erstes Login - Secret setzen
        client["secret_hash"] = hash_secret(request.client_secret)
        logger.info(f"First login for client: {request.client_id}, secret set")
    
    # Token generieren
    token = create_jwt_token(request.client_id, client["role"].value)
    
    # Session tracken
    ACTIVE_SESSIONS[request.client_id] = {
        "device_name": request.device_name,
        "capabilities": request.capabilities,
        "connected_at": datetime.now().isoformat(),
        "last_seen": datetime.now().isoformat()
    }
    
    logger.info(f"Client authenticated: {request.client_id} ({client['role'].value})")
    
    return ClientAuthResponse(
        access_token=token,
        expires_in=JWT_EXPIRY_HOURS * 3600,
        role=client["role"].value,
        client_id=request.client_id,
        allowed_tools=client.get("allowed_tools", [])
    )


@router.post("/client/register", response_model=ClientRegisterResponse)
async def register_client(
    request: ClientRegisterRequest,
    authorization: str = Header(None)
):
    """
    Neuen Client registrieren (nur Admin)
    
    Generiert ein neues Client-Secret das nur einmal angezeigt wird!
    """
    # Admin-Check
    if not authorization:
        raise HTTPException(401, "Authorization header required")
    
    try:
        token = authorization.replace("Bearer ", "")
        payload = decode_jwt_token(token)
        if payload.get("role") != "admin":
            raise HTTPException(403, "Admin access required")
    except:
        raise HTTPException(403, "Admin access required")
    
    # Prüfen ob Client schon existiert
    if request.client_id in CLIENT_REGISTRY:
        raise HTTPException(400, f"Client already exists: {request.client_id}")
    
    # Secret generieren
    client_secret = generate_client_secret()
    
    # Client registrieren
    CLIENT_REGISTRY[request.client_id] = {
        "secret_hash": hash_secret(client_secret),
        "name": request.name,
        "role": request.role,
        "created_at": datetime.now().isoformat(),
        "allowed_tools": request.allowed_tools or [
            "chat", "weather", "current_time", "web_search"
        ],
        "blocked_tools": request.blocked_tools or [
            "codebase_*", "restart_*", "vault_*"
        ]
    }
    
    logger.info(f"New client registered: {request.client_id}")
    
    return ClientRegisterResponse(
        client_id=request.client_id,
        client_secret=client_secret,
        message="WICHTIG: Speichere das Secret sicher - es wird nur einmal angezeigt!"
    )


@router.get("/client/me")
async def get_client_info(authorization: str = Header(None)):
    """Eigene Client-Info abrufen"""
    if not authorization:
        raise HTTPException(401, "Authorization header required")
    
    token = authorization.replace("Bearer ", "")
    payload = decode_jwt_token(token)
    
    client_id = payload.get("client_id")
    client = CLIENT_REGISTRY.get(client_id)
    
    if not client:
        raise HTTPException(404, "Client not found")
    
    session = ACTIVE_SESSIONS.get(client_id, {})
    
    return {
        "client_id": client_id,
        "name": client.get("name"),
        "role": client.get("role").value if isinstance(client.get("role"), ClientRole) else client.get("role"),
        "allowed_tools": client.get("allowed_tools", []),
        "blocked_tools": client.get("blocked_tools", []),
        "session": session
    }


@router.get("/client/list")
async def list_clients(authorization: str = Header(None)):
    """
    Alle Clients auflisten (nur Admin)
    """
    if not authorization:
        raise HTTPException(401, "Authorization header required")
    
    token = authorization.replace("Bearer ", "")
    payload = decode_jwt_token(token)
    
    if payload.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    
    clients = []
    for client_id, client in CLIENT_REGISTRY.items():
        session = ACTIVE_SESSIONS.get(client_id, {})
        clients.append({
            "client_id": client_id,
            "name": client.get("name"),
            "role": client.get("role").value if isinstance(client.get("role"), ClientRole) else client.get("role"),
            "created_at": client.get("created_at"),
            "is_online": client_id in ACTIVE_SESSIONS,
            "last_seen": session.get("last_seen")
        })
    
    return {"clients": clients, "count": len(clients)}


@router.delete("/client/{client_id}")
async def delete_client(client_id: str, authorization: str = Header(None)):
    """
    Client entfernen (nur Admin)
    """
    if not authorization:
        raise HTTPException(401, "Authorization header required")
    
    token = authorization.replace("Bearer ", "")
    payload = decode_jwt_token(token)
    
    if payload.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    
    if client_id not in CLIENT_REGISTRY:
        raise HTTPException(404, f"Client not found: {client_id}")
    
    del CLIENT_REGISTRY[client_id]
    
    if client_id in ACTIVE_SESSIONS:
        del ACTIVE_SESSIONS[client_id]
    
    logger.info(f"Client deleted: {client_id}")
    
    return {"deleted": True, "client_id": client_id}


# =============================================================================
# Tool Permission Check
# =============================================================================

def is_tool_allowed(client_id: str, tool_name: str) -> bool:
    """
    Prüft ob ein Client ein Tool nutzen darf
    
    Wildcards werden unterstützt:
    - "client_*" erlaubt alle Tools die mit "client_" beginnen
    - "codebase_*" in blocked_tools blockiert alle codebase-Tools
    """
    client = CLIENT_REGISTRY.get(client_id)
    if not client:
        return False
    
    allowed = client.get("allowed_tools", [])
    blocked = client.get("blocked_tools", [])
    
    # Blocked hat Vorrang
    for pattern in blocked:
        if pattern.endswith("*"):
            if tool_name.startswith(pattern[:-1]):
                return False
        elif pattern == tool_name:
            return False
    
    # Allowed prüfen
    for pattern in allowed:
        if pattern.endswith("*"):
            if tool_name.startswith(pattern[:-1]):
                return True
        elif pattern == tool_name:
            return True
    
    return False


# =============================================================================
# Dependency für geschützte Routen
# =============================================================================

async def get_current_client(authorization: str = Header(None)) -> dict:
    """
    FastAPI Dependency für Client-Auth
    
    Verwendung:
    @router.get("/protected")
    async def protected_route(client: dict = Depends(get_current_client)):
        ...
    """
    if not authorization:
        raise HTTPException(401, "Authorization header required")
    
    token = authorization.replace("Bearer ", "")
    payload = decode_jwt_token(token)
    
    client_id = payload.get("client_id")
    client = CLIENT_REGISTRY.get(client_id)
    
    if not client:
        raise HTTPException(401, "Client not found")
    
    # Last seen aktualisieren
    if client_id in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS[client_id]["last_seen"] = datetime.now().isoformat()
    
    return {
        "client_id": client_id,
        "role": payload.get("role"),
        "client": client
    }


async def require_admin(authorization: str = Header(None)) -> dict:
    """
    Dependency für Admin-Only Routen
    """
    client = await get_current_client(authorization)
    
    if client.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    
    return client
