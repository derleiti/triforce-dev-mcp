"""
Enhanced Memory Service v2.60 - Confidence, TTL, Versioning

Provides intelligent memory management for TriForce:
- Confidence scores (0.0-1.0) with validation support
- TTL (Time-To-Live) for automatic expiration
- Versioning with history tracking
- Project and tag-based filtering
- LLM validation tracking
"""

import uuid
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Set
from enum import Enum
import logging
import json
from pathlib import Path

from app.paths import STATE_DIR

logger = logging.getLogger("ailinux.triforce.memory")


class MemoryType(str, Enum):
    """Types of memory entries"""
    FACT = "fact"           # Factual information
    DECISION = "decision"   # Decisions made
    CODE = "code"           # Code snippets
    SUMMARY = "summary"     # Summaries
    CONTEXT = "context"     # Context information
    TODO = "todo"           # Tasks and TODOs


class Importance(str, Enum):
    """Importance levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class MemoryEntry:
    """A single memory entry with enhanced features"""
    id: str
    content: str
    type: MemoryType
    created_at: str
    updated_at: str

    # Enhanced features
    confidence: float = 0.8
    ttl_hours: Optional[int] = None
    expires_at: Optional[str] = None
    version: int = 1
    previous_version_id: Optional[str] = None

    # Metadata
    source_llm: Optional[str] = None
    validated_by: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    importance: str = "medium"
    project_id: Optional[str] = None

    # Search optimization
    keywords: List[str] = field(default_factory=list)

    def is_expired(self) -> bool:
        """Check if entry has expired"""
        if not self.expires_at:
            return False

        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return datetime.utcnow() > exp.replace(tzinfo=None)
        except Exception:
            return False

    def validate(self, llm_id: str) -> bool:
        """Add LLM validation and increase confidence"""
        if llm_id not in self.validated_by:
            self.validated_by.append(llm_id)
            # Each validation increases confidence by 5%, max 1.0
            self.confidence = min(1.0, self.confidence + 0.05)
            self.updated_at = datetime.utcnow().isoformat() + "Z"
            return True
        return False

    def invalidate(self, llm_id: str, reason: str = "") -> bool:
        """Reduce confidence due to invalidation"""
        # Each invalidation reduces confidence by 10%
        self.confidence = max(0.0, self.confidence - 0.1)
        self.updated_at = datetime.utcnow().isoformat() + "Z"
        return True

    def matches_query(self, query: str) -> bool:
        """Check if entry matches a search query"""
        query_lower = query.lower()

        # Check content
        if query_lower in self.content.lower():
            return True

        # Check keywords
        if any(query_lower in kw.lower() for kw in self.keywords):
            return True

        # Check tags
        if any(query_lower in tag.lower() for tag in self.tags):
            return True

        return False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        data = asdict(self)
        data["type"] = self.type.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        """Create from dictionary"""
        data = data.copy()
        if "type" in data:
            data["type"] = MemoryType(data["type"])
        return cls(**data)


class EnhancedMemoryService:
    """Enhanced memory service with confidence, TTL, and versioning"""

    def __init__(
        self,
        storage_dir: str | None = None,
        max_entries: int = 10000
    ):
        self.storage_dir = Path(storage_dir) if storage_dir is not None else (STATE_DIR / "memory")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries

        self._entries: Dict[str, MemoryEntry] = {}
        self._versions: Dict[str, List[str]] = {}  # original_id -> [version_ids]
        self._by_project: Dict[str, Set[str]] = {}  # project_id -> entry_ids
        self._by_tag: Dict[str, Set[str]] = {}      # tag -> entry_ids

        # Load persisted entries
        self._load_from_disk()

    async def store(
        self,
        content: str,
        type: MemoryType,
        project_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        importance: str = "medium",
        confidence: float = 0.8,
        ttl_hours: Optional[int] = None,
        source_llm: Optional[str] = None,
        keywords: Optional[List[str]] = None
    ) -> MemoryEntry:
        """Store a new memory entry"""
        now = datetime.utcnow()

        entry = MemoryEntry(
            id=f"mem_{uuid.uuid4().hex[:12]}",
            content=content,
            type=type,
            created_at=now.isoformat() + "Z",
            updated_at=now.isoformat() + "Z",
            confidence=confidence,
            ttl_hours=ttl_hours,
            expires_at=(now + timedelta(hours=ttl_hours)).isoformat() + "Z" if ttl_hours else None,
            source_llm=source_llm,
            tags=tags or [],
            importance=importance,
            project_id=project_id,
            keywords=keywords or []
        )

        self._add_entry(entry)
        await self._persist_entry(entry)

        logger.info(f"Stored memory {entry.id}: {content[:50]}...")
        return entry

    async def recall(
        self,
        query: Optional[str] = None,
        type: Optional[MemoryType] = None,
        project_id: Optional[str] = None,
        limit: int = 10,
        min_confidence: float = 0.0,
        max_age_hours: Optional[int] = None,
        tags: Optional[List[str]] = None,
        include_expired: bool = False
    ) -> List[MemoryEntry]:
        """Recall memories matching criteria"""
        results = []
        now = datetime.utcnow()

        for entry in self._entries.values():
            # Skip expired unless requested
            if not include_expired and entry.is_expired():
                continue

            # Filter by project
            if project_id and entry.project_id != project_id:
                continue

            # Filter by type
            if type and entry.type != type:
                continue

            # Filter by confidence
            if entry.confidence < min_confidence:
                continue

            # Filter by age
            if max_age_hours:
                created = datetime.fromisoformat(entry.created_at.replace("Z", ""))
                age_hours = (now - created).total_seconds() / 3600
                if age_hours > max_age_hours:
                    continue

            # Filter by tags (any match)
            if tags and not any(t in entry.tags for t in tags):
                continue

            # Filter by query
            if query and not entry.matches_query(query):
                continue

            results.append(entry)

        # Sort by confidence (desc) then by updated_at (desc)
        results.sort(
            key=lambda x: (x.confidence, x.updated_at),
            reverse=True
        )

        return results[:limit]

    async def update(
        self,
        memory_id: str,
        content: Optional[str] = None,
        confidence: Optional[float] = None,
        tags: Optional[List[str]] = None,
        validated_by: Optional[str] = None,
        importance: Optional[str] = None
    ) -> Optional[MemoryEntry]:
        """Update a memory entry, creating a new version"""
        if memory_id not in self._entries:
            return None

        old = self._entries[memory_id]
        now = datetime.utcnow()

        # Create new version
        new_id = f"mem_{uuid.uuid4().hex[:12]}"
        new_entry = MemoryEntry(
            id=new_id,
            content=content or old.content,
            type=old.type,
            created_at=old.created_at,
            updated_at=now.isoformat() + "Z",
            confidence=confidence if confidence is not None else old.confidence,
            ttl_hours=old.ttl_hours,
            expires_at=old.expires_at,
            version=old.version + 1,
            previous_version_id=memory_id,
            source_llm=old.source_llm,
            validated_by=old.validated_by.copy(),
            tags=tags if tags is not None else old.tags,
            importance=importance or old.importance,
            project_id=old.project_id,
            keywords=old.keywords
        )

        # Handle validation
        if validated_by:
            new_entry.validate(validated_by)

        # Track version history
        original_id = self._find_original_id(memory_id)
        if original_id not in self._versions:
            self._versions[original_id] = [memory_id]
        self._versions[original_id].append(new_id)

        self._add_entry(new_entry)
        await self._persist_entry(new_entry)

        logger.info(f"Updated memory {memory_id} -> {new_id} (v{new_entry.version})")
        return new_entry

    async def get(self, memory_id: str) -> Optional[MemoryEntry]:
        """Get a specific memory entry"""
        return self._entries.get(memory_id)

    async def delete(self, memory_id: str) -> bool:
        """Delete a memory entry"""
        if memory_id not in self._entries:
            return False

        entry = self._entries.pop(memory_id)
        self._remove_from_indexes(entry)

        logger.info(f"Deleted memory {memory_id}")
        return True

    async def get_history(self, memory_id: str) -> List[MemoryEntry]:
        """Get version history for a memory entry"""
        original_id = self._find_original_id(memory_id)

        if original_id not in self._versions:
            if memory_id in self._entries:
                return [self._entries[memory_id]]
            return []

        return [
            self._entries[vid]
            for vid in self._versions[original_id]
            if vid in self._entries
        ]

    async def validate(
        self,
        memory_id: str,
        llm_id: str
    ) -> Optional[MemoryEntry]:
        """Add validation from an LLM"""
        if memory_id not in self._entries:
            return None

        entry = self._entries[memory_id]
        if entry.validate(llm_id):
            await self._persist_entry(entry)

        return entry

    async def cleanup_expired(self) -> int:
        """Remove expired entries"""
        expired_ids = [
            eid for eid, entry in self._entries.items()
            if entry.is_expired()
        ]

        for eid in expired_ids:
            await self.delete(eid)

        if expired_ids:
            logger.info(f"Cleaned up {len(expired_ids)} expired memory entries")

        return len(expired_ids)

    async def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics"""
        by_type = {}
        by_project = {}
        total_confidence = 0.0
        expired = 0

        for entry in self._entries.values():
            # By type
            type_key = entry.type.value
            by_type[type_key] = by_type.get(type_key, 0) + 1

            # By project
            proj = entry.project_id or "global"
            by_project[proj] = by_project.get(proj, 0) + 1

            # Confidence
            total_confidence += entry.confidence

            # Expired
            if entry.is_expired():
                expired += 1

        return {
            "total": len(self._entries),
            "by_type": by_type,
            "by_project": by_project,
            "avg_confidence": total_confidence / max(len(self._entries), 1),
            "expired": expired,
            "versions_tracked": len(self._versions),
        }

    # Internal methods
    def _add_entry(self, entry: MemoryEntry):
        """Add entry to memory and indexes"""
        self._entries[entry.id] = entry

        # Index by project
        if entry.project_id:
            if entry.project_id not in self._by_project:
                self._by_project[entry.project_id] = set()
            self._by_project[entry.project_id].add(entry.id)

        # Index by tags
        for tag in entry.tags:
            if tag not in self._by_tag:
                self._by_tag[tag] = set()
            self._by_tag[tag].add(entry.id)

    def _remove_from_indexes(self, entry: MemoryEntry):
        """Remove entry from indexes"""
        if entry.project_id and entry.project_id in self._by_project:
            self._by_project[entry.project_id].discard(entry.id)

        for tag in entry.tags:
            if tag in self._by_tag:
                self._by_tag[tag].discard(entry.id)

    def _find_original_id(self, memory_id: str) -> str:
        """Find the original ID for a versioned entry"""
        # Check if this is an original
        if memory_id in self._versions:
            return memory_id

        # Find which chain this belongs to
        for orig_id, versions in self._versions.items():
            if memory_id in versions:
                return orig_id

        # It's a new original
        return memory_id

    async def _persist_entry(self, entry: MemoryEntry):
        """Persist entry to disk"""
        try:
            # Use project-based files
            project = entry.project_id or "global"
            filepath = self.storage_dir / f"memory_{project}.jsonl"

            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to persist memory {entry.id}: {e}")

    def _load_from_disk(self):
        """Load entries from disk"""
        if not self.storage_dir.exists():
            return

        count = 0
        for filepath in self.storage_dir.glob("memory_*.jsonl"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            entry = MemoryEntry.from_dict(data)
                            if not entry.is_expired():
                                self._add_entry(entry)
                                count += 1
            except Exception as e:
                logger.error(f"Failed to load {filepath}: {e}")

        if count:
            logger.info(f"Loaded {count} memory entries from disk")


# Singleton instance
memory_service = EnhancedMemoryService()
