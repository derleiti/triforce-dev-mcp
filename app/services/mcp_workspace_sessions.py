"""Session-scoped pairing between public MCP clients and local workspace nodes.

Two compatible pairing directions are supported:

1. Preferred web flow: opening the TriForce MCP page creates a fresh one-time
   pairing code. The local helper registers against that code and waits. The
   user posts the code in ChatGPT/Codex, which calls ``workspace_pair`` to bind
   the waiting helper to that MCP session.
2. Legacy/session-first flow: ``workspace_status`` may create a code already
   tied to an MCP session and the helper can connect directly with it.

All pairing codes are short-lived, single-use and kept only in memory.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid
from typing import Any, Optional

PAIR_TTL_SECONDS = 15 * 60
RECONNECT_TTL_SECONDS = 2 * 60 * 60
LEASE_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_WEB_PAIR_TICKETS = 2048
RESUME_TICKET_TTL_SECONDS = 60
REDIS_LEASE_PREFIX = "triforce:workspace:lease:"
REDIS_RESUME_PREFIX = "triforce:workspace:resume:"

# Legacy/session-first pairing.
_SESSION_PAIR: dict[str, dict[str, Any]] = {}
_PAIR_INDEX: dict[str, str] = {}

# Preferred web-first pairing. Keyed by SHA-256(code); raw code is retained only
# in the ticket itself so it can be rendered on the one page that created it.
_WEB_PAIR: dict[str, dict[str, Any]] = {}

# Active MCP session -> live local workspace binding.
_SESSION_WORKSPACE: dict[str, dict[str, Any]] = {}
# Resume-token hash -> live/in-memory web pair key. Raw resume tokens are never persisted server-side.
_RESUME_INDEX: dict[str, str] = {}
# One-shot WebSocket reconnect tickets. Values contain the raw resume token only
# in process memory for at most 60 seconds so long-lived credentials never enter URLs/logs.
_RESUME_TICKETS: dict[str, dict[str, Any]] = {}


def _pair_key(code: str) -> str:
    return hashlib.sha256(str(code or "").strip().upper().encode("utf-8")).hexdigest()


def _resume_key(token: str) -> str:
    return hashlib.sha256(str(token or "").strip().encode("utf-8")).hexdigest()


def _new_resume_token() -> str:
    return secrets.token_urlsafe(32)


def _redis_client():
    try:
        import redis
        client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


def _persist_lease(item: dict[str, Any]) -> None:
    """Persist reconnect metadata, never raw pair/resume credentials."""
    lease_id = str(item.get("lease_id") or "")
    resume_hash = str(item.get("resume_hash") or "")
    if not lease_id or not resume_hash:
        return
    payload = {
        "lease_id": lease_id,
        "resume_hash": resume_hash,
        "mode": normalize_workspace_access_mode(item.get("mode")),
        "task": str(item.get("task") or "")[:4000],
        "capabilities": list(item.get("capabilities") or []),
        "paired_session_ids": list(item.get("paired_session_ids") or []),
        "paired_session_id": item.get("paired_session_id"),
        "helper_connected_at": float(item.get("helper_connected_at") or 0),
        "expires_at": float(item.get("expires_at") or (time.time() + LEASE_TTL_SECONDS)),
    }
    client = _redis_client()
    if client is None:
        return
    ttl = max(1, int(payload["expires_at"] - time.time()))
    try:
        client.setex(REDIS_LEASE_PREFIX + lease_id, ttl, json.dumps(payload, separators=(",", ":")))
        client.setex(REDIS_RESUME_PREFIX + resume_hash, ttl, lease_id)
    except Exception:
        pass


def _delete_persisted_lease(item: dict[str, Any]) -> None:
    lease_id = str(item.get("lease_id") or "")
    resume_hash = str(item.get("resume_hash") or "")
    if resume_hash:
        _RESUME_INDEX.pop(resume_hash, None)
    client = _redis_client()
    if client is None:
        return
    keys = []
    if lease_id:
        keys.append(REDIS_LEASE_PREFIX + lease_id)
    if resume_hash:
        keys.append(REDIS_RESUME_PREFIX + resume_hash)
    if keys:
        try:
            client.delete(*keys)
        except Exception:
            pass


def _load_lease_by_resume(token: str) -> Optional[dict[str, Any]]:
    resume_hash = _resume_key(token)
    pair_key = _RESUME_INDEX.get(resume_hash)
    if pair_key:
        item = _WEB_PAIR.get(pair_key)
        if item and not _expired(item):
            return item
    client = _redis_client()
    if client is None:
        return None
    try:
        lease_id = client.get(REDIS_RESUME_PREFIX + resume_hash)
        if not lease_id:
            return None
        raw = client.get(REDIS_LEASE_PREFIX + lease_id)
        if not raw:
            return None
        data = json.loads(raw)
        if not secrets.compare_digest(str(data.get("resume_hash") or ""), resume_hash):
            return None
        now = time.time()
        pair_key = "resume:" + resume_hash
        item = {
            "code": "",
            "created_at": now,
            "expires_at": float(data.get("expires_at") or (now + LEASE_TTL_SECONDS)),
            "pair_expires_at": 0,
            "connection": None,
            "connection_id": None,
            "client_id": "",
            "mode": normalize_workspace_access_mode(data.get("mode")),
            "task": str(data.get("task") or ""),
            "capabilities": list(data.get("capabilities") or []),
            "paired_session_id": data.get("paired_session_id"),
            "paired_session_ids": list(data.get("paired_session_ids") or []),
            "lease_id": str(data.get("lease_id") or lease_id),
            "resume_hash": resume_hash,
            "resume_token": str(token),
            "helper_connected_at": float(data.get("helper_connected_at") or 0),
        }
        _WEB_PAIR[pair_key] = item
        _RESUME_INDEX[resume_hash] = pair_key
        return item
    except Exception:
        return None


def resolve_resume_token(token: str) -> Optional[dict[str, Any]]:
    item = _load_lease_by_resume(token)
    return dict(item) if item and not _expired(item) else None


def create_workspace_resume_ticket(token: str) -> str:
    if not resolve_resume_token(token):
        raise ValueError("Invalid or expired workspace resume token")
    now = time.time()
    for key, item in list(_RESUME_TICKETS.items()):
        if float(item.get("expires_at") or 0) <= now:
            _RESUME_TICKETS.pop(key, None)
    code = _new_code()
    _RESUME_TICKETS[_pair_key(code)] = {
        "resume_token": str(token),
        "expires_at": now + RESUME_TICKET_TTL_SECONDS,
    }
    return code


def consume_workspace_resume_ticket(code: str) -> str:
    item = _RESUME_TICKETS.pop(_pair_key(code), None)
    if not item or float(item.get("expires_at") or 0) <= time.time():
        return ""
    token = str(item.get("resume_token") or "")
    return token if resolve_resume_token(token) else ""



def _new_code() -> str:
    # 96 random bits, grouped for copy/paste/readability.
    raw = secrets.token_hex(12).upper()
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def _expired(item: dict[str, Any] | None) -> bool:
    return not item or float(item.get("expires_at") or 0) <= time.time()


def normalize_workspace_access_mode(mode: str | None) -> str:
    """Canonical permission mode for browser-selected local workspaces."""
    value = str(mode or "").strip().lower().replace("-", "_")
    return "write" if value in {"write", "readwrite", "read_write"} else "read_only"


def _connection_live(item: dict[str, Any] | None) -> bool:
    if not item:
        return False
    connection = item.get("connection")
    return bool(
        connection is not None
        and not bool(getattr(connection, "closed", True))
        and id(connection) == item.get("connection_id")
    )


def create_web_pair_code() -> str:
    """Create a fresh browser-first pairing code after local folder selection."""
    _cleanup_web_pairs()
    if len(_WEB_PAIR) >= MAX_WEB_PAIR_TICKETS:
        oldest = min(_WEB_PAIR, key=lambda key: float(_WEB_PAIR[key].get("created_at") or 0))
        _WEB_PAIR.pop(oldest, None)
    now = time.time()
    code = _new_code()
    _WEB_PAIR[_pair_key(code)] = {
        "code": code,
        "created_at": now,
        "expires_at": now + PAIR_TTL_SECONDS,
        "pair_expires_at": now + PAIR_TTL_SECONDS,
        "connection": None,
        "connection_id": None,
        "client_id": "",
        "mode": "read_only",
        "task": "",
        "paired_session_id": None,
        "paired_session_ids": [],
        "resume_hash": "",
    }
    return code


def resolve_web_pair_code(code: str) -> Optional[dict[str, Any]]:
    key = _pair_key(code)
    item = _WEB_PAIR.get(key)
    if _expired(item) or float(item.get("pair_expires_at") or item.get("expires_at") or 0) <= time.time():
        return None
    if not secrets.compare_digest(str(item.get("code") or ""), str(code or "").strip().upper()):
        return None
    return dict(item)


def register_waiting_workspace(code: str, connection: Any, *, mode: str, task: str = "", capabilities: list[str] | None = None) -> dict[str, Any]:
    """Attach a local helper to a web-created code without binding an MCP session yet."""
    key = _pair_key(code)
    item = _WEB_PAIR.get(key)
    if _expired(item):
        _WEB_PAIR.pop(key, None)
        raise ValueError("Invalid or expired workspace pairing code")
    if not secrets.compare_digest(str(item.get("code") or ""), str(code or "").strip().upper()):
        raise ValueError("Invalid workspace pairing code")
    existing = item.get("connection")
    if existing is not None and not bool(getattr(existing, "closed", True)) and id(existing) != id(connection):
        raise ValueError("Pairing code is already used by another live workspace helper")
    normalized_mode = normalize_workspace_access_mode(mode)
    item.update({
        "connection": connection,
        "connection_id": id(connection) if connection is not None else None,
        "client_id": str(getattr(connection, "client_id", "")) if connection is not None else "",
        "mode": normalized_mode,
        "task": str(task or "")[:4000],
        "capabilities": sorted({str(x) for x in (capabilities or []) if str(x)}),
        "helper_connected_at": time.time(),
    })
    return {
        "connected": True,
        "waiting_for_session": True,
        "client_id": item["client_id"],
        "mode": normalized_mode,
        "task": item["task"],
        "capabilities": list(item.get("capabilities") or []),
        "expires_at": item["expires_at"],
    }


def promote_session_pair_to_web_lease(
    code: str, session_id: str, connection: Any, *, mode: str, task: str = "",
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Promote a session-first browser pairing into a reusable shared lease.

    This lets clients that cannot expose ``workspace_pair`` (notably ChatGPT)
    bind through ``workspace_status`` + browser setup, while the same code can
    subsequently authorize Telegram/Mistral or another MCP transport.
    """
    resolved = resolve_pair_code(code)
    if resolved != session_id:
        raise ValueError("Invalid or expired session pairing code")
    key = _pair_key(code)
    normalized_mode = normalize_workspace_access_mode(mode)
    now = time.time()
    lease_id = uuid.uuid4().hex
    resume_token = _new_resume_token()
    resume_hash = _resume_key(resume_token)
    _WEB_PAIR[key] = {
        "code": str(code).strip().upper(),
        "created_at": now,
        "expires_at": now + LEASE_TTL_SECONDS,
        "pair_expires_at": now + PAIR_TTL_SECONDS,
        "connection": connection,
        "connection_id": id(connection),
        "client_id": str(getattr(connection, "client_id", "")),
        "mode": normalized_mode,
        "task": str(task or "")[:4000],
        "capabilities": sorted({str(x) for x in (capabilities or []) if str(x)}),
        "paired_session_id": session_id,
        "paired_session_ids": [session_id],
        "lease_id": lease_id,
        "resume_hash": resume_hash,
        "resume_token": resume_token,
        "helper_connected_at": now,
    }
    _RESUME_INDEX[resume_hash] = key
    _persist_lease(_WEB_PAIR[key])
    pair = _SESSION_PAIR.pop(session_id, None)
    if pair:
        _PAIR_INDEX.pop(key, None)
    binding = bind_workspace(
        session_id, connection, mode=normalized_mode, task=task, capabilities=capabilities,
        reconnect_pair_key=key, lease_id=lease_id,
    )
    binding["resume_token"] = resume_token
    return binding


def claim_waiting_workspace(code: str, session_id: str) -> dict[str, Any]:
    """Authorize one MCP session alias for the browser workspace lease.

    Possession of the high-entropy pairing code is the authorization. Multiple
    MCP transports (for example ChatGPT and Mistral) may therefore share the
    same browser lease concurrently instead of stealing a single session slot.
    """
    key = _pair_key(code)
    item = _WEB_PAIR.get(key)
    if _expired(item) or float((item or {}).get("pair_expires_at") or (item or {}).get("expires_at") or 0) <= time.time():
        raise ValueError("Invalid or expired workspace pairing code")
    if not secrets.compare_digest(str(item.get("code") or ""), str(code or "").strip().upper()):
        raise ValueError("Invalid workspace pairing code")
    connection = item.get("connection")
    resume_token = str(item.get("resume_token") or "")
    if not str(item.get("resume_hash") or ""):
        resume_token = _new_resume_token()
        item["resume_hash"] = _resume_key(resume_token)
        item["resume_token"] = resume_token
        _RESUME_INDEX[item["resume_hash"]] = key
    aliases = {str(x) for x in (item.get("paired_session_ids") or []) if str(x)}
    legacy = str(item.get("paired_session_id") or "")
    if legacy:
        aliases.add(legacy)
    if not _connection_live(item):
        # The pairing code itself is the authorization. Once a browser has
        # registered this ticket at least once, an MCP client may claim it even
        # if mobile backgrounding suspended the physical WebSocket milliseconds
        # earlier. This removes the browser->chat app-switch timing race. The
        # resulting lease is explicitly suspended until the same browser code
        # reconnects; no local tool can execute while connection is None.
        if not item.get("helper_connected_at"):
            raise RuntimeError("Local workspace browser has not connected for this code yet")
        aliases.add(session_id)
        lease_id = str(item.get("lease_id") or uuid.uuid4().hex)
        item["paired_session_id"] = session_id
        item["paired_session_ids"] = sorted(aliases)
        item["lease_id"] = lease_id
        item["expires_at"] = time.time() + LEASE_TTL_SECONDS
        binding = bind_workspace(
            session_id, None,
            mode=str(item.get("mode") or "read_only"),
            task=str(item.get("task") or ""),
            capabilities=list(item.get("capabilities") or []),
            reconnect_pair_key=key,
            lease_id=lease_id,
        )
        _persist_lease(item)
        if resume_token:
            binding["resume_token"] = resume_token
        return binding

    current = get_workspace(session_id)
    if current and current.get("reconnect_pair_key") == key:
        return current

    aliases.add(session_id)
    lease_id = str(item.get("lease_id") or uuid.uuid4().hex)
    item["paired_session_id"] = session_id
    item["paired_session_ids"] = sorted(aliases)
    item["lease_id"] = lease_id
    item["expires_at"] = time.time() + LEASE_TTL_SECONDS
    binding = bind_workspace(
        session_id, connection,
        mode=str(item.get("mode") or "read_only"),
        task=str(item.get("task") or ""),
        capabilities=list(item.get("capabilities") or []),
        reconnect_pair_key=key,
        lease_id=lease_id,
    )
    _persist_lease(item)
    if resume_token:
        binding["resume_token"] = resume_token
    return binding


def claim_workspace_with_resume_token(token: str, session_id: str) -> dict[str, Any]:
    """Attach a new MCP transport identity to a persisted workspace lease."""
    item = _load_lease_by_resume(token)
    if not item or _expired(item):
        raise ValueError("Invalid or expired workspace resume token")
    key = _RESUME_INDEX.get(str(item.get("resume_hash") or ""))
    if not key:
        raise ValueError("Workspace lease is unavailable")
    aliases = {str(x) for x in (item.get("paired_session_ids") or []) if str(x)}
    aliases.add(session_id)
    item["paired_session_ids"] = sorted(aliases)
    item["paired_session_id"] = session_id
    item["expires_at"] = time.time() + LEASE_TTL_SECONDS
    connection = item.get("connection") if _connection_live(item) else None
    binding = bind_workspace(
        session_id, connection,
        mode=str(item.get("mode") or "read_only"),
        task=str(item.get("task") or ""),
        capabilities=list(item.get("capabilities") or []),
        reconnect_pair_key=key, lease_id=str(item.get("lease_id") or uuid.uuid4().hex),
    )
    _persist_lease(item)
    return binding


def reconnect_workspace_with_resume_token(token: str, connection: Any, *, mode: str, task: str = "", capabilities: list[str] | None = None) -> dict[str, Any]:
    """Reconnect a browser executor using the durable lease handoff token."""
    item = _load_lease_by_resume(token)
    if not item or _expired(item):
        raise ValueError("Invalid or expired workspace resume token")
    key = _RESUME_INDEX.get(str(item.get("resume_hash") or ""))
    if not key:
        raise ValueError("Workspace lease is unavailable")
    aliases = {str(x) for x in (item.get("paired_session_ids") or []) if str(x)}
    legacy = str(item.get("paired_session_id") or "")
    if legacy:
        aliases.add(legacy)
    normalized_mode = normalize_workspace_access_mode(mode)
    # A reconnect may not silently escalate a read-only lease.
    stored_mode = normalize_workspace_access_mode(item.get("mode"))
    if stored_mode != "write":
        normalized_mode = "read_only"
    now = time.time()
    item.update({
        "connection": connection, "connection_id": id(connection),
        "client_id": str(getattr(connection, "client_id", "")),
        "mode": normalized_mode, "task": str(task or item.get("task") or "")[:4000],
        "capabilities": sorted({str(x) for x in (capabilities or item.get("capabilities") or []) if str(x)}),
        "expires_at": now + LEASE_TTL_SECONDS, "helper_connected_at": now,
        "paired_session_ids": sorted(aliases),
    })
    result = None
    for alias in sorted(aliases):
        bound = bind_workspace(alias, connection, mode=normalized_mode, task=item["task"], capabilities=item["capabilities"], reconnect_pair_key=key, lease_id=str(item.get("lease_id") or uuid.uuid4().hex))
        result = result or bound
    _persist_lease(item)
    return result or {
        "session_id": "", "connection": connection, "mode": normalized_mode,
        "task": item["task"], "capabilities": list(item["capabilities"]),
        "lease_id": str(item.get("lease_id") or ""), "reconnect_pair_key": key,
    }


def reconnect_web_workspace(code: str, connection: Any, *, mode: str, task: str = "", capabilities: list[str] | None = None) -> dict[str, Any]:
    """Reconnect a browser helper and refresh every authorized MCP alias."""
    key = _pair_key(code)
    item = _WEB_PAIR.get(key)
    if _expired(item):
        _WEB_PAIR.pop(key, None)
        raise ValueError("Invalid or expired workspace pairing code")
    if not secrets.compare_digest(str(item.get("code") or ""), str(code or "").strip().upper()):
        raise ValueError("Invalid workspace pairing code")
    aliases = {str(x) for x in (item.get("paired_session_ids") or []) if str(x)}
    legacy = str(item.get("paired_session_id") or "")
    if legacy:
        aliases.add(legacy)
    if not aliases:
        raise ValueError("Workspace pairing code has not been paired to an MCP session yet")
    normalized_mode = normalize_workspace_access_mode(mode)
    item.update({
        "connection": connection,
        "connection_id": id(connection),
        "client_id": str(getattr(connection, "client_id", "")),
        "mode": normalized_mode,
        "task": str(task or "")[:4000],
        "capabilities": sorted({str(x) for x in (capabilities or []) if str(x)}),
        "expires_at": time.time() + LEASE_TTL_SECONDS,
        "helper_connected_at": time.time(),
        "paired_session_ids": sorted(aliases),
    })
    lease_id = str(item.get("lease_id") or uuid.uuid4().hex)
    item["lease_id"] = lease_id
    _persist_lease(item)
    primary = str(item.get("paired_session_id") or next(iter(aliases)))
    result = None
    for alias in sorted(aliases):
        bound = bind_workspace(
            alias, connection, mode=normalized_mode, task=task, capabilities=capabilities,
            reconnect_pair_key=key, lease_id=lease_id,
        )
        if alias == primary:
            result = bound
    return result or bind_workspace(
        primary, connection, mode=normalized_mode, task=task, capabilities=capabilities,
        reconnect_pair_key=key, lease_id=lease_id,
    )


def get_or_create_pair_code(session_id: str) -> str:
    """Legacy session-first pairing retained for compatibility."""
    now = time.time()
    current = _SESSION_PAIR.get(session_id)
    if current and float(current.get("expires_at") or 0) > now:
        return str(current["code"])
    if current:
        _PAIR_INDEX.pop(_pair_key(str(current.get("code") or "")), None)
    code = _new_code()
    _SESSION_PAIR[session_id] = {"code": code, "created_at": now, "expires_at": now + PAIR_TTL_SECONDS}
    _PAIR_INDEX[_pair_key(code)] = session_id
    return code


def resolve_pair_code(code: str) -> Optional[str]:
    """Resolve only the legacy session-first code to a session id."""
    key = _pair_key(code)
    session_id = _PAIR_INDEX.get(key)
    if not session_id:
        return None
    item = _SESSION_PAIR.get(session_id)
    if _expired(item):
        _PAIR_INDEX.pop(key, None)
        if item:
            _SESSION_PAIR.pop(session_id, None)
        return None
    if not secrets.compare_digest(str(item.get("code") or ""), str(code or "").strip().upper()):
        return None
    return session_id


def pair_code_kind(code: str) -> tuple[str, Optional[str]]:
    """Return ('session', session_id), ('web', None), or ('invalid', None)."""
    session_id = resolve_pair_code(code)
    if session_id:
        return "session", session_id
    web = resolve_web_pair_code(code)
    if web:
        paired = str(web.get("paired_session_id") or "")
        return ("reconnect", paired) if paired else ("web", None)
    return "invalid", None


def get_workspace_by_token(token: str | None) -> Optional[dict[str, Any]]:
    """Resolve a web pairing ID as a transport-independent workspace lease token."""
    if not token:
        return None
    web = resolve_web_pair_code(str(token)) or resolve_resume_token(str(token))
    if not web:
        return None
    aliases = [str(x) for x in (web.get("paired_session_ids") or []) if str(x)]
    legacy = str(web.get("paired_session_id") or "")
    if legacy and legacy not in aliases:
        aliases.append(legacy)
    for alias in reversed(aliases):
        binding = get_workspace_lease(alias)
        if binding:
            return binding
    return None


def _detach_session_from_previous_pair(session_id: str, new_pair_key: str | None) -> None:
    """Remove one MCP alias from its old workspace lease before rebinding it.

    Without this, reconnecting an older browser tab could later bind the same
    logical MCP identity back to a stale workspace after the user deliberately
    switched to a new pairing ID.
    """
    current = _SESSION_WORKSPACE.get(session_id)
    if not current:
        return
    old_pair_key = str(current.get("reconnect_pair_key") or "")
    if not old_pair_key or old_pair_key == str(new_pair_key or ""):
        return
    old_pair = _WEB_PAIR.get(old_pair_key)
    if old_pair is None:
        return
    aliases = {str(x) for x in (old_pair.get("paired_session_ids") or []) if str(x)}
    aliases.discard(session_id)
    old_pair["paired_session_ids"] = sorted(aliases)
    if str(old_pair.get("paired_session_id") or "") == session_id:
        old_pair["paired_session_id"] = next(iter(sorted(aliases)), None)


def bind_workspace(
    session_id: str, connection: Any, *, mode: str, task: str = "",
    capabilities: list[str] | None = None, reconnect_pair_key: str | None = None,
    lease_id: str | None = None,
) -> dict[str, Any]:
    normalized_mode = normalize_workspace_access_mode(mode)
    _detach_session_from_previous_pair(session_id, reconnect_pair_key)
    binding = {
        "session_id": session_id,
        "connection": connection,
        "connection_id": id(connection) if connection is not None else None,
        "client_id": str(getattr(connection, "client_id", "")) if connection is not None else "",
        "mode": normalized_mode,
        "task": str(task or "")[:4000],
        "capabilities": sorted({str(x) for x in (capabilities or []) if str(x)}),
        "reconnect_pair_key": reconnect_pair_key,
        "lease_id": str(lease_id or uuid.uuid4().hex),
        "transport_active": True,
        "reconnect_until": time.time() + LEASE_TTL_SECONDS if reconnect_pair_key else 0,
        "bound_at": time.time(),
    }
    _SESSION_WORKSPACE[session_id] = binding
    if reconnect_pair_key:
        pair_item = _WEB_PAIR.get(reconnect_pair_key)
        if pair_item is not None:
            pair_item["lease_id"] = binding["lease_id"]
            pair_item["paired_session_id"] = session_id
            aliases = {str(x) for x in (pair_item.get("paired_session_ids") or []) if str(x)}
            aliases.add(session_id)
            pair_item["paired_session_ids"] = sorted(aliases)
            pair_item["expires_at"] = time.time() + LEASE_TTL_SECONDS
            _persist_lease(pair_item)
    pair = _SESSION_PAIR.pop(session_id, None)
    if pair:
        _PAIR_INDEX.pop(_pair_key(str(pair.get("code") or "")), None)
    return dict(binding)


def get_workspace(session_id: str | None) -> Optional[dict[str, Any]]:
    if not session_id:
        return None
    item = _SESSION_WORKSPACE.get(session_id)
    if not item:
        return None
    connection = item.get("connection")
    if connection is None or bool(getattr(connection, "closed", True)) or id(connection) != item.get("connection_id"):
        if float(item.get("reconnect_until") or 0) > time.time():
            return None
        _SESSION_WORKSPACE.pop(session_id, None)
        return None
    return dict(item)


def get_workspace_lease(session_id: str | None) -> Optional[dict[str, Any]]:
    """Return the logical workspace lease independently from executor transport state."""
    if not session_id:
        return None
    live = get_workspace(session_id)
    if live:
        live.update({
            "state": "ready", "lease_state": "ready", "connected": True,
            "transport_state": "online", "executor_online": True, "suspended": False,
        })
        return live
    item = _SESSION_WORKSPACE.get(session_id)
    if not item:
        return None
    if float(item.get("reconnect_until") or 0) <= time.time():
        _SESSION_WORKSPACE.pop(session_id, None)
        return None
    lease = dict(item)
    lease.update({
        "state": "ready",
        "lease_state": "ready",
        "connected": True,
        "transport_state": "offline",
        "executor_online": False,
        "suspended": False,
        "connection": None,
        "connection_id": None,
    })
    return lease


def mark_transport_detached(session_id: str) -> Optional[dict[str, Any]]:
    """Detach an MCP transport while preserving the browser workspace lease.

    The lease remains reconnectable/re-bindable with its existing secret pairing
    ID. This is deliberately different from ``clear_session``, which revokes it.
    """
    item = _SESSION_WORKSPACE.get(session_id)
    if not item:
        return None
    item["transport_active"] = False
    item["transport_detached_at"] = time.time()
    return dict(item)


def mark_transport_attached(session_id: str) -> Optional[dict[str, Any]]:
    item = _SESSION_WORKSPACE.get(session_id)
    if not item:
        return None
    item["transport_active"] = True
    item.pop("transport_detached_at", None)
    return dict(item)

def suspend_connection(connection: Any) -> None:
    """Keep a paired browser workspace reconnectable with the same pairing ID."""
    now = time.time()
    for session_id, item in list(_SESSION_WORKSPACE.items()):
        if item.get("connection_id") != id(connection):
            continue
        pair_key = str(item.get("reconnect_pair_key") or "")
        if not pair_key:
            _SESSION_WORKSPACE.pop(session_id, None)
            continue
        until = now + LEASE_TTL_SECONDS
        item.update({"connection": None, "connection_id": None, "client_id": "", "suspended_at": now, "reconnect_until": until})
        pair = _WEB_PAIR.get(pair_key)
        if pair:
            pair.update({"connection": None, "connection_id": None, "client_id": "", "expires_at": until})
            _persist_lease(pair)
    for item in _WEB_PAIR.values():
        if item.get("connection_id") == id(connection):
            item.update({"connection": None, "connection_id": None, "client_id": ""})


def unbind_connection(connection: Any) -> None:
    for session_id, item in list(_SESSION_WORKSPACE.items()):
        if item.get("connection_id") == id(connection):
            pair_key = str(item.get("reconnect_pair_key") or "")
            if pair_key:
                pair = _WEB_PAIR.pop(pair_key, None)
                if pair:
                    _delete_persisted_lease(pair)
            _SESSION_WORKSPACE.pop(session_id, None)
    for key, item in list(_WEB_PAIR.items()):
        if item.get("connection_id") == id(connection):
            removed = _WEB_PAIR.pop(key, None)
            if removed:
                _delete_persisted_lease(removed)


def clear_session(session_id: str) -> None:
    pair = _SESSION_PAIR.pop(session_id, None)
    if pair:
        _PAIR_INDEX.pop(_pair_key(str(pair.get("code") or "")), None)
    binding = _SESSION_WORKSPACE.pop(session_id, None)
    pair_key = str((binding or {}).get("reconnect_pair_key") or "")
    if pair_key:
        item = _WEB_PAIR.get(pair_key)
        if item:
            aliases = {str(x) for x in (item.get("paired_session_ids") or []) if str(x)}
            aliases.discard(session_id)
            item["paired_session_ids"] = sorted(aliases)
            if str(item.get("paired_session_id") or "") == session_id:
                item["paired_session_id"] = next(iter(sorted(aliases)), None)
            if not aliases:
                removed = _WEB_PAIR.pop(pair_key, None)
                if removed:
                    _delete_persisted_lease(removed)
            else:
                _persist_lease(item)


def _cleanup_web_pairs() -> None:
    now = time.time()
    for key, item in list(_WEB_PAIR.items()):
        if float(item.get("expires_at") or 0) <= now:
            _WEB_PAIR.pop(key, None)


def workspace_status(session_id: str) -> dict[str, Any]:
    binding = get_workspace_lease(session_id)
    if binding:
        return {
            "state": binding.get("state", "ready"),
            "lease_state": binding.get("lease_state", "ready"),
            "connected": bool(binding.get("connected", True)),
            "transport_state": binding.get("transport_state", "offline"),
            "executor_online": bool(binding.get("executor_online", False)),
            "suspended": False,
            "reconnectable": True,
            "access_mode": binding["mode"],
            "mode": binding["mode"],  # compatibility
            "task": binding.get("task", ""),
            "client_id": binding.get("client_id", ""),
            "lease_id": binding.get("lease_id", ""),
            "transport_active": bool(binding.get("transport_active", True)),
            "capabilities": list(binding.get("capabilities") or []),
            "reconnect_until": float(binding.get("reconnect_until") or 0),
            "persistent_lease": bool(binding.get("reconnect_pair_key")),
        }
    return {
        "state": "unpaired",
        "connected": False,
        "suspended": False,
        "reconnectable": False,
        "setup_url": "https://api.ailinux.me/v1/mcp",
        "procedure": "Open the setup page, choose a local folder, then post its pairing code in this chat.",
    }
