from __future__ import annotations

import json
import time
from time import perf_counter
from typing import AsyncGenerator, Iterable, List, Literal, Optional
from uuid import uuid4

from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import chat as chat_service
from ..services.model_registry import ModelInfo, registry
from ..utils.errors import api_error
from ..utils.throttle import request_slot

# Performance Monitor für Model-Latenz-Tracking
try:
    from ..routes.perf_monitor import monitor as perf_monitor
    _HAS_PERF_MONITOR = True
except ImportError:
    _HAS_PERF_MONITOR = False


class OpenAIChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str | List[dict]


class OpenAIChatCompletionRequest(BaseModel):
    model: str
    messages: List[OpenAIChatMessage]
    stream: bool = False
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    frequency_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0)
    presence_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0)
    stop: Optional[List[str]] = None


def _estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text.split()))


def _convert_messages(messages: Iterable[OpenAIChatMessage]) -> List[dict[str, str]]:
    return [{"role": item.role, "content": item.content} for item in messages if item.content]


async def _resolve_model(request_model: str) -> tuple[str, ModelInfo]:
    settings = get_settings()
    alias_map = settings.openai_model_aliases or {}
    mapped = alias_map.get(request_model, request_model).strip()
    candidate_ids: List[str] = []

    if mapped:
        candidate_ids.append(mapped)
        if mapped.startswith("ollama/"):
            candidate_ids.append(mapped.split("/", 1)[1])
        elif "/" not in mapped:
            candidate_ids.append(f"ollama/{mapped}")

    # Ensure we always attempt the raw request model as well.
    for value in (request_model, request_model.replace("ollama/", "")):
        if value and value not in candidate_ids:
            candidate_ids.append(value)

    seen: set[str] = set()
    for candidate in candidate_ids:
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        model = await registry.get_model(normalized)
        if model:
            return normalized, model

    # Fallback: try prefix matching using cached models
    models = await registry.list_models()
    lowered = mapped.lower()
    for model in models:
        if model.id.lower() == lowered:
            return model.id, model
        prefixed = f"{model.provider}/{model.id}".lower()
        if prefixed == lowered:
            return model.id, model

    raise api_error("Requested model is not available", status_code=404, code="model_not_found")


class _UsageTracker:
    def __init__(self, prompt_messages: List[dict[str, str]]) -> None:
        combined_prompt = "\n".join(message["content"] if isinstance(message["content"], str) else " ".join(str(p.get("text", "")) for p in message["content"] if p.get("type") == "text") for message in prompt_messages)
        self.prompt_tokens = _estimate_tokens(combined_prompt)
        self._completion_parts: List[str] = []

    def append(self, chunk: str) -> None:
        if chunk:
            self._completion_parts.append(chunk)

    @property
    def completion_text(self) -> str:
        return "".join(self._completion_parts)

    def usage(self) -> dict[str, int]:
        completion_tokens = _estimate_tokens(self.completion_text)
        total = self.prompt_tokens + completion_tokens
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total,
        }


async def _strict_provider_response(model_info, resolved_model, messages, payload):
    from .provider_chat import chat_completion
    from ..routes.client_chat import _validate_chat_content
    for message in messages:
        _validate_chat_content(message["content"])
    if any(isinstance(m["content"], list) for m in messages) and "vision" not in model_info.capabilities:
        raise api_error("Model does not support image input", status_code=400, code="unsupported_modality")
    if model_info.provider not in {"openrouter", "nvidia"}:
        raise api_error("Multimodal compatibility not supported for this provider", status_code=400, code="unsupported_provider")
    result = await chat_completion(model_info, resolved_model, messages, temperature=payload.temperature, max_tokens=payload.max_tokens or 1024)
    content = result.get("content")
    if not isinstance(content, str) or not content.strip():
        raise api_error("Provider returned no assistant content", status_code=502, code="empty_response")
    return content


def _strict_route(model_info, messages):
    return any(isinstance(m["content"], list) for m in messages) or (model_info.provider == "openrouter" and model_info.id.endswith(":free"))


async def create_chat_completion(payload: OpenAIChatCompletionRequest) -> dict[str, object]:
    if not payload.messages:
        raise api_error("At least one message is required", status_code=422, code="missing_messages")

    resolved_model, model_info = await _resolve_model(payload.model)
    internal_messages = _convert_messages(payload.messages)
    usage = _UsageTracker(internal_messages)
    created = int(time.time())
    completion_id = f"chatcmpl-{uuid4().hex}"

    # Model-Latenz-Tracking
    model_start = perf_counter()
    error_occurred = False
    
    try:
        async with request_slot():
            if _strict_route(model_info, internal_messages):
                usage.append(await _strict_provider_response(model_info, resolved_model, internal_messages, payload))
            else:
                async for chunk in chat_service.stream_chat(
                    model_info,
                    resolved_model,
                    (message for message in internal_messages),
                    stream=bool(payload.stream),
                    temperature=payload.temperature,
                ):
                    usage.append(chunk)
    except Exception as e:
        error_occurred = True
        raise
    finally:
        # Latenz aufzeichnen
        if _HAS_PERF_MONITOR:
            latency_ms = (perf_counter() - model_start) * 1000
            perf_monitor.record_model(resolved_model, latency_ms, error=error_occurred)

    content = usage.completion_text
    finish_reason = "stop"

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": payload.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage.usage(),
    }


async def stream_chat_completion(payload: OpenAIChatCompletionRequest) -> StreamingResponse:
    if not payload.messages:
        raise api_error("At least one message is required", status_code=422, code="missing_messages")

    resolved_model, model_info = await _resolve_model(payload.model)
    internal_messages = _convert_messages(payload.messages)
    usage = _UsageTracker(internal_messages)
    created = int(time.time())
    completion_id = f"chatcmpl-{uuid4().hex}"

    strict = _strict_route(model_info, internal_messages)
    if strict:
        from ..routes.client_chat import _validate_chat_content
        for message in internal_messages:
            _validate_chat_content(message["content"])
        if model_info.provider not in {"openrouter", "nvidia"}:
            raise api_error("Unsupported multimodal provider", status_code=400, code="unsupported_provider")
        if any(isinstance(m["content"], list) for m in internal_messages) and "vision" not in model_info.capabilities:
            raise api_error("Model does not support image input", status_code=400, code="unsupported_modality")

    async def chunks():
        if strict:
            from .provider_chat import stream_completion
            async for part in stream_completion(model_info, resolved_model, internal_messages, temperature=payload.temperature, max_tokens=payload.max_tokens or 1024):
                yield part
        else:
            async for part in chat_service.stream_chat(model_info, resolved_model, iter(internal_messages), stream=True, temperature=payload.temperature):
                yield part

    async def generator() -> AsyncGenerator[str, None]:
        # Model-Latenz-Tracking für Streaming
        model_start = perf_counter()
        error_occurred = False
        
        try:
            async with request_slot():
                async for chunk in chunks():
                    if not chunk:
                        continue
                    usage.append(chunk)
                    body = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": payload.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": chunk},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
        except Exception as e:
            error_occurred = True
            raise
        finally:
            # Latenz aufzeichnen (Gesamtzeit bis Stream-Ende)
            if _HAS_PERF_MONITOR:
                latency_ms = (perf_counter() - model_start) * 1000
                perf_monitor.record_model(resolved_model, latency_ms, error=error_occurred)

        final_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": payload.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
            "usage": usage.usage(),
        }
        yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")
