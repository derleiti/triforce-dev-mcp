"""Session-scoped pairing between public MCP clients and local workspace nodes."""
from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any, Optional

PAIR_TTL_SECONDS = 15 * 60
_SESSION_PAIR: dict[str, dict[str, Any]] = {}
_PAIR_INDEX: dict[str, str] = {}
_SESSION_WORKSPACE: dict[str, dict[str, Any]] = {}


def _pair_key(code: str) -> str:
    return hashlib.sha256(str(code or "").strip().encode("utf-8")).hexdigest()


def _new_code() -> str:
    # 96 bits, URL/terminal friendly. Display in four-hex groups.
    raw = secrets.token_hex(12).upper()
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def get_or_create_pair_code(session_id: str) -> str:
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
    key = _pair_key(code)
    session_id = _PAIR_INDEX.get(key)
    if not session_id:
        return None
    item = _SESSION_PAIR.get(session_id)
    if not item or float(item.get("expires_at") or 0) <= time.time():
        _PAIR_INDEX.pop(key, None)
        if item:
            _SESSION_PAIR.pop(session_id, None)
        return None
    if not secrets.compare_digest(str(item.get("code") or ""), str(code or "").strip()):
        return None
    return session_id


def bind_workspace(session_id: str, connection: Any, *, mode: str, task: str = "") -> dict[str, Any]:
    normalized_mode = "write" if str(mode).strip().lower() == "write" else "read_only"
    binding = {
        "session_id": session_id,
        "connection": connection,
        "connection_id": id(connection),
        "client_id": str(getattr(connection, "client_id", "")),
        "mode": normalized_mode,
        "task": str(task or "")[:4000],
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
        _SESSION_WORKSPACE.pop(session_id, None)
        return None
    return dict(item)


def unbind_connection(connection: Any) -> None:
    for session_id, item in list(_SESSION_WORKSPACE.items()):
        if item.get("connection_id") == id(connection):
            _SESSION_WORKSPACE.pop(session_id, None)


def clear_session(session_id: str) -> None:
    pair = _SESSION_PAIR.pop(session_id, None)
    if pair:
        _PAIR_INDEX.pop(_pair_key(str(pair.get("code") or "")), None)
    _SESSION_WORKSPACE.pop(session_id, None)


def workspace_status(session_id: str) -> dict[str, Any]:
    binding = get_workspace(session_id)
    if binding:
        return {
            "connected": True,
            "mode": binding["mode"],
            "task": binding.get("task", ""),
            "client_id": binding.get("client_id", ""),
        }
    code = get_or_create_pair_code(session_id)
    return {
        "connected": False,
        "pair_code": code,
        "pair_expires_seconds": PAIR_TTL_SECONDS,
    }
