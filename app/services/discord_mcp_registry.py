"""Discord MCP RC1 persistence. NO authentication is implemented by this module.

Only callers that already validated the AILinux account and Discord OAuth subject
may invoke mutations. Nothing in this module grants Discord API permissions.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path


class DiscordRegistry:
    def __init__(self, database: str | Path):
        self.database = str(database)
        self._init()

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _init(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS discord_links (
                  ailinux_subject TEXT NOT NULL,
                  discord_subject TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  PRIMARY KEY (ailinux_subject, discord_subject),
                  UNIQUE (discord_subject)
                );
                CREATE TABLE IF NOT EXISTS discord_guild_grants (
                  ailinux_subject TEXT NOT NULL,
                  discord_subject TEXT NOT NULL,
                  guild_id TEXT NOT NULL,
                  scopes TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  PRIMARY KEY (ailinux_subject, guild_id),
                  FOREIGN KEY (ailinux_subject, discord_subject)
                    REFERENCES discord_links(ailinux_subject, discord_subject)
                    ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS discord_pair_codes (
                  code_hash TEXT PRIMARY KEY,
                  ailinux_subject TEXT NOT NULL,
                  issued_at INTEGER NOT NULL,
                  expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discord_oauth_pending (
                  state_hash TEXT PRIMARY KEY,
                  ailinux_subject TEXT NOT NULL,
                  expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS discord_oauth_expiry
                  ON discord_oauth_pending(expires_at);
                CREATE INDEX IF NOT EXISTS discord_pair_expiry
                  ON discord_pair_codes(expires_at);
                CREATE TABLE IF NOT EXISTS discord_guild_grant_audit (
                  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ailinux_subject TEXT NOT NULL,
                  guild_id TEXT NOT NULL,
                  scopes TEXT NOT NULL,
                  changed_at INTEGER NOT NULL
                );
            """)

    @staticmethod
    def _valid_id(value: str) -> bool:
        return isinstance(value, str) and 1 <= len(value) <= 128 and not value.isspace()

    @staticmethod
    def _discord_id(value: str) -> bool:
        return isinstance(value, str) and value.isascii() and value.isdigit() and 17 <= len(value) <= 20

    def link(self, account: str, discord_user: str):
        if not self._valid_id(account) or not self._discord_id(discord_user):
            raise ValueError("invalid identity")
        with self._connect() as db:
            db.execute("INSERT INTO discord_links VALUES (?, ?, ?)",
                       (account, discord_user, int(time.time())))

    def grant(self, account: str, discord_user: str, guild: str, scopes: set[str]):
        allowed = {"text:read", "text:send", "voice:join", "voice:listen", "voice:speak", "mcp:read"}
        if not self._valid_id(account) or not self._discord_id(discord_user) or not self._discord_id(guild):
            raise ValueError("invalid identity or guild")
        if not scopes or not scopes.issubset(allowed):
            raise ValueError("unknown or empty scopes")
        with self._connect() as db:
            linked = db.execute("SELECT 1 FROM discord_links WHERE ailinux_subject=? AND discord_subject=?",
                                (account, discord_user)).fetchone()
            if not linked:
                raise PermissionError("Discord OAuth identity not linked")
            db.execute("INSERT OR REPLACE INTO discord_guild_grants VALUES (?, ?, ?, ?, ?)",
                       (account, discord_user, guild, " ".join(sorted(scopes)), int(time.time())))
            db.execute(
                "INSERT INTO discord_guild_grant_audit (ailinux_subject,guild_id,scopes,changed_at)"
                " VALUES (?,?,?,?)",
                (account, guild, " ".join(sorted(scopes)), int(time.time())))

    def revoke_guild(self, account: str, guild: str) -> None:
        if not self._valid_id(account) or not self._discord_id(guild):
            raise ValueError("invalid account or guild")
        with self._connect() as db:
            db.execute("DELETE FROM discord_guild_grants WHERE ailinux_subject=? AND guild_id=?",
                       (account, guild))
            db.execute(
                "INSERT INTO discord_guild_grant_audit (ailinux_subject,guild_id,scopes,changed_at)"
                " VALUES (?,?,?,?)", (account, guild, "", int(time.time())))

    def can(self, account: str, discord_user: str, guild: str, scope: str) -> bool:
        with self._connect() as db:
            record = db.execute("""
                SELECT scopes FROM discord_guild_grants
                WHERE ailinux_subject=? AND discord_subject=? AND guild_id=?
            """, (account, discord_user, guild)).fetchone()
            return bool(record and scope in record["scopes"].split())

    def create_pair(self, account: str, ttl: int = 300) -> str:
        if not self._valid_id(account) or not 30 <= ttl <= 600:
            raise ValueError("invalid account or TTL")
        code = secrets.token_urlsafe(32)
        digest = hashlib.sha256(code.encode("ascii")).hexdigest()
        now = int(time.time())
        with self._connect() as db:
            db.execute("DELETE FROM discord_pair_codes WHERE expires_at <= ?", (now,))
            db.execute("INSERT INTO discord_pair_codes VALUES (?, ?, ?, ?)",
                       (digest, account, now, now + ttl))
        return code

    def redeem_pair(self, code: str) -> str | None:
        if not isinstance(code, str) or not 20 <= len(code) <= 128:
            return None
        digest = hashlib.sha256(code.encode("utf8")).hexdigest()
        now = int(time.time())
        with self._connect() as db:
            record = db.execute("SELECT ailinux_subject, expires_at FROM discord_pair_codes WHERE code_hash=?",
                                (digest,)).fetchone()
            db.execute("DELETE FROM discord_pair_codes WHERE code_hash=?", (digest,))
            if record and record["expires_at"] > now:
                return record["ailinux_subject"]
        return None

    def begin_oauth(self, account: str, ttl: int = 300) -> str:
        if not self._valid_id(account) or not 30 <= ttl <= 600:
            raise ValueError("invalid account or TTL")
        state = secrets.token_urlsafe(32)
        digest = hashlib.sha256(state.encode("ascii")).hexdigest()
        now = int(time.time())
        with self._connect() as db:
            db.execute("DELETE FROM discord_oauth_pending WHERE expires_at <= ?", (now,))
            db.execute("INSERT INTO discord_oauth_pending VALUES (?, ?, ?)",
                       (digest, account, now + ttl))
        return state

    def consume_oauth(self, state: str) -> str | None:
        if not isinstance(state, str) or not 20 <= len(state) <= 128:
            return None
        digest = hashlib.sha256(state.encode("utf8")).hexdigest()
        with self._connect() as db:
            row = db.execute("SELECT ailinux_subject, expires_at FROM discord_oauth_pending WHERE state_hash=?",
                             (digest,)).fetchone()
            db.execute("DELETE FROM discord_oauth_pending WHERE state_hash=?", (digest,))
            if row and row["expires_at"] > int(time.time()):
                return row["ailinux_subject"]
        return None

    def linked_ids(self, account: str) -> list[str]:
        with self._connect() as db:
            return [x["discord_subject"] for x in db.execute(
                "SELECT discord_subject FROM discord_links WHERE ailinux_subject=?", (account,)
            ).fetchall()]

    def authorized_guilds(self, account: str) -> list[dict]:
        with self._connect() as db:
            return [{"discord_user_id": row["discord_subject"], "guild_id": row["guild_id"],
                     "scopes": row["scopes"].split()}
                    for row in db.execute(
                        "SELECT discord_subject,guild_id,scopes FROM discord_guild_grants "
                        "WHERE ailinux_subject=? ORDER BY guild_id", (account,)
                    ).fetchall()]

    def revoke(self, account: str):
        with self._connect() as db:
            db.execute("DELETE FROM discord_pair_codes WHERE ailinux_subject=?", (account,))
            db.execute("DELETE FROM discord_oauth_pending WHERE ailinux_subject=?", (account,))
            db.execute("DELETE FROM discord_guild_grants WHERE ailinux_subject=?", (account,))
            db.execute("DELETE FROM discord_links WHERE ailinux_subject=?", (account,))
