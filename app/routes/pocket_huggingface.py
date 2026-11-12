"""Public, read-only Pocket AI model catalogue backed by Hugging Face Hub.

This endpoint NEVER proxies arbitrary URLs, models' bytes, or private tokens.
Public model metadata only. Device app downloads the chosen GGUF directly from
the original Hub after explicit user confirmation. Memory cache is short-lived.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/pocket/huggingface", tags=["Pocket AI"])
_HF = "https://huggingface.co/api/models"
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,96}/[A-Za-z0-9_.-]{1,128}$")
_CACHE_TTL = 600
_CACHE: dict[tuple[str, str, int], tuple[float, dict[str, Any]]] = {}
_CURATED = (
    "bartowski/SmolLM2-360M-Instruct-GGUF",
    "bartowski/Qwen2.5-0.5B-Instruct-GGUF",
    "unsloth/Qwen3.5-0.8B-GGUF",
    "unsloth/gemma-3-1b-it-GGUF",
)
_MAX_SUGGESTED_BYTES = 1_500_000_000


def _cached(key: tuple[str, str, int]) -> dict[str, Any] | None:
    value = _CACHE.get(key)
    if value and time.monotonic() - value[0] < _CACHE_TTL:
        return value[1]
    _CACHE.pop(key, None)
    return None


def _save(key: tuple[str, str, int], data: dict[str, Any]) -> dict[str, Any]:
    if len(_CACHE) > 180:
        _CACHE.clear()
    _CACHE[key] = time.monotonic(), data
    return data


def _repo_id(value: Any) -> str | None:
    repo = str(value or "")
    return repo if _REPO.fullmatch(repo) and ".." not in repo else None


def _entry(data: dict[str, Any], model_format: str = "gguf") -> dict[str, Any] | None:
    repo = _repo_id(data.get("id"))
    tags = data.get("tags") or []
    fmt = model_format.lower()
    labels = {t.lower() for t in tags if isinstance(t, str)}
    has_format = ("gguf" in labels if fmt == "gguf"
                  else bool(labels.intersection({"litertlm", "litert-lm"})))
    pipeline = data.get("pipeline_tag")
    allowed_pipelines = (None, "text-generation", "image-text-to-text") if fmt == "litertlm"         else (None, "text-generation")
    if (
        not repo or data.get("private") or data.get("gated") not in (None, False)
        or pipeline not in allowed_pipelines or not has_format
    ):
        return None
    return {
        "id": repo,
        "title": repo.split("/", 1)[1][:130],
        "downloads": max(0, int(data.get("downloads") or 0)),
        "likes": max(0, int(data.get("likes") or 0)),
        "url": f"https://huggingface.co/{repo}",
        "source": "Hugging Face Hub",
        "format": "LiteRT-LM" if fmt == "litertlm" else "GGUF",
    }


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None) -> Any:
    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
        result = response.json()
        if len(response.content) > 1_500_000:
            raise ValueError("Hub metadata too large")
        return result
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Hugging Face catalogue temporarily unavailable") from exc


@router.get("/search")
async def hf_search(
    q: str = Query(default="SmolLM", min_length=1, max_length=96),
    limit: int = Query(default=16, ge=1, le=25),
    model_format: str = Query(default="gguf", alias="format", pattern="^(gguf|litertlm)$"),
):
    """Ranked, public, GGUF-only model results; no inference or model files."""
    query = q.strip()
    if not query or any(ord(c) < 32 for c in query):
        raise HTTPException(status_code=422, detail="Invalid search query")
    key = ("search-" + model_format, query.casefold(), limit)
    cached = _cached(key)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0), follow_redirects=False,
        headers={"User-Agent": "AILinux-Pocket-AI/0.2.0", "Accept": "application/json"},
    ) as client:
        data = await _get_json(client, _HF, {
            "search": query, "filter": "litert-lm" if model_format == "litertlm" else "gguf",
            "sort": "downloads",
            "direction": "-1", "limit": min(60, max(25, limit * 3)),
        })
    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail="Invalid Hugging Face response")
    result = []
    seen = set()
    for item in data:
        entry = _entry(item, model_format) if isinstance(item, dict) else None
        if entry and entry["id"].lower() not in seen:
            result.append(entry)
            seen.add(entry["id"].lower())
        if len(result) >= limit:
            break
    return _save(key, {"origin": "huggingface.co/api/models", "query": query,
                       "format": model_format, "results": result,
                       "count": len(result), "cached_seconds": _CACHE_TTL})


def _suggested_file(data: dict[str, Any]) -> dict[str, Any] | None:
    files = data.get("siblings") or []
    best = []
    for item in files[:150]:
        name = item.get("rfilename") if isinstance(item, dict) else None
        if (not isinstance(name, str) or not name.lower().endswith(".gguf")
                or "/" in name or "\\" in name or ".." in name):
            continue
        lfs = item.get("lfs") or {}
        size = lfs.get("size") or item.get("size")
        if not isinstance(size, int) or not (100_000_000 <= size <= _MAX_SUGGESTED_BYTES):
            continue
        if "q4_k_m" in name.lower():
            best.append((size, name))
    if not best:
        return None
    size, name = min(best)
    return {"filename": name[:190], "bytes": size}


@router.get("/recommended")
async def hf_recommended():
    """Live-verified small Q4_K_M model suggestions, never stale hardcoded claims."""
    key = ("recommended", "", 8)
    cached = _cached(key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0), follow_redirects=False,
        headers={"User-Agent": "AILinux-Pocket-AI/0.2.0", "Accept": "application/json"},
    ) as client:
        async def verify(repo: str):
            try:
                data = await _get_json(client, f"{_HF}/{repo}", {"blobs": "true"})
                if not isinstance(data, dict):
                    return None
                entry = _entry(data)
                file = _suggested_file(data)
                if entry is None or file is None:
                    return None
                return dict(entry, recommended=file)
            except HTTPException:
                return None

        entries = await asyncio.gather(*(verify(repo) for repo in _CURATED))
    result = sorted((entry for entry in entries if entry is not None),
                    key=lambda entry: entry["recommended"]["bytes"])
    if not result:
        raise HTTPException(status_code=502, detail="No live-verified small GGUF models")
    return _save(key, {"origin": "huggingface.co/api/models", "results": result,
                       "count": len(result), "cached_seconds": _CACHE_TTL})
