"""Private, persistent Discord voice conversation history.

Only transcribed *questions* and successful model answers are saved; no raw audio.
Stored on the TriForce host (not in Git). Retains up to 200 turns for 30 days.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_PATH = Path.home() / ".local/state/ailinux-nova-voice/conversation.sqlite3"
MAX_TURNS = 200
MAX_AGE_SECONDS = 30 * 24 * 60 * 60


class VoiceHistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else Path(os.getenv("NOVA_VOICE_HISTORY_DB", str(DEFAULT_PATH)))
        if self.path.parent.is_symlink():
            raise ValueError("History state directory must not be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        if self.path.is_symlink():
            raise ValueError("History file must not be a symlink")
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS turns_user_idx ON turns(guild_id, user_id, id DESC)")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=8)
        self.path.chmod(0o600)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _key(guild_id: str, user_id: str) -> tuple[str, str]:
        if not guild_id.isdecimal() or not user_id.isdecimal():
            raise ValueError("Invalid Discord identifiers")
        return guild_id, user_id

    def find_request(self, request_id: str, guild_id: str, user_id: str) -> dict | None:
        guild_id, user_id = self._key(guild_id, user_id)
        with self._db() as db:
            item = db.execute(
                "SELECT id, user_text, assistant_text FROM turns WHERE request_id=? AND guild_id=? AND user_id=?",
                (request_id, guild_id, user_id),
            ).fetchone()
        if not item:
            return None
        return {"turn_id": item["id"], "question": item["user_text"], "response": item["assistant_text"]}

    def recent(self, guild_id: str, user_id: str, limit: int = 8) -> list[dict]:
        guild_id, user_id = self._key(guild_id, user_id)
        limit = max(1, min(int(limit), 20))
        with self._db() as db:
            rows = db.execute(
                "SELECT id, user_text, assistant_text, created_at FROM turns "
                "WHERE guild_id=? AND user_id=? AND created_at>=? ORDER BY id DESC LIMIT ?",
                (guild_id, user_id, int(time.time()) - MAX_AGE_SECONDS, limit),
            ).fetchall()
        return [
            {"id": row["id"], "user": row["user_text"], "assistant": row["assistant_text"], "created_at": row["created_at"]}
            for row in reversed(rows)
        ]

    def messages(self, guild_id: str, user_id: str, limit: int = 8) -> list[dict]:
        result = []
        for turn in self.recent(guild_id, user_id, limit=limit):
            result.append({"role": "user", "content": turn["user"]})
            result.append({"role": "assistant", "content": turn["assistant"]})
        return result

    def save(self, request_id: str, guild_id: str, user_id: str, user_text: str, answer: str) -> dict:
        guild_id, user_id = self._key(guild_id, user_id)
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 80:
            raise ValueError("Invalid request identifier")
        if not (1 <= len(user_text.strip()) <= 550 and 1 <= len(answer.strip()) <= 380):
            raise ValueError("Conversation turn outside size limits")
        now = int(time.time())
        with self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO turns(request_id, guild_id, user_id, user_text, assistant_text, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (request_id, guild_id, user_id, user_text, answer, now),
            )
            db.execute("DELETE FROM turns WHERE created_at<?", (now - MAX_AGE_SECONDS,))
            db.execute(
                "DELETE FROM turns WHERE guild_id=? AND user_id=? AND id NOT IN "
                "(SELECT id FROM turns WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT ?)",
                (guild_id, user_id, guild_id, user_id, MAX_TURNS),
            )
        result = self.find_request(request_id, guild_id, user_id)
        if result is None:
            raise RuntimeError("Conversation turn not persisted")
        return result

    def count(self, guild_id: str, user_id: str) -> int:
        guild_id, user_id = self._key(guild_id, user_id)
        with self._db() as db:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM turns WHERE guild_id=? AND user_id=? AND created_at>=?",
                (guild_id, user_id, int(time.time()) - MAX_AGE_SECONDS),
            ).fetchone()
        return int(row["n"])

    def clear(self, guild_id: str, user_id: str) -> int:
        guild_id, user_id = self._key(guild_id, user_id)
        with self._db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM turns WHERE guild_id=? AND user_id=?", (guild_id, user_id)
            ).fetchone()[0]
            db.execute("DELETE FROM turns WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        return int(count)
