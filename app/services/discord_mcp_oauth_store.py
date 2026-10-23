"""Private OAuth 2.1 token/code store for the Discord MCP resource server.

All credentials are stored as SHA-256 hashes and are bound to their client,
account, exact resource URI and scopes. Independent of the legacy TriForce MCP.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time

RESOURCE = "https://discord.ailinux.me/mcp"
SCOPES = frozenset({"discord:read", "offline_access"})
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DiscordMcpOAuthStore:
    def __init__(self, location: str):
        self.location = str(location)
        self._initialize()

    def _db(self):
        db = sqlite3.connect(self.location, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self):
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS discord_mcp_clients (
                    client_id TEXT PRIMARY KEY,
                    client_name TEXT NOT NULL,
                    redirect_uris TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    caller_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discord_mcp_authorizations (
                    csrf_hash TEXT PRIMARY KEY,
                    account TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    state TEXT NOT NULL,
                    code_challenge TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discord_mcp_auth_codes (
                    code_hash TEXT PRIMARY KEY,
                    account TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    code_challenge TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discord_mcp_tokens (
                    token_hash TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    account TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS discord_mcp_token_owner ON discord_mcp_tokens(account);
            """)

    def register_client(self, name: str, redirect_uris: list[str], caller: str) -> dict:
        from urllib.parse import urlsplit
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 96:
            raise ValueError("Invalid client name")
        if not isinstance(redirect_uris, list) or not 1 <= len(redirect_uris) <= 6:
            raise ValueError("Invalid redirect URI list")
        for uri in redirect_uris:
            if not isinstance(uri, str) or len(uri) > 2048 or "#" in uri:
                raise ValueError("Invalid redirect URI")
            url = urlsplit(uri)
            if url.username or url.password or not url.hostname:
                raise ValueError("Invalid redirect URI")
            if url.scheme == "https" and url.netloc:
                continue
            if url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"} and url.port:
                continue
            raise ValueError("Redirect URI must be HTTPS or a loopback port")
        client_id = "dmcp_" + secrets.token_urlsafe(24)
        now = int(time.time())
        with self._db() as db:
            # No unlimited anonymous growth from DCR. Rate limiting persists across restarts.
            used = db.execute(
                "SELECT COUNT(*) FROM discord_mcp_clients WHERE caller_hash=? AND created_at>?",
                (_hash(caller), now - 3600),
            ).fetchone()[0]
            if used >= 15:
                raise PermissionError("Too many recent client registrations")
            db.execute(
                "INSERT INTO discord_mcp_clients VALUES (?,?,?,?,?)",
                (client_id, name.strip(), json.dumps(redirect_uris), now, _hash(caller)),
            )
        return {
            "client_id": client_id,
            "client_id_issued_at": now,
            "client_name": name.strip(),
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }

    def client_for_redirect(self, client_id: str, redirect: str) -> dict | None:
        if not client_id or not redirect:
            return None
        with self._db() as db:
            row = db.execute("SELECT * FROM discord_mcp_clients WHERE client_id=?", (client_id,)).fetchone()
            if row and redirect in json.loads(row["redirect_uris"]):
                return {"name": row["client_name"], "redirect": redirect}
        return None

    def begin_authorize(self, account: str, client_id: str, redirect: str,
                        state: str, challenge: str, scopes: str) -> str:
        if not self.client_for_redirect(client_id, redirect):
            raise PermissionError("Unregistered client redirect")
        csrf = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._db() as db:
            db.execute("DELETE FROM discord_mcp_authorizations WHERE expires_at < ?", (now,))
            db.execute("INSERT INTO discord_mcp_authorizations VALUES (?,?,?,?,?,?,?,?)",
                       (_hash(csrf), account, client_id, redirect, state, challenge, scopes, now + 300))
        return csrf

    def finish_authorize(self, csrf: str, account: str, approved: bool) -> dict | None:
        if not isinstance(csrf, str) or len(csrf) < 32:
            return None
        now = int(time.time())
        with self._db() as db:
            row = db.execute(
                "DELETE FROM discord_mcp_authorizations WHERE csrf_hash=? RETURNING *",
                (_hash(csrf),),
            ).fetchone()
            if not row or row["account"] != account or row["expires_at"] <= now:
                return None
            data = dict(row)
            if approved:
                code = secrets.token_urlsafe(40)
                db.execute(
                    "INSERT INTO discord_mcp_auth_codes VALUES (?,?,?,?,?,?,?)",
                    (_hash(code), account, data["client_id"], data["redirect_uri"],
                     data["code_challenge"], data["scopes"], now + 180),
                )
                data["code"] = code
        return data

    def exchange_code(self, code: str, client_id: str, redirect: str, verifier: str) -> dict | None:
        import base64
        if not isinstance(verifier, str) or not 43 <= len(verifier) <= 128:
            return None
        if not verifier.isascii() or not all(ch.isalnum() or ch in "-._~" for ch in verifier):
            return None
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        now = int(time.time())
        with self._db() as db:
            row = db.execute(
                "DELETE FROM discord_mcp_auth_codes WHERE code_hash=? RETURNING *",
                (_hash(code),),
            ).fetchone()
            if not row or row["expires_at"] <= now or row["client_id"] != client_id:
                return None
            if row["redirect_uri"] != redirect or not secrets.compare_digest(row["code_challenge"], challenge):
                return None
            return self._issue(db, row["account"], client_id, row["scopes"], now)

    def _issue(self, db, account: str, client_id: str, scopes: str, now: int) -> dict:
        access = secrets.token_urlsafe(40)
        refresh = secrets.token_urlsafe(40)
        db.executemany(
            "INSERT INTO discord_mcp_tokens VALUES (?,?,?,?,?,?,?)",
            [(_hash(access), "access", account, client_id, RESOURCE, scopes, now + ACCESS_TTL),
             (_hash(refresh), "refresh", account, client_id, RESOURCE, scopes, now + REFRESH_TTL)],
        )
        return {"access_token": access, "token_type": "Bearer",
                "expires_in": ACCESS_TTL, "refresh_token": refresh, "scope": scopes}

    def refresh(self, token: str, client_id: str) -> dict | None:
        now = int(time.time())
        with self._db() as db:
            row = db.execute("DELETE FROM discord_mcp_tokens WHERE token_hash=? AND kind='refresh' RETURNING *",
                             (_hash(token),)).fetchone()
            if not row or row["expires_at"] <= now or row["client_id"] != client_id or row["resource"] != RESOURCE:
                return None
            # Clear expired access tokens for this refresh client, but not all user's active sessions.
            return self._issue(db, row["account"], client_id, row["scopes"], now)

    def verify(self, token: str) -> dict | None:
        if not isinstance(token, str) or not 32 <= len(token) <= 200:
            return None
        with self._db() as db:
            row = db.execute(
                "SELECT account,client_id,resource,scopes,expires_at FROM discord_mcp_tokens "
                "WHERE token_hash=? AND kind='access'", (_hash(token),),
            ).fetchone()
            if row and row["expires_at"] > int(time.time()) and row["resource"] == RESOURCE:
                return dict(row)
        return None

    def revoke_account(self, account: str):
        with self._db() as db:
            db.execute("DELETE FROM discord_mcp_tokens WHERE account=?", (account,))
            db.execute("DELETE FROM discord_mcp_auth_codes WHERE account=?", (account,))
            db.execute("DELETE FROM discord_mcp_authorizations WHERE account=?", (account,))
