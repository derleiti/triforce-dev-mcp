"""
app/utils/mcp_security.py
==========================
Gemeinsame Allowlist + Internal-Profil-Helper fuer alle MCP-Routen.

Zwei Routen exponieren MCP:
  - app/routes/mcp_remote.py  (POST /mcp, /mcp/)
  - app/routes/mcp.py         (POST /v1/mcp via mcp_unified_endpoint)

Beide nutzen diese Helper damit die Allowlist und der internal_full-Bypass
genau einmal definiert sind.

Default-Deny: Wenn der Caller nicht "internal_full" ist, sieht und ruft er
nur die Tools aus EXTERNAL_TOOL_ALLOWLIST.

internal_full qualifiziert sich ueber explizite Autorisierung oder einen
vertrauenswuerdigen internen Ursprung:
  1) request.state.mcp_auth_full_access == True nach erfolgreicher AuthZ,
  2) gueltiger interner HMAC/Secret-Header, oder
  3) Source-IP in Loopback / WireGuard-Mesh (10.10.0.0/24).

Authentifizierungsmethoden wie Bearer/Basic/Query sind fuer sich allein keine
Autorisierung. X-Forwarded-For wird nur von bekannten lokalen Proxy-Netzen
akzeptiert. Der legacy Header X-TriForce-All gilt nur bei internem Ursprung.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import time as _time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("ailinux.mcp.security")

# HMAC Token Validation (Security Hardening 2026-05-06)
# Token-Format: "ts:nonce:client_ip:hmac_sha256(secret, 'ts:nonce:ip')"
_HMAC_TTL_SECONDS = 120
_NONCE_REPLAY_TTL = 300
_REDIS_NONCE_PREFIX = "triforce:hmac:nonce:"
_redis_client = None

# In-memory replay cache (fallback when Redis unavailable, single-worker safe)
import threading as _threading
_nonce_cache_local: Dict[str, float] = {}
_nonce_cache_lock = _threading.Lock()


def _check_and_consume_nonce(nonce: str) -> bool:
    """Atomically register nonce; return True if new, False if replay.
    Tries Redis first (multi-worker safe), falls back to in-memory dict
    (single-worker safe). Fail-closed if BOTH fail.
    """
    r = _get_redis()
    if r is not None:
        try:
            key = f"{_REDIS_NONCE_PREFIX}{nonce}"
            return bool(r.set(key, "1", nx=True, ex=_NONCE_REPLAY_TTL))
        except Exception as exc:
            logger.error(f"HMAC | redis nonce-set failed, falling to in-memory: {exc}")
            # Fall through to in-memory

    # In-memory fallback (process-local)
    now = _time.time()
    with _nonce_cache_lock:
        # Lazy cleanup of expired entries
        expired = [k for k, exp in _nonce_cache_local.items() if exp < now]
        for k in expired:
            del _nonce_cache_local[k]
        if nonce in _nonce_cache_local:
            return False  # replay
        _nonce_cache_local[nonce] = now + _NONCE_REPLAY_TTL
        return True


def _get_redis():
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            _redis_client = redis.Redis(
                host=os.environ.get("REDIS_HOST", "127.0.0.1"),
                port=int(os.environ.get("REDIS_PORT", 6379)),
                db=int(os.environ.get("REDIS_DB", 0)),
                socket_timeout=1.0, socket_connect_timeout=1.0,
                decode_responses=True,
            )
            _redis_client.ping()
        except Exception as exc:
            logger.warning(f"HMAC | Redis unavailable: {exc}")
            _redis_client = False
    return _redis_client if _redis_client else None


def generate_hmac_token(client_ip_str: str, secret: Optional[str] = None) -> Optional[str]:
    if secret is None:
        secret = (os.environ.get("MCP_INTERNAL_PROFILE_VALUE") or "").strip()
    if not secret:
        logger.error("HMAC | MCP_INTERNAL_PROFILE_VALUE not set")
        return None
    ts = str(int(_time.time()))
    nonce = secrets.token_hex(16)
    ip = (client_ip_str or "0.0.0.0").strip()
    message = f"{ts}:{nonce}:{ip}"
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{message}:{digest}"


def validate_hmac_token(token: str, request_ip: Optional[str]) -> bool:
    if not token:
        return False
    secret = (os.environ.get("MCP_INTERNAL_PROFILE_VALUE") or "").strip()
    if not secret:
        return False
    parts = token.split(":")
    if len(parts) != 4:
        return False
    ts_str, nonce, token_ip, signature = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    if abs(int(_time.time()) - ts) > _HMAC_TTL_SECONDS:
        logger.info(f"HMAC | expired")
        return False
    if not request_ip or token_ip.strip() != request_ip.strip():
        logger.info(f"HMAC | IP mismatch")
        return False
    message = f"{ts_str}:{nonce}:{token_ip}"
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        logger.warning(f"HMAC | signature mismatch from {request_ip}")
        return False
    if not _check_and_consume_nonce(nonce):
        logger.warning(f"HMAC | replay detected from {request_ip}")
        return False
    logger.info(f"HMAC | valid for {request_ip}")
    return True


# =============================================================================
# Allowlist & Privileged-Liste
# =============================================================================

# =============================================================================
# Allowlist-Tier: REMOTE Subset fuer /mcp (Claude.ai Custom Connector)
# =============================================================================
# Claude.ai's Custom Connector hat ein Token-Budget fuer tools/list.
# Daher zeigen wir ueber /mcp nur eine schmale Public-Surface.
# Ueber /v1/mcp (intern + andere Connectors) sehen Caller die volle Liste.
# Schnittmenge ist garantiert: REMOTE ist immer Subset von FULL.
EXTERNAL_TOOL_ALLOWLIST_REMOTE: Set[str] = {
    # Core AI
    "chat", "list_models", "ask_specialist", "list_specialists", "models",
    # Vision
    "analyze_image",
    # Crawling
    "crawl", "crawl_url", "crawl_site", "crawl_status",
    # Conversation/Prompts
    "conversation", "prompt_template",
    # Docs & Search
    "api_docs", "web_search", "search", "image_search",
    # Public read-only
    "current_time", "weather", "crypto_prices", "stock_indices", "market_overview",
    # Read-only diagnostics for Claude.ai self-debugging
    "health", "status", "ollama_list", "provider_env_status", "hive_status",
    # Group chat (Phase 3 Multi-AI Auto-Join)
    "group_chat_list", "group_chat_status", "group_chat_read",
    "group_chat_message", "group_chat_create", "group_chat_ask",
}


# =============================================================================
# Allowlist FULL (kanonisch fuer /v1/mcp)
# =============================================================================

EXTERNAL_TOOL_ALLOWLIST_FULL: Set[str] = {
    # Core AI
    "chat", "list_models", "ask_specialist", "list_specialists",
    "models", "specialist",
    # Vision
    "analyze_image",
    # Crawling
    "crawl", "crawl_url", "crawl_site", "crawl_status",
    # Conversation/Prompt
    "conversation", "prompt_template", "prompts",
    # Docs & search
    "api_docs", "web_search", "search", "search_health",
    "multi_search", "smart_search", "quick_smart_search",
    "google_deep_search", "ailinux_search", "grokipedia_search",
    "image_search",
    # Widgets
    "weather", "crypto_prices", "stock_indices", "market_overview",
    "current_time", "list_timezones",
    # Read-only Diagnostik
    "health", "status", "logs", "logs_errors", "logs_stats",
    "safe_probe", "agent_review", "mcp_telemetry", "mcp_analytics",
    # Read-only Wrapper
    "binary_list", "template_list", "task_reference",
    "service_status", "container_status", "file_read", "remote_status",
    "remote_hosts", "remote_info",
    # Read-only Listings
    "ollama_list", "ollama_status",
    "vault_status", "vault_keys",
    "memory_search",
    "agents",
    "system_info", "network_info", "log_viewer", "process_control",
    # Safe-Tools
    "provider_env_status", "hive_status",
    # Code-Read (read-only)
    "code_read", "code_tree", "code_search",
    "codebase_structure", "codebase_file", "codebase_search",
    "codebase_routes", "codebase_services",
    # Dev-Tools (analysis only, no apply)
    "dev_analyze", "dev_lint", "dev_debug", "dev_summarize",
    "dev_links", "dev_refactor",
    # Doc-Browser (read-only)
    "doc_scan", "doc_read", "doc_search", "doc_tree", "doc_stats",
    # Mail / Forum (read-only)
    "mail_inbox", "mail_read",
    "wp_list_drafts", "wp_list_posts",
    "flarum_discussions", "flarum_discussion", "flarum_discussion_get",
    "flarum_post_get", "flarum_posts", "flarum_tags", "flarum_users",
    "flarum_refresh",
    # Notification (read-only)
    "notify_list", "notify_status",
    # Group chat (read-only)
    "group_chat_list", "group_chat_status", "group_chat_read",
    "agent_chat_list", "agent_chat_read", "agent_chat_stream", "agent_chat_summary",
    # Memory (read)
    "memory_store",     # store ist OK (kein Loeschen)
    "hive_compress", "hive_recall", "hive_stats",
    # Models / Tristar
    "tristar_models", "tristar_init",
    "tristar_memory_store", "tristar_memory_search",
    # Misc lookups
    "current_time", "git",
    "image_search",
    "service_list",
    # Group chat (collaboration write - allowed externally to enable workflows)
    "group_chat_ask", "group_chat_consolidate",
    "group_chat_create", "group_chat_message", "group_chat_assign",
    "group_chat_add", "group_chat_remove", "group_chat_models",
    # ai-coder advisory workflow. This invokes models but grants no agent,
    # shell, filesystem-write, service, or administrative capability.
    "swarm_broadcast",
    # Mail send / WP / Forum write actions are PRIVILEGED, see below.
    # Notification mark-read
    "notify_read", "notify_send",
}

# Tools die NIE extern auftauchen und das internal_full Profil brauchen.
PRIVILEGED_TOOLS: Set[str] = {
    # Direkter Code/Shell-Zugriff
    "shell", "task_runner", "binary_exec",
    "custom_exec", "custom_binary",
    "remote_exec", "remote_admin", "remote_task",
    # Service / Container Aenderungen
    "service_control", "container_control",
    # File I/O
    "file_ops",
    # Code-Aenderungen
    "code_edit", "code_patch",
    # Konfiguration / Vault
    "config", "config_set",
    "vault_add",
    # Memory destructive
    "memory_clear",
    # Prompts schreiben
    "prompt_set",
    # Restart/Reload
    "restart", "restart_backend", "restart_agent",
    "hot_reload", "debug",
    "evolve",
    # Agent Lifecycle
    "agent_start", "agent_stop", "agent_call", "agent_broadcast",
    "agent_chat_cleanup",
    # Ollama destructive
    "ollama_pull", "ollama_delete", "ollama_run",
    # WordPress write
    "create_post",
    "wp_create_draft", "wp_update_post", "wp_create_page",
    "wp_publish_post", "wp_delete_post", "wp_multi_ai_post",
    # Mail send
    "mail_send", "mail_mark_seen",
    # Forum write
    "flarum_post_create", "flarum_post_edit", "flarum_discussion_create",
    "flarum_admin_request",
    # Group chat lifecycle (moved to allowlist 2026-05 — collaboration is non-destructive)
    # Notification destructive
    "notify_clear",
    # Mesh
    "mesh_task", "mesh_status",
    # Browser / external automation
    "browser_navigate", "browser_click", "browser_type", "browser_close",
    "browser_search", "browser_screenshot",
}


# Compatibility alias retained for older imports/tests. ai-coder no longer has
# a second coding-only namespace restriction: the authenticated account/RBAC
# and the normal external/internal MCP policy determine the effective surface.
AI_CODER_TOOL_ALLOWLIST: Set[str] = EXTERNAL_TOOL_ALLOWLIST_FULL


# =============================================================================
# Internal-Profil Erkennung
# =============================================================================

def _trusted_internal_networks():
    """Hosts that may directly receive internal_full privileges."""
    return [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("10.10.0.0/24"),
    ]


def _trusted_proxy_networks():
    """Local reverse-proxy networks allowed to supply X-Forwarded-For."""
    return [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("172.16.0.0/12"),
    ]


def _peer_ip(request) -> Optional[str]:
    try:
        client = getattr(request, "client", None)
        if client and getattr(client, "host", None):
            return client.host
    except Exception:
        return None
    return None


def _peer_is_trusted_proxy(request) -> bool:
    peer = _peer_ip(request)
    if not peer:
        return False
    try:
        ip = ipaddress.ip_address(peer)
        return any(ip in net for net in _trusted_proxy_networks())
    except ValueError:
        return False


def client_ip(request) -> Optional[str]:
    """Return the effective client IP without trusting arbitrary XFF headers."""
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff and _peer_is_trusted_proxy(request):
            return xff.split(",")[0].strip()
        return _peer_ip(request)
    except Exception:
        return None


def _is_authenticated_full(request) -> bool:
    """Authentication is not authorization; only an explicit AuthZ flag grants full."""
    st = getattr(request, "state", None)
    if st is None:
        return False
    return getattr(st, "mcp_auth_full_access", False) is True


def is_internal_full_request(request) -> bool:
    """
    True, wenn der Caller das internal_full Profil hat.

    Pruefreihenfolge:
      1) Header X-TriForce-Internal == ENV MCP_INTERNAL_PROFILE_VALUE.
      2) Source-IP in Loopback oder WireGuard-Mesh.
      3) Legacy X-TriForce-All Header NUR wenn IP ohnehin intern.
    """
    if request is None:
        return False
    if _is_authenticated_full(request):
        return True
    try:
        # HMAC-Token (2026-05-06 hardening)
        hmac_token = (request.headers.get("X-TriForce-Internal-Token") or "").strip()
        if hmac_token:
            if validate_hmac_token(hmac_token, client_ip(request)):
                return True
        # Legacy header eq (timing-safe)
        secret = (os.environ.get("MCP_INTERNAL_PROFILE_VALUE") or "").strip()
        sent = (request.headers.get("X-TriForce-Internal") or "").strip()
        if secret and sent and hmac.compare_digest(secret, sent):
            return True

        ip_str = client_ip(request)
        if ip_str:
            try:
                ip = ipaddress.ip_address(ip_str)
                for net in _trusted_internal_networks():
                    if ip in net:
                        return True
            except ValueError:
                pass

        legacy = (request.headers.get("X-TriForce-All") or "").lower() == "true"
        if legacy and ip_str:
            try:
                ip = ipaddress.ip_address(ip_str)
                for net in _trusted_internal_networks():
                    if ip in net:
                        return True
            except ValueError:
                pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("internal-profile check failed: %s", exc)
    return False


def is_ai_coder_request(request) -> bool:
    """Return whether the caller identifies as the ai-coder operator client."""
    try:
        profile = (request.headers.get("X-Client-Profile") or "").strip().lower()
    except Exception:
        return False
    return profile == "ai-coder"


def filter_tools_for_external(
    tools: List[Dict[str, Any]], request=None,
) -> List[Dict[str, Any]]:
    """Default-deny external catalogue with explicit product-scope separation.

    ``x_scope`` is discovery metadata, not authorization. It lets us keep the
    model-facing surface coherent: public callers see global + explicitly shared
    AILinux Helper tools, authenticated callers may additionally discover
    TriForce account/integration tools, and engine-admin tools remain internal.
    Existing per-tool allowlists/RBAC still gate server-side execution.
    """
    state = getattr(request, "state", None) if request is not None else None
    auth_method = str(getattr(state, "mcp_auth_method", "") or "")
    public_guest = auth_method == "public_guest"
    result: List[Dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name") or "")
        scope = str(tool.get("x_scope") or "global")
        if scope == "triforce_admin":
            continue
        if scope == "triforce_auth":
            if not public_guest and auth_method and str(getattr(state, "mcp_auth_user", "") or "").strip():
                result.append(tool)
            continue
        if scope == "aihelper":
            result.append(tool)
            continue
        if name in EXTERNAL_TOOL_ALLOWLIST:
            result.append(tool)
    return result


def _external_action_is_read_only(name: str, arguments: Optional[Dict[str, Any]]) -> bool:
    """Guard mixed read/write tools that remain useful in the external catalogue."""
    args = arguments or {}
    if name == "git":
        return str(args.get("mode") or "status").lower() in {"status", "diff", "log", "branch"}
    if name == "dev_lint":
        return not bool(args.get("fix", False))
    if name == "dev_refactor":
        return not bool(args.get("apply", False))
    return True


def _is_authenticated_request(request) -> bool:
    state = getattr(request, "state", None) if request is not None else None
    method = str(getattr(state, "mcp_auth_method", "") or "").strip().lower()
    user = str(getattr(state, "mcp_auth_user", "") or "").strip()
    return bool(method and method not in {"public_guest", "none", "anonymous"} and user)


def is_tool_allowed(tool_name: str, request, arguments: Optional[Dict[str, Any]] = None) -> bool:
    """Authorize one server-side MCP call after discovery filtering.

    Global tools follow the external allowlist/read-only rules. TriForce account
    integrations (mail/forum/WordPress/notifications/Nova/n8n) require a real
    authenticated identity. TriForce engine/admin tools remain internal_full only.
    AILinux Helper tools are authorized separately by the workspace lease/share
    manifest and are routed before this server-side gate.
    """
    name = tool_name[9:] if tool_name.startswith("triforce_") else tool_name
    internal_full = is_internal_full_request(request)
    if internal_full:
        return True
    try:
        from app.mcp.tool_registry_unified import (
            CANONICAL_TOOL_NAMES,
            TOOL_SCOPE_TRIFORCE_ADMIN,
            TOOL_SCOPE_TRIFORCE_AUTH,
            tool_scope,
        )
        # Scope metadata governs the canonical surface. Legacy compatibility
        # handlers keep their established external allowlist semantics until they
        # are removed or explicitly migrated; otherwise a broad legacy inventory
        # label (for example service_status -> admin) would silently break AICoder.
        scope = tool_scope(name) if name in CANONICAL_TOOL_NAMES else "legacy"
    except Exception:
        scope = "legacy"
    if scope == TOOL_SCOPE_TRIFORCE_ADMIN:
        return False
    if scope == TOOL_SCOPE_TRIFORCE_AUTH:
        return _is_authenticated_request(request)
    if name in PRIVILEGED_TOOLS:
        return False
    if name in EXTERNAL_TOOL_ALLOWLIST:
        return _external_action_is_read_only(name, arguments)
    return False


# Legacy-Alias: EXTERNAL_TOOL_ALLOWLIST == FULL fuer Backward-Compat
EXTERNAL_TOOL_ALLOWLIST: Set[str] = EXTERNAL_TOOL_ALLOWLIST_FULL


__all__ = [
    "EXTERNAL_TOOL_ALLOWLIST",
    "EXTERNAL_TOOL_ALLOWLIST_FULL",
    "EXTERNAL_TOOL_ALLOWLIST_REMOTE",
    "AI_CODER_TOOL_ALLOWLIST",
    "PRIVILEGED_TOOLS",
    "client_ip",
    "is_ai_coder_request",
    "is_internal_full_request",
    "filter_tools_for_external",
    "is_tool_allowed",
    "generate_hmac_token",
    "validate_hmac_token",
]
