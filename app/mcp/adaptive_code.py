"""
Adaptive Code Illumination MCP Extension v3.0

Simplified: Uses Linux Page Cache instead of manual tmpfs caching.
- Index stored in /var/tristar for worker synchronization  
- File content read directly from filesystem (Linux caches automatically)
- No duplication, no manual cache management
"""

import os
import fnmatch
import re
import time
import json
import logging
import subprocess
import tempfile
import fcntl
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from pathlib import Path

from app.paths import TRISTAR_DIR

logger = logging.getLogger(__name__)

INDEX_DIR = TRISTAR_DIR / "code_index"
INDEX_FILE = INDEX_DIR / "active_files.json"
INDEX_TTL = 7200  # 2 hours

@dataclass
class CacheEntry:
    path: str
    size: int
    timestamp: float
    mtime: float

class AdaptiveCodeIlluminator:
    def __init__(self, root_dir: str | None = None):
        self.root_dir = Path(root_dir or os.environ.get("TRIFORCE_ROOT", Path(__file__).resolve().parents[2]))
        self.default_ttl = INDEX_TTL
        self.ignored_patterns = [
            ".git", "__pycache__", "*.pyc", "node_modules", ".venv",
            ".pytest_cache", "dist", "build", ".idea", ".vscode",
            "*.log", "*.lock", "package-lock.json", ".backups"
        ]
        self._init_index_dir()
    
    def _init_index_dir(self):
        try:
            INDEX_DIR.mkdir(parents=True, exist_ok=True)
            os.chmod(INDEX_DIR, 0o777)
        except Exception as e:
            logger.warning(f"Could not create index dir: {e}")
    
    def _load_index(self) -> Dict[str, dict]:
        try:
            if INDEX_FILE.exists():
                with open(INDEX_FILE, 'r') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    data = json.load(f)
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    return data
        except Exception as e:
            logger.warning(f"Index load error: {e}")
        return {}
    
    def _save_index(self, index: Dict[str, dict]):
        try:
            with open(INDEX_FILE, 'w') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                json.dump(index, f, indent=2)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.error(f"Index save error: {e}")
    
    def _read_file(self, rel_path: str) -> Optional[str]:
        """Read file directly - Linux Page Cache handles caching."""
        try:
            full_path = self.root_dir / rel_path
            if full_path.exists() and full_path.is_file():
                return full_path.read_text(errors='replace')
        except Exception as e:
            logger.warning(f"Read error for {rel_path}: {e}")
        return None
    
    def _is_ignored(self, path: str) -> bool:
        for pattern in self.ignored_patterns:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(os.path.basename(path), pattern):
                return True
        return False

    def code_scout(self, path: str = ".", depth: int = 2) -> Dict[str, Any]:
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
                    children.append(child_info)
            except PermissionError:
                pass
            return children

        result["children"] = _scan(start_path, 1)
        return result

    def code_probe(self, paths: List[str]) -> Dict[str, Any]:
        """Register files in index and trigger Page Cache by reading them."""
        loaded = []
        failed = []
        total_size = 0
        index = self._load_index()

        for p in paths:
            try:
                full_path = (self.root_dir / p).resolve()
                if not full_path.is_relative_to(self.root_dir) or not full_path.exists() or not full_path.is_file():
                    failed.append({"path": p, "reason": "Invalid path or not a file"})
                    continue
                
                if self._is_ignored(full_path.name):
                    failed.append({"path": p, "reason": "Ignored file"})
                    continue

                stat = full_path.stat()
                
                # Read file to trigger Linux Page Cache
                content = full_path.read_text(errors='replace')
                content_size = len(content)
                
                # Store only metadata in index
                index[str(p)] = {
                    'timestamp': time.time(),
                    'size': content_size,
                    'mtime': stat.st_mtime,
                    'path': str(p)
                }
                
                loaded.append({"path": p, "size": content_size})
                total_size += content_size
                
            except Exception as e:
                failed.append({"path": p, "reason": str(e)})
        
        self._save_index(index)

        return {
            "loaded": loaded,
            "failed": failed,
            "total_loaded_size": total_size,
            "cache_count": len(index),
            "note": "Files now in Linux Page Cache"
        }

    def ram_search(self, query: str, regex: bool = False, context_lines: int = 2) -> Dict[str, Any]:
        """Search within indexed files (reads from filesystem/Page Cache)."""
        matches = []
        index = self._load_index()
        now = time.time()
        searched = 0
        
        try:
            pattern = re.compile(query, re.IGNORECASE) if regex else None
        except re.error as e:
            return {"error": f"Invalid regex: {e}"}

        for path, meta in index.items():
            if now - meta.get('timestamp', 0) > self.default_ttl:
                continue
                
            content = self._read_file(path)
            if not content:
                continue
                
            searched += 1
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
                        "context": "\n".join(context)
                    })

        return {
            "query": query,
            "regex": regex,
            "matches": matches,
            "count": len(matches),
            "searched_files": searched
        }

    def ram_context_export(self, file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """Export file contents from indexed files."""
        exported = {}
        index = self._load_index()
        paths_to_export = file_paths if file_paths else list(index.keys())
        
        for p in paths_to_export:
            if p in index:
                content = self._read_file(p)
                if content:
                    exported[p] = content
        
        return {"files": exported, "count": len(exported)}

    def ram_patch_apply(self, patch_content: str, dry_run: bool = True) -> Dict[str, Any]:
        """Apply unified diff patch."""
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.patch', delete=False) as tmp:
            tmp.write(patch_content)
            patch_file = tmp.name

        try:
            cmd = ["patch", "-p1", "-N"]
            if dry_run:
                cmd.append("--dry-run")
            cmd.extend(["-i", patch_file])
            
            try:
                proc = subprocess.run(cmd, cwd=self.root_dir, capture_output=True, text=True)
            except FileNotFoundError:
                return {"dry_run": dry_run, "success": False, "error": "patch command not available"}
            
            return {
                "dry_run": dry_run,
                "success": proc.returncode == 0,
                "stdout": proc.stdout,
                "stderr": proc.stderr
            }
        finally:
            if os.path.exists(patch_file):
                os.unlink(patch_file)


# Singleton
illuminator = AdaptiveCodeIlluminator()

# MCP Handlers
_CODE_TREE_BLOCKED_COMPONENTS = {".env", ".git", ".ssh", "secrets", "credentials", ".venv", "node_modules"}
_CODE_TREE_SAFE_HIDDEN = {".github"}


def _reject_sensitive_tree_path(value: str) -> None:
    for part in Path(str(value)).parts:
        lower = part.lower()
        if lower in _CODE_TREE_BLOCKED_COMPONENTS or (part.startswith(".") and part not in {".", ".."} and lower not in _CODE_TREE_SAFE_HIDDEN):
            raise ValueError(f"blocked path component: {part}")


async def handle_code_scout(params: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively inspect an approved project root instead of only TriForce."""
    from app.mcp.dev_tools import _resolve_dev_path

    root_value = params.get("root")
    path_value = str(params.get("path") or ".")
    _reject_sensitive_tree_path(path_value)
    if root_value:
        _reject_sensitive_tree_path(str(root_value))
    if root_value:
        base = _resolve_dev_path(".", root=str(root_value))
        target = _resolve_dev_path(path_value, root=str(base))
        if target != base and base not in target.parents:
            raise ValueError("Path is outside requested project root")
        relative = str(target.relative_to(base)) if target != base else "."
    else:
        raw = Path(path_value).expanduser()
        if raw.is_absolute():
            target = _resolve_dev_path(str(raw))
            if not target.is_dir():
                raise ValueError(f"Not a directory: {path_value}")
            base = target
            relative = "."
        else:
            base = _resolve_dev_path(".", root=str(illuminator.root_dir))
            target = _resolve_dev_path(path_value, root=str(base))
            if target != base and base not in target.parents:
                raise ValueError("Path is outside requested project root")
            relative = str(target.relative_to(base)) if target != base else "."

    worker = AdaptiveCodeIlluminator(str(base))
    extra_ignore = [str(item) for item in (params.get("ignore") or []) if str(item)]
    if extra_ignore:
        worker.ignored_patterns = [*worker.ignored_patterns, *extra_ignore]
    result = worker.code_scout(relative, max(1, min(8, int(params.get("depth") or 3))))
    result["root"] = str(base)
    return result

async def handle_code_probe(params: Dict[str, Any]) -> Dict[str, Any]:
    paths = params.get("paths", [])
    if isinstance(paths, str):
        paths = [paths]
    return illuminator.code_probe(paths)

async def handle_ram_search(params: Dict[str, Any]) -> Dict[str, Any]:
    query = params.get("query")
    if not query:
        raise ValueError("Query required")
    return illuminator.ram_search(query, params.get("regex", False))

async def handle_ram_context_export(params: Dict[str, Any]) -> Dict[str, Any]:
    return illuminator.ram_context_export(params.get("file_paths"))

async def handle_ram_patch_apply(params: Dict[str, Any]) -> Dict[str, Any]:
    diff = params.get("diff")
    if not diff:
        raise ValueError("Diff content required")
    return illuminator.ram_patch_apply(diff, params.get("dry_run", True))


ADAPTIVE_CODE_TOOLS = [
    {
        "name": "code_scout",
        "description": "Scout directory structure with depth limit and ignore patterns. Adaptive Code Illumination tool.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to scout (default: .)"},
                "depth": {"type": "integer", "description": "Depth to traverse (default: 2)"}
            }
        }
    },
    {
        "name": "code_probe",
        "description": "Selectively load files into RAM cache for fast analysis. Adaptive Code Illumination tool.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "description": "List of file paths to load"}
            },
            "required": ["paths"]
        }
    },
    {
        "name": "ram_search",
        "description": "Search text/regex within loaded RAM cache (no disk I/O). Adaptive Code Illumination tool.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "regex": {"type": "boolean", "description": "Use regex (default: false)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "ram_context_export",
        "description": "Export content from RAM cache for agent context. Adaptive Code Illumination tool.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_paths": {"type": "array", "items": {"type": "string"}, "description": "Specific files to export (optional, default: all cached)"}
            }
        }
    },
    {
        "name": "ram_patch_apply",
        "description": "Apply unified diff patch (default dry-run). Adaptive Code Illumination tool.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "diff": {"type": "string", "description": "Unified diff content"},
                "dry_run": {"type": "boolean", "description": "Preview only (default: true)"}
            },
            "required": ["diff"]
        }
    }
]

ADAPTIVE_CODE_HANDLERS = {
    "code_scout": handle_code_scout,
    "code_probe": handle_code_probe,
    "ram_search": handle_ram_search,
    "ram_context_export": handle_ram_context_export,
    "ram_patch_apply": handle_ram_patch_apply
}
