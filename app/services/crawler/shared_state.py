from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict, Optional, Set

from app.paths import DATA_DIR
from ...config import get_settings


class CrawlerSharedState:
    """Shared state for crawler instances (seen set + idempotency cache)."""

    def __init__(self, persist_name: str = "crawler-shared-state.json") -> None:
        settings = get_settings()
        spool_dir = Path(getattr(settings, "crawler_spool_dir", str(DATA_DIR / "crawler_spool")))
        spool_dir.mkdir(parents=True, exist_ok=True)

        self._persist_path = spool_dir / persist_name
        self._seen_urls: Set[str] = set()
        self._idempotency_map: Dict[str, str] = {}
        self._dirty = False
        self._flush_every = 200
        self._additions_since_flush = 0
        self._lock = asyncio.Lock()

        self._load_from_disk()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _load_from_disk(self) -> None:
        if not self._persist_path.exists():
            return
        try:
            with self._persist_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            seen = data.get("seen_urls", [])
            idempotency = data.get("idempotency_map", {})
            if isinstance(seen, list):
                self._seen_urls.update(map(str, seen))
            if isinstance(idempotency, dict):
                self._idempotency_map.update({str(k): str(v) for k, v in idempotency.items()})
        except (json.JSONDecodeError, OSError):
            # Corrupted file – start fresh but keep file for future flushes
            self._seen_urls.clear()
            self._idempotency_map.clear()

    async def _flush(self) -> None:
        payload = {
            "seen_urls": sorted(self._seen_urls),
            "idempotency_map": dict(self._idempotency_map),
        }
        await asyncio.to_thread(
            self._persist_path.write_text,
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._dirty = False
        self._additions_since_flush = 0

    async def _mark_dirty(self) -> None:
        self._dirty = True
        self._additions_since_flush += 1
        if self._additions_since_flush >= self._flush_every:
            await self._flush()

    async def flush(self) -> None:
        async with self._lock:
            if self._dirty:
                await self._flush()

    # ------------------------------------------------------------------
    # Seen-set management
    # ------------------------------------------------------------------
    async def mark_url_seen(self, url_hash: str) -> bool:
        """Mark URL hash as seen. Returns True if newly added."""
        async with self._lock:
            if url_hash in self._seen_urls:
                return False
            self._seen_urls.add(url_hash)
            await self._mark_dirty()
            return True

    async def has_seen(self, url_hash: str) -> bool:
        async with self._lock:
            return url_hash in self._seen_urls

    # ------------------------------------------------------------------
    # Idempotency keyed jobs
    # ------------------------------------------------------------------
    async def get_job_for_key(self, key: str) -> Optional[str]:
        async with self._lock:
            return self._idempotency_map.get(key)

    async def register_job_for_key(self, key: str, job_id: str) -> None:
        async with self._lock:
            existing = self._idempotency_map.get(key)
            if existing == job_id:
                return
            self._idempotency_map[key] = job_id
            await self._mark_dirty()


shared_crawler_state = CrawlerSharedState()
