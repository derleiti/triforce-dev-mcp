from __future__ import annotations
from typing import AsyncGenerator, List, Literal, Optional
from time import perf_counter
from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from ..utils.rate_limit_compat import RateLimiter

from ..services import chat as chat_service
from ..services.model_registry import registry
from ..utils.errors import api_error
from ..utils.throttle import request_slot

# Performance Monitor für Model-Latenz-Tracking
try:
    from .perf_monitor import monitor as perf_monitor
    _HAS_PERF_MONITOR = True
except ImportError:
    _HAS_PERF_MONITOR = False

router = APIRouter(tags=["chat"])


async def require_local_chat_auth(
    payload: "ChatRequest",
    authorization: str | None = Header(None, alias="Authorization"),
    x_internal_key: str | None = Header(None, alias="X-Internal-Key"),
) -> None:
    """Apply the same private worker policy as the OpenAI compatibility path."""
    from .openai_compat import require_pinned_ollama_auth
    await require_pinned_ollama_auth(payload, authorization, x_internal_key)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(..., min_length=1)

class ChatRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    stream: bool = True
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)

async def _chat_generator(payload: ChatRequest, model=None) -> AsyncGenerator[str, None]:
    # Model can be pre-validated by caller (avoids 404-mid-stream bug).
    # Fallback: validate here (back-compat for non-streaming callers).
    if model is None:
        model = await registry.get_model(payload.model)
        if not model or "chat" not in model.capabilities:
            raise api_error("Requested model does not support chat", status_code=404, code="model_not_found")

    # Model-Latenz-Tracking
    model_start = perf_counter()
    error_occurred = False

    async with request_slot():
        try:
            async for chunk in chat_service.stream_chat(
                model,
                payload.model,
                (m.model_dump() for m in payload.messages),
                stream=payload.stream,
                temperature=payload.temperature,
            ):
                if chunk:
                    yield chunk
        except Exception as exc:
            error_occurred = True
            # If streaming has started, yield error as text instead of raising
            import logging
            logger = logging.getLogger("ailinux.chat")
            logger.error("Streaming error: %s", exc)
            error_msg = f"\n\n[Fehler beim Streaming: {str(exc)}]"
            yield error_msg
        finally:
            # Latenz aufzeichnen
            if _HAS_PERF_MONITOR:
                latency_ms = (perf_counter() - model_start) * 1000
                perf_monitor.record_model(payload.model, latency_ms, error=error_occurred)

@router.post("/chat", dependencies=[Depends(RateLimiter(times=5, seconds=10)),
                                    Depends(require_local_chat_auth)])
async def chat_endpoint(payload: ChatRequest):
    if not payload.messages:
        raise api_error("At least one message is required", status_code=422, code="missing_messages")

    # Pre-validate model BEFORE creating StreamingResponse — otherwise the
    # 404 would be raised mid-stream after headers already flushed (results
    # in HTTP 200 + empty body + ASGI RuntimeError). Bug fixed 2026-06-04.
    model = await registry.get_model(payload.model)
    if not model or "chat" not in model.capabilities:
        raise api_error("Requested model does not support chat", status_code=404, code="model_not_found")

    if payload.stream:
        return StreamingResponse(_chat_generator(payload, model=model), media_type="text/plain")

    collected: List[str] = []
    async for chunk in _chat_generator(payload, model=model):
        collected.append(chunk)
    return {"text": "".join(collected)}

async def _openai_stream_wrapper(payload: ChatRequest, model) -> AsyncGenerator[str, None]:
    """Wraps _chat_generator output into OpenAI-compatible SSE chunks."""
    import json as _json
    import time as _time
    import uuid as _uuid
    chat_id = f"chatcmpl-{_uuid.uuid4().hex[:24]}"
    created = int(_time.time())
    async for chunk in _chat_generator(payload, model=model):
        if not chunk:
            continue
        sse_payload = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": payload.model,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": chunk},
                "finish_reason": None,
            }],
        }
        yield f"data: {_json.dumps(sse_payload)}\n\n"
    # Final chunk
    final = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": payload.model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {_json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/chat/completions", dependencies=[Depends(RateLimiter(times=5, seconds=10)),
                                                Depends(require_local_chat_auth)])
async def chat_completions_alias(payload: ChatRequest):
    """OpenAI-compatible chat completions endpoint.
    Returns proper OpenAI format (choices[].message.content) instead of {"text":"..."}.
    """
    if not payload.messages:
        raise api_error("At least one message is required", status_code=422, code="missing_messages")

    # Pre-validate model BEFORE creating StreamingResponse
    model = await registry.get_model(payload.model)
    if not model or "chat" not in model.capabilities:
        raise api_error("Requested model does not support chat", status_code=404, code="model_not_found")

    if payload.stream:
        return StreamingResponse(
            _openai_stream_wrapper(payload, model),
            media_type="text/event-stream",
        )

    # Non-streaming: collect and wrap in OpenAI completion format
    import time as _time
    import uuid as _uuid
    collected: list = []
    async for chunk in _chat_generator(payload, model=model):
        if chunk:
            collected.append(chunk)
    content = "".join(collected)
    prompt_tokens = sum(len(m.content.split()) for m in payload.messages)  # rough estimate
    completion_tokens = len(content.split())
    return {
        "id": f"chatcmpl-{_uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(_time.time()),
        "model": payload.model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }