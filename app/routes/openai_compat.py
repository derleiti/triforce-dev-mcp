from __future__ import annotations

import time
import hmac
import os

from fastapi import APIRouter, Depends, Query, Header, HTTPException
from ..utils.rate_limit_compat import RateLimiter

from ..services.openai_compat import (
    OpenAIChatCompletionRequest,
    create_chat_completion,
    stream_chat_completion,
)
from ..services.model_registry import registry

router = APIRouter(tags=["openai-compat"])


async def require_pinned_ollama_auth(
    payload: OpenAIChatCompletionRequest,
    authorization: str | None = Header(None, alias="Authorization"),
    x_internal_key: str | None = Header(None, alias="X-Internal-Key"),
) -> None:
    """Block anonymous direct inference against the private federation nodes.

    Other OpenAI-compatible provider behavior stays unchanged. Server-side
    callers may use the existing internal key; desktop/API users need a
    verified TriForce client JWT (never an untrusted X-User-ID header).
    """
    from ..services.ollama_node_router import LOCAL_OLLAMA_PLACEMENT
    name = payload.model.removeprefix("ollama/")
    if name not in LOCAL_OLLAMA_PLACEMENT:
        return
    expected = os.getenv("INTERNAL_API_KEY") or ""
    if expected and x_internal_key and hmac.compare_digest(x_internal_key, expected):
        return
    from .client_chat import get_user_and_tier_from_headers
    from ..services.user_tiers import UserTier
    user_id, tier = get_user_and_tier_from_headers(authorization)
    if user_id != "anonymous" and tier in (
        UserTier.REGISTERED, UserTier.PRO, UserTier.ENTERPRISE,
    ):
        return
    raise HTTPException(403, "Authenticated TriForce account required for local Ollama workers")


@router.get("/")
async def get_openai_metadata():
    """OpenAI-kompatible Root-Metadaten."""
    return {
        "object": "api",
        "version": "v1",
        "endpoints": ["/models", "/chat/completions"]
    }


@router.get("/models")
async def list_openai_models(
    force_refresh: bool = Query(False, description="Force-refresh the model registry"),
    provider: str = Query(None, description="Filter by provider (e.g., 'gemini', 'mistral')"),
    capability: str = Query(None, description="Filter by capability (e.g., 'chat', 'vision', 'code')")
):
    """
    OpenAI-kompatible Modell-Liste mit allen gefetchten Modellen.
    """
    models = await registry.list_models(force_refresh=force_refresh)
    
    # Filter anwenden
    if provider:
        models = [m for m in models if m.provider.lower() == provider.lower()]
    if capability:
        models = [m for m in models if capability.lower() in [c.lower() for c in m.capabilities]]
    
    # In OpenAI-Format konvertieren
    timestamp = int(time.time())
    openai_models = []
    
    for model in models:
        openai_models.append({
            "id": model.id,
            "object": "model",
            "created": timestamp,
            "owned_by": model.provider,
            "permission": [],
            "root": model.id,
            "parent": None,
            "capabilities": model.capabilities,
            "roles": getattr(model, 'roles', []),
            "api_method": getattr(model, 'api_method', 'generateContent')
        })
    
    return {
        "object": "list",
        "data": openai_models,
        "count": len(openai_models)
    }


@router.get("/models/{model_id:path}")
async def get_openai_model(model_id: str):
    """OpenAI-kompatible Modell-Details."""
    model = await registry.get_model(model_id)
    
    if not model:
        return {
            "error": {
                "message": f"Model '{model_id}' not found",
                "type": "invalid_request_error",
                "code": "model_not_found"
            }
        }
    
    return {
        "id": model.id,
        "object": "model",
        "created": int(time.time()),
        "owned_by": model.provider,
        "permission": [],
        "root": model.id,
        "parent": None,
        "capabilities": model.capabilities,
        "roles": getattr(model, 'roles', []),
        "api_method": getattr(model, 'api_method', 'generateContent')
    }


@router.post(
    "/chat/completions",
    dependencies=[Depends(RateLimiter(times=5, seconds=10)),
                  Depends(require_pinned_ollama_auth)],
)
async def openai_chat_completions(payload: OpenAIChatCompletionRequest):
    if payload.stream:
        return await stream_chat_completion(payload)
    return await create_chat_completion(payload)


@router.post(
    "/",
    dependencies=[Depends(RateLimiter(times=5, seconds=10)),
                  Depends(require_pinned_ollama_auth)],
)
async def openai_chat_completions_root(payload: OpenAIChatCompletionRequest):
    """Alternative endpoint at /v1/openai for compatibility."""
    if payload.stream:
        return await stream_chat_completion(payload)
    return await create_chat_completion(payload)