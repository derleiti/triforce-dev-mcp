"""
Memory Index Service v1.0
=========================

Token-effizienter Index für Memory-Einträge.
Speichert Stichwörter mit Memory-IDs für schnelles Lookup.

Funktionsweise:
1. Index enthält nur Stichworte + IDs (minimal Tokens)
2. Bei Bedarf wird per ID das vollständige Memory geladen
3. Parse-Mode: LLMs sollen Index nutzen statt alles zu memorieren
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path

from app.paths import TRISTAR_DIR

logger = logging.getLogger("ailinux.memory_index")

# Index-Datei Pfad
INDEX_PATH = TRISTAR_DIR / "memory_index.json"


@dataclass
class MemoryIndexEntry:
    """Ein Index-Eintrag mit Stichworte und Memory-ID"""
    memory_id: str
    keywords: List[str]
    category: str
    summary: str  # Max 50 Zeichen


class MemoryIndex:
    """
    Token-effizienter Index für Memory-Einträge.

    Verwendung:
        index = MemoryIndex()

        # Eintrag hinzufügen
        index.add("mem_abc123", ["claude", "coder", "review"], "agent", "Claude Coder Agent")

        # Suchen (gibt nur IDs zurück)
        ids = index.search("coder")

        # Vollständiges Memory laden
        memory = await memory_service.get(ids[0])
    """

    def __init__(self):
        self._index: Dict[str, MemoryIndexEntry] = {}
        self._keyword_map: Dict[str, List[str]] = {}  # keyword -> [memory_ids]
        self._category_map: Dict[str, List[str]] = {}  # category -> [memory_ids]
        self._load()

    def _load(self):
        """Lädt Index von Disk"""
        if INDEX_PATH.exists():
            try:
                data = json.loads(INDEX_PATH.read_text())
                for entry_data in data.get("entries", []):
                    entry = MemoryIndexEntry(
                        memory_id=entry_data["id"],
                        keywords=entry_data["kw"],
                        category=entry_data["cat"],
                        summary=entry_data["sum"]
                    )
                    self._add_to_maps(entry)
                logger.info(f"Memory Index loaded: {len(self._index)} entries")
            except Exception as e:
                logger.error(f"Failed to load memory index: {e}")

    def _save(self):
        """Speichert Index auf Disk"""
        try:
            INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "1.0",
                "entries": [
                    {
                        "id": e.memory_id,
                        "kw": e.keywords,
                        "cat": e.category,
                        "sum": e.summary
                    }
                    for e in self._index.values()
                ]
            }
            INDEX_PATH.write_text(json.dumps(data, separators=(',', ':')))
            logger.debug(f"Memory Index saved: {len(self._index)} entries")
        except Exception as e:
            logger.error(f"Failed to save memory index: {e}")

    def _add_to_maps(self, entry: MemoryIndexEntry):
        """Fügt Entry zu internen Maps hinzu"""
        self._index[entry.memory_id] = entry

        # Keyword-Map
        for kw in entry.keywords:
            kw_lower = kw.lower()
            if kw_lower not in self._keyword_map:
                self._keyword_map[kw_lower] = []
            if entry.memory_id not in self._keyword_map[kw_lower]:
                self._keyword_map[kw_lower].append(entry.memory_id)

        # Category-Map
        if entry.category not in self._category_map:
            self._category_map[entry.category] = []
        if entry.memory_id not in self._category_map[entry.category]:
            self._category_map[entry.category].append(entry.memory_id)

    def add(
        self,
        memory_id: str,
        keywords: List[str],
        category: str,
        summary: str
    ) -> MemoryIndexEntry:
        """
        Fügt Memory-Eintrag zum Index hinzu.

        Args:
            memory_id: Die Memory-ID (z.B. "mem_abc123")
            keywords: Liste von Stichwörtern
            category: Kategorie (agent, mesh, tool, config, etc.)
            summary: Kurze Zusammenfassung (max 50 Zeichen)
        """
        entry = MemoryIndexEntry(
            memory_id=memory_id,
            keywords=[kw.lower() for kw in keywords],
            category=category,
            summary=summary[:50]
        )
        self._add_to_maps(entry)
        self._save()
        return entry

    def search(self, query: str) -> List[str]:
        """
        Sucht nach Memory-IDs basierend auf Stichwort.

        Returns: Liste von Memory-IDs
        """
        query_lower = query.lower()

        # Exakter Match
        if query_lower in self._keyword_map:
            return self._keyword_map[query_lower].copy()

        # Partial Match
        results = []
        for kw, ids in self._keyword_map.items():
            if query_lower in kw or kw in query_lower:
                results.extend(ids)

        return list(set(results))

    def search_category(self, category: str) -> List[str]:
        """Sucht nach Memory-IDs in einer Kategorie"""
        return self._category_map.get(category, []).copy()

    def get_entry(self, memory_id: str) -> Optional[MemoryIndexEntry]:
        """Gibt Index-Entry für Memory-ID zurück"""
        return self._index.get(memory_id)

    def remove(self, memory_id: str):
        """Entfernt Entry aus Index"""
        if memory_id not in self._index:
            return

        entry = self._index.pop(memory_id)

        # Aus Keyword-Map entfernen
        for kw in entry.keywords:
            if kw in self._keyword_map:
                self._keyword_map[kw] = [
                    mid for mid in self._keyword_map[kw] if mid != memory_id
                ]

        # Aus Category-Map entfernen
        if entry.category in self._category_map:
            self._category_map[entry.category] = [
                mid for mid in self._category_map[entry.category] if mid != memory_id
            ]

        self._save()

    def get_compact_index(self) -> str:
        """
        Gibt kompakten Index-String für LLM-Context zurück.
        Format: category:keyword1,keyword2→id|...
        """
        lines = []
        for cat, ids in self._category_map.items():
            entries = []
            for mid in ids[:5]:  # Max 5 pro Kategorie
                entry = self._index.get(mid)
                if entry:
                    kws = ",".join(entry.keywords[:3])
                    entries.append(f"{kws}→{mid[-8:]}")  # Nur letzte 8 Zeichen der ID
            if entries:
                lines.append(f"{cat}:{' | '.join(entries)}")

        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Index-Statistiken zurück"""
        return {
            "total_entries": len(self._index),
            "categories": list(self._category_map.keys()),
            "category_counts": {
                cat: len(ids) for cat, ids in self._category_map.items()
            },
            "unique_keywords": len(self._keyword_map)
        }


# Singleton
memory_index = MemoryIndex()


# === MCP Tools ===

MEMORY_INDEX_TOOLS = [
    {
        "name": "memory_index_add",
        "description": "Fügt Memory-Eintrag zum Index hinzu (Stichworte + ID)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "Memory-ID"},
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Stichwörter für schnelles Finden"
                },
                "category": {
                    "type": "string",
                    "enum": ["agent", "mesh", "tool", "config", "prompt", "context"],
                    "description": "Kategorie"
                },
                "summary": {"type": "string", "description": "Kurze Zusammenfassung (max 50 Zeichen)"}
            },
            "required": ["memory_id", "keywords", "category", "summary"]
        }
    },
    {
        "name": "memory_index_search",
        "description": "Sucht Memory-IDs anhand von Stichwort",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchbegriff"},
                "category": {"type": "string", "description": "Optional: Kategorie-Filter"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "memory_index_get",
        "description": "Lädt vollständiges Memory per ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "Memory-ID"}
            },
            "required": ["memory_id"]
        }
    },
    {
        "name": "memory_index_compact",
        "description": "Gibt kompakten Index-String für LLM-Context zurück (token-effizient)",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "memory_index_stats",
        "description": "Zeigt Index-Statistiken",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]


async def handle_memory_index_add(params: Dict[str, Any]) -> Dict[str, Any]:
    """Fügt Entry zum Index hinzu"""
    entry = memory_index.add(
        memory_id=params["memory_id"],
        keywords=params["keywords"],
        category=params["category"],
        summary=params["summary"]
    )
    return {
        "status": "indexed",
        "memory_id": entry.memory_id,
        "keywords": entry.keywords,
        "category": entry.category
    }


async def handle_memory_index_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Sucht im Index"""
    query = params["query"]
    category = params.get("category")

    if category:
        ids = memory_index.search_category(category)
        # Filter by query
        ids = [mid for mid in ids if any(
            query.lower() in kw for kw in memory_index.get_entry(mid).keywords
        )]
    else:
        ids = memory_index.search(query)

    results = []
    for mid in ids[:10]:  # Max 10 Ergebnisse
        entry = memory_index.get_entry(mid)
        if entry:
            results.append({
                "id": mid,
                "keywords": entry.keywords,
                "category": entry.category,
                "summary": entry.summary
            })

    return {
        "query": query,
        "count": len(results),
        "results": results,
        "hint": "Use memory_index_get(id) to load full memory"
    }


async def handle_memory_index_get(params: Dict[str, Any]) -> Dict[str, Any]:
    """Lädt vollständiges Memory"""
    memory_id = params["memory_id"]

    # Hole Memory aus TriStar Memory Service
    from .triforce.memory_enhanced import memory_service

    entry = memory_service.get(memory_id)
    if not entry:
        return {"error": f"Memory {memory_id} not found"}

    return {
        "memory_id": memory_id,
        "content": entry.content,
        "memory_type": entry.memory_type,
        "tags": entry.tags,
        "confidence": entry.aggregate_confidence
    }


async def handle_memory_index_compact(params: Dict[str, Any]) -> Dict[str, Any]:
    """Gibt kompakten Index zurück"""
    return {
        "compact_index": memory_index.get_compact_index(),
        "stats": memory_index.get_stats(),
        "usage": "Search by keyword, then load by ID"
    }


async def handle_memory_index_stats(params: Dict[str, Any]) -> Dict[str, Any]:
    """Zeigt Index-Statistiken"""
    return memory_index.get_stats()


MEMORY_INDEX_HANDLERS = {
    "memory_index_add": handle_memory_index_add,
    "memory_index_search": handle_memory_index_search,
    "memory_index_get": handle_memory_index_get,
    "memory_index_compact": handle_memory_index_compact,
    "memory_index_stats": handle_memory_index_stats,
}


# === Hilfsfunktion zum Indizieren bestehender Memories ===

async def index_existing_memories():
    """Indiziert alle bestehenden Memory-Einträge"""
    from .triforce.memory_enhanced import memory_service

    # Hole alle Memories
    all_memories = memory_service.list_all()

    indexed = 0
    for mem in all_memories:
        # Extrahiere Keywords aus Tags und Content
        keywords = list(mem.tags) if mem.tags else []

        # Erste Zeile als Summary
        first_line = mem.content.split('\n')[0][:50] if mem.content else ""

        # Kategorie aus memory_type ableiten
        cat_map = {
            "context": "context",
            "fact": "config",
            "decision": "config",
            "code": "tool",
            "summary": "prompt",
            "todo": "config"
        }
        category = cat_map.get(mem.memory_type, "context")

        memory_index.add(
            memory_id=mem.entry_id,
            keywords=keywords,
            category=category,
            summary=first_line
        )
        indexed += 1

    return {"indexed": indexed}
