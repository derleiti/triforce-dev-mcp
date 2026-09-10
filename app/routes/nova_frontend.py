from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel

# BUG-005 FIX 2026-03-10: prefix /v1 wird jetzt konsistent über main.py gesetzt
import os as _os_nf

def _require_nova_auth(x_internal_key: str = Header(default="")):
    expected = _os_nf.environ.get("INTERNAL_API_KEY", "")
    if not expected or x_internal_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

router = APIRouter(prefix="/frontend/dashboard", tags=["nova-frontend"], dependencies=[Depends(_require_nova_auth)])
# Public sub-router ohne Auth — nur für /models (WP Plugin braucht das ohne Key)
public_router = APIRouter(prefix="/frontend/dashboard", tags=["nova-frontend-public"])
logger = logging.getLogger("ailinux.nova_frontend")

CATEGORY_ORDER = {
    "chat": 0,
    "vision": 1,
    "media_image": 2,
    "media_video": 3,
    "audio": 4,
    "ocr": 5,
    "embedding": 6,
    "code": 7,
    "reasoning": 8,
}

PROVIDER_ORDER = {
    "chat": ["openai", "anthropic", "gemini", "mistral", "groq", "cerebras", "cohere", "openrouter", "ollama", "cloudflare", "github", "together", "fireworks"],
    "vision": ["openai", "anthropic", "gemini", "mistral", "cohere", "openrouter", "ollama", "cloudflare", "github", "together", "fireworks"],
    "media_image": ["openai", "gemini", "cloudflare", "openrouter", "together", "fireworks", "replicate", "huggingface"],
    "media_video": ["openai", "gemini", "cloudflare", "openrouter", "replicate"],
    "audio": ["openai", "gemini", "groq", "mistral", "cloudflare"],
    "ocr": ["mistral", "cohere", "openai", "gemini"],
    "embedding": ["openai", "cohere", "mistral", "gemini", "cloudflare", "fireworks"],
}


def _primary_category(item: Dict[str, Any]) -> str:
    cats = item.get("categories") or []
    return min(cats, key=lambda c: CATEGORY_ORDER.get(c, 99), default="chat")


def _model_sort_key(item: Dict[str, Any]) -> tuple[int, int, str]:
    category = _primary_category(item)
    providers = PROVIDER_ORDER.get(category, [])
    provider = str(item.get("provider") or "other")
    provider_rank = providers.index(provider) if provider in providers else 99
    return (CATEGORY_ORDER.get(category, 99), provider_rank, str(item.get("name") or item.get("id") or "").lower())

def _base_url() -> str:
    return (
        os.getenv("TRIFORCE_PUBLIC_BASE_URL")
        or os.getenv("TRIFORCE_BASE_URL")
        or "http://127.0.0.1:9000"
    ).rstrip("/")

def _openai_key() -> Optional[str]:
    return os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY") or os.getenv("CHATGPT_API_KEY")

def _pick(d: Dict[str, Any], *keys: str, default=None):
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return default


def _lower_values(value: Any) -> set[str]:
    if value in (None, "", []):
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(v).lower() for v in value if v not in (None, "")}
    return {str(value).lower()}


def _openrouter_has_image_output(model: Dict[str, Any]) -> bool:
    arch = model.get("architecture") if isinstance(model.get("architecture"), dict) else {}
    outputs = set()
    outputs |= _lower_values(arch.get("output_modalities"))
    outputs |= _lower_values(arch.get("outputModalities"))
    outputs |= _lower_values(model.get("output_modalities"))
    outputs |= _lower_values(model.get("outputModalities"))
    return "image" in outputs


def _blocked_openrouter_image_model(model_id: str) -> bool:
    mid = str(model_id or "").lower().removeprefix("openrouter/")
    return mid in {"black-forest-labs/flux.2-pro"}

class ChatRequest(BaseModel):
    model: str
    # Accept single-turn (message: str) OR multi-turn (messages: list) from JS
    message: str = ""
    messages: Optional[List[Dict[str, Any]]] = None
    system: str = ""
    temperature: float = 0.4
    max_tokens: int = 1200

class VisionRequest(BaseModel):
    model: str
    prompt: str
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    mime_type: str = "image/png"
    temperature: float = 0.2
    max_tokens: int = 1200

class ImageRequest(BaseModel):
    model: str
    prompt: str
    size: str = "1024x1024"
    quality: str = "medium"
    n: int = 1

class VideoRequest(BaseModel):
    model: str
    prompt: str
    seconds: int = 8
    size: str = "1280x720"


def _parse_size(size: str, default: tuple[int, int] = (1024, 1024)) -> tuple[int, int]:
    try:
        width, height = str(size or "").lower().split("x", 1)
        return int(width), int(height)
    except Exception:
        return default


def _provider(model: Dict[str, Any]) -> str:
    raw = str(_pick(model, "provider", "owned_by", "vendor", default="")).lower()
    mid = str(_pick(model, "id", "model", "name", default="")).lower()

    # ID-Prefix hat höchste Priorität (verhindert falsche Klassifizierung)
    # z.B. openrouter/google/gemini-* → openrouter, nicht google
    if mid.startswith("openrouter/"):
        return "openrouter"
    if mid.startswith("cloudflare/") or mid.startswith("@cf/") or mid.startswith("cf/"):
        return "cloudflare"
    if mid.startswith("github/"):
        return "github"
    if mid.startswith("openai/"):
        return "openai"
    if mid.startswith("groq/"):
        return "groq"
    if mid.startswith("gemini/") or mid.startswith("google/"):
        return "gemini"
    if mid.startswith("mistral/"):
        return "mistral"
    if mid.startswith("anthropic/") or mid.startswith("claude"):
        return "anthropic"
    if mid.startswith("ollama/"):
        return "ollama"

    # Fallback: raw provider field
    for known in ("openai", "anthropic", "google", "gemini", "mistral", "groq", "cloudflare", "cerebras", "cohere", "openrouter", "ollama", "github", "together", "fireworks"):
        if known in raw:
            return "gemini" if known == "google" else known
    if "openai" in raw or mid.startswith("gpt-") or "dall-e" in mid or "gpt-image" in mid or "sora" in mid:
        return "openai"
    if "anthropic" in raw or "claude" in mid:
        return "anthropic"
    if "google" in raw or "gemini" in mid or "imagen" in mid or "veo" in mid:
        return "google"
    if "mistral" in raw or "pixtral" in mid or "ministral" in mid:
        return "mistral"
    if "groq" in raw:
        return "groq"
    if "cloudflare" in raw:
        return "cloudflare"
    if "cerebras" in raw:
        return "cerebras"
    if "openrouter" in raw:
        return "openrouter"
    if "ollama" in raw:
        return "ollama"
    return raw or "unknown"

def _categorize(model: Dict[str, Any]) -> Dict[str, Any]:
    mid = str(_pick(model, "id", "model", "name", default=""))
    name = str(_pick(model, "name", "display_name", default=mid))
    provider = _provider(model)
    blob = json.dumps(model, default=str).lower() + " " + mid.lower() + " " + name.lower()

    caps = {
        "chat": False,
        "vision": False,
        "media_image": False,
        "media_video": False,
        "audio": False,
        "embedding": False,
        "ocr": False,
        "code": False,
        "reasoning": False,
        "backend_supported": False,
    }

    if any(x in blob for x in ["gpt-", "claude", "gemini", "mistral", "ministral", "pixtral", "llama", "qwen", "deepseek", "command-r", "chat"]):
        caps["chat"] = True

    if any(x in blob for x in [
            "vision", "image understanding", "image-to-text", "multimodal",
            # Mistral: pixtral + all current mistral models have vision
            "pixtral",
            # OpenAI: gpt-4o, gpt-5 (all have vision)
            "gpt-4o", "gpt-5",
            # Anthropic: all claude-3+ have vision
            "claude-3", "claude-4", "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
            # Google: all gemini models
            "gemini",
            # Meta: llama-3.2-vision + llama-4-scout
            "llama-3.2-11b-vision", "llama-3.2-90b-vision", "llama-4-scout",
    ]):
        caps["vision"] = True
    # Mistral: mistral-small/medium/large all support vision natively since 2025
    if provider == "mistral" and any(x in blob for x in ["mistral-small", "mistral-medium", "mistral-large"]):
        caps["vision"] = True
    # Vision-Blacklist: Modelle die zwar "gemini" im Namen haben, aber KEIN vision-chat können
    _vision_blacklist = [
        "native-audio", "-tts", "preview-tts", "-embed", "embedding",
        "resnet", "computer-use", "deep-research", "audio-preview",
        "flash-image", "pro-image", "nano-banana", "imagen",  # Bildgenerierung ≠ Vision-Chat
    ]
    if any(x in blob for x in _vision_blacklist):
        caps["vision"] = False

    # image_gen: check capabilities list from model_registry output
    caps_list = model.get("capabilities", [])
    if not isinstance(caps_list, list):
        caps_list = []
    caps_set = {str(c).lower() for c in caps_list}
    if "audio" in caps_set or any(x in blob for x in ["audio", "whisper", "transcribe", "tts", "voxtral", "realtime"]):
        caps["audio"] = True
    if "embedding" in caps_set or "embed" in blob:
        caps["embedding"] = True
    if "ocr" in caps_set or "document ai" in blob or "ocr" in blob:
        caps["ocr"] = True
    if "code" in caps_set or any(x in blob for x in ["codex", "codestral", "devstral", "coder", "qwen3-coder"]):
        caps["code"] = True
    if "reasoning" in caps_set or any(x in blob for x in ["reasoning", "thinking", "gpt-oss", "magistral", " o3", "/o3", "o4-mini"]):
        caps["reasoning"] = True
    # Exclude img2img, inpainting, audio and other non-text2image models
    _img_blacklist = ["img2img", "inpainting", "deepgram", "whisper", "aura-", "melotts",
                      "reranker", "embed", "stt", "tts", "transcribe", "guard", "classification",
                      "dreamshaper"]
    _is_blacklisted = any(x in blob for x in _img_blacklist)
    # OpenRouter: image gen works via /chat/completions with modalities: ["image","text"]
    # → DO NOT blacklist OpenRouter image models
    if not _is_blacklisted and ("image_gen" in caps_set or any(x in blob for x in [
            # OpenAI image models
            "gpt-image", "gpt-5-image", "dall-e",
            # Google image models
            "imagen", "flash-image", "nano-banana", "gemini-3-pro-image", "gemini-3.1-flash-image",
            # Flux / Stability
            "flux", "stable diffusion", "sdxl", "lucid-origin", "phoenix",
            # OpenRouter additional image models
            "seedream", "riverflow", "sourceful",
            # Generic markers
            "image generation", "text-to-image", "image_gen",
    ])):
        caps["media_image"] = True

    if provider == "openrouter" and caps["media_image"] and not _openrouter_has_image_output(model):
        caps["media_image"] = False

    if "video_gen" in caps_set or any(x in blob for x in ["sora", "veo", "video generation", "text-to-video", "video_gen", "hailuo"]):
        caps["media_video"] = True

    if caps["media_image"] or caps["media_video"] or caps["audio"] or caps["embedding"] or caps["ocr"]:
        caps["chat"] = caps["chat"] and not (caps["media_image"] or caps["media_video"] or caps["embedding"])

    if provider in {"openai", "anthropic", "google", "gemini", "mistral", "groq", "cloudflare", "cerebras", "cohere", "openrouter", "ollama", "github", "together", "fireworks"}:
        caps["backend_supported"] = any(caps[k] for k in ("chat", "vision", "media_image", "media_video", "audio", "ocr", "embedding"))

    return {
        "id": mid,
        "name": name,
        "provider": provider,
        **caps,
        "categories": [k for k in ("chat", "vision", "media_image", "media_video", "audio", "ocr", "embedding", "code", "reasoning") if caps[k]],
    }

async def _get_models() -> List[Dict[str, Any]]:
    urls = [
        f"{_base_url()}/v1/models/all",
        f"{_base_url()}/v1/models?include_unavailable=true",
        f"{_base_url()}/models?include_unavailable=true",
        f"{_base_url()}/v1/tristar/models",
    ]
    async with httpx.AsyncClient(timeout=15.0) as client:
        for url in urls:
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                data = r.json()
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict)]
                if isinstance(data, dict):
                    for k in ("models", "data", "items", "available_models"):
                        if isinstance(data.get(k), list):
                            return [x for x in data[k] if isinstance(x, dict)]
            except Exception:
                pass
    return []

async def _chat_proxy(payload: Dict[str, Any]) -> Dict[str, Any]:
    urls = [
        f"{_base_url()}/v1/chat/completions",
        f"{_base_url()}/chat/completions",
    ]
    last_error = None
    async with httpx.AsyncClient(timeout=120.0) as client:
        for url in urls:
            try:
                r = await client.post(url, json=payload)
                if r.status_code == 200:
                    return r.json()
                # Non-200 but valid JSON → return error to frontend (not 502)
                try:
                    err_data = r.json()
                    last_error = err_data
                except Exception:
                    last_error = {"error": {"message": f"Backend HTTP {r.status_code}", "code": "backend_error"}}
            except Exception as e:
                last_error = {"error": {"message": str(e), "code": "proxy_error"}}
    # Return last error as JSON instead of raising 502
    if last_error:
        return last_error
    raise HTTPException(status_code=502, detail="chat proxy failed")

async def _vision_proxy(model: str, prompt: str, image_url: Optional[str], image_base64: Optional[str], mime_type: str) -> Dict[str, Any]:
    """
    Route vision requests to the real /v1/images/analyze endpoint.
    - URL input  -> GET/POST /v1/images/analyze  (JSON body)
    - Base64 input -> POST /v1/images/analyze/upload (multipart)
    Does NOT tunnel through the chat endpoint (avoids content: str validator crash).
    """
    base = _base_url()

    if image_url:
        payload = {"model": model, "image_url": image_url, "prompt": prompt}
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{base}/v1/images/analyze", json=payload)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail=r.text)
            return r.json()

    # Base64 path — multipart upload
    if not image_base64:
        raise HTTPException(status_code=400, detail="image_url or image_base64 required")

    try:
        image_bytes = base64.b64decode(image_base64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64: {exc}")

    # BUG-FIX: detect actual MIME type from magic bytes (browser can lie)
    def _detect_mime(data: bytes) -> str:
        if data[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if data[:4] == b"\x89PNG":
            return "image/png"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return mime_type  # fallback to declared type

    actual_mime = _detect_mime(image_bytes)
    ext = actual_mime.split("/")[-1] if "/" in actual_mime else "png"
    filename = f"upload.{ext}"

    async with httpx.AsyncClient(timeout=120.0) as client:
        files = {"image_file": (filename, image_bytes, actual_mime)}
        data = {"model": model, "prompt": prompt}
        r = await client.post(f"{base}/v1/images/analyze/upload", files=files, data=data)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()

async def _image_fallback(req: ImageRequest, exclude_prefixes: tuple[str, ...] = ()) -> Optional[Dict[str, Any]]:
    """Try configured image providers when a selected provider is unavailable."""
    candidates: list[str] = []
    if os.getenv("GOOGLE_AI_STUDIO_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_GEMINI_KEY"):
        candidates.append("gemini/gemini-2.5-flash-image")
    if _openai_key():
        candidates.append("openai/gpt-image-1-mini")
    if os.getenv("OPENROUTER_API_KEY"):
        candidates.extend([
            "openrouter/google/gemini-2.5-flash-image",
            "openrouter/openai/gpt-5-image-mini",
        ])
    if os.getenv("REPLICATE_API_KEY"):
        candidates.append("replicate/black-forest-labs/flux-schnell")

    tried_errors: list[str] = []
    for model in candidates:
        if any(model.startswith(prefix) for prefix in exclude_prefixes):
            continue
        try:
            fallback_req = ImageRequest(
                model=model,
                prompt=req.prompt,
                size=req.size,
                quality=req.quality,
                n=req.n,
            )
            result = await _image_proxy(fallback_req)
            result["fallback_from"] = req.model
            return result
        except HTTPException as exc:
            tried_errors.append(f"{model}: {exc.detail}")
        except Exception as exc:
            tried_errors.append(f"{model}: {exc}")
    if tried_errors:
        logger.warning("Image fallback failed after %d attempt(s): %s", len(tried_errors), tried_errors[:3])
    return None

async def _image_proxy(req: ImageRequest) -> Dict[str, Any]:
    """
    Multi-provider image generation:
    - OpenAI (gpt-image, dall-e) -> OpenAI API direct
    - Replicate (replicate/... or bare owner/name like black-forest-labs/flux-*) -> Replicate Predictions
    - Gemini (imagen, nano-banana, flash-image, pro-image) -> Gemini API
    - Cloudflare (@cf/...) -> Workers AI
    - OpenRouter (openrouter/...) -> OpenRouter chat with image modality
    - HuggingFace (hf/...) -> HF Inference API
    - Bare 'owner/name' without prefix -> assume Replicate (frontend strips prefix)
    - Otherwise -> internal /v1/txt2img (ComfyUI legacy path, will 503 if not configured)
    """
    m = req.model.lower()

    # Frontend-ID normalization: WP plugin's PHP mapper splits 'provider/...' on
    # the first slash and uses the right side as 'name', which the JS then
    # apparently sends back as the model ID. Result: '@cf/...' instead of
    # 'cloudflare/@cf/...', or 'black-forest-labs/...' instead of 'replicate/...'.
    # We compensate by re-prefixing here.

    # Cloudflare Workers AI: bare '@cf/...' → 'cloudflare/@cf/...'
    if m.startswith("@cf/"):
        logger.info("Normalizing bare CF model '%s' to 'cloudflare/%s'", req.model, req.model)
        req = ImageRequest(model=f"cloudflare/{req.model}", prompt=req.prompt,
                           size=req.size, quality=req.quality, n=req.n)
        m = req.model.lower()

    # Replicate: 'owner/name' without known provider prefix → 'replicate/owner/name'
    _known_prefixes = ("replicate/", "cloudflare/", "openrouter/", "hf/",
                       "gemini/", "openai/", "anthropic/", "ollama/",
                       "mistral/", "groq/", "cerebras/", "github/", "@cf/")
    if "/" in m and not any(m.startswith(p) for p in _known_prefixes) \
            and "gpt-image" not in m and "dall-e" not in m:
        logger.info("Normalizing bare model '%s' to 'replicate/%s'", req.model, req.model)
        req = ImageRequest(
            model=f"replicate/{req.model}",
            prompt=req.prompt,
            size=req.size,
            quality=req.quality,
            n=req.n,
        )
        m = req.model.lower()

    # OpenAI direct path
    if "gpt-image" in m or "dall-e" in m:
        key = _openai_key()
        if not key:
            raise HTTPException(status_code=400, detail="OPENAI_API_KEY missing for OpenAI image generation")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        payload = {"model": req.model, "prompt": req.prompt, "size": req.size, "quality": req.quality, "n": req.n}
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post("https://api.openai.com/v1/images/generations", headers=headers, json=payload)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail=r.text)
            return {"ok": True, "mode": "media_image", "provider": "openai", "result": r.json()}

    # ── Replicate image generation (FLUX, SDXL) ──
    if m.startswith("replicate/"):
        from ..config import get_settings as _gs
        _s = _gs()
        rep_key = _s.replicate_api_key
        if not rep_key:
            raise HTTPException(status_code=503, detail="Replicate API key not configured")
        rep_model = m.replace("replicate/", "", 1)
        # Map size to aspect_ratio for FLUX models
        _SIZE_TO_AR = {"1024x1024": "1:1", "1792x1024": "16:9", "1024x1792": "9:16", "512x512": "1:1"}
        rep_payload: dict = {"input": {"prompt": req.prompt}}
        if "flux" in rep_model.lower():
            rep_payload["input"]["aspect_ratio"] = _SIZE_TO_AR.get(req.size, "1:1")
            rep_payload["input"]["num_outputs"] = min(req.n, 4)
            # jpg works for all FLUX variants (Schnell/Dev/Pro/Pro Ultra);
            # webp is rejected by flux-1.1-pro-ultra (HTTP 422 enum mismatch).
            rep_payload["input"]["output_format"] = "jpg"
            rep_payload["input"]["output_quality"] = 90 if req.quality == "hd" else 80
        else:
            # SDXL / other models use width/height
            parts = req.size.split("x")
            if len(parts) == 2:
                rep_payload["input"]["width"] = int(parts[0])
                rep_payload["input"]["height"] = int(parts[1])
            rep_payload["input"]["num_outputs"] = min(req.n, 4)
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"https://api.replicate.com/v1/models/{rep_model}/predictions",
                headers={"Authorization": f"Bearer {rep_key}", "Content-Type": "application/json", "Prefer": "wait=60"},
                json=rep_payload,
            )
            if r.status_code not in (200, 201):
                raise HTTPException(status_code=r.status_code, detail=r.text)
            rdata = r.json()
            # Poll if not yet done
            if rdata.get("status") in ("starting", "processing"):
                import asyncio
                get_url = rdata.get("urls", {}).get("get", "")
                for _ in range(60):
                    await asyncio.sleep(2)
                    pr = await client.get(get_url, headers={"Authorization": f"Bearer {rep_key}"})
                    pd = pr.json()
                    if pd.get("status") == "succeeded":
                        rdata = pd
                        break
                    elif pd.get("status") in ("failed", "canceled"):
                        raise HTTPException(status_code=500, detail=f"Replicate {pd['status']}: {pd.get('error')}")
            output = rdata.get("output", [])
            if isinstance(output, str):
                output = [output]
            images = [{"url": u} for u in output if isinstance(u, str) and u.startswith("http")]
            return {"ok": True, "mode": "media_image", "provider": "replicate", "result": {"data": images}}

    # Gemini native image generation (gemini-2.5-flash-image = "nano banana" — FREE tier 500 RPD)
    # Uses generateContent with responseModalities=["image","text"]
    _GEMINI_NATIVE_IMG = ("gemini/gemini-2.5-flash-image", "gemini/gemini-3-pro-image", "gemini/gemini-3.1-flash-image", "gemini/gemini-3.1-pro-image", "gemini/nano-banana")
    # gemini-3.1-pro-image via :predict (Imagen), but gemini-3.1-flash-image via generateContent (free)
    if any(m.startswith(p) for p in _GEMINI_NATIVE_IMG):
        from ..services.google_genai import resolve_ai_studio_key
        gemini_key = resolve_ai_studio_key()
        if not gemini_key:
            raise HTTPException(status_code=400, detail="GOOGLE_AI_STUDIO_KEY missing for Gemini native image")
        gemini_model = req.model[len("gemini/"):]
        # Map pixel size to aspect ratio string for imageConfig
        _aspect_map2 = {
            "512x512": "1:1", "768x768": "1:1", "1024x1024": "1:1",
            "640x360": "16:9", "1280x720": "16:9", "1920x1080": "16:9",
            "640x480": "4:3", "800x600": "4:3", "1024x768": "4:3",
            "480x640": "3:4", "600x800": "3:4", "768x1024": "3:4",
            "360x640": "9:16", "480x854": "9:16", "720x1280": "9:16", "1080x1920": "9:16",
        }
        aspect_ratio = _aspect_map2.get(req.size or "", "1:1")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent"
        payload_native = {
            "contents": [{"parts": [{"text": req.prompt}]}],
            "generationConfig": {
                "responseModalities": ["image", "text"],
                "candidateCount": min(req.n or 1, 4),
                "imageConfig": {"aspectRatio": aspect_ratio},
            },
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(url, headers={"x-goog-api-key": gemini_key}, json=payload_native)
            if r.status_code >= 400:
                try:
                    err_msg = r.json().get("error", {}).get("message", r.text)
                except Exception:
                    err_msg = r.text or f"HTTP {r.status_code}"
                err_lower = err_msg.lower()
                if any(k in err_lower for k in ("paid", "upgrade", "billing", "permission", "quota", "resource exhausted")):
                    raise HTTPException(status_code=429,
                        detail="Gemini Free-Tier-Limit vorübergehend erreicht (500 Requests/Tag via AI Studio). Bitte in einigen Minuten erneut versuchen oder Cloudflare Flux nutzen.")
                raise HTTPException(status_code=r.status_code, detail=f"Gemini native image error: {err_msg}")
            rdata = r.json()
            result_images = []
            for cand in rdata.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    idata = part.get("inlineData") or part.get("inline_data")
                    if idata and idata.get("data"):
                        result_images.append({"b64_json": idata["data"], "content_type": idata.get("mimeType", "image/png")})
            if not result_images:
                logger.error("Gemini native image returned no image payload: %s", str(rdata)[:300])
                raise HTTPException(status_code=500, detail="Gemini native image generation failed")
            return {"ok": True, "mode": "media_image", "provider": "google_native",
                    "result": {"data": result_images}}

    # Gemini Imagen path
    if m.startswith("gemini/imagen"):
        from ..services.google_genai import resolve_ai_studio_key
        gemini_key = resolve_ai_studio_key()
        if not gemini_key:
            raise HTTPException(status_code=400, detail="GOOGLE_AI_STUDIO_KEY missing for Imagen")
        gemini_model = req.model[len("gemini/"):]
        aspect_map = {
            # 1:1 Square
            "512x512": "1:1", "768x768": "1:1", "1024x1024": "1:1",
            # 16:9 Widescreen
            "640x360": "16:9", "1280x720": "16:9", "1920x1080": "16:9",
            # 4:3 Standard
            "640x480": "4:3", "800x600": "4:3", "1024x768": "4:3",
            # 3:4 Portrait
            "480x640": "3:4", "600x800": "3:4", "768x1024": "3:4",
            # 9:16 Smartphone
            "360x640": "9:16", "480x854": "9:16", "720x1280": "9:16", "1080x1920": "9:16",
        }
        aspect_ratio = aspect_map.get(req.size, "1:1")
        # Imagen 3/4 uses :predict endpoint with Vertex-style payload
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:predict"
        payload_gemini = {
            "instances": [{"prompt": req.prompt}],
            "parameters": {"sampleCount": req.n, "aspectRatio": aspect_ratio},
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(url, headers={"x-goog-api-key": gemini_key}, json=payload_gemini)
            if r.status_code >= 400:
                try:
                    err_msg = r.json().get("error", {}).get("message", r.text)
                except Exception:
                    err_msg = r.text or f"HTTP {r.status_code}"
                # Paid plan gate — give friendly message
                if "paid" in err_msg.lower() or "upgrade" in err_msg.lower() or "billing" in err_msg.lower():
                    raise HTTPException(status_code=402,
                        detail="Gemini Imagen erfordert einen bezahlten Google AI-Plan. Bitte upgraden unter https://ai.dev")
                raise HTTPException(status_code=r.status_code, detail=f"Gemini Imagen error: {err_msg}")
            rdata = r.json()
            # :predict returns predictions[].bytesBase64Encoded
            predictions = rdata.get("predictions") or []
            result_images = []
            for pred in predictions:
                b64 = pred.get("bytesBase64Encoded") or pred.get("image", {}).get("imageBytes") or pred.get("imageBytes", "")
                if b64:
                    result_images.append({"b64_json": b64, "content_type": "image/png"})
            if not result_images:
                logger.error("Gemini Imagen returned no image payload: %s", str(rdata)[:300])
                raise HTTPException(status_code=500, detail="Gemini Imagen generation failed")
            return {"ok": True, "mode": "media_image", "provider": "google",
                    "result": {"data": result_images}}

    # ── Cloudflare Workers AI image path ──────────────────────────────────────
    if m.startswith("cloudflare/@cf/"):
        import os as _os
        cf_account = _os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
        # Workers-AI-spezifischer Token (cfut_…) hat Vorrang — der generische
        # CLOUDFLARE_API_TOKEN ist oft ein Global API Key (37-char hex), der
        # für /ai/run mit Bearer-Auth nicht akzeptiert wird (CF Code 10000).
        cf_token = (
            _os.getenv("CLOUDFLARE_AI_WORKERS_API")
            or _os.getenv("CLOUDFLARE_API_TOKEN")
            or ""
        ).strip()
        if not cf_account or not cf_token:
            raise HTTPException(status_code=503, detail="Cloudflare Workers AI nicht konfiguriert (CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_AI_WORKERS_API fehlen)")
        # Modell-ID ohne "cloudflare/" prefix
        cf_model = req.model[len("cloudflare/"):]  # z.B. @cf/black-forest-labs/flux-1-schnell
        cf_url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/{cf_model}"
        # CF Workers AI: POST {"prompt": ..., "num_steps": 4}
        # Size → width/height
        _w, _h = (1024, 1024)
        if req.size and "x" in req.size:
            try:
                _parts = req.size.split("x")
                _w, _h = int(_parts[0]), int(_parts[1])
            except Exception:
                pass
        # Flux-2 models require multipart/form-data, older models use JSON
        _cf_multipart = any(x in cf_model for x in ["flux-2-dev", "flux-2-klein"])
        cf_headers = {"Authorization": f"Bearer {cf_token}"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            if _cf_multipart:
                # Multipart form-data for Flux-2 models
                _form_data = {"prompt": req.prompt, "width": str(_w), "height": str(_h)}
                if "flux-2-dev" in cf_model:
                    _form_data["steps"] = "25"
                r = await client.post(cf_url, data=_form_data, headers=cf_headers)
            else:
                # JSON for older models (flux-1-schnell, SDXL, phoenix, lucid-origin)
                cf_payload = {"prompt": req.prompt, "num_steps": 8, "width": _w, "height": _h}
                cf_headers["Content-Type"] = "application/json"
                r = await client.post(cf_url, json=cf_payload, headers=cf_headers)
            if r.status_code >= 400:
                try:
                    err_body = r.json()
                    err_msg = str(err_body.get("errors", err_body))[:200]
                except Exception:
                    err_msg = r.text[:200]
                if r.status_code in (401, 403) or "authentication" in err_msg.lower() or "'code': 10000" in err_msg:
                    fallback = await _image_fallback(req, exclude_prefixes=("cloudflare/", "@cf/"))
                    if fallback:
                        return fallback
                raise HTTPException(status_code=r.status_code,
                    detail=f"Cloudflare Workers AI Fehler: {err_msg}")
            # CF returns raw PNG bytes OR JSON depending on model
            import base64 as _b64
            ct = r.headers.get("content-type", "")
            if "json" in ct:
                # Some CF models return JSON: {"result":{"image":"base64..."}} or {"image":"..."}
                try:
                    jbody = r.json()
                    raw_b64 = (jbody.get("result", {}) or {}).get("image") or jbody.get("image", "")
                    if not raw_b64:
                        # Try: result is list or nested
                        preds = jbody.get("result", jbody)
                        if isinstance(preds, list) and preds:
                            raw_b64 = preds[0].get("image", "")
                    b64_data = raw_b64
                except Exception:
                    b64_data = _b64.b64encode(r.content).decode()
            else:
                # Raw PNG bytes (Flux, SDXL etc.)
                b64_data = _b64.b64encode(r.content).decode()
            return {"ok": True, "mode": "media_image", "provider": "cloudflare",
                    "result": {"data": [{"b64_json": b64_data, "content_type": "image/png"}]}}

    # ── OpenRouter image generation path ──────────────────────────────────────
    # OpenRouter uses /chat/completions with modalities: ["image", "text"]
    if m.startswith("openrouter/"):
        import os as _os
        or_key = _os.getenv("OPENROUTER_API_KEY", "")
        if not or_key:
            raise HTTPException(status_code=503, detail="OpenRouter API Key fehlt (OPENROUTER_API_KEY)")
        or_model = req.model[len("openrouter/"):]
        if _blocked_openrouter_image_model(or_model):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"OpenRouter-Modell '{or_model}' ist in Nova nicht als Bildgenerator verfuegbar, "
                    "weil OpenRouter dafuer keinen Image-Output-Endpunkt anbietet. "
                    "Bitte Gemini Imagen, Cloudflare Flux, Replicate Flux oder OpenAI gpt-image waehlen."
                ),
            )
        _or_aspect_map = {
            "512x512": "1:1", "768x768": "1:1", "1024x1024": "1:1",
            "640x360": "16:9", "1280x720": "16:9", "1920x1080": "16:9",
            "640x480": "4:3", "800x600": "4:3", "1024x768": "4:3",
            "480x640": "3:4", "600x800": "3:4", "768x1024": "3:4",
            "360x640": "9:16", "480x854": "9:16", "720x1280": "9:16", "1080x1920": "9:16",
        }
        or_aspect = _or_aspect_map.get(req.size or "", "1:1")
        or_payload = {
            "model": or_model,
            "messages": [{"role": "user", "content": req.prompt}],
            "modalities": ["image", "text"],
            "image_config": {"aspect_ratio": or_aspect},
        }
        or_headers = {
            "Authorization": f"Bearer {or_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://ailinux.me",
            "X-Title": "AILinux Nova",
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post("https://openrouter.ai/api/v1/chat/completions", json=or_payload, headers=or_headers)
            if r.status_code >= 400:
                try:
                    err_data = r.json()
                    err_msg = err_data.get("error", {}).get("message", "") or str(err_data)[:300]
                except Exception:
                    err_msg = r.text[:300]
                err_lower = err_msg.lower()
                if any(k in err_lower for k in ("credit", "quota", "billing", "insufficient", "limit", "exceeded")):
                    raise HTTPException(status_code=402,
                        detail="OpenRouter API-Limit erreicht. Bitte API-Credits aufladen unter https://openrouter.ai/credits")
                raise HTTPException(status_code=r.status_code, detail=f"OpenRouter image error: {err_msg}")
            rdata = r.json()
            import base64 as _b64, re as _re
            result_images = []
            for choice in rdata.get("choices", []):
                msg = choice.get("message", {})
                # OpenRouter returns images in message.images[] (separate from content)
                for img_block in msg.get("images", []):
                    if img_block.get("type") == "image_url":
                        img_url = img_block.get("image_url", {})
                        url_str = img_url.get("url", "") if isinstance(img_url, dict) else str(img_url)
                        if url_str.startswith("data:image"):
                            b64_part = url_str.split(",", 1)[1] if "," in url_str else ""
                            if b64_part:
                                # Extract mime type from data URL
                                _mime = "image/png"
                                if url_str.startswith("data:image/"):
                                    _mime = url_str.split(";")[0][5:]  # "data:image/png" -> "image/png"
                                result_images.append({"b64_json": b64_part, "content_type": _mime})
                        elif url_str.startswith("http"):
                            try:
                                img_resp = await client.get(url_str, timeout=30.0)
                                if img_resp.status_code == 200 and "image" in img_resp.headers.get("content-type", ""):
                                    result_images.append({"b64_json": _b64.b64encode(img_resp.content).decode(),
                                                          "content_type": img_resp.headers.get("content-type", "image/png").split(";")[0]})
                            except Exception:
                                pass
                content = msg.get("content", "")
                # Content can be array of content blocks (image_url type)
                if isinstance(content, list):
                    for block in content:
                        btype = block.get("type", "")
                        # image_url format: {"type":"image_url","image_url":{"url":"data:image/..."}}
                        if btype == "image_url":
                            img_url = block.get("image_url", {})
                            url_str = img_url.get("url", "") if isinstance(img_url, dict) else str(img_url)
                            if url_str.startswith("data:image"):
                                b64_part = url_str.split(",", 1)[1] if "," in url_str else ""
                                if b64_part:
                                    result_images.append({"b64_json": b64_part, "content_type": "image/png"})
                            elif url_str.startswith("http"):
                                # External URL — fetch and convert to b64
                                try:
                                    img_resp = await client.get(url_str, timeout=30.0)
                                    if img_resp.status_code == 200 and "image" in img_resp.headers.get("content-type", ""):
                                        result_images.append({"b64_json": _b64.b64encode(img_resp.content).decode(),
                                                              "content_type": img_resp.headers.get("content-type", "image/png").split(";")[0]})
                                except Exception:
                                    pass
                        # inline_data format (Gemini-style via OR): {"type":"inline_data","inline_data":{"mime_type":"...","data":"..."}}
                        elif btype == "inline_data":
                            idata = block.get("inline_data", {})
                            if idata.get("data"):
                                result_images.append({"b64_json": idata["data"], "content_type": idata.get("mime_type", "image/png")})
                # Content can be string with embedded data URLs
                elif isinstance(content, str) and "data:image" in content:
                    for match in _re.finditer(r'data:image/(\w+);base64,([A-Za-z0-9+/=]+)', content):
                        result_images.append({"b64_json": match.group(2), "content_type": f"image/{match.group(1)}"})
            if not result_images:
                # Log full response for debugging
                logger.warning("OpenRouter image: no images found. Status=%d Response=%s", r.status_code, str(rdata)[:800])
                # Check if response indicates insufficient credits
                usage = rdata.get("usage", {})
                err_hint = ""
                if not rdata.get("choices"):
                    err_hint = " Keine choices in Antwort — moeglicherweise unzureichende Credits."
                raise HTTPException(status_code=500,
                    detail=f"OpenRouter: Kein Bild in der Antwort.{err_hint} Modell unterstuetzt moeglicherweise keine Bildgenerierung oder API-Credits sind leer.")
            return {"ok": True, "mode": "media_image", "provider": "openrouter",
                    "result": {"data": result_images}}

    # ── HuggingFace Inference API image path ──────────────────────────────────
    if m.startswith("hf/"):
        import os as _os
        hf_key = _os.getenv("HUGGINGFACE_API_KEY", "") or _os.getenv("HF_TOKEN", "")
        if not hf_key:
            raise HTTPException(status_code=503, detail="HuggingFace API Key fehlt (HUGGINGFACE_API_KEY)")
        hf_model = req.model[len("hf/"):]  # e.g. black-forest-labs/FLUX.1-schnell
        hf_url = f"https://router.huggingface.co/hf-inference/models/{hf_model}"
        hf_headers = {"Authorization": f"Bearer {hf_key}"}
        hf_payload = {"inputs": req.prompt}
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(hf_url, json=hf_payload, headers=hf_headers)
            if r.status_code >= 400:
                try:
                    err_data = r.json()
                    err_msg = err_data.get("error", str(err_data))[:300]
                except Exception:
                    err_msg = r.text[:300]
                raise HTTPException(status_code=r.status_code, detail=f"HuggingFace Fehler: {err_msg}")
            # HF Inference API returns raw image bytes (PNG/JPEG)
            import base64 as _b64
            ct = r.headers.get("content-type", "")
            if "image" in ct:
                b64_data = _b64.b64encode(r.content).decode()
                return {"ok": True, "mode": "media_image", "provider": "huggingface",
                        "result": {"data": [{"b64_json": b64_data, "content_type": ct.split(";")[0]}]}}
            # Some models return JSON with base64
            try:
                jdata = r.json()
                if isinstance(jdata, list) and jdata:
                    img = jdata[0].get("image") or jdata[0].get("generated_image", "")
                    if img:
                        return {"ok": True, "mode": "media_image", "provider": "huggingface",
                                "result": {"data": [{"b64_json": img, "content_type": "image/png"}]}}
            except Exception:
                pass
            raise HTTPException(status_code=500, detail="HuggingFace: Unerwartetes Antwortformat")

    # Internal image path. ComfyUI/A1111 txt2img is obsolete; route all
    # remaining server-side generation through the cloud-provider endpoint.
    base = _base_url()
    width, height = _parse_size(req.size)
    payload = {
        "prompt": req.prompt,
        "model": req.model,
        "width": width,
        "height": height,
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(f"{base}/v1/images/generate", json=payload)
        if r.status_code == 200:
            return {"ok": True, "mode": "media_image", "provider": "internal", "result": r.json()}
        raise HTTPException(
            status_code=503,
            detail=f"Bild-Generierung für Modell '{req.model}' nicht verfügbar. Bitte ein anderes Modell wählen."
        )

async def _video_proxy(req: VideoRequest) -> Dict[str, Any]:
    """
    Multi-provider video generation:
    - OpenAI Sora -> OpenAI API direct
    - Others      -> unsupported (honest error)
    """
    m = req.model.lower()

    if "sora" in m:
        key = _openai_key()
        if not key:
            raise HTTPException(status_code=400, detail="OPENAI_API_KEY missing for Sora")
        # FIX 2026-06-04: OpenAI /v1/videos verlangt JSON (kein form-urlencoded);
        # seconds als int (nicht str), size als "WxH"-String.
        # FIX 2026-06-04: "openai/" prefix entfernen — API kennt nur "sora-2", "sora-2-pro" etc.
        openai_model = req.model.split("/", 1)[-1] if "/" in req.model else req.model
        # FIX 2026-06-04: seconds MUSS string sein ("4"|"8"|"12"), nicht int.
        # OpenAI Sora-2 lehnt int explicit ab: "expected one of '4', '8', or '12'".
        seconds_str = str(int(req.seconds))
        if seconds_str not in ("4", "8", "12"):
            seconds_str = "4"  # closest valid default
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        payload = {"model": openai_model, "prompt": req.prompt, "seconds": seconds_str, "size": req.size}
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post("https://api.openai.com/v1/videos", headers=headers, json=payload)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail=r.text)
            return {"ok": True, "mode": "media_video", "provider": "openai", "result": r.json()}

    # Gemini Veo — Google AI API (long-running operation + polling)
    if "veo" in m:
        from ..services.google_genai import resolve_ai_studio_key
        key = resolve_ai_studio_key()
        if not key:
            raise HTTPException(status_code=400, detail="GOOGLE_AI_STUDIO_KEY fehlt für Gemini Veo")
        size_clean = req.size.replace("\u00d7", "x").replace("×", "x").replace(" ", "")
        parts = size_clean.split("x")
        width, height = (int(parts[0]), int(parts[1])) if len(parts) == 2 else (1280, 720)
        payload = {
            "model": req.model,
            "prompt": req.prompt,
            "config": {
                "durationSeconds": req.seconds,
                "aspectRatio": f"{width}:{height}",
                "numberOfVideos": 1,
            }
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{req.model}:generateVideo",
                headers={"x-goog-api-key": key},
                json=payload
            )
            try:
                rdata = r.json()
            except Exception:
                rdata = {}
            if r.status_code >= 400:
                err_msg = rdata.get("error", {}).get("message", "") or r.text[:300]
                # Leeres 404 = Endpoint nicht verfügbar (free tier) oder Modell nicht erreichbar
                if not err_msg.strip() or r.status_code == 404:
                    raise HTTPException(
                        status_code=402,
                        detail="Gemini Veo ist auf dem kostenlosen Google AI Studio Plan nicht verfügbar. Bitte upgraden unter https://ai.dev"
                    )
                if any(x in err_msg.lower() for x in ["paid", "billing", "upgrade", "quota", "permission"]):
                    raise HTTPException(
                        status_code=402,
                        detail="Gemini Veo erfordert einen bezahlten Google AI-Plan. Bitte upgraden unter https://ai.dev"
                    )
                raise HTTPException(status_code=r.status_code, detail=f"Veo API Fehler: {err_msg}")
            op_name = rdata.get("name", "")
            if not op_name:
                logger.error("Veo returned no operation name: %s", str(rdata)[:300])
                raise HTTPException(status_code=500, detail="Veo video generation failed")
            import asyncio as _asyncio
            for _ in range(24):  # 24 × 5s = 120s
                await _asyncio.sleep(5)
                poll = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/{op_name}",
                    headers={"x-goog-api-key": key}
                )
                poll_data = poll.json() if poll.status_code == 200 else {}
                if poll_data.get("done"):
                    resp_data = poll_data.get("response", {})
                    videos = resp_data.get("generatedVideos", [])
                    if not videos:
                        logger.error("Veo returned no generated videos: %s", str(poll_data)[:300])
                        raise HTTPException(status_code=500, detail="Veo video generation failed")
                    video_uri = videos[0].get("video", {}).get("uri", "")
                    if not video_uri:
                        logger.error("Veo returned no video URI: %s", str(poll_data)[:300])
                        raise HTTPException(status_code=500, detail="Veo video generation failed")
                    dl = await client.get(video_uri, headers={"x-goog-api-key": key})
                    import base64 as _b64
                    b64 = _b64.b64encode(dl.content).decode()
                    return {
                        "ok": True, "mode": "media_video", "provider": "google",
                        "result": {"data": [{"b64_json": b64, "content_type": "video/mp4"}]}
                    }
            raise HTTPException(status_code=504, detail="Veo: Timeout beim Polling (>120s)")

    raise HTTPException(
        status_code=400,
        detail=f"video generation not wired for model: {req.model}. Supported: sora (OpenAI)."
    )

@router.get("/health")
async def health() -> Dict[str, Any]:
    models = await _get_models()
    return {"ok": True, "status": "ok", "model_count": len(models)}

@router.get("/config")
async def config() -> Dict[str, Any]:
    return {
        "ok": True,
        "status": "ok",
        "routes": {
            "health": "/v1/frontend/dashboard/health",
            "models": "/v1/frontend/dashboard/models",
            "chat": "/v1/frontend/dashboard/chat",
            "vision": "/v1/frontend/dashboard/vision",
            "media_image": "/v1/frontend/dashboard/media/image",
            "media_video": "/v1/frontend/dashboard/media/video",
        }
    }

@public_router.get("/models")
async def models(response: Response) -> Dict[str, Any]:
    # Tell CF + browser to keep this fresh — model list changes whenever
    # a provider key is added/removed or discovery picks up new models.
    # 60s edge cache + must-revalidate keeps the dashboard responsive
    # without staleness blocking new providers from showing up.
    response.headers["Cache-Control"] = "public, max-age=60, must-revalidate"
    raw = await _get_models()
    items = [_categorize(m) for m in raw]
    # Paused-flag entfernt 2026-04-20 — Anthropic API-Pause lief am 2026-04-01 aus
    items.sort(key=lambda x: (not x["backend_supported"], *_model_sort_key(x)))
    return {
        "ok": True,
        "count": len(items),
        "categories": {
            "chat": [m for m in items if m["chat"]],
            "vision": [m for m in items if m["vision"]],
            "media_image": [m for m in items if m["media_image"]],
            "media_video": [m for m in items if m["media_video"]],
            "audio": [m for m in items if m["audio"]],
            "ocr": [m for m in items if m["ocr"]],
            "embedding": [m for m in items if m["embedding"]],
            "code": [m for m in items if m["code"]],
            "reasoning": [m for m in items if m["reasoning"]],
        },
        "models": items,
    }

@router.post("/chat")
async def chat(req: ChatRequest) -> Dict[str, Any]:
    # Build messages: prefer req.messages[] (multi-turn from JS), fall back to req.message string
    if req.messages:
        messages = list(req.messages)
        # Prepend system prompt if provided and not already in messages
        if req.system.strip() and (not messages or messages[0].get("role") != "system"):
            messages.insert(0, {"role": "system", "content": req.system.strip()})
    else:
        if not req.message:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="Either 'message' or 'messages' required")
        messages = []
        if req.system.strip():
            messages.append({"role": "system", "content": req.system.strip()})
        messages.append({"role": "user", "content": req.message})
    raw = await _chat_proxy({
        "model": req.model,
        "messages": messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "stream": False,
    })
    # Normalize response: extract text from raw
    text = None
    if isinstance(raw, dict):
        text = (raw.get("text") or raw.get("content") or raw.get("response")
                or (raw.get("choices") or [{}])[0].get("message", {}).get("content"))
    return {"ok": True, "mode": "chat", "content": text or str(raw), "raw": raw}

@router.post("/vision")
async def vision(req: VisionRequest) -> Dict[str, Any]:
    if not req.image_url and not req.image_base64:
        raise HTTPException(status_code=400, detail="image_url or image_base64 required")
    result = await _vision_proxy(req.model, req.prompt, req.image_url, req.image_base64, req.mime_type)
    return {"ok": True, "mode": "vision", "raw": result}


@router.post("/vision-upload")
async def vision_upload(
    model: str = Form(""),
    prompt: str = Form("Beschreibe dieses Bild detailliert."),
    image_file: UploadFile = File(...),
) -> Dict[str, Any]:
    """Multipart file upload endpoint for vision analysis."""
    import base64 as _b64
    raw_bytes = await image_file.read()
    mime = image_file.content_type or "image/jpeg"
    if mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        mime = "image/jpeg"
    b64 = _b64.b64encode(raw_bytes).decode()
    mdl = model or "gemini/gemini-2.5-flash"
    result = await _vision_proxy(mdl, prompt, None, b64, mime)
    return {"ok": True, "mode": "vision", "raw": result}

@router.post("/media/image")
async def media_image(req: ImageRequest) -> Dict[str, Any]:
    """Generate an image and return it in MULTIPLE formats so any frontend JS
    schema (legacy ComfyUI-style, OpenAI-style, dashboard-style) finds the
    image data on first try.

    Output keys (all populated):
    - ok / mode / provider / result.data[]    – canonical dashboard format
    - images[]   – array of "data:image/png;base64,..." URLs (legacy ComfyUI shape)
    - urls[]     – same as images[] (some JS pipelines look for this name)
    - data[]     – top-level mirror of result.data[] (OpenAI-style)
    - url        – first image URL (single-image case)
    """
    proxy_result = await _image_proxy(req)
    data_blocks = (proxy_result.get("result", {}) or {}).get("data", []) or []
    data_urls: List[str] = []
    for item in data_blocks:
        if not isinstance(item, dict):
            continue
        if item.get("b64_json"):
            ct = item.get("content_type", "image/png") or "image/png"
            data_urls.append(f"data:{ct};base64,{item['b64_json']}")
        elif item.get("url"):
            try:
                async with httpx.AsyncClient(timeout=60.0) as _cli:
                    _r = await _cli.get(item["url"])
                    if _r.status_code == 200:
                        _ct = _r.headers.get("content-type", "image/png").split(";")[0]
                        _b64 = base64.b64encode(_r.content).decode("utf-8")
                        data_urls.append(f"data:{_ct};base64,{_b64}")
                    else:
                        logger.warning("media_image: failed to fetch %s (HTTP %d)",
                                       item["url"], _r.status_code)
            except Exception as _exc:
                logger.warning("media_image: image URL fetch failed: %s", _exc)

    # Multi-format response so any JS schema finds the data
    proxy_result["images"] = data_urls
    proxy_result["urls"] = data_urls
    proxy_result["data"] = data_blocks
    if data_urls:
        proxy_result["url"] = data_urls[0]
    return proxy_result

@router.post("/media/video")
async def media_video(req: VideoRequest) -> Dict[str, Any]:
    return await _video_proxy(req)

@router.get("/downloads")
async def downloads() -> Dict[str, Any]:
    """Browsable folder tree from WordPress downloads directory.
    BUG-FIX 2026-03-11: was only scanning client-deploy/*.deb — now returns full tree with all filetypes.
    Supports custom descriptions via download_descriptions.json.
    """
    import os as _os, hashlib, mimetypes as _mt, json as _json, datetime as _dt

    WP_DOWNLOADS   = "/home/zombie/triforce/docker/wordpress/html/downloads"
    DESCRIPTIONS_F = "/home/zombie/triforce/docker/wordpress/html/wp-content/plugins/nova-ai-frontend/config/download_descriptions.json"
    BASE_URL       = "https://ailinux.me/downloads"

    desc_map: Dict[str, str] = {}
    try:
        with open(DESCRIPTIONS_F) as _df:
            desc_map = _json.load(_df)
    except Exception:
        pass

    TYPE_ICONS = {
        "deb":"📦","rpm":"📦","apk":"📱","exe":"🖥️","msi":"🖥️",
        "tar":"🗜️","gz":"🗜️","zip":"🗜️","xz":"🗜️","7z":"🗜️","bz2":"🗜️",
        "mp4":"🎬","mkv":"🎬","avi":"🎬","mov":"🎬","webm":"🎬",
        "mp3":"🎵","flac":"🎵","wav":"🎵","ogg":"🎵",
        "iso":"💿","img":"💿",
        "pdf":"📄","txt":"📝","md":"📝",
        "sh":"⚙️","py":"⚙️","js":"⚙️",
    }

    def _fmt(n: int) -> str:
        for unit in ["B","KB","MB","GB"]:
            if n < 1024:
                return f"{n:.1f} {unit}"
            n //= 1024
        return f"{n:.1f} TB"

    def _sha1(fp: str, max_mb: int = 2) -> str:
        h = hashlib.sha1()
        try:
            with open(fp,"rb") as _f:
                h.update(_f.read(max_mb * 1024 * 1024))
        except Exception:
            return ""
        return h.hexdigest()

    def _scan(dir_path: str, rel_base: str = "") -> Dict[str, Any]:
        files: list = []
        folders: list = []
        try:
            names = sorted(_os.listdir(dir_path))
        except Exception:
            return {"files": [], "folders": []}
        for name in names:
            if name.startswith("."):
                continue
            full = _os.path.join(dir_path, name)
            rel  = f"{rel_base}/{name}" if rel_base else name
            if _os.path.isdir(full):
                sub = _scan(full, rel)
                total_sub = sum(f["size"] for f in sub["files"])
                for sf in sub["folders"]:
                    total_sub += sf.get("total_size", 0)
                folders.append({
                    "name": name, "path": rel,
                    "description": desc_map.get(f"folder:{rel}", ""),
                    "icon": "📁",
                    "file_count": len(sub["files"]),
                    "total_size": total_sub,
                    "total_size_formatted": _fmt(total_sub),
                    "files": sub["files"],
                    "folders": sub["folders"],
                })
            elif _os.path.isfile(full):
                try:
                    stat = _os.stat(full)
                except Exception:
                    continue
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                mime = _mt.guess_type(name)[0] or "application/octet-stream"
                files.append({
                    "name": name, "path": rel,
                    "size": stat.st_size,
                    "size_formatted": _fmt(stat.st_size),
                    "type": ext,
                    "mime": mime,
                    "icon": TYPE_ICONS.get(ext, "📄"),
                    "modified": _dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
                    "sha1": _sha1(full),
                    "url": f"{BASE_URL}/{rel}",
                    "description": desc_map.get(f"file:{rel}", ""),
                })
        return {"files": files, "folders": folders}

    if not _os.path.isdir(WP_DOWNLOADS):
        return {"ok": False, "error": "downloads directory not found", "files": [], "folders": [], "total_bytes": 0}

    tree = _scan(WP_DOWNLOADS)
    total_bytes = sum(f["size"] for f in tree["files"])
    for folder in tree["folders"]:
        total_bytes += folder.get("total_size", 0)

    return {
        "ok": True,
        "files": tree["files"],
        "folders": tree["folders"],
        "total_bytes": total_bytes,
        "total_formatted": _fmt(total_bytes),
    }

@router.get("/downloads/descriptions")
async def downloads_descriptions_get() -> Dict[str, Any]:
    """Get all download descriptions for admin editing."""
    DESCRIPTIONS_F = "/home/zombie/triforce/docker/wordpress/html/wp-content/plugins/nova-ai-frontend/config/download_descriptions.json"
    import json as _json
    try:
        with open(DESCRIPTIONS_F) as f:
            return {"ok": True, "descriptions": _json.load(f)}
    except Exception:
        return {"ok": True, "descriptions": {}}

@router.post("/downloads/descriptions")
async def downloads_descriptions_set(data: Dict[str, Any]) -> Dict[str, Any]:
    """Save download descriptions."""
    DESCRIPTIONS_F = "/home/zombie/triforce/docker/wordpress/html/wp-content/plugins/nova-ai-frontend/config/download_descriptions.json"
    import json as _json, os as _os
    try:
        descs = data.get("descriptions", {})
        _os.makedirs(_os.path.dirname(DESCRIPTIONS_F), exist_ok=True)
        with open(DESCRIPTIONS_F, "w") as f:
            _json.dump(descs, f, indent=2, ensure_ascii=False)
        return {"ok": True}
    except Exception as e:
        logger.error("Saving download descriptions failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e
