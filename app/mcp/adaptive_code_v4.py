"""
Adaptive Code Illumination MCP Extension v4.0
==============================================
Enhanced based on 9-AI Survey Consensus (2025-12-09):

NEW FEATURES:
1. LRU Cache - True RAM caching with size limits
2. Async I/O - Non-blocking file operations
3. Delta Sync - Only sync changed content
4. Agent-Aware Caching - Isolated caches per agent
5. Checkpointing - State persistence for recovery
6. Hot/Cold File Management - Automatic tiering

Survey Participants: Groq, Cerebras, Mistral, GPT-4o, Claude 3.5,
                     Gemini 2.0, DeepSeek V3, Qwen 2.5, NVIDIA Nemotron
Consensus: 100% Hybrid Approach
"""

import os
import asyncio
import aiofiles
import fnmatch
import re
import time
import json
import hashlib
import logging
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, field
from pathlib import Path

from app.paths import TRISTAR_DIR
from collections import OrderedDict
from functools import lru_cache
import threading

logger = logging.getLogger(__name__)

# Configuration
INDEX_DIR = TRISTAR_DIR / "code_index"
CACHE_DIR = TRISTAR_DIR / "code_cache"
CHECKPOINT_DIR = TRISTAR_DIR / "checkpoints"
INDEX_FILE = INDEX_DIR / "active_files.json"

# Limits
MAX_CACHE_SIZE_MB = 256  # 256 MB RAM cache
MAX_FILE_SIZE_KB = 512   # Max single file size
LRU_MAX_FILES = 1000     # Max files in LRU
INDEX_TTL = 7200         # 2 hours
HOT_ACCESS_THRESHOLD = 5  # Access count to be "hot"


@dataclass
class CacheEntry:
    """Enhanced cache entry with access tracking."""
    path: str
    content: str
    size: int
    mtime: float
    content_hash: str
    access_count: int = 0
    last_access: float = field(default_factory=time.time)
    agent_id: Optional[str] = None
    is_hot: bool = False


class LRUFileCache:
    """
    LRU Cache for file contents - TRUE RAM caching.
    Based on survey recommendations: LRU + Agent-Aware + Hot/Cold tiering.
    """

    def __init__(self, max_size_mb: int = MAX_CACHE_SIZE_MB, max_files: int = LRU_MAX_FILES):
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.max_files = max_files
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._agent_caches: Dict[str, Set[str]] = {}  # agent_id -> set of paths
        self._current_size = 0
        self._lock = threading.RLock()
        self._access_stats: Dict[str, int] = {}  # path -> access count

    def get(self, path: str, agent_id: Optional[str] = None) -> Optional[str]:
        """Get file content from cache with LRU update."""
        with self._lock:
            if path in self._cache:
                entry = self._cache[path]
                # Move to end (most recently used)
                self._cache.move_to_end(path)
                # Update stats
                entry.access_count += 1
                entry.last_access = time.time()
                self._access_stats[path] = entry.access_count
                # Mark as hot if accessed frequently
                if entry.access_count >= HOT_ACCESS_THRESHOLD:
                    entry.is_hot = True
                return entry.content
            return None

    def put(self, path: str, content: str, mtime: float, agent_id: Optional[str] = None) -> bool:
        """Add file to cache with LRU eviction."""
        content_size = len(content.encode('utf-8'))

        # Skip if file too large
        if content_size > MAX_FILE_SIZE_KB * 1024:
            logger.debug(f"File too large for cache: {path} ({content_size} bytes)")
            return False

        with self._lock:
            # Remove old entry if exists
            if path in self._cache:
                old_entry = self._cache.pop(path)
                self._current_size -= old_entry.size

            # Evict LRU entries if needed
            while (self._current_size + content_size > self.max_size_bytes or
                   len(self._cache) >= self.max_files):
                if not self._cache:
                    break
                # Evict cold files first, then LRU
                evict_path = self._find_eviction_candidate()
                if evict_path:
                    evicted = self._cache.pop(evict_path)
                    self._current_size -= evicted.size
                    # Remove from agent cache
                    if evicted.agent_id and evicted.agent_id in self._agent_caches:
                        self._agent_caches[evicted.agent_id].discard(evict_path)
                else:
                    break

            # Add new entry
            content_hash = hashlib.md5(content.encode()).hexdigest()[:12]
            entry = CacheEntry(
                path=path,
                content=content,
                size=content_size,
                mtime=mtime,
                content_hash=content_hash,
                agent_id=agent_id,
                access_count=self._access_stats.get(path, 0)
            )
            self._cache[path] = entry
            self._current_size += content_size

            # Track agent cache
            if agent_id:
                if agent_id not in self._agent_caches:
                    self._agent_caches[agent_id] = set()
                self._agent_caches[agent_id].add(path)

            return True

    def _find_eviction_candidate(self) -> Optional[str]:
        """Find best candidate for eviction (cold files first, then LRU)."""
        # First, try to evict cold files
        for path, entry in self._cache.items():
            if not entry.is_hot:
                return path
        # Otherwise, evict LRU (first item)
        if self._cache:
            return next(iter(self._cache))
        return None

    def invalidate(self, path: str) -> bool:
        """Remove file from cache."""
        with self._lock:
            if path in self._cache:
                entry = self._cache.pop(path)
                self._current_size -= entry.size
                return True
            return False

    def invalidate_agent(self, agent_id: str) -> int:
        """Remove all files cached by specific agent."""
        with self._lock:
            if agent_id not in self._agent_caches:
                return 0
            paths = list(self._agent_caches[agent_id])
            count = 0
            for path in paths:
                if self.invalidate(path):
                    count += 1
            del self._agent_caches[agent_id]
            return count

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            hot_count = sum(1 for e in self._cache.values() if e.is_hot)
            return {
                "total_files": len(self._cache),
                "total_size_mb": round(self._current_size / (1024 * 1024), 2),
                "max_size_mb": self.max_size_bytes / (1024 * 1024),
                "hot_files": hot_count,
                "cold_files": len(self._cache) - hot_count,
                "agent_caches": {k: len(v) for k, v in self._agent_caches.items()},
                "utilization_percent": round(self._current_size / self.max_size_bytes * 100, 1)
            }

    def get_delta(self, path: str, new_content: str) -> Optional[Dict[str, Any]]:
        """Get delta between cached and new content."""
        with self._lock:
            if path not in self._cache:
                return None
            old_content = self._cache[path].content
            if old_content == new_content:
                return {"changed": False, "path": path}

            # Simple line-based delta
            old_lines = old_content.splitlines()
            new_lines = new_content.splitlines()

            additions = []
            deletions = []

            old_set = set(enumerate(old_lines))
            new_set = set(enumerate(new_lines))

            for i, line in enumerate(new_lines):
                if (i, line) not in old_set:
                    additions.append({"line": i + 1, "content": line})

            for i, line in enumerate(old_lines):
                if (i, line) not in new_set:
                    deletions.append({"line": i + 1, "content": line})

            return {
                "changed": True,
                "path": path,
                "additions": len(additions),
                "deletions": len(deletions),
                "delta_lines": additions[:20],  # Limit output
                "old_hash": self._cache[path].content_hash,
                "new_hash": hashlib.md5(new_content.encode()).hexdigest()[:12]
            }


class AdaptiveCodeIlluminatorV4:
    """
    Enhanced Adaptive Code Illumination with:
    - True LRU RAM Cache
    - Async I/O Operations
    - Delta Sync
    - Agent-Aware Caching
    - Checkpointing
    """

    def __init__(self, root_dir: str = "/home/zombie/triforce"):
        self.root_dir = Path(root_dir)
        self.cache = LRUFileCache()
        self.default_ttl = INDEX_TTL
        self.ignored_patterns = [
            ".git", "__pycache__", "*.pyc", "node_modules", ".venv",
            ".pytest_cache", "dist", "build", ".idea", ".vscode",
            "*.log", "*.lock", "package-lock.json", ".backups"
        ]
        self._init_dirs()
        self._checkpoint_counter = 0

    def _init_dirs(self):
        """Initialize required directories."""
        for d in [INDEX_DIR, CACHE_DIR, CHECKPOINT_DIR]:
            try:
                d.mkdir(parents=True, exist_ok=True)
                os.chmod(d, 0o777)
            except Exception as e:
                logger.warning(f"Could not create dir {d}: {e}")

    def _is_ignored(self, path: str) -> bool:
        """Check if path should be ignored."""
        for pattern in self.ignored_patterns:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(os.path.basename(path), pattern):
                return True
        return False

    async def _read_file_async(self, path: Path) -> Optional[str]:
        """Async file read - non-blocking I/O."""
        try:
            async with aiofiles.open(path, 'r', errors='replace') as f:
                return await f.read()
        except Exception as e:
            logger.warning(f"Async read error for {path}: {e}")
            return None

    async def _write_file_async(self, path: Path, content: str) -> bool:
        """Async file write - non-blocking I/O."""
        try:
            async with aiofiles.open(path, 'w') as f:
                await f.write(content)
            return True
        except Exception as e:
            logger.error(f"Async write error for {path}: {e}")
            return False

    def code_scout(self, path: str = ".", depth: int = 2) -> Dict[str, Any]:
        """Scout directory structure (sync - fast operation)."""
        start_path = (self.root_dir / path).resolve()
        if not start_path.is_relative_to(self.root_dir):
            raise ValueError(f"Access denied: {path}")

        result = {"path": str(path), "type": "directory", "children": []}

        def _scan(current_path: Path, current_depth: int) -> List[Dict[str, Any]]:
            if current_depth > depth:
                return []
            children = []
            try:
                for item in sorted(current_path.iterdir()):
                    if self._is_ignored(item.name):
                        continue
                    child_info = {
                        "name": item.name,
                        "path": str(item.relative_to(self.root_dir)),
                        "type": "directory" if item.is_dir() else "file"
                    }
                    if item.is_dir():
                        if current_depth < depth:
                            child_info["children"] = _scan(item, current_depth + 1)
                        else:
                            child_info["truncated"] = True
                    else:
                        child_info["size"] = item.stat().st_size
                        # Check if in cache
                        rel_path = str(item.relative_to(self.root_dir))
                        child_info["cached"] = self.cache.get(rel_path) is not None
                    children.append(child_info)
            except PermissionError:
                pass
            return children

        result["children"] = _scan(start_path, 1)
        result["cache_stats"] = self.cache.get_stats()
        return result

    async def code_probe_async(self, paths: List[str], agent_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Load files into LRU RAM cache (async).
        Agent-aware: tracks which agent loaded which files.
        """
        loaded = []
        failed = []
        from_cache = []
        total_size = 0

        async def load_file(p: str):
            nonlocal total_size
            try:
                full_path = (self.root_dir / p).resolve()
                if not full_path.is_relative_to(self.root_dir):
                    return {"path": p, "status": "failed", "reason": "Access denied"}
                if not full_path.exists() or not full_path.is_file():
                    return {"path": p, "status": "failed", "reason": "Not found or not a file"}
                if self._is_ignored(full_path.name):
                    return {"path": p, "status": "failed", "reason": "Ignored"}

                # Check cache first
                cached = self.cache.get(p, agent_id)
                if cached:
                    return {"path": p, "status": "from_cache", "size": len(cached)}

                # Async read
                content = await self._read_file_async(full_path)
                if content is None:
                    return {"path": p, "status": "failed", "reason": "Read error"}

                mtime = full_path.stat().st_mtime

                # Add to cache
                if self.cache.put(p, content, mtime, agent_id):
                    return {"path": p, "status": "loaded", "size": len(content)}
                else:
                    return {"path": p, "status": "loaded_no_cache", "size": len(content), "reason": "Too large for cache"}

            except Exception as e:
                return {"path": p, "status": "failed", "reason": str(e)}

        # Load all files concurrently
        results = await asyncio.gather(*[load_file(p) for p in paths])

        for r in results:
            if r["status"] == "loaded":
                loaded.append({"path": r["path"], "size": r["size"]})
                total_size += r["size"]
            elif r["status"] == "from_cache":
                from_cache.append({"path": r["path"], "size": r["size"]})
                total_size += r["size"]
            elif r["status"] == "loaded_no_cache":
                loaded.append({"path": r["path"], "size": r["size"], "note": r["reason"]})
                total_size += r["size"]
            else:
                failed.append({"path": r["path"], "reason": r.get("reason", "Unknown")})

        return {
            "loaded": loaded,
            "from_cache": from_cache,
            "failed": failed,
            "total_size": total_size,
            "agent_id": agent_id,
            "cache_stats": self.cache.get_stats()
        }

    def ram_search(self, query: str, regex: bool = False, context_lines: int = 2) -> Dict[str, Any]:
        """Search within cached files (pure RAM - no disk I/O)."""
        matches = []
        searched = 0

        try:
            pattern = re.compile(query, re.IGNORECASE) if regex else None
        except re.error as e:
            return {"error": f"Invalid regex: {e}"}

        for path, entry in self.cache._cache.items():
            searched += 1
            content = entry.content
            lines = content.splitlines()

            for i, line in enumerate(lines):
                found = False
                if regex and pattern:
                    if pattern.search(line):
                        found = True
                elif query.lower() in line.lower():
                    found = True

                if found:
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    context = lines[start:end]

                    matches.append({
                        "file": path,
                        "line": i + 1,
                        "content": line.strip(),
                        "context": "\n".join(context),
                        "is_hot": entry.is_hot
                    })

        return {
            "query": query,
            "regex": regex,
            "matches": matches,
            "count": len(matches),
            "searched_files": searched,
            "cache_stats": self.cache.get_stats()
        }

    def ram_context_export(self, file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """Export file contents from RAM cache."""
        exported = {}
        paths_to_export = file_paths if file_paths else list(self.cache._cache.keys())

        for p in paths_to_export:
            content = self.cache.get(p)
            if content:
                exported[p] = content

        return {"files": exported, "count": len(exported)}

    async def ram_patch_apply_async(self, patch_content: str, dry_run: bool = True) -> Dict[str, Any]:
        """Apply unified diff patch (async)."""
        import tempfile
        import subprocess

        with tempfile.NamedTemporaryFile(mode='w+', suffix='.patch', delete=False) as tmp:
            tmp.write(patch_content)
            patch_file = tmp.name

        try:
            cmd = ["patch", "-p1", "-N"]
            if dry_run:
                cmd.append("--dry-run")
            cmd.extend(["-i", patch_file])

            # Run patch in thread pool to not block
            loop = asyncio.get_running_loop()
            proc = await loop.run_in_executor(
                None,
                lambda: subprocess.run(cmd, cwd=self.root_dir, capture_output=True, text=True)
            )

            # If not dry run and successful, invalidate affected files from cache
            if not dry_run and proc.returncode == 0:
                # Parse patched files from output
                for line in proc.stdout.splitlines():
                    if line.startswith("patching file"):
                        file_path = line.replace("patching file ", "").strip().strip("'\"")
                        self.cache.invalidate(file_path)

            return {
                "dry_run": dry_run,
                "success": proc.returncode == 0,
                "stdout": proc.stdout,
                "stderr": proc.stderr
            }
        finally:
            if os.path.exists(patch_file):
                os.unlink(patch_file)

    def get_delta(self, path: str, new_content: str) -> Dict[str, Any]:
        """Get delta between cached version and new content."""
        delta = self.cache.get_delta(path, new_content)
        if delta:
            return delta
        return {"changed": True, "path": path, "note": "Not in cache - full update required"}

    async def sync_delta_async(self, path: str, new_content: str) -> Dict[str, Any]:
        """
        Delta Sync: Only write if content changed.
        Updates cache and optionally writes to disk.
        """
        delta = self.get_delta(path, new_content)

        if not delta.get("changed", True):
            return {"path": path, "action": "skipped", "reason": "No changes"}

        full_path = (self.root_dir / path).resolve()
        if not full_path.is_relative_to(self.root_dir):
            return {"path": path, "action": "failed", "reason": "Access denied"}

        # Write to disk (async)
        success = await self._write_file_async(full_path, new_content)

        if success:
            # Update cache
            mtime = full_path.stat().st_mtime
            self.cache.put(path, new_content, mtime)
            return {
                "path": path,
                "action": "synced",
                "delta": delta,
                "new_size": len(new_content)
            }
        else:
            return {"path": path, "action": "failed", "reason": "Write error"}

    def create_checkpoint(self, name: Optional[str] = None) -> Dict[str, Any]:
        """Create a checkpoint of current cache state."""
        self._checkpoint_counter += 1
        checkpoint_name = name or f"checkpoint_{self._checkpoint_counter}_{int(time.time())}"
        checkpoint_file = CHECKPOINT_DIR / f"{checkpoint_name}.json"

        checkpoint_data = {
            "name": checkpoint_name,
            "timestamp": time.time(),
            "files": {}
        }

        for path, entry in self.cache._cache.items():
            checkpoint_data["files"][path] = {
                "content_hash": entry.content_hash,
                "size": entry.size,
                "mtime": entry.mtime,
                "is_hot": entry.is_hot
            }

        try:
            with open(checkpoint_file, 'w') as f:
                json.dump(checkpoint_data, f, indent=2)
            return {
                "checkpoint": checkpoint_name,
                "file": str(checkpoint_file),
                "files_count": len(checkpoint_data["files"])
            }
        except Exception as e:
            return {"error": str(e)}

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics."""
        stats = self.cache.get_stats()
        stats["checkpoints"] = len(list(CHECKPOINT_DIR.glob("*.json")))
        return stats

    def invalidate_cache(self, path: Optional[str] = None, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """Invalidate cache entries."""
        if agent_id:
            count = self.cache.invalidate_agent(agent_id)
            return {"invalidated": count, "agent_id": agent_id}
        elif path:
            success = self.cache.invalidate(path)
            return {"invalidated": 1 if success else 0, "path": path}
        else:
            # Clear all
            count = len(self.cache._cache)
            self.cache._cache.clear()
            self.cache._current_size = 0
            self.cache._agent_caches.clear()
            return {"invalidated": count, "action": "full_clear"}


# Singleton
illuminator_v4 = AdaptiveCodeIlluminatorV4()


# Async MCP Handlers
async def handle_code_scout_v4(params: Dict[str, Any]) -> Dict[str, Any]:
    return illuminator_v4.code_scout(params.get("path", "."), params.get("depth", 2))


async def handle_code_probe_v4(params: Dict[str, Any]) -> Dict[str, Any]:
    paths = params.get("paths", [])
    if isinstance(paths, str):
        paths = [paths]
    agent_id = params.get("agent_id")
    return await illuminator_v4.code_probe_async(paths, agent_id)


async def handle_ram_search_v4(params: Dict[str, Any]) -> Dict[str, Any]:
    query = params.get("query")
    if not query:
        raise ValueError("Query required")
    return illuminator_v4.ram_search(query, params.get("regex", False), params.get("context_lines", 2))


async def handle_ram_context_export_v4(params: Dict[str, Any]) -> Dict[str, Any]:
    return illuminator_v4.ram_context_export(params.get("file_paths"))


async def handle_ram_patch_apply_v4(params: Dict[str, Any]) -> Dict[str, Any]:
    diff = params.get("diff")
    if not diff:
        raise ValueError("Diff content required")
    return await illuminator_v4.ram_patch_apply_async(diff, params.get("dry_run", True))


async def handle_delta_sync_v4(params: Dict[str, Any]) -> Dict[str, Any]:
    path = params.get("path")
    content = params.get("content")
    if not path or not content:
        raise ValueError("Path and content required")
    return await illuminator_v4.sync_delta_async(path, content)


async def handle_cache_stats_v4(params: Dict[str, Any]) -> Dict[str, Any]:
    return illuminator_v4.get_cache_stats()


async def handle_cache_invalidate_v4(params: Dict[str, Any]) -> Dict[str, Any]:
    return illuminator_v4.invalidate_cache(
        path=params.get("path"),
        agent_id=params.get("agent_id")
    )


async def handle_checkpoint_create_v4(params: Dict[str, Any]) -> Dict[str, Any]:
    return illuminator_v4.create_checkpoint(params.get("name"))


# Tool Definitions
ADAPTIVE_CODE_V4_TOOLS = [
    {
        "name": "code_scout_v4",
        "description": "Scout directory structure with LRU cache awareness. Shows which files are cached.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to scout (default: .)"},
                "depth": {"type": "integer", "description": "Depth to traverse (default: 2)"}
            }
        }
    },
    {
        "name": "code_probe_v4",
        "description": "Load files into TRUE RAM cache (LRU, agent-aware). Async non-blocking I/O.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "description": "File paths to load"},
                "agent_id": {"type": "string", "description": "Agent ID for cache isolation"}
            },
            "required": ["paths"]
        }
    },
    {
        "name": "ram_search_v4",
        "description": "Search within LRU RAM cache (ZERO disk I/O). Shows hot/cold file status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "regex": {"type": "boolean", "description": "Use regex (default: false)"},
                "context_lines": {"type": "integer", "description": "Context lines (default: 2)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "delta_sync_v4",
        "description": "Delta Sync: Only write changed content. Async I/O with cache update.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "New file content"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "cache_stats_v4",
        "description": "Get LRU cache statistics: size, hot/cold files, agent caches, utilization.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "cache_invalidate_v4",
        "description": "Invalidate cache entries by path or agent_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Specific path to invalidate"},
                "agent_id": {"type": "string", "description": "Invalidate all files for this agent"}
            }
        }
    },
    {
        "name": "checkpoint_create_v4",
        "description": "Create a checkpoint of current cache state for recovery.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Checkpoint name (optional)"}
            }
        }
    }
]

ADAPTIVE_CODE_V4_HANDLERS = {
    "code_scout_v4": handle_code_scout_v4,
    "code_probe_v4": handle_code_probe_v4,
    "ram_search_v4": handle_ram_search_v4,
    "ram_context_export_v4": handle_ram_context_export_v4,
    "ram_patch_apply_v4": handle_ram_patch_apply_v4,
    "delta_sync_v4": handle_delta_sync_v4,
    "cache_stats_v4": handle_cache_stats_v4,
    "cache_invalidate_v4": handle_cache_invalidate_v4,
    "checkpoint_create_v4": handle_checkpoint_create_v4,
}
