"""Authenticated project observations mirrored into episodic Claude-Mem history."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.routes.client_auth import get_current_client
from app.services.episodic_memory import redact
from app.services.memory_trigger import get_memory_engine

logger = logging.getLogger("ailinux.project_observations")
router = APIRouter()

ObservationKind = Literal[
    "decision", "bugfix", "release", "todo", "verification", "fact", "discovery"
]
_KEY_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")


def _validate_project_key(value: str) -> str:
    value = value.strip()
    if not _KEY_RE.fullmatch(value):
        raise ValueError("project_key contains unsupported characters")
    if value.startswith(("/", "\\")) or "\\" in value:
        raise ValueError("project_key must be a logical project identifier, not a filesystem path")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("project_key contains invalid path-like segments")
    return value


class ProjectObservationSearchRequest(BaseModel):
    project_key: str = Field(min_length=1, max_length=160)
    query: str = Field(default="", max_length=512)
    limit: int = Field(default=8, ge=1, le=20)

    @field_validator("project_key")
    @classmethod
    def valid_project_key(cls, value: str) -> str:
        return _validate_project_key(value)


class ProjectObservationRequest(BaseModel):
    project_key: str = Field(min_length=1, max_length=160)
    kind: ObservationKind
    title: str = Field(min_length=1, max_length=180)
    content: str = Field(default="", max_length=6000)
    status: str = Field(default="active", max_length=64)
    evidence: str = Field(default="", max_length=1000)
    repo: str = Field(default="", max_length=500)
    commit: str = Field(default="", max_length=128)
    source: str = Field(default="project-observation-api", max_length=96)
    files: list[str] = Field(default_factory=list, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=24)
    verified: bool = False

    @field_validator("project_key")
    @classmethod
    def valid_project_key(cls, value: str) -> str:
        return _validate_project_key(value)


def _account_scope(client: dict[str, Any]) -> str:
    email = str(client.get("email") or "").strip().lower()
    client_id = str(client.get("client_id") or "").strip()
    stable = f"account:{email}" if email else f"client:{client_id}"
    if stable in {"account:", "client:"}:
        raise HTTPException(401, "Authenticated account identity required")
    return hashlib.sha256(stable.encode("utf-8", errors="replace")).hexdigest()[:20]


def _project_scope(engine, client: dict[str, Any], project_key: str) -> str:
    account_scope = _account_scope(client)
    root = engine.settings.memory_project_id or "triforce"
    return f"{root}:account:{account_scope}:project:{project_key}"


def _verification_state(request: ProjectObservationRequest) -> str:
    if request.verified:
        return "verified"
    if request.kind == "todo":
        return "open"
    if request.kind in {"decision", "fact", "discovery"}:
        return "observed"
    return "completed"


@router.post("/project-observations")
async def create_project_observation(
    request: ProjectObservationRequest,
    client: dict = Depends(get_current_client),
):
    engine = get_memory_engine()
    if not engine.settings.episodic_memory_enabled or not engine.settings.memory_record_enabled:
        return {"status": "disabled"}

    project = _project_scope(engine, client, request.project_key)
    state = _verification_state(request)

    payload = redact({
        "kind": request.kind,
        "title": request.title,
        "content": request.content,
        "status": request.status,
        "evidence": request.evidence,
        "repo": request.repo,
        "commit": request.commit,
        "source": request.source,
        "files": request.files,
        "tags": request.tags,
        "verification_state": state,
        "project_key": request.project_key,
    })
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:6000]
    metadata = {k: v for k, v in payload.items() if k != "content"}
    metadata["summary"] = str(payload.get("content") or "")[:1600]
    metadata["event_type"] = "project_observation"

    try:
        observation_id = await engine.provider.record(
            text=text,
            title=f"{request.kind}: {request.title}"[:180],
            project=project,
            metadata=metadata,
        )
    except Exception as exc:
        logger.info("project_observation_degraded", extra={"failure_type": type(exc).__name__})
        return {"status": "degraded"}

    return {
        "status": "recorded",
        "observation_id": observation_id,
        "project_key": request.project_key,
        "kind": request.kind,
        "verification_state": state,
    }


@router.post("/project-observations/search")
async def search_project_observations(
    request: ProjectObservationSearchRequest,
    client: dict = Depends(get_current_client),
):
    engine = get_memory_engine()
    if not engine.settings.episodic_memory_enabled:
        return {"status": "disabled", "results": []}
    project = _project_scope(engine, client, request.project_key)
    try:
        rows = await engine.provider.search(request.query, project, request.limit)
    except Exception as exc:
        logger.info("project_observation_search_degraded", extra={"failure_type": type(exc).__name__})
        return {"status": "degraded", "results": []}

    results = []
    for row in rows[:request.limit]:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        results.append({
            "id": row.get("id"),
            "title": row.get("title", ""),
            "kind": meta.get("kind", ""),
            "summary": str(meta.get("summary") or "")[:1600],
            "status": meta.get("status", ""),
            "verification_state": meta.get("verification_state", ""),
            "evidence": str(meta.get("evidence") or "")[:1000],
            "repo": str(meta.get("repo") or "")[:500],
            "commit": str(meta.get("commit") or "")[:128],
            "source": str(meta.get("source") or "")[:96],
            "tags": meta.get("tags") if isinstance(meta.get("tags"), list) else [],
        })
    return {"status": "ok", "project_key": request.project_key, "results": results}
