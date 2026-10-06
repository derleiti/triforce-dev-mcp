"""Authenticated Project Memory synchronization API."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.routes.client_auth import get_current_client
from app.services.project_memory import (
    ProjectMemoryValidationError,
    get_project_memory_store,
)

logger = logging.getLogger("ailinux.project_memory")
router = APIRouter()


class ProjectMemorySyncRequest(BaseModel):
    project_key: str = Field(min_length=1, max_length=256)
    cursor: int = Field(default=0, ge=0)
    changes: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


def _owner_id(client: dict[str, Any]) -> str:
    # Stable across desktop reinstalls/logins, unlike client_id.
    email = str(client.get("email") or "").strip().lower()
    stable = ("account:" + email) if email else ("client:" + str(client.get("client_id") or ""))
    if stable in {"account:", "client:"}:
        raise HTTPException(401, "Authenticated account identity required")
    return hashlib.sha256(stable.encode("utf-8", errors="replace")).hexdigest()


async def _mirror_accepted(project_key: str, accepted: list[dict[str, Any]]) -> None:
    """Best-effort append-only history mirror. Never affects sync success."""
    if not accepted:
        return
    try:
        from app.services.memory_trigger import get_memory_engine
        engine = get_memory_engine()
        if not engine.settings.episodic_memory_enabled or not engine.settings.memory_record_enabled:
            return
        project = f"{engine.settings.memory_project_id or 'aicoder'}:project:{project_key}"
        for item in accepted:
            if item.get("idempotent"):
                continue
            metadata = {
                "event_type": "project_memory_revision",
                "entity_id": item.get("entity_id"),
                "project_key": project_key,
                "kind": item.get("kind"),
                "version": item.get("version"),
                "server_seq": item.get("server_seq"),
                "deleted": bool(item.get("deleted")),
                "source": item.get("source"),
                "device_id": item.get("device_id"),
                "verification_state": "accepted",
            }
            text = json.dumps({
                "title": item.get("title", ""),
                "content": item.get("content", ""),
                "status": item.get("status", ""),
                "deleted": bool(item.get("deleted")),
            }, ensure_ascii=False, separators=(",", ":"))
            try:
                await engine.provider.record(
                    text=text[:4096],
                    title=f"project_memory_revision: {str(item.get('title') or item.get('entity_id'))[:140]}",
                    project=project,
                    metadata=metadata,
                )
            except Exception as exc:
                logger.info("project_memory_mirror_degraded", extra={"failure_type": type(exc).__name__})
    except Exception as exc:
        logger.info("project_memory_mirror_degraded", extra={"failure_type": type(exc).__name__})


@router.post("/project-memory/sync")
async def sync_project_memory(
    request: ProjectMemorySyncRequest,
    client: dict = Depends(get_current_client),
):
    owner_id = _owner_id(client)
    try:
        result = get_project_memory_store().sync(
            owner_id=owner_id,
            project_key=request.project_key,
            cursor=request.cursor,
            changes=request.changes,
        )
    except ProjectMemoryValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Project Memory commit already succeeded. History is explicitly fail-open.
    await _mirror_accepted(request.project_key, result["accepted"])
    return result
