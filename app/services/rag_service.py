"""
Local RAG service for AILinux/TriForce.

Phase 1 deliberately uses Python stdlib storage/search so the API is usable even
before optional vector dependencies (llama-index/chromadb/sentence-transformers)
are installed. It indexes project folders into JSONL chunks and performs a
hybrid lexical ranking. The storage layout is stable enough to migrate to Chroma
later without changing the public /v1/rag API contract.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.paths import DATA_DIR, PROJECT_ROOT
from typing import Any, Dict, Iterable, List, Optional, Sequence


DEFAULT_ROOT = Path(os.getenv("AILINUX_RAG_ROOT", str(PROJECT_ROOT))).expanduser().resolve()
DEFAULT_STORE = Path(os.getenv("AILINUX_RAG_STORE", str(DATA_DIR / "rag"))).expanduser().resolve()


def _configured_allowed_roots() -> List[Path]:
    """Return directories that the RAG indexer is allowed to read.

    The index endpoint can be triggered through HTTP, so never let it walk
    arbitrary server paths. Operators can extend the allowlist with
    AILINUX_RAG_ALLOWED_ROOTS=/path/one:/path/two.
    """
    raw = os.getenv("AILINUX_RAG_ALLOWED_ROOTS", str(DEFAULT_ROOT))
    roots: List[Path] = []
    for item in raw.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        try:
            roots.append(Path(item).expanduser().resolve())
        except OSError:
            continue
    return roots or [DEFAULT_ROOT]


def _is_within_allowed_roots(path: Path) -> bool:
    """Check that path is inside one configured allowed root."""
    for allowed in _configured_allowed_roots():
        try:
            path.relative_to(allowed)
            return True
        except ValueError:
            continue
    return False


class RagPathNotAllowed(PermissionError):
    """Raised when a requested RAG index path is outside allowed roots."""


TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".css", ".html",
    ".xml", ".sql", ".env.example", ".service", ".desktop",
}

IGNORED_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules",
    ".venv", "venv", "env", "dist", "build", ".cache", ".idea", ".vscode",
    "site-packages", ".patch_backups", ".repair-backup", "backups",
}

MAX_FILE_BYTES = int(os.getenv("AILINUX_RAG_MAX_FILE_BYTES", "1500000"))
DEFAULT_CHUNK_CHARS = int(os.getenv("AILINUX_RAG_CHUNK_CHARS", "2200"))
DEFAULT_OVERLAP_CHARS = int(os.getenv("AILINUX_RAG_OVERLAP_CHARS", "250"))


@dataclass
class RagChunk:
    id: str
    project: str
    path: str
    rel_path: str
    chunk_index: int
    text: str
    sha256: str
    mtime: float
    size: int
    indexed_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_project_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name.strip())[:80].strip(".-")
    return cleaned or "default"


def _tokenize(text: str) -> List[str]:
    return [t for t in re.findall(r"[\wÄÖÜäöüß][\wÄÖÜäöüß_-]{1,}", text.lower()) if len(t) > 1]


def _read_text(path: Path) -> Optional[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data or len(data) > MAX_FILE_BYTES or b"\x00" in data[:4096]:
        return None
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _is_indexable(path: Path) -> bool:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in {"requirements.txt", "dockerfile", "makefile", "readme", "license"}:
        return True
    if name.startswith(".env") and name != ".env.example":
        return False
    return suffix in TEXT_EXTENSIONS or any(name.endswith(ext) for ext in TEXT_EXTENSIONS)


def _iter_files(root: Path, include_globs: Optional[Sequence[str]], exclude_dirs: Sequence[str]) -> Iterable[Path]:
    excludes = set(IGNORED_DIRS) | {x for x in exclude_dirs if x}
    if include_globs:
        seen: set[Path] = set()
        for pattern in include_globs:
            for path in root.glob(pattern):
                if path.is_file() and path not in seen:
                    seen.add(path)
                    yield path
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excludes and not d.endswith(".bak")]
        current = Path(dirpath)
        for filename in filenames:
            path = current / filename
            if _is_indexable(path):
                yield path


def _chunk_text(text: str, chunk_chars: int, overlap_chars: int) -> List[str]:
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_chars, length)
        if end < length:
            boundary = max(text.rfind("\n\n", start, end), text.rfind("\n", start, end), text.rfind(". ", start, end))
            if boundary > start + int(chunk_chars * 0.55):
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        start = max(0, end - overlap_chars)
    return chunks


class LocalRagService:
    def __init__(self, store_dir: Path = DEFAULT_STORE):
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def _jsonl_path(self, project: str) -> Path:
        return self.store_dir / f"{_safe_project_name(project)}.jsonl"

    def _meta_path(self, project: str) -> Path:
        return self.store_dir / f"{_safe_project_name(project)}.meta.json"

    def list_projects(self) -> List[Dict[str, Any]]:
        projects: List[Dict[str, Any]] = []
        for meta_path in sorted(self.store_dir.glob("*.meta.json")):
            try:
                projects.append(json.loads(meta_path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return projects

    def index_path(
        self,
        project: str,
        path: str,
        include_globs: Optional[Sequence[str]] = None,
        exclude_dirs: Optional[Sequence[str]] = None,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
        overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    ) -> Dict[str, Any]:
        project = _safe_project_name(project)
        root = Path(path).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"Path does not exist: {root}")
        if not _is_within_allowed_roots(root):
            raise RagPathNotAllowed("RAG index path rejected by allowlist")
        if root.is_file():
            files = [root] if _is_indexable(root) else []
            base = root.parent
        else:
            files = list(_iter_files(root, include_globs, exclude_dirs or []))
            base = root

        chunks: List[RagChunk] = []
        skipped = 0
        for file_path in files:
            text = _read_text(file_path)
            if text is None:
                skipped += 1
                continue
            try:
                stat = file_path.stat()
            except OSError:
                skipped += 1
                continue
            sha = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
            rel_path = str(file_path.relative_to(base)) if file_path != base else file_path.name
            for idx, chunk_text in enumerate(_chunk_text(text, chunk_chars, overlap_chars)):
                chunk_id = hashlib.sha256(f"{project}:{file_path}:{idx}:{sha}".encode()).hexdigest()[:24]
                chunks.append(RagChunk(
                    id=chunk_id,
                    project=project,
                    path=str(file_path),
                    rel_path=rel_path,
                    chunk_index=idx,
                    text=chunk_text,
                    sha256=sha,
                    mtime=stat.st_mtime,
                    size=stat.st_size,
                    indexed_at=_utc_now(),
                ))

        jsonl_path = self._jsonl_path(project)
        with jsonl_path.open("w", encoding="utf-8") as fh:
            for chunk in chunks:
                fh.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")

        meta = {
            "project": project,
            "root": str(root),
            "store": str(jsonl_path),
            "indexed_at": _utc_now(),
            "files_seen": len(files),
            "files_skipped": skipped,
            "chunks": len(chunks),
            "backend": "local-jsonl-lexical-v1",
            "chunk_chars": chunk_chars,
            "overlap_chars": overlap_chars,
        }
        self._meta_path(project).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def _load_chunks(self, project: str) -> List[Dict[str, Any]]:
        path = self._jsonl_path(project)
        if not path.exists():
            return []
        chunks: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return chunks

    def query(self, project: str, query: str, top_k: int = 8, path_filter: Optional[str] = None) -> Dict[str, Any]:
        project = _safe_project_name(project)
        chunks = self._load_chunks(project)
        if path_filter:
            chunks = [c for c in chunks if path_filter.lower() in str(c.get("rel_path", "")).lower()]
        q_tokens = _tokenize(query)
        if not q_tokens:
            return {"project": project, "query": query, "hits": [], "total_chunks": len(chunks), "backend": "local-jsonl-lexical-v1"}
        q_set = set(q_tokens)
        scored: List[Dict[str, Any]] = []
        for chunk in chunks:
            text = chunk.get("text", "")
            tokens = _tokenize(text)
            if not tokens:
                continue
            token_counts: Dict[str, int] = {}
            for token in tokens:
                if token in q_set:
                    token_counts[token] = token_counts.get(token, 0) + 1
            if not token_counts:
                continue
            coverage = len(token_counts) / len(q_set)
            frequency = sum(1 + math.log(count) for count in token_counts.values())
            title_bonus = 0.6 if any(t in str(chunk.get("rel_path", "")).lower() for t in q_set) else 0.0
            score = round((coverage * 5.0) + frequency + title_bonus, 5)
            snippet = text[:900].strip()
            scored.append({
                "id": chunk.get("id"),
                "score": score,
                "path": chunk.get("path"),
                "rel_path": chunk.get("rel_path"),
                "chunk_index": chunk.get("chunk_index"),
                "snippet": snippet,
                "matched_terms": sorted(token_counts.keys()),
                "sha256": chunk.get("sha256"),
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return {
            "project": project,
            "query": query,
            "hits": scored[: max(1, min(top_k, 50))],
            "total_chunks": len(chunks),
            "backend": "local-jsonl-lexical-v1",
        }


rag_service = LocalRagService()
