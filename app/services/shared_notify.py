"""Account-scoped Shared Notify, presence and mailbox service.

Logical identity is deliberately separate from transport identity:
user -> stable device_id -> stable endpoint_id -> globally unique @handle.
Transient login client IDs and websocket/node IDs are never used as durable
addresses. SQLite provides process-safe handle claims and durable offline mail.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .shared_authority import (
    AuthorityRole, HUMAN_AUTHORITY_ROLES, authorize_message, default_role_for_kind, parse_authority_role,
)


AVAILABILITIES = {
    "available", "busy", "waiting", "blocked", "do_not_disturb", "quota_limited", "offline",
}
ACTIVITIES = {
    "idle", "open_for_human_chat", "open_for_ai_chat", "working", "working_hard",
    "researching", "coding", "reviewing", "thinking", "waiting_for_operator", "waiting_for_agent",
}
ENDPOINT_KINDS = {"human", "ai", "client", "agent", "model", "service"}
MESSAGE_KINDS = {"human_chat", "request", "task", "review", "coordination", "ai_optimization", "brainstorm", "handoff"}
VISIBILITIES = {"account", "public", "private"}
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
_ENDPOINT_RE = re.compile(r"^ep_[a-zA-Z0-9_-]{12,64}$")
_RESERVED_HANDLES = {"admin", "root", "system", "support", "broadcast", "all", "everyone"}
_DEFAULT_TTL = max(30, min(int(os.environ.get("SHARED_NOTIFY_PRESENCE_TTL", "120")), 3600))
_MAX_HOPS = max(2, min(int(os.environ.get("SHARED_NOTIFY_MAX_HOPS", "8")), 32))
_DB_PATH = Path(os.environ.get("SHARED_NOTIFY_DB", "/var/tristar/shared-notify/shared_notify.db"))


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _owner(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value:
        raise PermissionError("authenticated owner identity required")
    return value[:320]


def normalize_handle(value: str) -> str:
    handle = str(value or "").strip().lower().lstrip("@")
    handle = re.sub(r"[^a-z0-9_-]+", "-", handle).strip("-_")
    handle = handle[:32]
    if not _HANDLE_RE.fullmatch(handle) or handle in _RESERVED_HANDLES:
        raise ValueError("handle must be 2-32 characters: a-z, 0-9, '_' or '-' and not reserved")
    return handle


def _safe_handle_base(value: str) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").lower()).strip("-_")[:24]
    if len(base) < 2 or not base[0].isalnum() or base in _RESERVED_HANDLES:
        base = "client"
    return base


def _bounded_list(value: Iterable[Any] | None, limit: int = 32) -> list[str]:
    out: list[str] = []
    for item in value or []:
        text = str(item).strip().lower()[:64]
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _bounded_dict(value: Any, max_bytes: int = 4096) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    raw = _json(value)
    if len(raw.encode("utf-8")) > max_bytes:
        raise ValueError("metadata too large")
    return value


@dataclass(frozen=True)
class DeliveryTarget:
    endpoint_id: str
    owner_id: str
    handle: str
    kind: str
    transport: str
    target: str
    availability: str
    activity: str
    online: bool
    accept_human_chat: bool
    accept_ai_chat: bool
    accept_tasks: bool


class SharedNotifyStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else _DB_PATH
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _tx(self, immediate: bool = False):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield conn
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    def _ensure_schema(self) -> None:
        # sqlite3.executescript() manages its own transaction boundary. Do not
        # nest it in _tx(), otherwise Python 3.14 may commit the script before
        # the context manager and the outer COMMIT sees no active transaction.
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                CREATE TABLE IF NOT EXISTS endpoints (
                    endpoint_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    handle TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    kind TEXT NOT NULL,
                    authority_role TEXT NOT NULL DEFAULT 'client_member',
                    label TEXT NOT NULL DEFAULT '',
                    transport TEXT NOT NULL DEFAULT 'mailbox',
                    target TEXT NOT NULL DEFAULT '',
                    capabilities_json TEXT NOT NULL DEFAULT '[]',
                    visibility TEXT NOT NULL DEFAULT 'account',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_endpoints_owner ON endpoints(owner_id, enabled);
                CREATE INDEX IF NOT EXISTS idx_endpoints_device ON endpoints(owner_id, device_id);

                CREATE TABLE IF NOT EXISTS presence (
                    endpoint_id TEXT PRIMARY KEY REFERENCES endpoints(endpoint_id) ON DELETE CASCADE,
                    availability TEXT NOT NULL DEFAULT 'available',
                    activity TEXT NOT NULL DEFAULT 'idle',
                    status_text TEXT NOT NULL DEFAULT '',
                    accept_human_chat INTEGER NOT NULL DEFAULT 1,
                    accept_ai_chat INTEGER NOT NULL DEFAULT 1,
                    accept_tasks INTEGER NOT NULL DEFAULT 0,
                    current_task_id TEXT NOT NULL DEFAULT '',
                    task_started_at INTEGER,
                    eta_seconds INTEGER,
                    last_seen INTEGER NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    host_authoritative INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    sender_owner_id TEXT NOT NULL,
                    sender_endpoint_id TEXT,
                    target_endpoint_id TEXT NOT NULL REFERENCES endpoints(endpoint_id),
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    thread_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL DEFAULT '',
                    hop_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'queued',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    dedup_key TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    delivered_at INTEGER,
                    acknowledged_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_messages_target_status
                    ON messages(target_endpoint_id, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_sender_dedup
                    ON messages(sender_owner_id, dedup_key) WHERE dedup_key <> '';

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'group',
                    thread_id TEXT NOT NULL UNIQUE,
                    created_by_endpoint_id TEXT NOT NULL REFERENCES endpoints(endpoint_id),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_owner
                    ON conversations(owner_id, enabled, updated_at);

                CREATE TABLE IF NOT EXISTS conversation_members (
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    endpoint_id TEXT NOT NULL REFERENCES endpoints(endpoint_id),
                    role TEXT NOT NULL DEFAULT 'member',
                    joined_at INTEGER NOT NULL,
                    PRIMARY KEY(conversation_id, endpoint_id)
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_members_endpoint
                    ON conversation_members(endpoint_id, conversation_id);
                    """
                )
                # Forward-compatible migration for databases created before the
                # authority layer existed. Existing endpoints receive only the
                # least-privilege role implied by their server-known endpoint kind.
                columns = {row[1] for row in conn.execute("PRAGMA table_info(endpoints)").fetchall()}
                if "authority_role" not in columns:
                    conn.execute("ALTER TABLE endpoints ADD COLUMN authority_role TEXT NOT NULL DEFAULT 'client_member'")
                    conn.execute("UPDATE endpoints SET authority_role='human_member' WHERE kind='human'")
                    conn.execute("UPDATE endpoints SET authority_role='ai_observer' WHERE kind IN ('ai','agent','model')")
                    conn.execute("UPDATE endpoints SET authority_role='service' WHERE kind='service'")
            finally:
                conn.close()
        try:
            if self.db_path.exists():
                os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _endpoint_public(row: sqlite3.Row, presence: sqlite3.Row | None = None) -> dict[str, Any]:
        now = _now()
        p = presence or row
        last_seen = int(p["last_seen"] or 0) if "last_seen" in p.keys() else 0
        ttl = int(p["ttl_seconds"] or _DEFAULT_TTL) if "ttl_seconds" in p.keys() else _DEFAULT_TTL
        online = bool(last_seen and now - last_seen <= ttl and (p["availability"] if "availability" in p.keys() else "") != "offline")
        availability = str(p["availability"] if "availability" in p.keys() else "offline")
        if not online:
            availability = "offline"
        return {
            "endpoint_id": row["endpoint_id"],
            "device_id": row["device_id"],
            "handle": "@" + row["handle"],
            "kind": row["kind"],
            "authority_role": row["authority_role"],
            "label": row["label"],
            "capabilities": json.loads(row["capabilities_json"] or "[]"),
            "visibility": row["visibility"],
            "availability": availability,
            "activity": str(p["activity"] if "activity" in p.keys() else "idle"),
            "status_text": str(p["status_text"] if "status_text" in p.keys() else ""),
            "accept_human_chat": bool(p["accept_human_chat"] if "accept_human_chat" in p.keys() else 0),
            "accept_ai_chat": bool(p["accept_ai_chat"] if "accept_ai_chat" in p.keys() else 0),
            "accept_tasks": bool(p["accept_tasks"] if "accept_tasks" in p.keys() else 0),
            "current_task_id": str(p["current_task_id"] if "current_task_id" in p.keys() else ""),
            "task_started_at": p["task_started_at"] if "task_started_at" in p.keys() else None,
            "eta_seconds": p["eta_seconds"] if "eta_seconds" in p.keys() else None,
            "last_seen": last_seen,
            "ttl_seconds": ttl,
            "online": online,
        }

    def _allocate_handle(self, conn: sqlite3.Connection, requested: str, *, endpoint_id: str = "") -> str:
        try:
            base = normalize_handle(requested)
        except ValueError:
            base = _safe_handle_base(requested)
        candidates = [base] + [f"{base[:28]}-{i}" for i in range(2, 100)]
        candidates += [f"{base[:26]}-{secrets.token_hex(2)}" for _ in range(8)]
        for candidate in candidates:
            row = conn.execute("SELECT endpoint_id FROM endpoints WHERE handle=? COLLATE NOCASE", (candidate,)).fetchone()
            if row is None or (endpoint_id and row["endpoint_id"] == endpoint_id):
                return candidate
        raise RuntimeError("could not allocate a unique handle")

    def register_endpoint(
        self, *, owner_id: str, device_id: str, requested_handle: str, kind: str = "client",
        label: str = "", capabilities: Iterable[Any] | None = None, visibility: str = "account",
        endpoint_id: str = "", transport: str = "mailbox", target: str = "",
        ttl_seconds: int = _DEFAULT_TTL, host_authoritative: bool = False,
    ) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        device_id = str(device_id or "").strip()[:128]
        if not device_id:
            raise ValueError("device_id required")
        if kind not in ENDPOINT_KINDS:
            raise ValueError("unsupported endpoint kind")
        authority_role = default_role_for_kind(kind).value
        if visibility not in VISIBILITIES:
            raise ValueError("unsupported visibility")
        capabilities = _bounded_list(capabilities)
        now = _now()
        ttl_seconds = max(30, min(int(ttl_seconds or _DEFAULT_TTL), 3600))
        with self._tx(immediate=True) as conn:
            existing = None
            if endpoint_id:
                if not _ENDPOINT_RE.fullmatch(endpoint_id):
                    raise ValueError("invalid endpoint_id")
                existing = conn.execute("SELECT * FROM endpoints WHERE endpoint_id=?", (endpoint_id,)).fetchone()
                if existing is not None and existing["owner_id"] != owner_id:
                    raise PermissionError("endpoint belongs to another owner")
            if existing is None:
                endpoint_id = endpoint_id or "ep_" + secrets.token_urlsafe(18).replace("-", "_")
            handle = self._allocate_handle(conn, requested_handle or label or device_id, endpoint_id=endpoint_id)
            if existing is None:
                conn.execute(
                    """INSERT INTO endpoints(endpoint_id,owner_id,device_id,handle,kind,authority_role,label,transport,target,
                       capabilities_json,visibility,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (endpoint_id, owner_id, device_id, handle, kind, authority_role, str(label)[:120], transport[:32], target[:256],
                     _json(capabilities), visibility, 1, now, now),
                )
                conn.execute(
                    """INSERT INTO presence(endpoint_id,last_seen,ttl_seconds,host_authoritative,updated_at)
                       VALUES(?,?,?,?,?)""", (endpoint_id, now, ttl_seconds, int(host_authoritative), now),
                )
            else:
                conn.execute(
                    """UPDATE endpoints SET device_id=?,handle=?,kind=?,label=?,transport=?,target=?,
                       capabilities_json=?,visibility=?,enabled=1,updated_at=? WHERE endpoint_id=?""",
                    (device_id, handle, kind, str(label)[:120], transport[:32], target[:256],
                     _json(capabilities), visibility, now, endpoint_id),
                )
                conn.execute(
                    "UPDATE presence SET last_seen=?,ttl_seconds=?,host_authoritative=?,updated_at=? WHERE endpoint_id=?",
                    (now, ttl_seconds, int(host_authoritative), now, endpoint_id),
                )
            row = conn.execute(
                "SELECT e.*,p.* FROM endpoints e JOIN presence p USING(endpoint_id) WHERE e.endpoint_id=?", (endpoint_id,)
            ).fetchone()
            return self._endpoint_public(row, row)

    def rename_handle(self, *, owner_id: str, endpoint_id: str, requested_handle: str) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        requested = normalize_handle(requested_handle)
        with self._tx(immediate=True) as conn:
            row = conn.execute("SELECT * FROM endpoints WHERE endpoint_id=?", (endpoint_id,)).fetchone()
            if row is None or row["owner_id"] != owner_id:
                raise LookupError("endpoint not found")
            taken = conn.execute("SELECT endpoint_id FROM endpoints WHERE handle=? COLLATE NOCASE", (requested,)).fetchone()
            if taken is not None and taken["endpoint_id"] != endpoint_id:
                raise ValueError("handle already claimed")
            conn.execute("UPDATE endpoints SET handle=?,updated_at=? WHERE endpoint_id=?", (requested, _now(), endpoint_id))
        return self.get_endpoint(owner_id=owner_id, endpoint_id=endpoint_id)

    def get_endpoint(self, *, owner_id: str, endpoint_id: str) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT e.*,p.* FROM endpoints e JOIN presence p USING(endpoint_id) WHERE e.endpoint_id=? AND e.owner_id=? AND e.enabled=1",
                (endpoint_id, owner_id),
            ).fetchone()
            if row is None:
                raise LookupError("endpoint not found")
            return self._endpoint_public(row, row)

    def resolve_target(self, *, owner_id: str, handle: str) -> DeliveryTarget:
        owner_id = _owner(owner_id)
        normalized = normalize_handle(handle)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT e.*,p.* FROM endpoints e JOIN presence p USING(endpoint_id) WHERE e.handle=? COLLATE NOCASE AND e.enabled=1",
                (normalized,),
            ).fetchone()
            if row is None:
                raise LookupError("notify handle not found")
            if row["owner_id"] != owner_id and row["visibility"] != "public":
                raise PermissionError("notify target is not shared with this account")
            view = self._endpoint_public(row, row)
            return DeliveryTarget(
                endpoint_id=row["endpoint_id"], owner_id=row["owner_id"], handle="@" + row["handle"],
                kind=row["kind"], transport=row["transport"], target=row["target"],
                availability=view["availability"], activity=view["activity"], online=view["online"],
                accept_human_chat=view["accept_human_chat"], accept_ai_chat=view["accept_ai_chat"],
                accept_tasks=view["accept_tasks"],
            )

    def set_presence(
        self, *, owner_id: str, endpoint_id: str, availability: str | None = None,
        activity: str | None = None, status_text: str | None = None,
        accept_human_chat: bool | None = None, accept_ai_chat: bool | None = None,
        accept_tasks: bool | None = None, current_task_id: str | None = None,
        task_started_at: int | None = None, eta_seconds: int | None = None,
        ttl_seconds: int | None = None, host_authoritative: bool | None = None,
    ) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        if availability is not None and availability not in AVAILABILITIES:
            raise ValueError("unsupported availability")
        if activity is not None and activity not in ACTIVITIES:
            raise ValueError("unsupported activity")
        now = _now()
        with self._tx(immediate=True) as conn:
            row = conn.execute("SELECT owner_id FROM endpoints WHERE endpoint_id=? AND enabled=1", (endpoint_id,)).fetchone()
            if row is None or row["owner_id"] != owner_id:
                raise LookupError("endpoint not found")
            old = conn.execute("SELECT * FROM presence WHERE endpoint_id=?", (endpoint_id,)).fetchone()
            if old is None:
                raise LookupError("presence not found")
            values = {
                "availability": availability if availability is not None else old["availability"],
                "activity": activity if activity is not None else old["activity"],
                "status_text": str(status_text)[:240] if status_text is not None else old["status_text"],
                "accept_human_chat": int(accept_human_chat) if accept_human_chat is not None else old["accept_human_chat"],
                "accept_ai_chat": int(accept_ai_chat) if accept_ai_chat is not None else old["accept_ai_chat"],
                "accept_tasks": int(accept_tasks) if accept_tasks is not None else old["accept_tasks"],
                "current_task_id": str(current_task_id)[:128] if current_task_id is not None else old["current_task_id"],
                "task_started_at": task_started_at if task_started_at is not None else old["task_started_at"],
                "eta_seconds": max(0, min(int(eta_seconds), 86400)) if eta_seconds is not None else old["eta_seconds"],
                "ttl_seconds": max(30, min(int(ttl_seconds), 3600)) if ttl_seconds is not None else old["ttl_seconds"],
                "host_authoritative": int(host_authoritative) if host_authoritative is not None else old["host_authoritative"],
            }
            if values["availability"] == "available" and values["activity"] == "idle":
                values["current_task_id"] = ""
                values["task_started_at"] = None
                values["eta_seconds"] = None
            conn.execute(
                """UPDATE presence SET availability=:availability,activity=:activity,status_text=:status_text,
                   accept_human_chat=:accept_human_chat,accept_ai_chat=:accept_ai_chat,accept_tasks=:accept_tasks,
                   current_task_id=:current_task_id,task_started_at=:task_started_at,eta_seconds=:eta_seconds,
                   last_seen=:last_seen,ttl_seconds=:ttl_seconds,host_authoritative=:host_authoritative,
                   updated_at=:updated_at WHERE endpoint_id=:endpoint_id""",
                {**values, "last_seen": now, "updated_at": now, "endpoint_id": endpoint_id},
            )
        return self.get_endpoint(owner_id=owner_id, endpoint_id=endpoint_id)

    def directory(self, *, owner_id: str, include_offline: bool = True) -> list[dict[str, Any]]:
        owner_id = _owner(owner_id)
        with self._tx() as conn:
            rows = conn.execute(
                """SELECT e.*,p.* FROM endpoints e JOIN presence p USING(endpoint_id)
                   WHERE e.enabled=1 AND (e.owner_id=? OR e.visibility='public') ORDER BY e.handle COLLATE NOCASE""",
                (owner_id,),
            ).fetchall()
            views = [self._endpoint_public(row, row) for row in rows]
            return views if include_offline else [row for row in views if row["online"]]

    def heartbeat(self, *, owner_id: str, endpoint_id: str, **presence: Any) -> dict[str, Any]:
        return self.set_presence(owner_id=owner_id, endpoint_id=endpoint_id, **presence)

    def send(
        self, *, sender_owner_id: str, target_handle: str, kind: str, body: str,
        title: str = "", sender_endpoint_id: str = "", thread_id: str = "",
        correlation_id: str = "", message_id: str = "", hop_count: int = 0,
        ttl_seconds: int = 86400, metadata: dict[str, Any] | None = None,
        dedup_key: str = "", actor_authority_role: str = "",
    ) -> tuple[dict[str, Any], DeliveryTarget]:
        sender_owner_id = _owner(sender_owner_id)
        if kind not in MESSAGE_KINDS:
            raise ValueError("unsupported message kind")
        body = str(body or "")[:10000]
        title = str(title or "")[:300]
        if not body and not title:
            raise ValueError("message title or body required")
        hop_count = int(hop_count or 0)
        if hop_count < 0 or hop_count > _MAX_HOPS:
            raise ValueError("message hop limit exceeded")
        metadata = _bounded_dict(metadata or {})
        target = self.resolve_target(owner_id=sender_owner_id, handle=target_handle)
        # Authentication proves tenancy. Human organizational authority comes
        # from the verified account session, while AI/model/service endpoints
        # always retain their own non-human authority even when they share the
        # same account token. This prevents an AI endpoint from inheriting its
        # operator's admin role merely because it runs inside AICoder.
        sender_kind = "client"
        sender_role = AuthorityRole.CLIENT_MEMBER.value
        if sender_endpoint_id:
            with self._tx() as conn:
                sender = conn.execute("SELECT owner_id,kind,authority_role FROM endpoints WHERE endpoint_id=? AND enabled=1", (sender_endpoint_id,)).fetchone()
                if sender is None or sender["owner_id"] != sender_owner_id:
                    raise PermissionError("sender endpoint does not belong to authenticated account")
                sender_kind = str(sender["kind"])
                sender_role = str(sender["authority_role"] or default_role_for_kind(sender_kind).value)
        if sender_kind in {"human", "client"} and actor_authority_role:
            actor = parse_authority_role(actor_authority_role)
            if actor not in HUMAN_AUTHORITY_ROLES:
                raise PermissionError("client/human actor requires verified human authority")
            sender_role = actor.value
        authorize_message(sender_role, kind, delegated_task=False)
        is_ai_sender = sender_kind in {"ai", "agent", "model", "service"}
        if kind == "task" and not target.accept_tasks:
            raise PermissionError("target is not accepting tasks")
        if is_ai_sender and kind != "task" and not target.accept_ai_chat:
            raise PermissionError("target is not accepting AI chat")
        if not is_ai_sender and kind == "human_chat" and not target.accept_human_chat:
            raise PermissionError("target is not accepting human chat")
        now = _now()
        ttl_seconds = max(30, min(int(ttl_seconds or 86400), 7 * 86400))
        message_id = str(message_id or "").strip()[:96] or "msg_" + secrets.token_urlsafe(18).replace("-", "_")
        if not thread_id:
            thread_id = "thr_" + secrets.token_urlsafe(12).replace("-", "_")
        thread_id = str(thread_id)[:96]
        correlation_id = str(correlation_id or "")[:128]
        if not dedup_key:
            material = f"{sender_endpoint_id}\0{target.endpoint_id}\0{kind}\0{thread_id}\0{title}\0{body}"
            dedup_key = hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:32]
        else:
            dedup_key = hashlib.sha256(str(dedup_key).encode()).hexdigest()[:32]
        with self._tx(immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM messages WHERE sender_owner_id=? AND dedup_key=?", (sender_owner_id, dedup_key)
            ).fetchone()
            if existing is not None:
                return self._message_public(existing), target
            conn.execute(
                """INSERT INTO messages(message_id,sender_owner_id,sender_endpoint_id,target_endpoint_id,kind,title,body,
                   thread_id,correlation_id,hop_count,status,metadata_json,dedup_key,created_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (message_id, sender_owner_id, sender_endpoint_id or None, target.endpoint_id, kind, title, body,
                 thread_id, correlation_id, hop_count, "queued", _json(metadata), dedup_key, now, now + ttl_seconds),
            )
            row = conn.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
            return self._message_public(row), target

    @staticmethod
    def _message_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "message_id": row["message_id"],
            "sender_endpoint_id": row["sender_endpoint_id"] or "",
            "target_endpoint_id": row["target_endpoint_id"],
            "kind": row["kind"], "title": row["title"], "body": row["body"],
            "thread_id": row["thread_id"], "correlation_id": row["correlation_id"],
            "hop_count": row["hop_count"], "status": row["status"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"], "expires_at": row["expires_at"],
            "delivered_at": row["delivered_at"], "acknowledged_at": row["acknowledged_at"],
        }

    def create_conversation(
        self, *, owner_id: str, created_by_endpoint_id: str, title: str,
        member_handles: list[str], kind: str = "group",
    ) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        title = str(title or "").strip()[:120]
        kind = str(kind or "group").strip().lower()
        if kind not in {"group", "direct"}:
            raise ValueError("unsupported conversation kind")
        if not created_by_endpoint_id:
            raise ValueError("created_by_endpoint_id is required")
        with self._tx() as conn:
            creator = conn.execute(
                "SELECT * FROM endpoints WHERE endpoint_id=? AND owner_id=? AND enabled=1",
                (created_by_endpoint_id, owner_id),
            ).fetchone()
        if creator is None:
            raise PermissionError("conversation creator endpoint is not owned by authenticated account")

        endpoint_ids = [created_by_endpoint_id]
        seen = {created_by_endpoint_id}
        for handle in list(member_handles or [])[:31]:
            target = self.resolve_target(owner_id=owner_id, handle=handle)
            if target.endpoint_id not in seen:
                seen.add(target.endpoint_id)
                endpoint_ids.append(target.endpoint_id)
        if len(endpoint_ids) < 2:
            raise ValueError("conversation requires at least two participants")
        if kind == "direct" and len(endpoint_ids) != 2:
            raise ValueError("direct conversation requires exactly two participants")
        if kind == "direct":
            # Direct chat identity is the participant pair. Re-opening a contact
            # must resume the existing thread instead of creating endless rooms.
            wanted = set(endpoint_ids)
            with self._tx() as conn:
                candidates = conn.execute(
                    "SELECT conversation_id FROM conversations WHERE owner_id=? AND kind='direct' AND enabled=1 ORDER BY updated_at DESC",
                    (owner_id,),
                ).fetchall()
                for candidate in candidates:
                    members = conn.execute(
                        "SELECT endpoint_id FROM conversation_members WHERE conversation_id=?",
                        (candidate["conversation_id"],),
                    ).fetchall()
                    if {row["endpoint_id"] for row in members} == wanted:
                        return self.get_conversation(owner_id=owner_id, conversation_id=candidate["conversation_id"])

        now = _now()
        conversation_id = "conv_" + secrets.token_urlsafe(15).replace("-", "_")
        thread_id = "thr_" + secrets.token_urlsafe(15).replace("-", "_")
        with self._tx(immediate=True) as conn:
            conn.execute(
                "INSERT INTO conversations(conversation_id,owner_id,title,kind,thread_id,created_by_endpoint_id,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
                (conversation_id, owner_id, title, kind, thread_id, created_by_endpoint_id, now, now),
            )
            for endpoint_id in endpoint_ids:
                role = "owner" if endpoint_id == created_by_endpoint_id else "member"
                conn.execute(
                    "INSERT INTO conversation_members(conversation_id,endpoint_id,role,joined_at) VALUES(?,?,?,?)",
                    (conversation_id, endpoint_id, role, now),
                )
        return self.get_conversation(owner_id=owner_id, conversation_id=conversation_id)

    def get_conversation(self, *, owner_id: str, conversation_id: str) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE conversation_id=? AND owner_id=? AND enabled=1",
                (conversation_id, owner_id),
            ).fetchone()
            if row is None:
                raise LookupError("conversation not found")
            members = conn.execute(
                """SELECT e.endpoint_id,e.handle,e.kind,e.label,cm.role,p.availability,p.activity,p.last_seen,p.ttl_seconds
                   FROM conversation_members cm
                   JOIN endpoints e ON e.endpoint_id=cm.endpoint_id AND e.enabled=1
                   JOIN presence p ON p.endpoint_id=e.endpoint_id
                   WHERE cm.conversation_id=? ORDER BY cm.joined_at,e.handle COLLATE NOCASE""",
                (conversation_id,),
            ).fetchall()
        return {
            "conversation_id": row["conversation_id"], "title": row["title"], "kind": row["kind"],
            "thread_id": row["thread_id"], "created_by_endpoint_id": row["created_by_endpoint_id"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "members": [
                {
                    "endpoint_id": m["endpoint_id"], "handle": "@" + m["handle"], "kind": m["kind"],
                    "label": m["label"], "role": m["role"],
                    "online": int(m["last_seen"] or 0) + int(m["ttl_seconds"] or 0) > _now(),
                    "availability": m["availability"], "activity": m["activity"],
                } for m in members
            ],
        }

    def list_conversations(self, *, owner_id: str, endpoint_id: str = "") -> list[dict[str, Any]]:
        owner_id = _owner(owner_id)
        with self._tx() as conn:
            if endpoint_id:
                rows = conn.execute(
                    """SELECT c.conversation_id FROM conversations c
                       JOIN conversation_members cm ON cm.conversation_id=c.conversation_id
                       WHERE c.owner_id=? AND c.enabled=1 AND cm.endpoint_id=? ORDER BY c.updated_at DESC""",
                    (owner_id, endpoint_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT conversation_id FROM conversations WHERE owner_id=? AND enabled=1 ORDER BY updated_at DESC",
                    (owner_id,),
                ).fetchall()
        return [self.get_conversation(owner_id=owner_id, conversation_id=row["conversation_id"]) for row in rows]

    def send_conversation(
        self, *, owner_id: str, conversation_id: str, sender_endpoint_id: str,
        kind: str, body: str, title: str = "", metadata: dict[str, Any] | None = None,
        ttl_seconds: int = 86400, actor_authority_role: str = "",
    ) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        conversation = self.get_conversation(owner_id=owner_id, conversation_id=conversation_id)
        kind = str(kind or "human_chat").strip().lower()
        if kind == "task":
            raise PermissionError("group/direct conversation messages are advisory; executable tasks require explicit task delivery/delegation")
        member_ids = {row["endpoint_id"] for row in conversation["members"]}
        if sender_endpoint_id not in member_ids:
            raise PermissionError("sender is not a member of this conversation")
        recipients = [row for row in conversation["members"] if row["endpoint_id"] != sender_endpoint_id]
        if not recipients:
            raise ValueError("conversation has no recipients")
        logical_id = "grp_" + secrets.token_urlsafe(15).replace("-", "_")
        shared_meta = dict(metadata or {})
        shared_meta["conversation_id"] = conversation_id
        shared_meta["logical_message_id"] = logical_id
        delivered: list[dict[str, Any]] = []
        for recipient in recipients:
            message, _target = self.send(
                sender_owner_id=owner_id, sender_endpoint_id=sender_endpoint_id,
                target_handle=recipient["handle"], kind=kind, title=title, body=body,
                thread_id=conversation["thread_id"], correlation_id=logical_id,
                ttl_seconds=ttl_seconds, metadata=shared_meta,
                dedup_key=f"conversation:{conversation_id}:{logical_id}:{recipient['endpoint_id']}",
                actor_authority_role=actor_authority_role,
            )
            delivered.append(message)
        with self._tx(immediate=True) as conn:
            conn.execute("UPDATE conversations SET updated_at=? WHERE conversation_id=?", (_now(), conversation_id))
        return {"conversation": conversation, "logical_message_id": logical_id, "deliveries": delivered}

    def conversation_history(self, *, owner_id: str, conversation_id: str, limit: int = 200) -> list[dict[str, Any]]:
        owner_id = _owner(owner_id)
        conversation = self.get_conversation(owner_id=owner_id, conversation_id=conversation_id)
        limit = max(1, min(int(limit or 200), 500))
        with self._tx() as conn:
            rows = conn.execute(
                """SELECT m.*, e.handle AS sender_handle, e.kind AS sender_kind
                   FROM messages m LEFT JOIN endpoints e ON e.endpoint_id=m.sender_endpoint_id
                   WHERE m.thread_id=? ORDER BY m.created_at ASC LIMIT ?""",
                (conversation["thread_id"], limit * 32),
            ).fetchall()
        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            public = self._message_public(row)
            logical = str(public.get("correlation_id") or public["message_id"])
            if logical in unique:
                unique[logical]["delivery_count"] += 1
                continue
            public["sender_handle"] = ("@" + row["sender_handle"]) if row["sender_handle"] else ""
            public["sender_kind"] = row["sender_kind"] or ""
            public["delivery_count"] = 1
            unique[logical] = public
        return list(unique.values())[-limit:]

    def mark_local_delivery(self, *, message_id: str, acknowledged: bool = False) -> dict[str, Any]:
        now = _now()
        with self._tx(immediate=True) as conn:
            row = conn.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
            if row is None:
                raise LookupError("message not found")
            status = "acknowledged" if acknowledged else "delivered"
            conn.execute(
                "UPDATE messages SET status=?, delivered_at=COALESCE(delivered_at,?), acknowledged_at=? WHERE message_id=?",
                (status, now, now if acknowledged else row["acknowledged_at"], message_id),
            )
            return self._message_public(conn.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone())

    def inbox(self, *, owner_id: str, endpoint_id: str, limit: int = 50, mark_delivered: bool = True) -> list[dict[str, Any]]:
        owner_id = _owner(owner_id)
        now = _now()
        limit = max(1, min(int(limit or 50), 200))
        with self._tx(immediate=mark_delivered) as conn:
            ep = conn.execute("SELECT owner_id FROM endpoints WHERE endpoint_id=? AND enabled=1", (endpoint_id,)).fetchone()
            if ep is None or ep["owner_id"] != owner_id:
                raise LookupError("endpoint not found")
            conn.execute("UPDATE messages SET status='expired' WHERE status IN ('queued','delivered') AND expires_at<=?", (now,))
            rows = conn.execute(
                """SELECT * FROM messages WHERE target_endpoint_id=? AND status IN ('queued','delivered') AND expires_at>?
                   ORDER BY created_at ASC LIMIT ?""", (endpoint_id, now, limit),
            ).fetchall()
            if mark_delivered:
                for row in rows:
                    if row["status"] == "queued":
                        conn.execute("UPDATE messages SET status='delivered',delivered_at=? WHERE message_id=?", (now, row["message_id"]))
                rows = [conn.execute("SELECT * FROM messages WHERE message_id=?", (r["message_id"],)).fetchone() for r in rows]
            return [self._message_public(row) for row in rows]

    def ack(self, *, owner_id: str, endpoint_id: str, message_id: str) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        now = _now()
        with self._tx(immediate=True) as conn:
            row = conn.execute(
                """SELECT m.* FROM messages m JOIN endpoints e ON e.endpoint_id=m.target_endpoint_id
                   WHERE m.message_id=? AND e.endpoint_id=? AND e.owner_id=?""",
                (message_id, endpoint_id, owner_id),
            ).fetchone()
            if row is None:
                raise LookupError("message not found")
            conn.execute(
                "UPDATE messages SET status='acknowledged',delivered_at=COALESCE(delivered_at,?),acknowledged_at=? WHERE message_id=?",
                (now, now, message_id),
            )
            return self._message_public(conn.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone())

    def disable_endpoint(self, *, owner_id: str, endpoint_id: str) -> bool:
        owner_id = _owner(owner_id)
        with self._tx(immediate=True) as conn:
            cur = conn.execute(
                "UPDATE endpoints SET enabled=0,updated_at=? WHERE endpoint_id=? AND owner_id=?",
                (_now(), endpoint_id, owner_id),
            )
            return bool(cur.rowcount)

    def stats(self, *, owner_id: str) -> dict[str, int]:
        owner_id = _owner(owner_id)
        with self._tx() as conn:
            ep = conn.execute("SELECT COUNT(*) FROM endpoints WHERE owner_id=? AND enabled=1", (owner_id,)).fetchone()[0]
            queued = conn.execute(
                """SELECT COUNT(*) FROM messages m JOIN endpoints e ON e.endpoint_id=m.target_endpoint_id
                   WHERE e.owner_id=? AND m.status='queued' AND m.expires_at>?""", (owner_id, _now()),
            ).fetchone()[0]
            return {"endpoints": int(ep), "queued_messages": int(queued)}


_store: SharedNotifyStore | None = None
_store_lock = threading.Lock()


def get_shared_notify_store() -> SharedNotifyStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = SharedNotifyStore()
    return _store
