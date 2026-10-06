"""Canonical synchronized Project Memory store.

Project Memory is current project state. It is authoritative for sync ordering,
while Claude-Mem remains append-only episodic history.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.paths import TRISTAR_DIR
from app.services.episodic_memory import redact

ALLOWED_KINDS = {
    "todo", "idea", "decision", "architecture", "bug", "lesson",
    "feature", "project_summary", "documentation",
}
DB_PATH = Path(os.environ.get(
    "TRIFORCE_PROJECT_MEMORY_DB",
    str(TRISTAR_DIR / "project-memory" / "project_memory.db"),
))

MAX_CHANGES = 100
MAX_PROJECT_KEY = 256
MAX_ENTITY_ID = 128
MAX_TITLE = 512
MAX_CONTENT = 12_000
MAX_STATUS = 64
MAX_DEVICE_ID = 128
MAX_SOURCE = 128


class ProjectMemoryValidationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded(value: Any, limit: int, field: str, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ProjectMemoryValidationError(f"{field} is required")
    if len(text) > limit:
        raise ProjectMemoryValidationError(f"{field} exceeds {limit} characters")
    return text


def _content_hash(kind: str, title: str, content: str, status: str, deleted: bool) -> str:
    payload = json.dumps(
        {"kind": kind, "title": title, "content": content, "status": status, "deleted": bool(deleted)},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _assert_no_secrets(*values: str) -> None:
    for value in values:
        if redact(value) != value:
            raise ProjectMemoryValidationError("project memory contains secret-like material")


def normalize_change(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ProjectMemoryValidationError("change must be an object")
    entity_id = _bounded(raw.get("entity_id"), MAX_ENTITY_ID, "entity_id", required=True)
    kind = _bounded(raw.get("kind"), 40, "kind", required=True)
    if kind not in ALLOWED_KINDS:
        raise ProjectMemoryValidationError(f"unsupported kind: {kind}")
    title = _bounded(raw.get("title"), MAX_TITLE, "title", required=True)
    content = _bounded(raw.get("content"), MAX_CONTENT, "content")
    status = _bounded(raw.get("status") or "active", MAX_STATUS, "status", required=True)
    device_id = _bounded(raw.get("device_id") or "unknown", MAX_DEVICE_ID, "device_id", required=True)
    source = _bounded(raw.get("source") or "aicoder", MAX_SOURCE, "source", required=True)
    try:
        base_version = int(raw.get("base_version", 0))
    except (TypeError, ValueError) as exc:
        raise ProjectMemoryValidationError("base_version must be an integer") from exc
    if base_version < 0:
        raise ProjectMemoryValidationError("base_version must be >= 0")
    deleted = bool(raw.get("deleted", False))
    _assert_no_secrets(title, content, status, source, device_id)
    digest = _content_hash(kind, title, content, status, deleted)
    claimed = str(raw.get("content_hash") or "").strip()
    if claimed and claimed != digest:
        raise ProjectMemoryValidationError("content_hash mismatch")
    return {
        "entity_id": entity_id,
        "kind": kind,
        "title": title,
        "content": content,
        "status": status,
        "base_version": base_version,
        "content_hash": digest,
        "deleted": deleted,
        "device_id": device_id,
        "source": source,
    }


class ProjectMemoryStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else DB_PATH
        self.lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        conn = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self.lock:
            conn = self._connect()
            try:
                conn.executescript("""
                    BEGIN;
                    CREATE TABLE IF NOT EXISTS project_memory_current (
                        owner_id TEXT NOT NULL,
                        project_key TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        status TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        server_seq INTEGER NOT NULL,
                        content_hash TEXT NOT NULL,
                        deleted INTEGER NOT NULL DEFAULT 0,
                        source TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(owner_id, project_key, entity_id)
                    );
                    CREATE TABLE IF NOT EXISTS project_memory_revisions (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id TEXT NOT NULL,
                        project_key TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        status TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        base_version INTEGER NOT NULL,
                        content_hash TEXT NOT NULL,
                        deleted INTEGER NOT NULL DEFAULT 0,
                        source TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        accepted INTEGER NOT NULL,
                        conflict INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_pm_pull
                        ON project_memory_revisions(owner_id, project_key, accepted, seq);
                    CREATE INDEX IF NOT EXISTS idx_pm_current
                        ON project_memory_current(owner_id, project_key, server_seq);
                    CREATE INDEX IF NOT EXISTS idx_pm_idempotency
                        ON project_memory_revisions(
                            owner_id, project_key, entity_id, base_version,
                            content_hash, deleted, source, device_id, accepted
                        );
                    COMMIT;
                """)
            finally:
                conn.close()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["deleted"] = bool(data.get("deleted"))
        for key in ("owner_id", "accepted", "conflict", "created_at"):
            data.pop(key, None)
        if "seq" in data and "server_seq" not in data:
            data["server_seq"] = int(data.pop("seq"))
        return data

    def _cursor(self, conn: sqlite3.Connection, owner_id: str, project_key: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq),0) FROM project_memory_revisions "
            "WHERE owner_id=? AND project_key=? AND accepted=1",
            (owner_id, project_key),
        ).fetchone()
        return int(row[0] if row else 0)

    def sync(
        self, *, owner_id: str, project_key: str, cursor: int = 0,
        changes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        owner_id = _bounded(owner_id, 256, "owner_id", required=True)
        project_key = _bounded(project_key, MAX_PROJECT_KEY, "project_key", required=True)
        try:
            cursor = max(0, int(cursor or 0))
        except (TypeError, ValueError) as exc:
            raise ProjectMemoryValidationError("cursor must be an integer") from exc
        raw_changes = list(changes or [])
        if len(raw_changes) > MAX_CHANGES:
            raise ProjectMemoryValidationError(f"too many changes; max {MAX_CHANGES}")
        normalized = [normalize_change(item) for item in raw_changes]

        accepted: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for change in normalized:
                    idem = conn.execute(
                        "SELECT * FROM project_memory_revisions WHERE "
                        "owner_id=? AND project_key=? AND entity_id=? AND base_version=? "
                        "AND content_hash=? AND deleted=? AND source=? AND device_id=? AND accepted=1 "
                        "ORDER BY seq DESC LIMIT 1",
                        (
                            owner_id, project_key, change["entity_id"], change["base_version"],
                            change["content_hash"], int(change["deleted"]), change["source"], change["device_id"],
                        ),
                    ).fetchone()
                    current = conn.execute(
                        "SELECT * FROM project_memory_current "
                        "WHERE owner_id=? AND project_key=? AND entity_id=?",
                        (owner_id, project_key, change["entity_id"]),
                    ).fetchone()
                    if idem is not None and current is not None and int(current["server_seq"]) == int(idem["seq"]):
                        item = self._public(idem)
                        item["idempotent"] = True
                        accepted.append(item)
                        continue
                    if idem is not None and current is not None:
                        conflicts.append({
                            "entity_id": change["entity_id"],
                            "base_version": change["base_version"],
                            "attempted_revision_seq": int(idem["seq"]),
                            "attempted": self._public(idem),
                            "current": self._public(current),
                            "idempotent_stale": True,
                        })
                        continue

                    current_version = int(current["version"]) if current is not None else 0
                    now = _now()

                    if change["base_version"] != current_version:
                        proposed_version = change["base_version"] + 1
                        cur = conn.execute(
                            """INSERT INTO project_memory_revisions
                               (owner_id,project_key,entity_id,kind,title,content,status,version,base_version,
                                content_hash,deleted,source,device_id,updated_at,accepted,conflict,created_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (
                                owner_id, project_key, change["entity_id"], change["kind"], change["title"],
                                change["content"], change["status"], proposed_version, change["base_version"],
                                change["content_hash"], int(change["deleted"]), change["source"], change["device_id"],
                                now, 0, 1, now,
                            ),
                        )
                        conflicts.append({
                            "entity_id": change["entity_id"],
                            "base_version": change["base_version"],
                            "attempted_revision_seq": int(cur.lastrowid),
                            "attempted": {
                                **change,
                                "version": proposed_version,
                                "updated_at": now,
                            },
                            "current": self._public(current) if current is not None else None,
                        })
                        continue

                    version = current_version + 1
                    cur = conn.execute(
                        """INSERT INTO project_memory_revisions
                           (owner_id,project_key,entity_id,kind,title,content,status,version,base_version,
                            content_hash,deleted,source,device_id,updated_at,accepted,conflict,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            owner_id, project_key, change["entity_id"], change["kind"], change["title"],
                            change["content"], change["status"], version, change["base_version"],
                            change["content_hash"], int(change["deleted"]), change["source"], change["device_id"],
                            now, 1, 0, now,
                        ),
                    )
                    seq = int(cur.lastrowid)
                    conn.execute(
                        """INSERT INTO project_memory_current
                           (owner_id,project_key,entity_id,kind,title,content,status,version,server_seq,
                            content_hash,deleted,source,device_id,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(owner_id,project_key,entity_id) DO UPDATE SET
                             kind=excluded.kind,title=excluded.title,content=excluded.content,status=excluded.status,
                             version=excluded.version,server_seq=excluded.server_seq,content_hash=excluded.content_hash,
                             deleted=excluded.deleted,source=excluded.source,device_id=excluded.device_id,
                             updated_at=excluded.updated_at""",
                        (
                            owner_id, project_key, change["entity_id"], change["kind"], change["title"],
                            change["content"], change["status"], version, seq, change["content_hash"],
                            int(change["deleted"]), change["source"], change["device_id"], now,
                        ),
                    )
                    accepted.append({
                        **change,
                        "version": version,
                        "server_seq": seq,
                        "updated_at": now,
                    })

                remote_rows = conn.execute(
                    """SELECT seq,entity_id,kind,title,content,status,version,base_version,content_hash,
                              deleted,source,device_id,updated_at
                       FROM project_memory_revisions
                       WHERE owner_id=? AND project_key=? AND accepted=1 AND seq>?
                       ORDER BY seq ASC LIMIT 1000""",
                    (owner_id, project_key, cursor),
                ).fetchall()
                new_cursor = self._cursor(conn, owner_id, project_key)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

        return {
            "cursor": new_cursor,
            "accepted": accepted,
            "conflicts": conflicts,
            "remote": [self._public(row) for row in remote_rows],
        }

    def current(self, *, owner_id: str, project_key: str, include_deleted: bool = False) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            sql = (
                "SELECT entity_id,kind,title,content,status,version,server_seq,content_hash,"
                "deleted,source,device_id,updated_at FROM project_memory_current "
                "WHERE owner_id=? AND project_key=?"
            )
            params: list[Any] = [owner_id, project_key]
            if not include_deleted:
                sql += " AND deleted=0"
            sql += " ORDER BY server_seq DESC"
            return [self._public(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()


_STORE: ProjectMemoryStore | None = None
_STORE_LOCK = threading.Lock()


def get_project_memory_store() -> ProjectMemoryStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = ProjectMemoryStore()
    return _STORE
