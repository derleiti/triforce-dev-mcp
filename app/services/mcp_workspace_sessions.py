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
import secrets
import time
from typing import Any, Optional

PAIR_TTL_SECONDS = 15 * 60
RECONNECT_TTL_SECONDS = 2 * 60 * 60
MAX_WEB_PAIR_TICKETS = 2048

# Legacy/session-first pairing.
_SESSION_PAIR: dict[str, dict[str, Any]] = {}
_PAIR_INDEX: dict[str, str] = {}

# Preferred web-first pairing. Keyed by SHA-256(code); raw code is retained only
# in the ticket itself so it can be rendered on the one page that created it.
_WEB_PAIR: dict[str, dict[str, Any]] = {}

# Active MCP session -> live local workspace binding.
_SESSION_WORKSPACE: dict[str, dict[str, Any]] = {}


def _pair_key(code: str) -> str:
    return hashlib.sha256(str(code or "").strip().upper().encode("utf-8")).hexdigest()



def _new_code() -> str:
    # 96 random bits, grouped for copy/paste/readability.
    raw = secrets.token_hex(12).upper()
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def _expired(item: dict[str, Any] | None) -> bool:
    return not item or float(item.get("expires_at") or 0) <= time.time()


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
        "connection": None,
        "connection_id": None,
        "client_id": "",
        "mode": "read_only",
        "task": "",
        "paired_session_id": None,
    }
    return code


def resolve_web_pair_code(code: str) -> Optional[dict[str, Any]]:
    key = _pair_key(code)
    item = _WEB_PAIR.get(key)
    if _expired(item):
        _WEB_PAIR.pop(key, None)
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
    normalized_mode = "write" if str(mode).strip().lower() == "write" else "read_only"
    item.update({
        "connection": connection,
        "connection_id": id(connection),
        "client_id": str(getattr(connection, "client_id", "")),
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


def claim_waiting_workspace(code: str, session_id: str) -> dict[str, Any]:
    """Bind a browser ticket once; keep the same ID for reconnects to that session."""
    key = _pair_key(code)
    item = _WEB_PAIR.get(key)
    if _expired(item):
        _WEB_PAIR.pop(key, None)
        raise ValueError("Invalid or expired workspace pairing code")
    if not secrets.compare_digest(str(item.get("code") or ""), str(code or "").strip().upper()):
        raise ValueError("Invalid workspace pairing code")
    paired = str(item.get("paired_session_id") or "")
    if paired and paired != session_id:
        raise ValueError("Workspace pairing code is already bound to another MCP session")
    if paired == session_id:
        current = get_workspace(session_id)
        if current:
            return current
        raise RuntimeError("Workspace is paired but the browser is currently disconnected")
    connection = item.get("connection")
    if connection is None or bool(getattr(connection, "closed", True)) or id(connection) != item.get("connection_id"):
        raise RuntimeError("Local workspace browser has not connected for this code yet")
    item["paired_session_id"] = session_id
    item["expires_at"] = time.time() + RECONNECT_TTL_SECONDS
    return bind_workspace(
        session_id, connection,
        mode=str(item.get("mode") or "read_only"),
        task=str(item.get("task") or ""),
        capabilities=list(item.get("capabilities") or []),
        reconnect_pair_key=key,
    )


def reconnect_web_workspace(code: str, connection: Any, *, mode: str, task: str = "", capabilities: list[str] | None = None) -> dict[str, Any]:
    """Reconnect the same browser pairing ID to its already-bound MCP session."""
    key = _pair_key(code)
    item = _WEB_PAIR.get(key)
    if _expired(item):
        _WEB_PAIR.pop(key, None)
        raise ValueError("Invalid or expired workspace pairing code")
    if not secrets.compare_digest(str(item.get("code") or ""), str(code or "").strip().upper()):
        raise ValueError("Invalid workspace pairing code")
    session_id = str(item.get("paired_session_id") or "")
    if not session_id:
        raise ValueError("Workspace pairing code has not been paired to an MCP session yet")
    normalized_mode = "write" if str(mode).strip().lower() == "write" else "read_only"
    item.update({
        "connection": connection,
        "connection_id": id(connection),
        "client_id": str(getattr(connection, "client_id", "")),
        "mode": normalized_mode,
        "task": str(task or "")[:4000],
        "capabilities": sorted({str(x) for x in (capabilities or []) if str(x)}),
        "expires_at": time.time() + RECONNECT_TTL_SECONDS,
        "helper_connected_at": time.time(),
    })
    return bind_workspace(session_id, connection, mode=normalized_mode, task=task, capabilities=capabilities, reconnect_pair_key=key)


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


def bind_workspace(session_id: str, connection: Any, *, mode: str, task: str = "", capabilities: list[str] | None = None, reconnect_pair_key: str | None = None) -> dict[str, Any]:
    normalized_mode = "write" if str(mode).strip().lower() == "write" else "read_only"
    binding = {
        "session_id": session_id,
        "connection": connection,
        "connection_id": id(connection),
        "client_id": str(getattr(connection, "client_id", "")),
        "mode": normalized_mode,
        "task": str(task or "")[:4000],
        "capabilities": sorted({str(x) for x in (capabilities or []) if str(x)}),
        "reconnect_pair_key": reconnect_pair_key,
        "reconnect_until": time.time() + RECONNECT_TTL_SECONDS if reconnect_pair_key else 0,
        "bound_at": time.time(),
    }
    _SESSION_WORKSPACE[session_id] = binding
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
        until = now + RECONNECT_TTL_SECONDS
        item.update({"connection": None, "connection_id": None, "client_id": "", "suspended_at": now, "reconnect_until": until})
        pair = _WEB_PAIR.get(pair_key)
        if pair:
            pair.update({"connection": None, "connection_id": None, "client_id": "", "expires_at": until})
    for item in _WEB_PAIR.values():
        if item.get("connection_id") == id(connection):
            item.update({"connection": None, "connection_id": None, "client_id": ""})


def unbind_connection(connection: Any) -> None:
    for session_id, item in list(_SESSION_WORKSPACE.items()):
        if item.get("connection_id") == id(connection):
            pair_key = str(item.get("reconnect_pair_key") or "")
            if pair_key:
                _WEB_PAIR.pop(pair_key, None)
            _SESSION_WORKSPACE.pop(session_id, None)
    for key, item in list(_WEB_PAIR.items()):
        if item.get("connection_id") == id(connection):
            _WEB_PAIR.pop(key, None)


def clear_session(session_id: str) -> None:
    pair = _SESSION_PAIR.pop(session_id, None)
    if pair:
        _PAIR_INDEX.pop(_pair_key(str(pair.get("code") or "")), None)
    binding = _SESSION_WORKSPACE.pop(session_id, None)
    if binding:
        pair_key = str(binding.get("reconnect_pair_key") or "")
        if pair_key:
            _WEB_PAIR.pop(pair_key, None)
    for key, item in list(_WEB_PAIR.items()):
        if str(item.get("paired_session_id") or "") == session_id:
            _WEB_PAIR.pop(key, None)


def _cleanup_web_pairs() -> None:
    now = time.time()
    for key, item in list(_WEB_PAIR.items()):
        if float(item.get("expires_at") or 0) <= now:
            _WEB_PAIR.pop(key, None)


def workspace_status(session_id: str) -> dict[str, Any]:
    binding = get_workspace(session_id)
    if binding:
        return {
            "connected": True,
            "mode": binding["mode"],
            "task": binding.get("task", ""),
            "client_id": binding.get("client_id", ""),
            "capabilities": list(binding.get("capabilities") or []),
        }
    suspended = _SESSION_WORKSPACE.get(session_id)
    if suspended and float(suspended.get("reconnect_until") or 0) > time.time():
        return {"connected": False, "suspended": True, "mode": suspended.get("mode", "read_only"), "task": suspended.get("task", ""), "capabilities": list(suspended.get("capabilities") or []), "reconnect_until": float(suspended.get("reconnect_until") or 0)}
    return {
        "connected": False,
        "setup_url": "https://api.ailinux.me/v1/mcp",
        "procedure": "Open the setup page, choose a local folder, then post its pairing code in this chat.",
    }
