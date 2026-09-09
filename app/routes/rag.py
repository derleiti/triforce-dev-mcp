"""RAG API routes for local project/document indexes."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.paths import PROJECT_ROOT

from ..services.rag_service import RagPathNotAllowed, rag_service


router = APIRouter(prefix="/rag", tags=["RAG"])


class RagIndexRequest(BaseModel):
    project: str = Field(default="triforce", min_length=1, max_length=80)
    path: str = Field(default=str(PROJECT_ROOT), min_length=1, max_length=4096)
    include_globs: Optional[List[str]] = Field(default=None)
    exclude_dirs: List[str] = Field(default_factory=list)
    chunk_chars: int = Field(default=2200, ge=500, le=12000)
    overlap_chars: int = Field(default=250, ge=0, le=2000)


class RagQueryRequest(BaseModel):
    project: str = Field(default="triforce", min_length=1, max_length=80)
    query: str = Field(..., min_length=2, max_length=4000)
    top_k: int = Field(default=8, ge=1, le=50)
    path_filter: Optional[str] = Field(default=None, max_length=1000)


@router.get("/health")
def rag_health():
    return {
        "ok": True,
        "service": "ailinux-rag",
        "backend": "local-jsonl-lexical-v1",
        "projects": rag_service.list_projects(),
    }


@router.get("/projects")
def rag_projects():
    return {"projects": rag_service.list_projects()}


@router.post("/index")
def rag_index(request: RagIndexRequest):
    try:
        return rag_service.index_path(
            project=request.project,
            path=request.path,
            include_globs=request.include_globs,
            exclude_dirs=request.exclude_dirs,
            chunk_chars=request.chunk_chars,
            overlap_chars=request.overlap_chars,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"RAG index failed: {exc}") from exc


@router.post("/query")
def rag_query(request: RagQueryRequest):
    result = rag_service.query(
        project=request.project,
        query=request.query,
        top_k=request.top_k,
        path_filter=request.path_filter,
    )
    if result["total_chunks"] == 0:
        result["warning"] = "No chunks indexed for this project. Run POST /v1/rag/index first."
    return result
