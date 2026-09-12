"""
Memory MCP Controller v2.80
Shared Memory Management für 120+ LLM-Instanzen

Features:
- Distributed Memory Pool mit Sharding
- Confidence Scoring Engine
- TTL & Version Management
- Query Optimizer mit Caching
- Health Monitoring

Basiert auf Empfehlungen von:
- DeepSeek (Algorithmus-Design)
- Mistral (Security/Isolation)
- Cogito (Konsistenz-Struktur)
"""

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Any, Tuple
from pathlib import Path
from collections import defaultdict
import aiofiles
import logging

from app.paths import TRISTAR_DIR

logger = logging.getLogger("ailinux.tristar.memory_controller")

# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class MemoryEntry:
    """Ein Memory-Eintrag im Shared Memory Pool"""
    entry_id: str
    content: str
    content_hash: str
    memory_type: str  # fact, decision, code, summary, context, todo

    # Confidence Tracking
    confidence_scores: Dict[str, float] = field(default_factory=dict)  # {llm_id: score}
    aggregate_confidence: float = 0.0

    # Versioning
    version: int = 1
    previous_version_id: Optional[str] = None

    # Lifecycle
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    access_count: int = 0
    ttl_seconds: int = 86400  # 24h default
    expires_at: Optional[datetime] = None

    # Metadata
    tags: Set[str] = field(default_factory=set)
    project_id: Optional[str] = None
    created_by: str = "system"

    def __post_init__(self):
        if self.expires_at is None:
            self.expires_at = self.created_at + timedelta(seconds=self.ttl_seconds)
        self._update_aggregate_confidence()

    def _update_aggregate_confidence(self):
        """Berechnet den aggregierten Konfidenz-Score"""
        if self.confidence_scores:
            self.aggregate_confidence = sum(self.confidence_scores.values()) / len(self.confidence_scores)
        else:
            self.aggregate_confidence = 0.0

    def update_confidence(self, llm_id: str, score: float):
        """Aktualisiert den Konfidenz-Score für einen LLM"""
        self.confidence_scores[llm_id] = max(0.0, min(1.0, score))
        self._update_aggregate_confidence()
        self.updated_at = datetime.now(timezone.utc)

    def touch(self):
        """Markiert den Eintrag als zugegriffen"""
        self.last_accessed = datetime.now(timezone.utc)
        self.access_count += 1

    def is_expired(self) -> bool:
        """Prüft ob der Eintrag abgelaufen ist"""
        return datetime.now(timezone.utc) > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "content": self.content,
            "content_hash": self.content_hash,
            "memory_type": self.memory_type,
            "confidence_scores": self.confidence_scores,
            "aggregate_confidence": self.aggregate_confidence,
            "version": self.version,
            "previous_version_id": self.previous_version_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "access_count": self.access_count,
            "ttl_seconds": self.ttl_seconds,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "tags": list(self.tags),
            "project_id": self.project_id,
            "created_by": self.created_by,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        return cls(
            entry_id=data["entry_id"],
            content=data["content"],
            content_hash=data["content_hash"],
            memory_type=data["memory_type"],
            confidence_scores=data.get("confidence_scores", {}),
            aggregate_confidence=data.get("aggregate_confidence", 0.0),
            version=data.get("version", 1),
            previous_version_id=data.get("previous_version_id"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(timezone.utc),
            last_accessed=datetime.fromisoformat(data["last_accessed"]) if data.get("last_accessed") else datetime.now(timezone.utc),
            access_count=data.get("access_count", 0),
            ttl_seconds=data.get("ttl_seconds", 86400),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            tags=set(data.get("tags", [])),
            project_id=data.get("project_id"),
            created_by=data.get("created_by", "system"),
        )


# ============================================================================
# Memory Shard
# ============================================================================

class MemoryShard:
    """Ein Shard des verteilten Memory Pools"""

    def __init__(self, shard_id: int, max_entries: int = 10000):
        self.shard_id = shard_id
        self.max_entries = max_entries
        self.entries: Dict[str, MemoryEntry] = {}
        self._lock = asyncio.Lock()
        self._metrics = {
            "stores": 0,
            "retrievals": 0,
            "evictions": 0,
            "hits": 0,
            "misses": 0,
        }

    async def store(self, entry: MemoryEntry) -> bool:
        """Speichert einen Eintrag im Shard"""
        async with self._lock:
            # Eviction wenn voll
            if len(self.entries) >= self.max_entries:
                await self._evict_lru()

            self.entries[entry.entry_id] = entry
            self._metrics["stores"] += 1
            return True

    async def get(self, entry_id: str) -> Optional[MemoryEntry]:
        """Holt einen Eintrag aus dem Shard"""
        async with self._lock:
            self._metrics["retrievals"] += 1
            entry = self.entries.get(entry_id)
            if entry:
                if entry.is_expired():
                    del self.entries[entry_id]
                    self._metrics["evictions"] += 1
                    self._metrics["misses"] += 1
                    return None
                entry.touch()
                self._metrics["hits"] += 1
                return entry
            self._metrics["misses"] += 1
            return None

    async def search(self, query: str, min_confidence: float = 0.0,
                     tags: Optional[Set[str]] = None) -> List[MemoryEntry]:
        """Sucht Einträge im Shard"""
        results = []
        query_lower = query.lower()

        async with self._lock:
            for entry in self.entries.values():
                if entry.is_expired():
                    continue
                if entry.aggregate_confidence < min_confidence:
                    continue
                if tags and not tags.issubset(entry.tags):
                    continue
                if query_lower in entry.content.lower():
                    results.append(entry)

        return results

    async def delete(self, entry_id: str) -> bool:
        """Löscht einen Eintrag"""
        async with self._lock:
            if entry_id in self.entries:
                del self.entries[entry_id]
                return True
            return False

    async def get_expired(self) -> List[str]:
        """Gibt IDs abgelaufener Einträge zurück"""
        expired = []
        async with self._lock:
            for entry_id, entry in self.entries.items():
                if entry.is_expired():
                    expired.append(entry_id)
        return expired

    async def _evict_lru(self):
        """Entfernt den am längsten nicht genutzten Eintrag"""
        if not self.entries:
            return

        lru_id = min(self.entries.keys(),
                     key=lambda k: self.entries[k].last_accessed)
        del self.entries[lru_id]
        self._metrics["evictions"] += 1

    def get_metrics(self) -> Dict[str, Any]:
        return {
            "shard_id": self.shard_id,
            "entry_count": len(self.entries),
            "max_entries": self.max_entries,
            **self._metrics,
        }


# ============================================================================
# Memory MCP Controller
# ============================================================================

class MemoryMCPController:
    """
    Zentraler Memory Controller für das Multi-LLM Mesh.

    Verwaltet Shared Memory zwischen 120+ LLM-Instanzen mit:
    - Confidence Scoring pro LLM
    - TTL und Versionierung
    - Effiziente Sharded Storage
    - Persistenz nach JSONL
    """

    def __init__(
        self,
        num_shards: int = 12,
        max_entries_per_shard: int = 10000,
        data_dir: str | Path = TRISTAR_DIR / "memory",
        cleanup_interval: int = 300,  # 5 Minuten
    ):
        self.num_shards = num_shards
        self.data_dir = Path(data_dir)
        self.cleanup_interval = cleanup_interval

        # Shards initialisieren
        self.shards = [
            MemoryShard(i, max_entries_per_shard)
            for i in range(num_shards)
        ]

        # LRU Query Cache
        self._query_cache: Dict[str, Tuple[float, List[MemoryEntry]]] = {}
        self._cache_ttl = 60  # 1 Minute Cache
        self._cache_max_size = 1000

        # LLM Registry
        self._llm_registry: Dict[str, Dict[str, Any]] = {}

        # Cleanup Task
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

        # Metrics
        self._metrics = {
            "total_stores": 0,
            "total_retrievals": 0,
            "total_searches": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }

    async def initialize(self):
        """Initialisiert den Controller"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        await self._load_from_disk()
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"MemoryMCPController initialized with {self.num_shards} shards")

    async def shutdown(self):
        """Fährt den Controller herunter"""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        await self._persist_to_disk()
        logger.info("MemoryMCPController shutdown complete")

    def _get_shard(self, entry_id: str) -> MemoryShard:
        """Bestimmt den Shard für eine Entry-ID"""
        hash_value = int(hashlib.md5(entry_id.encode()).hexdigest(), 16)
        return self.shards[hash_value % self.num_shards]

    def _generate_id(self) -> str:
        """Generiert eine eindeutige Entry-ID"""
        return f"mem_{uuid.uuid4().hex[:16]}"

    def _content_hash(self, content: str) -> str:
        """Berechnet den Hash des Contents"""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    # ========================================================================
    # Core Operations
    # ========================================================================

    async def store(
        self,
        content: str,
        memory_type: str = "fact",
        llm_id: str = "system",
        initial_confidence: float = 0.8,
        tags: Optional[List[str]] = None,
        project_id: Optional[str] = None,
        ttl_seconds: int = 86400,
    ) -> MemoryEntry:
        """Speichert einen neuen Memory-Eintrag"""
        entry_id = self._generate_id()

        entry = MemoryEntry(
            entry_id=entry_id,
            content=content,
            content_hash=self._content_hash(content),
            memory_type=memory_type,
            confidence_scores={llm_id: initial_confidence},
            tags=set(tags) if tags else set(),
            project_id=project_id,
            ttl_seconds=ttl_seconds,
            created_by=llm_id,
        )

        shard = self._get_shard(entry_id)
        await shard.store(entry)

        self._metrics["total_stores"] += 1
        self._invalidate_cache()

        logger.debug(f"Stored memory {entry_id} by {llm_id}")
        return entry

    async def retrieve(self, entry_id: str) -> Optional[MemoryEntry]:
        """Holt einen Memory-Eintrag"""
        shard = self._get_shard(entry_id)
        entry = await shard.get(entry_id)
        self._metrics["total_retrievals"] += 1
        return entry

    async def search(
        self,
        query: str,
        min_confidence: float = 0.0,
        tags: Optional[List[str]] = None,
        project_id: Optional[str] = None,
        memory_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[MemoryEntry]:
        """Sucht Memory-Einträge"""
        # Cache Check
        cache_key = f"{query}:{min_confidence}:{tags}:{project_id}:{memory_type}"
        if cache_key in self._query_cache:
            cached_time, cached_results = self._query_cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                self._metrics["cache_hits"] += 1
                return cached_results[:limit]

        self._metrics["cache_misses"] += 1
        self._metrics["total_searches"] += 1

        # Search across all shards
        tag_set = set(tags) if tags else None
        all_results = []

        for shard in self.shards:
            results = await shard.search(query, min_confidence, tag_set)
            all_results.extend(results)

        # Filter by project and type
        if project_id:
            all_results = [e for e in all_results if e.project_id == project_id]
        if memory_type:
            all_results = [e for e in all_results if e.memory_type == memory_type]

        # Sort by confidence
        all_results.sort(key=lambda e: e.aggregate_confidence, reverse=True)

        # Cache results
        self._cache_result(cache_key, all_results)

        return all_results[:limit]

    async def update_confidence(
        self,
        entry_id: str,
        llm_id: str,
        score: float,
    ) -> bool:
        """Aktualisiert den Konfidenz-Score eines LLMs für einen Eintrag"""
        entry = await self.retrieve(entry_id)
        if not entry:
            return False

        entry.update_confidence(llm_id, score)
        self._invalidate_cache()
        return True

    async def create_version(
        self,
        entry_id: str,
        new_content: str,
        llm_id: str,
        confidence: float = 0.8,
    ) -> Optional[MemoryEntry]:
        """Erstellt eine neue Version eines Eintrags"""
        old_entry = await self.retrieve(entry_id)
        if not old_entry:
            return None

        new_entry = MemoryEntry(
            entry_id=self._generate_id(),
            content=new_content,
            content_hash=self._content_hash(new_content),
            memory_type=old_entry.memory_type,
            confidence_scores={llm_id: confidence},
            version=old_entry.version + 1,
            previous_version_id=entry_id,
            tags=old_entry.tags.copy(),
            project_id=old_entry.project_id,
            ttl_seconds=old_entry.ttl_seconds,
            created_by=llm_id,
        )

        shard = self._get_shard(new_entry.entry_id)
        await shard.store(new_entry)

        self._invalidate_cache()
        return new_entry

    async def delete(self, entry_id: str) -> bool:
        """Löscht einen Memory-Eintrag"""
        shard = self._get_shard(entry_id)
        result = await shard.delete(entry_id)
        if result:
            self._invalidate_cache()
        return result

    # ========================================================================
    # LLM Registry
    # ========================================================================

    async def register_llm(
        self,
        llm_id: str,
        role: str,
        capabilities: List[str],
        trust_score: float = 0.5,
    ):
        """Registriert einen LLM im Memory Controller"""
        self._llm_registry[llm_id] = {
            "llm_id": llm_id,
            "role": role,
            "capabilities": capabilities,
            "trust_score": trust_score,
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "memory_access_count": 0,
        }
        logger.info(f"Registered LLM {llm_id} with role {role}")

    async def get_llm_info(self, llm_id: str) -> Optional[Dict[str, Any]]:
        """Gibt Informationen über einen registrierten LLM zurück"""
        return self._llm_registry.get(llm_id)

    async def list_llms(self) -> List[Dict[str, Any]]:
        """Listet alle registrierten LLMs"""
        return list(self._llm_registry.values())

    # ========================================================================
    # Statistics & Health
    # ========================================================================

    async def get_stats(self) -> Dict[str, Any]:
        """Gibt Statistiken des Controllers zurück"""
        shard_stats = [shard.get_metrics() for shard in self.shards]
        total_entries = sum(s["entry_count"] for s in shard_stats)

        return {
            "total_entries": total_entries,
            "num_shards": self.num_shards,
            "registered_llms": len(self._llm_registry),
            "cache_size": len(self._query_cache),
            "metrics": self._metrics,
            "shard_stats": shard_stats,
        }

    async def health_check(self) -> Dict[str, Any]:
        """Führt einen Health Check durch"""
        stats = await self.get_stats()

        return {
            "status": "healthy",
            "total_entries": stats["total_entries"],
            "registered_llms": stats["registered_llms"],
            "shards_active": self.num_shards,
            "cleanup_running": self._running,
        }

    # ========================================================================
    # Cache Management
    # ========================================================================

    def _cache_result(self, key: str, results: List[MemoryEntry]):
        """Cached ein Suchergebnis"""
        if len(self._query_cache) >= self._cache_max_size:
            # Entferne ältesten Eintrag
            oldest_key = min(self._query_cache.keys(),
                           key=lambda k: self._query_cache[k][0])
            del self._query_cache[oldest_key]

        self._query_cache[key] = (time.time(), results)

    def _invalidate_cache(self):
        """Invalidiert den Query Cache"""
        self._query_cache.clear()

    # ========================================================================
    # Persistence
    # ========================================================================

    async def _persist_to_disk(self):
        """Persistiert alle Einträge auf Disk"""
        persist_file = self.data_dir / "memory_state.jsonl"

        async with aiofiles.open(persist_file, "w") as f:
            for shard in self.shards:
                for entry in shard.entries.values():
                    await f.write(json.dumps(entry.to_dict()) + "\n")

        # LLM Registry
        registry_file = self.data_dir / "llm_registry.json"
        async with aiofiles.open(registry_file, "w") as f:
            await f.write(json.dumps(self._llm_registry, indent=2))

        logger.info(f"Persisted memory state to {persist_file}")

    async def _load_from_disk(self):
        """Lädt Einträge von Disk"""
        persist_file = self.data_dir / "memory_state.jsonl"

        if persist_file.exists():
            async with aiofiles.open(persist_file, "r") as f:
                async for line in f:
                    try:
                        data = json.loads(line.strip())
                        entry = MemoryEntry.from_dict(data)
                        if not entry.is_expired():
                            shard = self._get_shard(entry.entry_id)
                            await shard.store(entry)
                    except Exception as e:
                        logger.warning(f"Failed to load entry: {e}")

            logger.info(f"Loaded memory state from {persist_file}")

        # LLM Registry
        registry_file = self.data_dir / "llm_registry.json"
        if registry_file.exists():
            async with aiofiles.open(registry_file, "r") as f:
                content = await f.read()
                self._llm_registry = json.loads(content)

    # ========================================================================
    # Cleanup
    # ========================================================================

    async def _cleanup_loop(self):
        """Background Task für Cleanup"""
        while self._running:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    async def _cleanup_expired(self):
        """Entfernt abgelaufene Einträge"""
        total_removed = 0

        for shard in self.shards:
            expired_ids = await shard.get_expired()
            for entry_id in expired_ids:
                await shard.delete(entry_id)
                total_removed += 1

        if total_removed > 0:
            logger.info(f"Cleaned up {total_removed} expired entries")
            self._invalidate_cache()


# ============================================================================
# Singleton Instance
# ============================================================================

memory_controller = MemoryMCPController()


async def init_memory_controller():
    """Initialisiert den Memory Controller"""
    await memory_controller.initialize()
    return memory_controller
