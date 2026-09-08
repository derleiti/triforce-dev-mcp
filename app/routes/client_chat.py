"""
AILinux Client Chat API
Tier-basierter Chat:
- Guest: verifizierte Ollama-Modelle
- Registered: Ollama + konfigurierte Free-Quota-Provider
- Pro/Enterprise: alle konfigurierten Chat-Provider
"""
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Any, Optional, List
import asyncio
import json
import httpx
import os
import logging
import time
from datetime import datetime

from ..services.user_tiers import (
    tier_service, UserTier, FREE_MODELS_OLLAMA, LOCAL_FALLBACK_MODEL
)
from ..services.model_registry import OPENROUTER_FREE_ROUTER, registry
from ..services.provider_chat import chat_completion, normalize_tools
from ..services.model_availability import availability_service

logger = logging.getLogger("ailinux.client_chat")

router = APIRouter(prefix="/client", tags=["Client Chat"])

# Backend Config
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# JWT Config - Import from auth module to share secret
from .client_auth import decode_authorization_header




def extract_user_and_tier_from_token(authorization: str = None) -> tuple:
    """Return (user, tier) for a verified bearer token.

    Missing auth is still allowed for guest endpoints. Once an Authorization
    header is supplied, however, it must be valid; malformed/expired bearer
    tokens must never silently downgrade to an anonymous guest session.
    """
    if not authorization:
        return None, None

    payload = decode_authorization_header(authorization)
    email = payload.get("email") or payload.get("sub")
    tier = payload.get("tier") or payload.get("role")
    if email:
        logger.debug("Token valid: email=%s, tier=%s", email, tier)
    return email, tier


def get_user_and_tier_from_headers(
    authorization: str = None,
    x_user_id: str = None
) -> tuple:
    """
    Ermittelt User-ID und Tier aus Headers.
    
    Priorität:
    1. JWT Token (enthält beides - kein Lookup)
    2. X-User-ID + tier_service Lookup  
    3. "anonymous" + GUEST
    
    Returns: (user_id, UserTier)
    """
    # 1. JWT Token - Tier direkt aus Token
    if authorization:
        email, tier_str = extract_user_and_tier_from_token(authorization)
        if email:
            try:
                tier = UserTier(tier_str) if tier_str else tier_service.get_user_tier(email)
            except ValueError:
                tier = tier_service.get_user_tier(email)
            return email, tier
    
    # 2. X-User-ID Header
    if x_user_id and x_user_id not in ("", "anonymous", "none", "null"):
        tier = tier_service.get_user_tier(x_user_id)
        return x_user_id, tier
    
    # 3. Guest
    return "anonymous", UserTier.GUEST


# Legacy wrapper for compatibility
def extract_user_from_token(authorization: str = None) -> Optional[str]:
    """Legacy wrapper - use extract_user_and_tier_from_token instead."""
    email, _ = extract_user_and_tier_from_token(authorization)
    return email


def get_user_id_from_headers(authorization: str = None, x_user_id: str = None) -> str:
    """Legacy wrapper - use get_user_and_tier_from_headers instead."""
    user_id, _ = get_user_and_tier_from_headers(authorization, x_user_id)
    return user_id




class ChatMessage(BaseModel):
    role: str
    # String for normal chat, OpenAI-style content blocks for multimodal turns.
    content: Any
    # Preserve the OpenAI-compatible native tool-call loop across the AICoder ->
    # TriForce -> provider boundary. These fields are required on continuation
    # turns after an assistant requested a tool.
    tool_calls: Optional[List[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ChatRequest(BaseModel):
    message: Optional[str] = None
    messages: Optional[List[ChatMessage]] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 4096
    tools: Optional[List[dict[str, Any]]] = None
    tool_choice: Any = "auto"


class ChatResponse(BaseModel):
    response: str
    model: str
    tier: str
    backend: str  # "ollama" oder Provider-ID
    tool_calls: List[dict[str, Any]] = Field(default_factory=list)
    tokens_used: Optional[int] = None  # None bei Ollama für Pro (unlimited)
    tokens_unlimited: Optional[bool] = False  # True wenn Ollama für Pro/Enterprise
    latency_ms: Optional[int] = None
    fallback_used: Optional[bool] = False  # True wenn lokales Fallback-Modell verwendet wurde
    tool_transport: str = "none"  # none | native | text_fallback
    finish_reason: Optional[str] = None
    provider_diagnostics: dict[str, Any] = Field(default_factory=dict)


class ModelsResponse(BaseModel):
    tier: str
    tier_name: str
    model_count: int
    models: List[str]
    model_details: List[dict[str, Any]] = Field(default_factory=list)
    backend: str
    upgrade_available: bool


SUPPORTED_CHAT_PROVIDERS = {
    "ollama", "openai", "anthropic", "gemini", "mistral", "groq",
    "cerebras", "nvidia", "cohere", "openrouter", "together", "fireworks",
    "cloudflare", "huggingface", "github",
}

PROVIDER_KEY_ENVS = {
    "openai": ("OPENAI_API_KEY", "OPENROUTER_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": (
        "GEMINI_API_KEY", "GOOGLE_AI_STUDIO_KEY", "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
    ),
    "mistral": ("MISTRAL_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "cerebras": ("CEREBRAS_API_KEY",),
    "nvidia": ("NVIDIA_API_KEY",),
    "cohere": ("COHERE_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "together": ("TOGETHER_API_KEY",),
    "fireworks": ("FIREWORKS_API_KEY",),
    "huggingface": ("HUGGINGFACE_API_KEY", "HF_TOKEN"),
    "github": ("GITHUB_TOKEN", "OPENROUTER_API_KEY"),
}


def provider_is_configured(provider: str) -> bool:
    """Avoid advertising static models whose provider cannot execute chat."""
    if provider == "ollama":
        return True
    if provider == "cloudflare":
        return bool(
            os.getenv("CLOUDFLARE_API_TOKEN")
            and os.getenv("CLOUDFLARE_ACCOUNT_ID")
        )
    return any(os.getenv(name) for name in PROVIDER_KEY_ENVS.get(provider, ()))


def chat_capable_model_ids(models) -> List[str]:
    """Return only executable, configured chat entries for ai-coder."""
    return [
        model.id for model in models
        if "chat" in (getattr(model, "capabilities", None) or set())
        and getattr(model, "provider", "") in SUPPORTED_CHAT_PROVIDERS
        and provider_is_configured(getattr(model, "provider", ""))
    ]


GUEST_FREE_PROVIDER_KEYS = {
    "mistral": "MISTRAL_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
}

NVIDIA_AICODER_FREE_MODELS = {
    "nvidia/poolside/laguna-xs-2.1",
    "nvidia/z-ai/glm-5.2",
    "nvidia/minimaxai/minimax-m3",
    "nvidia/openai/gpt-oss-120b",
    "nvidia/openai/gpt-oss-20b",
    "nvidia/nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nvidia/nemotron-3-nano-30b-a3b",
    "nvidia/nvidia/nemotron-nano-3-30b-a3b",
    "nvidia/nvidia/nvidia-nemotron-nano-9b-v2",
    "nvidia/mistralai/codestral-22b-instruct-v0.1",
    "nvidia/ibm/granite-34b-code-instruct",
    "nvidia/ibm/granite-8b-code-instruct",
    "nvidia/google/codegemma-7b",
    "nvidia/google/codegemma-1.1-7b",
    "nvidia/deepseek-ai/deepseek-coder-6.7b-instruct",
    "nvidia/bigcode/starcoder2-15b",
    "nvidia/meta/codellama-70b",
}

REGISTERED_FREE_PROVIDER_KEYS = {
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "cloudflare": "CLOUDFLARE_API_TOKEN",
    "huggingface": "HUGGINGFACE_API_KEY",
}

def is_guest_free_model(model: str) -> bool:
    """Guest policy: curated Ollama plus configured Mistral/NVIDIA chat models."""
    if model.startswith("ollama/"):
        return model in FREE_MODELS_OLLAMA
    provider = model.split("/", 1)[0]
    env_name = GUEST_FREE_PROVIDER_KEYS.get(provider)
    if not env_name or not os.getenv(env_name):
        return False
    if provider == "mistral":
        return True
    if provider == "nvidia":
        return model in NVIDIA_AICODER_FREE_MODELS
    return False

def guest_free_model_ids(models) -> List[str]:
    return [
        model.id for model in models
        if "chat" in (getattr(model, "capabilities", None) or set())
        and is_guest_free_model(model.id)
    ]


def is_registered_free_model(model: str) -> bool:
    """Free-account policy: curated Ollama plus configured free-quota providers."""
    if model.startswith("ollama/"):
        return model in FREE_MODELS_OLLAMA
    if model == OPENROUTER_FREE_ROUTER:
        return bool(os.getenv("OPENROUTER_API_KEY"))
    if model.startswith("openrouter/") and model.endswith(":free"):
        return bool(os.getenv("OPENROUTER_API_KEY"))
    provider = model.split("/", 1)[0]
    env_name = REGISTERED_FREE_PROVIDER_KEYS.get(provider)
    return bool(env_name and (os.getenv(env_name) or (env_name == "HUGGINGFACE_API_KEY" and os.getenv("HF_TOKEN"))))


def registered_free_model_ids(models) -> List[str]:
    return [
        model.id for model in models
        if "chat" in (getattr(model, "capabilities", None) or set())
        and is_registered_free_model(model.id)
    ]


def get_default_ollama_model() -> str:
    """Schnelles lokales Default-Modell für alle Tiers."""
    return normalize_ollama_model(LOCAL_FALLBACK_MODEL)


def get_default_model(tier: UserTier) -> str:
    """Zuverlässiges lokales Default-Modell für alle Tiers."""
    # Cloud-Free-Modelle bleiben auswählbar, sind aber nicht mehr der
    # latenzkritische Default- und Notfallpfad.
    return LOCAL_FALLBACK_MODEL


def normalize_ollama_model(model: str) -> str:
    """Normalisiere Model-ID für Ollama"""
    if model.startswith("ollama/"):
        return model[7:]  # Entferne "ollama/" Prefix
    return model


def normalize_openrouter_model(model: str) -> str:
    """Normalisiere Model-ID für OpenRouter"""
    if model.startswith("openrouter/"):
        return model[11:]  # Entferne "openrouter/" Prefix
    return model


def route_cloud_model(model: str) -> tuple[str, str]:
    """
    Entscheidet Provider und provider-spezifische Model-ID.

    Wichtig:
    - openrouter/<vendor>/<model> geht zu OpenRouter als <vendor>/<model>
    - mistral/<model> geht NICHT zu OpenRouter
    - unbekannte vendor/model IDs bleiben OpenRouter-kompatibel
    """
    if model.startswith("openrouter/"):
        return "openrouter", model[len("openrouter/"):]

    if model.startswith("mistral/"):
        return "mistral", model[len("mistral/"):]

    if model.startswith("groq/"):
        return "groq", model[len("groq/"):]

    if model.startswith("gemini/"):
        return "gemini", model[len("gemini/"):]

    if model.startswith("cerebras/"):
        return "cerebras", model[len("cerebras/"):]

    if model.startswith("cloudflare/"):
        return "cloudflare", model[len("cloudflare/"):]

    # OpenRouter-native IDs wie mistralai/mistral-large, anthropic/..., openai/...
    return "openrouter", model


MAX_CHAT_IMAGES = 8
MAX_CHAT_IMAGE_DATA_CHARS = int(8 * 1024 * 1024 * 1.4)
MAX_CHAT_TEXT_BLOCK_CHARS = 400_000


def _validate_chat_content(content: Any) -> None:
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        raise HTTPException(400, "message content must be a string or content-block list")
    image_count = 0
    for part in content:
        if not isinstance(part, dict):
            raise HTTPException(400, "multimodal content blocks must be objects")
        kind = part.get("type")
        if kind in {"text", "input_text"}:
            text = str(part.get("text") or "")
            if len(text) > MAX_CHAT_TEXT_BLOCK_CHARS:
                raise HTTPException(413, "text attachment block too large")
            continue
        if kind in {"image_url", "input_image"}:
            image_count += 1
            if image_count > MAX_CHAT_IMAGES:
                raise HTTPException(413, f"too many images (max {MAX_CHAT_IMAGES})")
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if not isinstance(url, str) or not url.startswith("data:image/") or ";base64," not in url:
                raise HTTPException(400, "images must use data:image/...;base64 URLs")
            if len(url) > MAX_CHAT_IMAGE_DATA_CHARS:
                raise HTTPException(413, "image attachment too large")
            continue
        raise HTTPException(400, f"unsupported content block type: {kind}")


def _content_has_image(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"image_url", "input_image", "image"}:
            return True
    return False


def _messages_have_images(messages: List[dict]) -> bool:
    return any(_content_has_image(item.get("content")) for item in messages if isinstance(item, dict))


def _ollama_multimodal_messages(messages: List[dict]) -> List[dict]:
    """Translate normalized image_url blocks to Ollama's message.images format."""
    converted: List[dict] = []
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        content = item.get("content")
        if not isinstance(content, list):
            converted.append(item)
            continue
        text: list[str] = []
        images: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"text", "input_text"}:
                text.append(str(part.get("text") or ""))
                continue
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if isinstance(url, str) and url.startswith("data:image/") and "," in url:
                images.append(url.split(",", 1)[1])
        item["content"] = "\n".join(chunk for chunk in text if chunk)
        if images:
            item["images"] = images
        converted.append(item)
    return converted


async def call_ollama(
    model: str,
    messages: List[dict],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    is_fallback: bool = False,
    tools: Optional[List[dict[str, Any]]] = None,
    tool_choice: Any = "auto",
) -> dict:
    """
    Call Ollama API (lokal auf Server)
    
    Bei Cloud-Proxy Fehlern (502, 503, Timeout) → konfiguriertes lokales Fallback
    """

    # Normalisiere Model-Name
    model_name = normalize_ollama_model(model)

    payload = {
        "model": model_name,
        "messages": _ollama_multimodal_messages(messages),
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
    }
    native_tools = normalize_tools(tools)
    tool_transport = "native" if native_tools else "none"
    if native_tools:
        payload["tools"] = native_tools

    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=payload
            )
            # Not every Ollama model is tool-trained. Preserve chat by retrying
            # without native tools; ai-coder's textual protocol remains active.
            if response.status_code in (400, 404, 422) and payload.get("tools"):
                fallback_payload = dict(payload)
                fallback_payload.pop("tools", None)
                tool_transport = "text_fallback"
                response = await client.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json=fallback_payload,
                )

            # Cloud-Proxy Fehler → Fallback auf lokales Modell
            if response.status_code in (502, 503, 504) and not is_fallback and not _messages_have_images(messages):
                logger.warning(f"Ollama Cloud-Proxy Error {response.status_code} für {model} - Fallback auf lokales Modell")
                return await call_ollama(
                    model=LOCAL_FALLBACK_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    is_fallback=True
                )

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"Ollama Error: {error_text}")
                
                # Bei anderen Fehlern auch Fallback versuchen
                if not is_fallback and "cloud" in model.lower() and not _messages_have_images(messages):
                    logger.warning(f"Cloud-Modell {model} fehlgeschlagen - Fallback auf lokales Modell")
                    return await call_ollama(
                        model=LOCAL_FALLBACK_MODEL,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        is_fallback=True
                    )
                
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Ollama Error: {error_text}"
                )

            result = response.json()
            message = result.get("message") or {}
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls") or []
            done_reason = result.get("done_reason")

            # Reasoning models can consume a tiny num_predict budget entirely in
            # hidden/thinking tokens and return HTTP 200 with no assistant text.
            # Never expose that as a successful empty answer: the caller must be
            # able to classify it as truncation and retry with a larger budget.
            if not content and not tool_calls:
                if done_reason == "length":
                    raise HTTPException(
                        502,
                        detail={
                            "error": "ollama_response_truncated",
                            "message": "Ollama exhausted max_tokens before producing an answer",
                            "model": model_name,
                            "finish_reason": "length",
                        },
                    )
                raise HTTPException(
                    502,
                    detail={
                        "error": "empty_ollama_response",
                        "message": "Ollama returned no assistant content or tool calls",
                        "model": model_name,
                        "finish_reason": done_reason,
                    },
                )

            # Ollama Response in OpenAI-Format konvertieren
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls,
                    },
                    "finish_reason": done_reason or "stop",
                }],
                "usage": {
                    "total_tokens": result.get("eval_count", 0) + result.get("prompt_eval_count", 0)
                },
                "model_used": model_name,
                "is_fallback": is_fallback,
                "tool_transport": tool_transport
            }

        except httpx.ConnectError:
            logger.error("Ollama nicht erreichbar")
            raise HTTPException(503, "Ollama Backend nicht erreichbar")
        except httpx.TimeoutException:
            # Timeout bei Cloud-Proxy → Fallback
            if not is_fallback and "cloud" in model.lower() and not _messages_have_images(messages):
                logger.warning(f"Timeout für {model} - Fallback auf lokales Modell")
                return await call_ollama(
                    model=LOCAL_FALLBACK_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    is_fallback=True
                )
            raise HTTPException(504, "Ollama Timeout")


async def call_openrouter(
    model: str,
    messages: List[dict],
    temperature: float = 0.7,
    max_tokens: int = 4096
) -> dict:
    """Call OpenRouter API (für Pro/Enterprise) mit Ollama-Fallback bei 402"""

    # Normalisiere Model-Name
    model_name = normalize_openrouter_model(model)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://ailinux.me",
        "X-Title": "AILinux Client"
    }

    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload
        )

        # Bei 402 (Payment Required) → Fallback zu Ollama
        if response.status_code in (402, 429, 500, 502, 503, 504):
            logger.warning(f"OpenRouter {response.status_code} für {model} - Fallback zu Ollama")
            # Markiere Model als unavailable
            availability_service.mark_error(model, 402, "Payment Required")
            # Fallback zu Ollama (kostenlos)
            return await call_ollama(
                model=LOCAL_FALLBACK_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )

        if response.status_code != 200:
            error_text = response.text
            raise HTTPException(
                status_code=response.status_code,
                detail=f"OpenRouter Error: {error_text}"
            )

        return response.json()


async def call_registered_model(
    model: str,
    messages: List[dict],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    tools: Optional[List[dict[str, Any]]] = None,
    tool_choice: Any = "auto",
) -> dict:
    """Call any chat-capable registry model through the normalized adapter."""
    model_info = await registry.get_model(model)
    if not model_info or "chat" not in model_info.capabilities:
        raise HTTPException(404, f"Chat model not available: {model}")
    if _messages_have_images(messages) and "vision" not in model_info.capabilities:
        raise HTTPException(400, f"Selected model does not support vision: {model}")

    try:
        result = await chat_completion(
            model_info,
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
        )
    except HTTPException as exc:
        # Provider discovery can advertise models that the configured account
        # cannot actually invoke (notably NVIDIA NIM account-scoped functions).
        # Feed real execution failures back into the availability filter so a
        # broken model disappears from subsequent /client/models responses.
        availability_service.mark_error(model, exc.status_code, str(exc.detail))
        raise
    else:
        availability_service.mark_success(model)

    content = result.get("content", "")
    tool_calls = result.get("tool_calls") or []
    provider_diagnostics = result.get("provider_diagnostics") if isinstance(result.get("provider_diagnostics"), dict) else {}
    if not str(content or "").strip() and not tool_calls:
        detail = {
            "error": "empty_provider_response",
            "provider": str(model_info.provider),
            "requested_model": model,
            "model_used": result.get("model_used") or model,
            "retryable": True,
            "retry_after": 2,
            "provider_diagnostics": provider_diagnostics,
        }
        logger.warning("Provider returned empty assistant response: %s", detail)
        raise HTTPException(status_code=502, detail=detail)
    prompt_tokens = sum(len(str(item.get("content", "")).split()) for item in messages)
    completion_tokens = len(content.split())
    return {
        "choices": [{"message": {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        }, "finish_reason": provider_diagnostics.get("finish_reason")}],
        "usage": {"total_tokens": result.get("usage_total") or prompt_tokens + completion_tokens},
        "model_used": result.get("model_used") or model,
        "is_fallback": False,
        "tool_transport": result.get("tool_transport", "native" if tools else "none"),
        "provider_diagnostics": provider_diagnostics,
    }


def _default_provider_retry_after(status: int) -> int | None:
    """Fallback retry hint used only when the provider supplied none."""
    if int(status) == 429:
        return 30
    if int(status) in {408, 500, 502, 503, 504}:
        return 5
    if int(status) == 524:
        return 15
    return None


@router.post("/chat", response_model=ChatResponse)
async def client_chat(
    request: ChatRequest,
    authorization: str = Header(None, alias="Authorization"),
    x_user_id: str = Header(None, alias="X-User-ID"),
    x_client_id: str = Header(None, alias="X-Client-ID"),
    x_aicoder_keepalive: str = Header("", alias="X-AICoder-Keepalive"),
):
    """
    Chat-Endpoint für AILinux Client

    Tier-Routing:
    - GUEST: Ollama only, kein MCP, 50k/Tag
    - REGISTERED: Ollama + Free-Quota-Provider, MCP ✓, 100k/Tag
    - PRO: Alle Modelle, Ollama ∞, Cloud 250k/Tag
    - ENTERPRISE: Alle Modelle unlimited

    Headers (Priorität):
        1. Authorization: Bearer <JWT-Token> (aus /auth/login)
        2. X-User-ID: User-Email oder ID
        3. Ohne Header = Guest
    """
    if str(x_aicoder_keepalive or "").strip().lower() in {"1", "true", "json"}:
        async def stream_json_keepalive():
            started = time.monotonic()
            keepalive_count = 0
            requested_model = str(request.model or "default")
            logger.info("chat_keepalive_start model=%s", requested_model)
            task = asyncio.create_task(client_chat(
                request, authorization, x_user_id, x_client_id, ""
            ))
            try:
                while not task.done():
                    # asyncio.wait_for(await task) re-raises a provider exception here,
                    # before the structured HTTPException handler below can serialize it.
                    # Poll task completion without consuming its result/exception; the
                    # post-loop await is the single place that converts success/failure
                    # into the final JSON envelope.
                    done, _pending = await asyncio.wait({task}, timeout=15.0)
                    if done:
                        break
                    keepalive_count += 1
                    elapsed = time.monotonic() - started
                    logger.info(
                        "chat_keepalive_tick model=%s count=%d elapsed=%.1fs provider_pending=%s",
                        requested_model, keepalive_count, elapsed, not task.done(),
                    )
                    # JSON permits insignificant leading whitespace. Sending it
                    # periodically keeps reverse proxies from treating a long
                    # provider inference as an idle origin connection.
                    yield (b" " * 2048) + b"\n"
                try:
                    result = await task
                    logger.info(
                        "chat_keepalive_complete model=%s keepalives=%d elapsed=%.1fs",
                        requested_model, keepalive_count, time.monotonic() - started,
                    )
                    if isinstance(result, BaseModel):
                        payload = result.model_dump(mode="json")
                    elif isinstance(result, dict):
                        payload = result
                    else:
                        payload = {"response": str(result)}
                    yield json.dumps(payload, ensure_ascii=False).encode("utf-8")
                except HTTPException as exc:
                    logger.warning(
                        "chat_keepalive_http_error model=%s status=%s keepalives=%d elapsed=%.1fs",
                        requested_model, exc.status_code, keepalive_count, time.monotonic() - started,
                    )
                    detail_retry_after = (
                        exc.detail.get("retry_after")
                        if isinstance(exc.detail, dict) else None
                    )
                    payload = {
                        "error": {
                            "status": int(exc.status_code),
                            "detail": exc.detail,
                            "retryable": int(exc.status_code) in {408, 429, 500, 502, 503, 504, 524},
                            "retry_after": (
                                int(detail_retry_after)
                                if isinstance(detail_retry_after, (int, float)) and detail_retry_after > 0
                                else _default_provider_retry_after(int(exc.status_code))
                            ),
                        }
                    }
                    yield json.dumps(payload, ensure_ascii=False).encode("utf-8")
                except Exception as exc:
                    logger.exception(
                        "Streaming chat failed model=%s keepalives=%d elapsed=%.1fs",
                        requested_model, keepalive_count, time.monotonic() - started,
                    )
                    payload = {
                        "error": {
                            "status": 500,
                            "detail": f"{type(exc).__name__}: {exc}",
                            "retryable": True,
                        }
                    }
                    yield json.dumps(payload, ensure_ascii=False).encode("utf-8")
            finally:
                if not task.done():
                    logger.warning(
                        "chat_keepalive_cancel model=%s keepalives=%d elapsed=%.1fs",
                        requested_model, keepalive_count, time.monotonic() - started,
                    )
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

        return StreamingResponse(
            stream_json_keepalive(),
            media_type="application/json",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    start_time = datetime.now()

    # User-ID ermitteln (Token hat Priorität)
    user_id, tier = get_user_and_tier_from_headers(authorization, x_user_id)
    logger.debug(f"Chat request: user={user_id}, auth={'yes' if authorization else 'no'}")


    # Messages bauen — unterstützt OpenAI messages[] und Legacy message.
    # Native tool-call continuation fields must survive this hop verbatim;
    # dropping tool_calls/tool_call_id creates an invalid provider history.
    if request.messages:
        messages = [m.model_dump(exclude_none=True) for m in request.messages]
    elif request.message:
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.message})
    else:
        raise HTTPException(400, "Either 'message' or 'messages' is required")

    for item in messages:
        _validate_chat_content(item.get("content"))

    # Model bestimmen
    model = request.model or get_default_model(tier)

    # === GUEST: Ollama + NVIDIA NIM, no MCP tools ===
    if tier == UserTier.GUEST:
        if not is_guest_free_model(model):
            logger.warning("Guest requested unavailable model %s; using local fallback", model)
            model = LOCAL_FALLBACK_MODEL
        limit_check = tier_service.check_token_limit(user_id, model)
        if not limit_check["allowed"]:
            raise HTTPException(429, f"Token-Limit erreicht ({limit_check['limit']}/Tag)")
        if model.startswith("ollama/") or tier_service.is_ollama_model(model):
            if not model.startswith("ollama/"):
                model = f"ollama/{model}"
            result = await call_ollama(
                model=model,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
            backend = "ollama"
        else:
            result = await call_registered_model(
                model=model,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                tools=None,
                tool_choice="none",
            )
            backend = model.split("/", 1)[0]

    # === REGISTERED: Ollama Cloud/local + configured free-quota providers ===
    elif tier == UserTier.REGISTERED:
        if not is_registered_free_model(model):
            logger.warning("Registered user requested non-free model %s; using local fallback", model)
            model = LOCAL_FALLBACK_MODEL
        limit_check = tier_service.check_token_limit(user_id, model)
        if not limit_check["allowed"]:
            raise HTTPException(429, f"Token-Limit erreicht ({limit_check['limit']}/Tag)")
        if model.startswith("ollama/") or tier_service.is_ollama_model(model):
            if not model.startswith("ollama/"):
                model = f"ollama/{model}"
            result = await call_ollama(
                model=model,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                tools=request.tools,
                tool_choice=request.tool_choice,
            )
            backend = "ollama"
        else:
            result = await call_registered_model(
                model=model,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                tools=request.tools,
                tool_choice=request.tool_choice,
            )
            backend = model.split("/", 1)[0]

    # === PRO / ENTERPRISE: all configured chat providers ===
    else:
        if model.startswith("ollama/") or tier_service.is_ollama_model(model):
            if not model.startswith("ollama/"):
                model = f"ollama/{model}"
            result = await call_ollama(
                model=model,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                tools=request.tools,
                tool_choice=request.tool_choice,
            )
            backend = "ollama"
        else:
            if tier != UserTier.ENTERPRISE:
                limit_check = tier_service.check_token_limit(user_id, model)
                if not limit_check["allowed"]:
                    raise HTTPException(429, f"Token-Limit erreicht ({limit_check['limit']}/Tag). Nutze Ollama-Modelle für unlimited.")
            result = await call_registered_model(
                model=model,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                tools=request.tools,
                tool_choice=request.tool_choice,
            )
            backend = model.split("/", 1)[0] if "/" in model else "registry"

    # Response extrahieren (text plus provider-native tool calls)
    response_message = result.get("choices", [{}])[0].get("message", {})
    response_text = response_message.get("content", "") or ""
    tool_calls = response_message.get("tool_calls") or []
    tokens = result.get("usage", {}).get("total_tokens")
    latency = int((datetime.now() - start_time).total_seconds() * 1000)
    fallback_used = result.get("is_fallback", False)
    
    # Bei Fallback: Model aktualisieren
    if fallback_used:
        model = f"ollama/{result.get('model_used', 'ministral-3:14b')}"

    # Prüfen ob Ollama unlimited (Pro/Enterprise mit Ollama-Modell)
    is_ollama = tier_service.is_ollama_model(model) or backend == "ollama"
    is_unlimited = (tier in (UserTier.PRO, UserTier.ENTERPRISE) and is_ollama) or tier == UserTier.ENTERPRISE

    # Tokens tracken - NUR bei erfolgreicher Operation und NICHT für unlimited Ollama
    if tokens and user_id != "anonymous" and (response_text or tool_calls):
        # Pro mit Ollama = nicht tracken (unlimited)
        if not (tier == UserTier.PRO and is_ollama):
            tier_service.track_tokens(user_id, tokens, model)

    return ChatResponse(
        response=response_text,
        model=model,
        tier=tier.value,
        backend=backend,
        tool_calls=tool_calls,
        tokens_used=tokens if not is_unlimited else None,  # Nicht anzeigen wenn unlimited
        tokens_unlimited=is_unlimited,
        latency_ms=latency,
        fallback_used=fallback_used,
        tool_transport=result.get("tool_transport", "native" if request.tools else "none"),
        finish_reason=((result.get("choices") or [{}])[0].get("finish_reason") if isinstance((result.get("choices") or [{}])[0], dict) else None),
        provider_diagnostics=(result.get("provider_diagnostics") if isinstance(result.get("provider_diagnostics"), dict) else {}),
    )


@router.get("/models", response_model=ModelsResponse)
async def get_client_models(
    authorization: str = Header(None, alias="Authorization"),
    x_user_id: str = Header(None, alias="X-User-ID")
):
    """
    Hole verfügbare Modelle für den Client

    - Guest: verifizierte Ollama-Modelle + NVIDIA NIM Chat-Modelle
    - Registered: Ollama + konfigurierte Free-Quota-Modelle
    - Pro/Enterprise: alle konfigurierten Chat-Modelle + Ollama
    
    Headers (Priorität):
        1. Authorization: Bearer <JWT-Token>
        2. X-User-ID: User-Email
    """
    user_id, tier = get_user_and_tier_from_headers(authorization, x_user_id)
    logger.debug(f"Models request: user={user_id}")
    
    config = tier_service.get_tier_info(tier)

    if tier == UserTier.GUEST:
        all_models = await registry.list_models()
        free_cloud = availability_service.filter_available(
            guest_free_model_ids(all_models)
        )
        models = list(FREE_MODELS_OLLAMA)
        models.extend(model for model in free_cloud if model not in models)
        backend = "mixed-free"
    elif tier == UserTier.REGISTERED:
        all_models = await registry.list_models()
        free_cloud = availability_service.filter_available(
            registered_free_model_ids(all_models)
        )
        models = list(FREE_MODELS_OLLAMA)
        models.extend(model for model in free_cloud if model not in models)
        backend = "mixed-free"
    else:
        all_models = await registry.list_models()
        all_ids = chat_capable_model_ids(all_models)
        models = availability_service.filter_available(all_ids)
        for om in FREE_MODELS_OLLAMA:
            if om not in models:
                models.append(om)
        backend = "mixed"

    all_models = await registry.list_models()
    by_id = {entry.id: entry for entry in all_models}
    model_details = []
    for model_id in models:
        entry = by_id.get(model_id)
        if entry is not None:
            model_details.append(entry.to_dict())
        else:
            provider = model_id.split("/", 1)[0] if "/" in model_id else "other"
            model_details.append({
                "id": model_id,
                "provider": provider,
                "capabilities": ["chat"],
                "roles": ["assistant"],
                "api_method": "generateContent",
            })

    return ModelsResponse(
        tier=tier.value,
        tier_name=config["name"],
        model_count=len(models),
        models=models,
        model_details=model_details,
        backend=backend,
        upgrade_available=(tier in (UserTier.GUEST, UserTier.REGISTERED))
    )


@router.get("/tier")
async def get_client_tier(
    authorization: str = Header(None, alias="Authorization"),
    x_user_id: str = Header(None, alias="X-User-ID")
):
    """
    Hole Tier-Info für den aktuellen User
    
    Headers (Priorität):
        1. Authorization: Bearer <JWT-Token>
        2. X-User-ID: User-Email
    """
    user_id, tier = get_user_and_tier_from_headers(authorization, x_user_id)
    info = tier_service.get_tier_info(tier)
    info["backend"] = (
        "ollama" if tier == UserTier.GUEST
        else "mixed-free" if tier == UserTier.REGISTERED
        else "mixed"
    )
    info["user_id"] = user_id  # Für Debug
    return info


@router.post("/analyze")
async def analyze_file(
    content: str,
    filename: str,
    action: str = "analyze",
    authorization: str = Header(None, alias="Authorization"),
    x_user_id: str = Header(None, alias="X-User-ID")
):
    """
    Datei-Analyse via KI

    Actions: analyze, bugs, optimize, summarize, document, security
    """
    user_id, tier = get_user_and_tier_from_headers(authorization, x_user_id)

    prompts = {
        "analyze": f"Analysiere diese Datei '{filename}' gründlich. Erkläre was sie tut, die Struktur und wichtige Teile:\n\n```\n{content[:8000]}\n```",
        "bugs": f"Finde Bugs, Fehler und potenzielle Probleme in '{filename}':\n\n```\n{content[:8000]}\n```",
        "optimize": f"Optimiere '{filename}'. Zeige verbesserten Code mit Erklärungen:\n\n```\n{content[:8000]}\n```",
        "summarize": f"Fasse '{filename}' kurz zusammen:\n\n```\n{content[:8000]}\n```",
        "document": f"Erstelle Dokumentation für '{filename}':\n\n```\n{content[:8000]}\n```",
        "security": f"Security-Check für '{filename}':\n\n```\n{content[:8000]}\n```",
    }

    prompt = prompts.get(action, prompts["analyze"])
    model = get_default_model(tier)
    messages = [{"role": "user", "content": prompt}]

    # Free: Ollama, Pro+: OpenRouter
    if tier == UserTier.GUEST:
        result = await call_ollama(model, messages)
        backend = "ollama"
    else:
        result = await call_openrouter(normalize_openrouter_model(model), messages)
        backend = "openrouter"

    response_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")

    return {
        "action": action,
        "filename": filename,
        "model": model,
        "tier": tier.value,
        "backend": backend,
        "result": response_text
    }


@router.get("/ollama/status")
async def ollama_status():
    """Prüfe Ollama Backend Status"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if response.status_code == 200:
                data = response.json()
                models = [m["name"] for m in data.get("models", [])]
                return {
                    "status": "online",
                    "url": OLLAMA_BASE_URL,
                    "models_loaded": len(models),
                    "models": models[:20]  # Max 20 anzeigen
                }
    except Exception as e:
        logger.error(f"Ollama Status Check failed: {e}")

    return {
        "status": "offline",
        "url": OLLAMA_BASE_URL,
        "models_loaded": 0,
        "models": []
    }


# ========= MODEL AVAILABILITY ROUTES =========

@router.get("/models/availability")
async def get_model_availability():
    """
    Zeige Model-Availability Status
    - Excluded Models (Quota/Rate-Limit)  
    - Provider Health
    """
    return await availability_service.run_health_check()


@router.post("/models/availability/reset/{model_id:path}")
async def reset_model_availability(model_id: str):
    """Reset Availability-Status für ein Model (Admin)"""
    availability_service.reset_model(model_id)
    return {"reset": model_id, "status": "ok"}


@router.post("/models/availability/exclude")
async def exclude_model(model_id: str, reason: str = "manual"):
    """Manuell ein Model excluden (Admin)"""
    availability_service.add_exclusion(model_id, reason)
    return {"excluded": model_id, "reason": reason}


# ========= TOKEN MANAGEMENT ROUTES =========

@router.post("/tokens/reset/{user_id}")
async def reset_user_tokens(user_id: str):
    """Reset Token-Usage für einen User (Admin)"""
    result = tier_service.reset_token_usage(user_id)
    return result


@router.get("/tokens/usage/{user_id}")
async def get_user_token_usage(user_id: str):
    """Hole Token-Verbrauch für einen User"""
    return tier_service.get_token_usage(user_id)


@router.get("/tokens/usage")
async def get_current_user_token_usage(
    authorization: str = Header(None, alias="Authorization"),
    x_user_id: str = Header(None, alias="X-User-ID")
):
    """Hole eigenen Token-Verbrauch"""
    user_id, tier = get_user_and_tier_from_headers(authorization, x_user_id)
    return tier_service.get_token_usage(user_id)
