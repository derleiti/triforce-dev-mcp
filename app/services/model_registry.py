from __future__ import annotations

import asyncio
import logging
import json
import re
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import httpx
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..config import get_settings
from ..utils.http_client import HttpClient

logger = logging.getLogger("ailinux.model_registry")


VISION_PATTERN = re.compile(r"(llava|vision|vl|moondream|llama-vision|bakllava|pixtral|minicpm|qwen3-vl)", re.IGNORECASE)
IMAGE_GEN_PATTERN = re.compile(r"(flux|stable-diffusion|sd-|sdxl|dall-e|dalle|gpt-image|imagen|image-gen|text-to-image)", re.IGNORECASE)
VIDEO_GEN_PATTERN = re.compile(r"(veo|video-gen|sora)", re.IGNORECASE)
AUDIO_PATTERN = re.compile(r"(audio|tts|transcribe|voxtral|whisper|orpheus)", re.IGNORECASE)
CODE_PATTERN = re.compile(r"(codestral|devstral|code|coder)", re.IGNORECASE)
EMBEDDING_PATTERN = re.compile(r"(embed)", re.IGNORECASE)
REASONING_PATTERN = re.compile(r"(thinking|reason|magistral|o1|o3)", re.IGNORECASE)
MODERATION_PATTERN = re.compile(r"(moderation|safety|guard)", re.IGNORECASE)
OCR_PATTERN = re.compile(r"(ocr|document)", re.IGNORECASE)

OPENROUTER_FREE_ROUTER = "openrouter/openrouter/free"
OPENROUTER_AUTO_ROUTER = "openrouter/openrouter/auto"


PROVIDER_SORT_ORDER = {
    "openai": 0,
    "anthropic": 1,
    "gemini": 2,
    "mistral": 3,
    "groq": 4,
    "cerebras": 5,
    "nvidia": 6,
    "cohere": 7,
    "openrouter": 8,
    "kimi": 9,
    "ollama": 10,
    "cloudflare": 10,
    "together": 11,
    "fireworks": 12,
    "github": 13,
    "huggingface": 14,
}

CAPABILITY_SORT_ORDER = {
    "chat": 0,
    "reasoning": 1,
    "code": 2,
    "vision": 3,
    "image_gen": 4,
    "video_gen": 5,
    "audio": 6,
    "ocr": 7,
    "embedding": 8,
    "rerank": 9,
    "moderation": 10,
}

# Gemini models to exclude (experimental, deprecated, or specialized)
GEMINI_EXCLUDE_PATTERN = re.compile(r"(aqa|attribution|legacy|tunedModels)", re.IGNORECASE)

SENSITIVE_QUERY_KEYS = {
    "key",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "auth",
    "authorization",
    "client_secret",
    "secret",
}


def redact_url(value: object) -> str:
    """Return a log-safe URL/string with credential query parameters redacted."""
    raw = str(value)
    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw
    if not parsed.query:
        return raw

    redacted_query = urlencode(
        [
            (key, "[REDACTED]" if key.lower() in SENSITIVE_QUERY_KEYS else val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        ],
        doseq=True,
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, redacted_query, parsed.fragment))


def safe_http_error(exc: httpx.HTTPError) -> str:
    """Format httpx errors without leaking API keys embedded in request URLs."""
    request = getattr(exc, "request", None)
    response = getattr(exc, "response", None)
    method = getattr(request, "method", None) if request else None
    url = redact_url(getattr(request, "url", "")) if request else ""
    status_code = getattr(response, "status_code", None) if response else None

    parts = [exc.__class__.__name__]
    if status_code is not None:
        parts.append(f"status={status_code}")
    if method or url:
        parts.append(f"request={method or 'UNKNOWN'} {url}".strip())
    return " | ".join(parts)

# Model role mapping based on capabilities
CAPABILITY_TO_ROLE = {
    "chat": "assistant",
    "vision": "vision_analyst",
    "image_gen": "image_generator",
    "video_gen": "video_generator",
    "audio": "audio_processor",
    "code": "code_assistant",
    "embedding": "embedder",
    "reasoning": "reasoning_engine",
    "moderation": "content_moderator",
    "ocr": "document_reader",
    "function_calling": "tool_user",
}


@dataclass(slots=True)
class ModelInfo:
    id: str
    provider: str
    capabilities: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)
    api_method: str = "generateContent"  # generateContent, predict, predictLongRunning
    availability: str = "unknown"        # ok | quota_exceeded | rate_limited | unauthorised | not_found | error
    capability_source: str = "heuristic"  # declared | probed | heuristic

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "provider": self.provider,
            "capabilities": self.capabilities,
            "roles": self.roles,
            "api_method": self.api_method,
            "availability": self.availability,
            "capability_source": self.capability_source,
        }

    @property
    def primary_role(self) -> str:
        """Return the primary role based on capabilities."""
        if not self.roles:
            return "assistant"
        # Prioritize specialized roles
        priority = ["video_generator", "image_generator", "code_assistant", "vision_analyst", "assistant"]
        for role in priority:
            if role in self.roles:
                return role
        return self.roles[0]


def detect_capabilities(model_name: str, supported_methods: List[str] = None) -> tuple[List[str], List[str], str]:
    """Detect capabilities, roles, and API method from model name and supported methods."""
    capabilities = []
    roles = []
    api_method = "generateContent"

    name_lower = model_name.lower()

    # Check supported generation methods first
    if supported_methods:
        if "generateContent" in supported_methods:
            capabilities.append("chat")
        if "embedContent" in supported_methods:
            capabilities.append("embedding")
        if "predict" in supported_methods:
            api_method = "predict"
        if "predictLongRunning" in supported_methods:
            api_method = "predictLongRunning"

    # Pattern-based detection
    if VIDEO_GEN_PATTERN.search(name_lower):
        capabilities.append("video_gen")
        api_method = "predictLongRunning"
    if IMAGE_GEN_PATTERN.search(name_lower):
        capabilities.append("image_gen")
        if "predict" not in (api_method or ""):
            api_method = "predict"
    if VISION_PATTERN.search(name_lower):
        capabilities.append("vision")
    if AUDIO_PATTERN.search(name_lower):
        capabilities.append("audio")
    if CODE_PATTERN.search(name_lower):
        capabilities.append("code")
    if EMBEDDING_PATTERN.search(name_lower):
        capabilities.append("embedding")
    if REASONING_PATTERN.search(name_lower):
        capabilities.append("reasoning")
    if MODERATION_PATTERN.search(name_lower):
        capabilities.append("moderation")
    if OCR_PATTERN.search(name_lower):
        capabilities.append("ocr")
        capabilities.append("vision")

    # Default to chat if no specific capability detected
    if not capabilities:
        capabilities.append("chat")

    # Map capabilities to roles
    for cap in capabilities:
        role = CAPABILITY_TO_ROLE.get(cap)
        if role and role not in roles:
            roles.append(role)

    return list(set(capabilities)), roles, api_method


class ModelRegistry:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._lock = asyncio.Lock()
        self._cache: List[ModelInfo] | None = None
        self._cache_expiry: float = 0.0
        self._ttl_seconds: float = 300.0  # Increased from 30s to 5m to reduce API load
        self._refresh_task: asyncio.Task | None = None
        self._refresh_interval: float = 3600.0
        self._sd_discovery_disabled: bool = False
        self._sd_discovery_warned: bool = False
        # Normalize long-lived aliases (historic spellings) to a single canonical ID
        canonical = "gpt-oss:cloud/120b"
        aliases = [
            canonical,
            "gpt-oss:cloud",
            "gpt-oss:cloud-120",
            "gpt-oss:cloud/120",
            "gpt-oss:cloud/120b",
            "gpt-oss:120b-cloud",
            "ollama/gpt-oss:120b-cloud",
        ]
        self._alias_lookup = {alias.lower(): canonical for alias in aliases}

    def _normalize_id(self, model_id: str) -> str:
        key = model_id.strip().lower()
        return self._alias_lookup.get(key, model_id.strip())

    def _normalize_entry(self, entry: ModelInfo) -> ModelInfo:
        normalized_id = self._normalize_id(entry.id)
        if normalized_id == entry.id:
            return entry
        return ModelInfo(
            id=normalized_id,
            provider=entry.provider,
            capabilities=list(entry.capabilities),
            roles=list(entry.roles),
            api_method=entry.api_method,
        )

    def normalize_model_id(self, model_id: str) -> str:
        """Public helper so other services can resolve historical aliases."""
        return self._normalize_id(model_id)

    async def refresh_models_now(self) -> List[ModelInfo]:
        """Force a discovery cycle and return the latest model list."""
        return await self.list_models(force_refresh=True)

    def _cached_provider(self, provider: str) -> List[ModelInfo]:
        """Keep the last API-verified provider catalog during transient outages."""
        return [m for m in (self._cache or []) if m.provider == provider]

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await self.refresh_models_now()
                logger.debug("Model registry refreshed successfully")
            except Exception as exc:
                logger.exception("Failed to refresh model registry: %s", exc)
            await asyncio.sleep(self._refresh_interval)

    def start_periodic_refresh(self, interval_seconds: float = 3600.0) -> None:
        """Start background task to refresh models on the given interval."""
        self._refresh_interval = max(60.0, float(interval_seconds))
        if self._refresh_task and not self._refresh_task.done():
            return
        loop = asyncio.get_running_loop()
        self._refresh_task = loop.create_task(self._refresh_loop())
        logger.info("Started model registry periodic refresh every %.0f seconds", self._refresh_interval)

    async def stop_periodic_refresh(self) -> None:
        """Cancel the background refresh task if running."""
        if not self._refresh_task:
            return
        task = self._refresh_task
        self._refresh_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("Stopped model registry periodic refresh")

    async def list_models(self, force_refresh: bool = False) -> List[ModelInfo]:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            if (
                not force_refresh
                and self._cache
                and self._cache_expiry > now
            ):
                return list(self._cache)

            models: List[ModelInfo] = []

            # Discover from all sources concurrently
            # Note: Stable Diffusion discovery disabled - use ComfyUI txt2img endpoint directly
            results = await asyncio.gather(
                self._discover_ollama(),
                self._discover_openai(),
                self._discover_gemini(),
                self._discover_anthropic(),
                self._discover_mistral(),
                self._discover_groq(),
                self._discover_cerebras(),
                self._discover_nvidia(),
                self._discover_kimi(),
                self._discover_cohere(),
                self._discover_openrouter(),
                self._discover_together(),
                self._discover_fireworks(),
                self._discover_cloudflare(),
                # GitHub Models was retired on 2026-07-30. Keep the legacy
                # helper/config for compatibility, but do not probe a dead API.
                self._discover_huggingface(),
                return_exceptions=True
            )

            for result in results:
                if isinstance(result, Exception):
                    logger.warning("Model discovery failed: %s", result)
                elif isinstance(result, list):
                    models.extend(result)

            # Deduplicate by canonical ID and merge capabilities/roles if the same model was discovered multiple times
            deduped: Dict[str, ModelInfo] = {}
            for entry in models:
                normalized = self._normalize_entry(entry)
                existing = deduped.get(normalized.id)
                if existing:
                    merged_capabilities = sorted(set(existing.capabilities) | set(normalized.capabilities))
                    merged_roles = list(set(existing.roles) | set(normalized.roles))
                    deduped[normalized.id] = ModelInfo(
                        id=normalized.id,
                        provider=existing.provider or normalized.provider,
                        capabilities=merged_capabilities,
                        roles=merged_roles,
                        api_method=existing.api_method or normalized.api_method,
                    )
                else:
                    deduped[normalized.id] = normalized

            self._apply_capability_store(deduped)

            self._cache = sorted(deduped.values(), key=self._sort_key)
            self._cache_expiry = now + self._ttl_seconds
            return list(self._cache)

    @staticmethod
    def _apply_capability_store(deduped: Dict[str, ModelInfo]) -> None:
        """Overlay verified capability data onto freshly discovered models.

        Reads only the in-memory store, never the network, so listing models
        stays fast. Probing happens out of band via refresh_capabilities().
        Name-based guesses are only replaced by facts we actually verified.
        """
        try:
            from .model_capabilities import get_store
            store = get_store()
        except Exception as exc:  # pragma: no cover - store is optional
            logger.debug("Capability store unavailable: %s", exc)
            return

        for model_id, entry in deduped.items():
            record = store.get(model_id)
            if not record:
                continue
            entry.availability = record.availability
            entry.capability_source = record.source
            if record.capabilities and record.source in ("declared", "probed"):
                entry.capabilities = list(record.capabilities)
                entry.roles = [
                    role for cap in record.capabilities
                    if (role := CAPABILITY_TO_ROLE.get(cap))
                ]

    @staticmethod
    def _sort_key(entry: ModelInfo) -> tuple[int, int, str]:
        cap_rank = min((CAPABILITY_SORT_ORDER.get(cap, 99) for cap in entry.capabilities), default=99)
        provider_rank = PROVIDER_SORT_ORDER.get(entry.provider, 99)
        return (cap_rank, provider_rank, entry.id.lower())

    def model_catalog(self, models: List[ModelInfo]) -> Dict[str, object]:
        """Build one shared frontend catalog from the registry model list.

        AICoder, Nova AI, Discuss-with-AI and Playground should all consume this
        structure. Playground can use the category buckets for media/vision/etc.
        without maintaining its own divergent model list.
        """
        categories: Dict[str, List[Dict[str, object]]] = {
            "chat": [],
            "code": [],
            "reasoning": [],
            "vision": [],
            "media_generation": [],
            "audio": [],
            "embedding": [],
            "moderation": [],
            "ocr": [],
        }
        serialized = [model.to_dict() for model in models]
        by_provider: Dict[str, List[Dict[str, object]]] = {}
        for model, item in zip(models, serialized):
            by_provider.setdefault(model.provider, []).append(item)
            caps = set(model.capabilities)
            if "chat" in caps:
                categories["chat"].append(item)
            if "code" in caps:
                categories["code"].append(item)
            if "reasoning" in caps:
                categories["reasoning"].append(item)
            if "vision" in caps:
                categories["vision"].append(item)
            if caps & {"image_gen", "video_gen"}:
                categories["media_generation"].append(item)
            if "audio" in caps:
                categories["audio"].append(item)
            if "embedding" in caps:
                categories["embedding"].append(item)
            if "moderation" in caps:
                categories["moderation"].append(item)
            if "ocr" in caps:
                categories["ocr"].append(item)
        return {
            "data": serialized,
            "total": len(serialized),
            "by_provider": by_provider,
            "categories": categories,
        }

    async def get_model(self, model_id: str) -> Optional[ModelInfo]:
        canonical_id = self._normalize_id(model_id)
        models = await self.list_models()
        for entry in models:
            if entry.id == canonical_id:
                return entry
        return None

    async def _discover_ollama(self) -> List[ModelInfo]:
        # The frontend catalogue must not expose arbitrary local /api/tags models.
        # Ollama entries here are documented Cloud tags only.
        return self._ollama_cloud_models()

    async def _discover_stable_diffusion(self) -> List[ModelInfo]:
        settings = self._settings
        if self._sd_discovery_disabled:
            return self._sd_fallback_models()

        # Check if ComfyUI is configured
        if settings.comfyui_url and settings.stable_diffusion_backend == "comfyui":
            return await self._discover_comfyui_models()

        # Try Automatic1111 API first
        sd_url = httpx.URL(str(settings.stable_diffusion_url)).join("/sdapi/v1/sd-models")
        auth = None
        if settings.stable_diffusion_username and settings.stable_diffusion_password:
            auth = httpx.BasicAuth(settings.stable_diffusion_username, settings.stable_diffusion_password)

        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout, auth=auth) as client:
                response = await client.get(sd_url)
                response.raise_for_status()
        except httpx.RequestError as exc:
            logger.warning("Failed to connect to Stable Diffusion at %s: %s", sd_url, exc)
            return self._sd_fallback_models()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                if not self._sd_discovery_warned:
                    logger.info(
                        "Stable Diffusion discovery endpoint %s returned 404. "
                        "Assuming ComfyUI workflow and using configured fallback models.",
                        sd_url,
                    )
                    self._sd_discovery_warned = True
                self._sd_discovery_disabled = True
                return self._sd_fallback_models()
            logger.warning("Stable Diffusion returned HTTP error %s for %s: %s", exc.response.status_code, sd_url, exc)
            return self._sd_fallback_models()

        try:
            data = response.json()
        except json.JSONDecodeError:
            logger.warning("Stable Diffusion response from %s could not be parsed as JSON", sd_url)
            return self._sd_fallback_models()

        results: List[ModelInfo] = []
        for item in data or []:
            name = item.get("title") or item.get("model_name") or item.get("name")
            if not name:
                continue
            results.append(ModelInfo(id=name, provider="sd", capabilities=["image_gen"]))
        if results:
            return results
        return self._sd_fallback_models()

    async def _discover_comfyui_models(self) -> List[ModelInfo]:
        """Discover models from ComfyUI API."""
        settings = self._settings
        if not settings.comfyui_url:
            return self._sd_fallback_models()

        auth = None
        if settings.stable_diffusion_username and settings.stable_diffusion_password:
            auth = httpx.BasicAuth(settings.stable_diffusion_username, settings.stable_diffusion_password)

        models: List[ModelInfo] = []

        async def fetch_list(path: str) -> List[str]:
            """Fetch model lists from ComfyUI with graceful fallbacks."""
            base_url = str(settings.comfyui_url).rstrip("/")
            endpoints = [
                f"{base_url}/{path}?format=json",
                f"{base_url}/{path}",
            ]
            for url in endpoints:
                try:
                    async with httpx.AsyncClient(timeout=settings.request_timeout, auth=auth) as client:
                        response = await client.get(url)
                        response.raise_for_status()
                        payload = response.json()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        continue
                    logger.warning("ComfyUI responded with HTTP %s for %s", exc.response.status_code, url)
                    continue
                except Exception as exc:
                    logger.warning("Failed to fetch ComfyUI models from %s: %s", url, exc)
                    continue

                if isinstance(payload, list):
                    return payload
                if isinstance(payload, dict):
                    # ComfyUI sometimes nests results under 'items'
                    items = payload.get("items")
                    if isinstance(items, list):
                        return items
            return []

        # Fetch checkpoint models
        for model_name in await fetch_list("models/checkpoints"):
            models.append(ModelInfo(id=model_name, provider="comfyui", capabilities=["image_gen"]))

        # Fetch LoRA models (exposed as additional image modifiers)
        for model_name in await fetch_list("models/loras"):
            models.append(ModelInfo(id=model_name, provider="comfyui", capabilities=["image_gen"]))

        if models:
            return models
        return self._sd_fallback_models()

    def _sd_fallback_models(self) -> List[ModelInfo]:
        configured = getattr(self._settings, "stable_diffusion_default_models", "")
        model_names = [name.strip() for name in configured.split(",") if name.strip()]
        return [ModelInfo(id=name, provider="sd", capabilities=["image_gen"]) for name in model_names]

    async def _discover_openai(self) -> List[ModelInfo]:
        """Discover only models accessible to the configured OpenAI project."""
        settings = self._settings
        if not settings.openai_api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning("Failed to discover OpenAI models: %s", safe_http_error(exc))
            return self._cached_provider("openai")

        models: List[ModelInfo] = []
        chat_prefixes = ("gpt-", "chatgpt-", "o1", "o3", "o4", "codex", "computer-use")
        for item in data.get("data", []):
            model_id = str(item.get("id") or "")
            if not model_id or model_id.startswith("ft:"):
                continue
            capabilities, roles, api_method = detect_capabilities(model_id)
            if capabilities == ["chat"] and not model_id.lower().startswith(chat_prefixes):
                continue
            models.append(ModelInfo(
                id=f"openai/{model_id}",
                provider="openai",
                capabilities=sorted(set(capabilities)),
                roles=sorted(set(roles)),
                api_method=api_method,
            ))
        logger.info("Discovered %d OpenAI models from API", len(models))
        return models

    async def _discover_gemini(self) -> List[ModelInfo]:
        """Discover models from Google Gemini API."""
        settings = self._settings
        if not settings.gemini_api_key:
            return []

        models: List[ModelInfo] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    headers={"x-goog-api-key": settings.gemini_api_key},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.RequestError as exc:
            logger.warning("Failed to discover Gemini models: %s", exc)
            return self._gemini_fallback_models()
        except httpx.HTTPStatusError as exc:
            logger.warning("Gemini API returned HTTP %s: %s", exc.response.status_code, safe_http_error(exc))
            return self._gemini_fallback_models()

        for model in data.get("models", []):
            name = model.get("name", "")
            # Extract model ID from full path (e.g., "models/gemini-2.0-flash" -> "gemini-2.0-flash")
            if name.startswith("models/"):
                name = name[7:]

            # Skip excluded models and provider-listed legacy IDs that the
            # Gemini Developer API explicitly rejects for current/new accounts.
            if GEMINI_EXCLUDE_PATTERN.search(name) or name in {"gemini-2.5-pro"}:
                continue

            # Use the detect_capabilities function for consistent detection
            supported_methods = model.get("supportedGenerationMethods", [])
            capabilities, roles, api_method = detect_capabilities(name, supported_methods)

            # Add vision for most Gemini chat models
            if "chat" in capabilities and "vision" not in capabilities:
                if not EMBEDDING_PATTERN.search(name) and not AUDIO_PATTERN.search(name):
                    capabilities.append("vision")
                    if "vision_analyst" not in roles:
                        roles.append("vision_analyst")

            models.append(ModelInfo(
                id=f"gemini/{name}",
                provider="gemini",
                capabilities=list(set(capabilities)),
                roles=roles,
                api_method=api_method
            ))

        if models:
            logger.info("Discovered %d Gemini models from API", len(models))
            return models
        return self._gemini_fallback_models()

    def _gemini_fallback_models(self) -> List[ModelInfo]:
        """Fallback Gemini models if API discovery fails."""
        return [
            # Gemini text / multimodal
            ModelInfo(id="gemini/gemini-3.5-flash", provider="gemini", capabilities=["chat", "vision", "reasoning", "code", "function_calling"], roles=["assistant", "vision_analyst", "reasoning_engine", "code_assistant", "tool_user"]),
            ModelInfo(id="gemini/gemini-3.1-pro-preview", provider="gemini", capabilities=["chat", "vision", "reasoning", "code", "function_calling"], roles=["assistant", "vision_analyst", "reasoning_engine", "code_assistant", "tool_user"]),
            ModelInfo(id="gemini/gemini-3.1-pro-preview-customtools", provider="gemini", capabilities=["chat", "vision", "reasoning", "code", "function_calling"], roles=["assistant", "vision_analyst", "reasoning_engine", "code_assistant", "tool_user"]),
            ModelInfo(id="gemini/gemini-3.1-flash-lite", provider="gemini", capabilities=["chat", "vision"], roles=["assistant", "vision_analyst"]),
            ModelInfo(id="gemini/gemini-3-pro-preview", provider="gemini", capabilities=["chat", "vision", "reasoning", "code"], roles=["assistant", "vision_analyst", "reasoning_engine", "code_assistant"]),
            ModelInfo(id="gemini/gemini-2.5-flash", provider="gemini", capabilities=["chat", "vision"], roles=["assistant", "vision_analyst"]),
            ModelInfo(id="gemini/gemini-2.5-flash-lite", provider="gemini", capabilities=["chat", "vision"], roles=["assistant", "vision_analyst"]),
            ModelInfo(id="gemini/gemini-live-2.5-flash-preview", provider="gemini", capabilities=["chat", "vision", "audio"], roles=["assistant", "vision_analyst", "audio_processor"]),
            ModelInfo(id="gemini/gemini-2.5-flash-preview-native-audio-dialog", provider="gemini", capabilities=["chat", "audio"], roles=["assistant", "audio_processor"]),
            ModelInfo(id="gemini/gemini-2.5-flash-exp-native-audio-thinking-dialog", provider="gemini", capabilities=["chat", "audio", "reasoning"], roles=["assistant", "audio_processor", "reasoning_engine"]),
            ModelInfo(id="gemini/gemini-2.0-flash", provider="gemini", capabilities=["chat", "vision"], roles=["assistant", "vision_analyst"]),
            # Gemini/Imagen image generation
            ModelInfo(id="gemini/gemini-2.5-flash-image", provider="gemini", capabilities=["image_gen"], roles=["image_generator"]),
            ModelInfo(id="gemini/imagen-4.0-generate-001", provider="gemini", capabilities=["image_gen"], roles=["image_generator"], api_method="predict"),
            ModelInfo(id="gemini/imagen-4.0-fast-generate-001", provider="gemini", capabilities=["image_gen"], roles=["image_generator"], api_method="predict"),
            ModelInfo(id="gemini/imagen-4.0-ultra-generate-001", provider="gemini", capabilities=["image_gen"], roles=["image_generator"], api_method="predict"),
            # Veo video generation
            ModelInfo(id="gemini/veo-3.1-generate-preview", provider="gemini", capabilities=["video_gen"], roles=["video_generator"], api_method="predictLongRunning"),
            ModelInfo(id="gemini/veo-3.0-generate-001", provider="gemini", capabilities=["video_gen"], roles=["video_generator"], api_method="predictLongRunning"),
            ModelInfo(id="gemini/veo-3.0-fast-generate-001", provider="gemini", capabilities=["video_gen"], roles=["video_generator"], api_method="predictLongRunning"),
            ModelInfo(id="gemini/veo-2.0-generate-001", provider="gemini", capabilities=["video_gen"], roles=["video_generator"], api_method="predictLongRunning"),
            ModelInfo(id="gemini/gemini-embedding-001", provider="gemini", capabilities=["embedding"], roles=["embedder"]),
        ]
    async def _discover_anthropic(self) -> List[ModelInfo]:
        """Discover models from Anthropic API, falling back to documented static IDs."""
        settings = self._settings
        if not settings.anthropic_api_key:
            return self._anthropic_static_models()

        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
                response = await client.get("https://api.anthropic.com/v1/models", headers=headers)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning("Failed to discover Anthropic models: %s", exc)
            return self._anthropic_static_models()

        items = payload.get("data", []) if isinstance(payload, dict) else []
        models: List[ModelInfo] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if not model_id:
                continue
            lower = str(model_id).lower()
            capabilities = ["chat"]
            roles = ["assistant"]
            if "claude-3" in lower or "claude-opus" in lower or "claude-sonnet" in lower or "claude-haiku" in lower:
                capabilities.append("vision")
                roles.append("vision_analyst")
            if "opus" in lower or "sonnet" in lower:
                capabilities.append("code")
                roles.append("code_assistant")
            if "opus" in lower or "sonnet" in lower or "thinking" in lower:
                capabilities.append("reasoning")
                roles.append("reasoning_engine")
            models.append(ModelInfo(id=f"anthropic/{model_id}", provider="anthropic", capabilities=sorted(set(capabilities)), roles=list(dict.fromkeys(roles))))

        return models or self._anthropic_static_models()


    async def _discover_mistral(self) -> List[ModelInfo]:
        """Discover models from Mistral API."""
        settings = self._settings
        if not settings.mistral_api_key:
            return []

        models: List[ModelInfo] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.mistral.ai/v1/models",
                    headers={"Authorization": f"Bearer {settings.mistral_api_key}"}
                )
                response.raise_for_status()
                data = response.json()
        except httpx.RequestError as exc:
            logger.warning("Failed to discover Mistral models: %s", exc)
            return self._mistral_fallback_models()
        except httpx.HTTPStatusError as exc:
            logger.warning("Mistral API returned HTTP %s: %s", exc.response.status_code, safe_http_error(exc))
            return self._mistral_fallback_models()

        for model in data.get("data", []):
            model_id = model.get("id", "")
            if not model_id:
                continue

            # Determine capabilities from API response
            capabilities = []
            roles = []
            model_caps = model.get("capabilities", {})

            if model_caps.get("completion_chat", False):
                capabilities.append("chat")
                roles.append("assistant")
            if model_caps.get("vision", False):
                capabilities.append("vision")
                roles.append("vision_analyst")
            if model_caps.get("function_calling", False):
                capabilities.append("function_calling")
                roles.append("tool_user")
            if model_caps.get("reasoning", False):
                capabilities.append("reasoning")
                roles.append("reasoning_engine")
            if model_caps.get("ocr", False):
                capabilities.extend(["ocr", "vision"])
                roles.extend(["document_reader", "vision_analyst"])
            if model_caps.get("moderation", False):
                capabilities.append("moderation")
                roles.append("content_moderator")
            if model_caps.get("audio", False) or model_caps.get("audio_transcription", False) or model_caps.get("audio_speech", False):
                capabilities.append("audio")
                roles.append("audio_processor")

            # Check for special model types using patterns
            if CODE_PATTERN.search(model_id):
                capabilities.append("code")
                if "code_assistant" not in roles:
                    roles.append("code_assistant")
            if EMBEDDING_PATTERN.search(model_id):
                capabilities.append("embedding")
                if "embedder" not in roles:
                    roles.append("embedder")
            if AUDIO_PATTERN.search(model_id):
                capabilities.append("audio")
                if "audio_processor" not in roles:
                    roles.append("audio_processor")
            if REASONING_PATTERN.search(model_id):
                capabilities.append("reasoning")
                if "reasoning_engine" not in roles:
                    roles.append("reasoning_engine")
            if MODERATION_PATTERN.search(model_id):
                capabilities.append("moderation")
                if "content_moderator" not in roles:
                    roles.append("content_moderator")
            if OCR_PATTERN.search(model_id):
                capabilities.append("ocr")
                if "document_reader" not in roles:
                    roles.append("document_reader")

            if not capabilities:
                capabilities.append("chat")
                roles.append("assistant")

            models.append(ModelInfo(
                id=f"mistral/{model_id}",
                provider="mistral",
                capabilities=list(set(capabilities)),
                roles=roles
            ))

        if models:
            logger.info("Discovered %d Mistral models from API", len(models))
            return models
        return self._mistral_fallback_models()

    def _mistral_fallback_models(self) -> List[ModelInfo]:
        """Fallback Mistral models if API discovery fails."""
        return [
            # Current generation models
            ModelInfo(id="mistral/mistral-medium-3.5", provider="mistral", capabilities=["chat", "vision", "code", "function_calling"], roles=["assistant", "vision_analyst", "code_assistant", "tool_user"]),
            ModelInfo(id="mistral/mistral-large-3", provider="mistral", capabilities=["chat", "vision", "function_calling"], roles=["assistant", "vision_analyst", "tool_user"]),
            ModelInfo(id="mistral/devstral-2", provider="mistral", capabilities=["chat", "code"], roles=["assistant", "code_assistant"]),
            ModelInfo(id="mistral/mistral-large-latest", provider="mistral", capabilities=["chat", "vision", "function_calling"], roles=["assistant", "vision_analyst", "tool_user"]),
            ModelInfo(id="mistral/mistral-medium-latest", provider="mistral", capabilities=["chat", "function_calling"], roles=["assistant", "tool_user"]),
            ModelInfo(id="mistral/mistral-small-latest", provider="mistral", capabilities=["chat", "function_calling"], roles=["assistant", "tool_user"]),
            ModelInfo(id="mistral/ministral-8b-latest", provider="mistral", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="mistral/ministral-3b-latest", provider="mistral", capabilities=["chat"], roles=["assistant"]),
            # Specialist models
            ModelInfo(id="mistral/codestral-latest", provider="mistral", capabilities=["chat", "code"], roles=["assistant", "code_assistant"]),
            ModelInfo(id="mistral/magistral-medium-latest", provider="mistral", capabilities=["chat", "reasoning"], roles=["assistant", "reasoning_engine"]),
            ModelInfo(id="mistral/mistral-embed", provider="mistral", capabilities=["embedding"], roles=["embedder"]),
            ModelInfo(id="mistral/mistral-ocr-latest", provider="mistral", capabilities=["ocr", "vision"], roles=["document_reader", "vision_analyst"]),
        ]

    # =========================================================================
    # NEW FREE TIER PROVIDERS (2025)
    # =========================================================================

    async def _discover_groq(self) -> List[ModelInfo]:
        """Discover models from Groq API (OpenAI-compatible, fastest inference)."""
        settings = self._settings
        if not settings.groq_api_key:
            return []

        models: List[ModelInfo] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.groq_base_url}/models",
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"}
                )
                response.raise_for_status()
                data = response.json()
        except httpx.RequestError as exc:
            logger.warning("Failed to discover Groq models: %s", exc)
            return self._groq_fallback_models()
        except httpx.HTTPStatusError as exc:
            logger.warning("Groq API returned HTTP %s: %s", exc.response.status_code, safe_http_error(exc))
            return self._groq_fallback_models()

        for model in data.get("data", []):
            model_id = model.get("id", "")
            if not model_id:
                continue

            capabilities, roles, api_method = detect_capabilities(model_id)
            if "orpheus" in model_id.lower():
                capabilities = ["audio"]
                roles = ["audio_processor"]
            elif "prompt-guard" in model_id.lower() or "safeguard" in model_id.lower():
                capabilities = ["moderation"]
                roles = ["content_moderator"]

            models.append(ModelInfo(
                id=f"groq/{model_id}",
                provider="groq",
                capabilities=list(set(capabilities)),
                roles=roles,
                api_method=api_method
            ))

        if models:
            logger.info("Discovered %d Groq models from API", len(models))
            return models
        return self._groq_fallback_models()

    def _groq_fallback_models(self) -> List[ModelInfo]:
        """Fallback Groq models if API discovery fails."""
        return [
            ModelInfo(id="groq/llama-3.3-70b-versatile", provider="groq", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="groq/llama-3.1-8b-instant", provider="groq", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="groq/openai/gpt-oss-120b", provider="groq", capabilities=["chat", "reasoning"], roles=["assistant", "reasoning_engine"]),
            ModelInfo(id="groq/openai/gpt-oss-20b", provider="groq", capabilities=["chat", "reasoning"], roles=["assistant", "reasoning_engine"]),
            ModelInfo(id="groq/groq/compound", provider="groq", capabilities=["chat", "function_calling"], roles=["assistant", "tool_user"]),
            ModelInfo(id="groq/groq/compound-mini", provider="groq", capabilities=["chat", "function_calling"], roles=["assistant", "tool_user"]),
            ModelInfo(id="groq/meta-llama/llama-4-scout-17b-16e-instruct", provider="groq", capabilities=["chat", "vision"], roles=["assistant", "vision_analyst"]),
            ModelInfo(id="groq/qwen/qwen3-32b", provider="groq", capabilities=["chat", "reasoning"], roles=["assistant", "reasoning_engine"]),
            ModelInfo(id="groq/whisper-large-v3", provider="groq", capabilities=["audio"], roles=["audio_processor"]),
            ModelInfo(id="groq/whisper-large-v3-turbo", provider="groq", capabilities=["audio"], roles=["audio_processor"]),
        ]

    async def _discover_cerebras(self) -> List[ModelInfo]:
        """Discover models from Cerebras API (OpenAI-compatible, 20x faster than GPU)."""
        settings = self._settings
        if not settings.cerebras_api_key:
            return []

        models: List[ModelInfo] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.cerebras_base_url}/models",
                    headers={"Authorization": f"Bearer {settings.cerebras_api_key}"}
                )
                response.raise_for_status()
                data = response.json()
        except httpx.RequestError as exc:
            logger.warning("Failed to discover Cerebras models: %s", exc)
            return self._cerebras_fallback_models()
        except httpx.HTTPStatusError as exc:
            logger.warning("Cerebras API returned HTTP %s: %s", exc.response.status_code, safe_http_error(exc))
            return self._cerebras_fallback_models()

        for model in data.get("data", []):
            model_id = model.get("id", "")
            if not model_id:
                continue

            capabilities, roles, api_method = detect_capabilities(model_id)

            models.append(ModelInfo(
                id=f"cerebras/{model_id}",
                provider="cerebras",
                capabilities=list(set(capabilities)),
                roles=roles,
                api_method=api_method
            ))

        if models:
            logger.info("Discovered %d Cerebras models from API", len(models))
            return models
        return self._cerebras_fallback_models()

    def _cerebras_fallback_models(self) -> List[ModelInfo]:
        """Fallback Cerebras models if API discovery fails."""
        return [
            ModelInfo(id="cerebras/gpt-oss-120b", provider="cerebras", capabilities=["chat", "reasoning", "function_calling"], roles=["assistant", "reasoning_engine", "tool_user"]),
            ModelInfo(id="cerebras/zai-glm-4.7", provider="cerebras", capabilities=["chat", "reasoning", "function_calling"], roles=["assistant", "reasoning_engine", "tool_user"]),
            ModelInfo(id="cerebras/gemma-4-31b", provider="cerebras", capabilities=["chat"], roles=["assistant"]),
        ]

    async def _discover_nvidia(self) -> List[ModelInfo]:
        """Discover the hosted NVIDIA NIM catalog from its OpenAI-compatible API."""
        settings = self._settings
        if not settings.nvidia_api_key:
            return []

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{settings.nvidia_base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {settings.nvidia_api_key}"},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning("Failed to discover NVIDIA NIM models: %s", safe_http_error(exc))
            return self._cached_provider("nvidia")

        # NVIDIA's /models endpoint can include NIMs that are globally
        # discoverable but not executable by the configured account. Keep an
        # account-specific denylist so those entries are not advertised to
        # clients after a backend restart. Values are raw NVIDIA model IDs.
        disabled_models = {
            item.strip()
            for item in os.getenv("NVIDIA_DISABLED_MODELS", "").split(",")
            if item.strip()
        }

        models: List[ModelInfo] = []
        for item in data.get("data", []):
            model_id = str(item.get("id") or "")
            if not model_id or model_id in disabled_models:
                continue

            capabilities, roles, api_method = detect_capabilities(model_id)
            lower = model_id.lower()
            # /v1/models is broader than chat: keep obvious non-chat entries out of AICoder.
            if EMBEDDING_PATTERN.search(lower):
                capabilities = ["embedding"]
                roles = ["embedder"]
            elif MODERATION_PATTERN.search(lower):
                capabilities = ["moderation"]
                roles = ["content_moderator"]
            elif any(token in lower for token in ("deplot", "kosmos", "fuyu")):
                capabilities = ["vision"]
                roles = ["vision_analyst"]

            models.append(ModelInfo(
                id=f"nvidia/{model_id}",
                provider="nvidia",
                capabilities=sorted(set(capabilities)),
                roles=list(dict.fromkeys(roles)),
                api_method=api_method,
            ))

        logger.info("Discovered %d NVIDIA NIM models from API", len(models))
        return models

    async def _discover_kimi(self) -> List[ModelInfo]:
        """Discover Moonshot/Kimi models exposed to the configured account."""
        api_key = os.getenv("KIMI_API_KEY")
        base_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning("Failed to discover Kimi models: %s", safe_http_error(exc))
            return self._cached_provider("kimi")

        models: List[ModelInfo] = []
        for item in data.get("data", []):
            model_id = str(item.get("id") or "")
            if not model_id:
                continue
            capabilities, roles, api_method = detect_capabilities(model_id)
            if "chat" not in capabilities:
                capabilities.append("chat")
                roles.append("assistant")
            models.append(ModelInfo(
                id=f"kimi/{model_id}",
                provider="kimi",
                capabilities=sorted(set(capabilities)),
                roles=sorted(set(roles)),
                api_method=api_method,
            ))
        logger.info("Discovered %d Kimi models from API", len(models))
        return models

    async def _discover_cohere(self) -> List[ModelInfo]:
        """Discover models from Cohere API (Best RAG & Embeddings)."""
        settings = self._settings
        if not settings.cohere_api_key:
            return []

        models: List[ModelInfo] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.cohere.ai/v1/models",
                    headers={"Authorization": f"Bearer {settings.cohere_api_key}"}
                )
                response.raise_for_status()
                data = response.json()
        except httpx.RequestError as exc:
            logger.warning("Failed to discover Cohere models: %s", exc)
            return self._cohere_fallback_models()
        except httpx.HTTPStatusError as exc:
            logger.warning("Cohere API returned HTTP %s: %s", exc.response.status_code, safe_http_error(exc))
            return self._cohere_fallback_models()

        for model in data.get("models", []):
            model_id = model.get("name", "")
            if not model_id:
                continue

            # Determine capabilities from endpoints
            endpoints = model.get("endpoints", [])
            capabilities = []
            roles = []

            if "chat" in endpoints:
                capabilities.append("chat")
                roles.append("assistant")
            if "embed" in endpoints:
                capabilities.append("embedding")
                roles.append("embedder")
            if "rerank" in endpoints:
                capabilities.append("rerank")
            if "classify" in endpoints:
                capabilities.append("classification")

            if not capabilities:
                capabilities.append("chat")
                roles.append("assistant")

            models.append(ModelInfo(
                id=f"cohere/{model_id}",
                provider="cohere",
                capabilities=list(set(capabilities)),
                roles=roles
            ))

        if models:
            logger.info("Discovered %d Cohere models from API", len(models))
            return models
        return self._cohere_fallback_models()

    def _cohere_fallback_models(self) -> List[ModelInfo]:
        """Fallback Cohere models if API discovery fails."""
        return [
            ModelInfo(id="cohere/command-a-plus-05-2026", provider="cohere", capabilities=["chat", "vision", "reasoning"], roles=["assistant", "vision_analyst", "reasoning_engine"]),
            ModelInfo(id="cohere/command-a-03-2025", provider="cohere", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="cohere/command-r7b-12-2024", provider="cohere", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="cohere/command-a-translate-08-2025", provider="cohere", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="cohere/command-a-reasoning-08-2025", provider="cohere", capabilities=["chat", "reasoning"], roles=["assistant", "reasoning_engine"]),
            ModelInfo(id="cohere/command-a-vision-07-2025", provider="cohere", capabilities=["chat", "vision", "ocr"], roles=["assistant", "vision_analyst", "document_reader"]),
            ModelInfo(id="cohere/command-r-08-2024", provider="cohere", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="cohere/command-r-plus-08-2024", provider="cohere", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="cohere/embed-v4.0", provider="cohere", capabilities=["embedding"], roles=["embedder"]),
            ModelInfo(id="cohere/rerank-v4.0-pro", provider="cohere", capabilities=["rerank"], roles=["assistant"]),
        ]

    async def _discover_openrouter(self) -> List[ModelInfo]:
        """Discover models from OpenRouter API (300+ models, one API key)."""
        settings = self._settings
        if not settings.openrouter_api_key:
            return []

        models: List[ModelInfo] = []
        try:
            headers = {
                "HTTP-Referer": "https://api.ailinux.me",
                "X-Title": "AILinux TriForce"
            }
            if settings.openrouter_api_key:
                headers["Authorization"] = f"Bearer {settings.openrouter_api_key}"
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{settings.openrouter_base_url}/models",
                    params={"output_modalities": "all"},
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()
        except httpx.RequestError as exc:
            logger.warning("Failed to discover OpenRouter models: %s", exc)
            return self._openrouter_fallback_models()
        except httpx.HTTPStatusError as exc:
            logger.warning("OpenRouter API returned HTTP %s: %s", exc.response.status_code, safe_http_error(exc))
            return self._openrouter_fallback_models()

        for model in data.get("data", []):
            model_id = model.get("id", "")
            if not model_id:
                continue

            # Get pricing info to identify free models
            pricing = model.get("pricing", {})
            is_free = pricing.get("prompt", "0") == "0" and pricing.get("completion", "0") == "0"

            capabilities, roles, api_method = detect_capabilities(model_id)

            # OpenRouter publishes tool support explicitly. Treat this as
            # authoritative instead of guessing function-calling from model names.
            supported_parameters = set(model.get("supported_parameters") or [])
            capabilities = [c for c in capabilities if c != "function_calling"]
            roles = [r for r in roles if r != "tool_user"]
            if "tools" in supported_parameters:
                capabilities.append("function_calling")
                roles.append("tool_user")

            # OpenRouter publishes authoritative input/output modalities.
            architecture = model.get("architecture", {})
            input_mods = set(architecture.get("input_modalities") or [])
            output_mods = set(architecture.get("output_modalities") or [])
            if "text" in output_mods:
                if "chat" not in capabilities:
                    capabilities.append("chat")
                    roles.append("assistant")
            else:
                capabilities = [c for c in capabilities if c != "chat"]
                roles = [r for r in roles if r != "assistant"]
            if "image" in input_mods and "vision" not in capabilities:
                capabilities.append("vision")
                roles.append("vision_analyst")
            if "image" in output_mods and "image_gen" not in capabilities:
                capabilities.append("image_gen")
                roles.append("image_generator")
            if "video" in output_mods and "video_gen" not in capabilities:
                capabilities.append("video_gen")
                roles.append("video_generator")
            if "audio" in output_mods and "audio" not in capabilities:
                capabilities.append("audio")
                roles.append("audio_processor")

            models.append(ModelInfo(
                id=f"openrouter/{model_id}",
                provider="openrouter",
                capabilities=list(dict.fromkeys(capabilities)),
                roles=list(dict.fromkeys(roles)),
                api_method=api_method
            ))

        if models:
            logger.info("Discovered %d OpenRouter models from API", len(models))
            return models
        return self._openrouter_fallback_models()

    def _openrouter_fallback_models(self) -> List[ModelInfo]:
        """Fallback to OpenRouter's maintained free router if discovery is unavailable.

        Exact model aliases are intentionally not hard-coded here because that
        catalog changes over time. OpenRouter maintains stable ``openrouter/free``
        and ``openrouter/auto`` routers; request requirements such as tool calling
        are enforced by the provider routing payload.
        """
        return [
            ModelInfo(
                id=OPENROUTER_FREE_ROUTER,
                provider="openrouter",
                capabilities=["chat", "function_calling"],
                roles=["assistant", "tool_user"],
            ),
            ModelInfo(
                id=OPENROUTER_AUTO_ROUTER,
                provider="openrouter",
                capabilities=["chat", "function_calling"],
                roles=["assistant", "tool_user"],
            ),
        ]

    async def _discover_together(self) -> List[ModelInfo]:
        """Discover models from Together AI API ($25 free credits)."""
        settings = self._settings
        if not settings.together_api_key:
            return []

        models: List[ModelInfo] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.together_base_url}/models",
                    headers={"Authorization": f"Bearer {settings.together_api_key}"}
                )
                response.raise_for_status()
                data = response.json()
        except httpx.RequestError as exc:
            logger.warning("Failed to discover Together AI models: %s", exc)
            return self._together_fallback_models()
        except httpx.HTTPStatusError as exc:
            logger.warning("Together AI API returned HTTP %s: %s", exc.response.status_code, safe_http_error(exc))
            return self._together_fallback_models()

        for model in data if isinstance(data, list) else data.get("data", data.get("models", [])):
            model_id = model.get("id", "") if isinstance(model, dict) else model
            if not model_id:
                continue

            capabilities, roles, api_method = detect_capabilities(model_id)

            # Check model type
            model_type = model.get("type", "") if isinstance(model, dict) else ""
            if model_type == "embedding":
                capabilities = ["embedding"]
                roles = ["embedder"]
            elif model_type == "image":
                capabilities = ["image_gen"]
                roles = ["image_generator"]

            models.append(ModelInfo(
                id=f"together/{model_id}",
                provider="together",
                capabilities=list(set(capabilities)),
                roles=roles,
                api_method=api_method
            ))

        if models:
            logger.info("Discovered %d Together AI models from API", len(models))
            return models
        return self._together_fallback_models()

    def _together_fallback_models(self) -> List[ModelInfo]:
        """Fallback Together AI models if API discovery fails."""
        return [
            ModelInfo(id="together/meta-llama/Llama-3.3-70B-Instruct-Turbo", provider="together", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="together/meta-llama/Llama-3.2-90B-Vision-Instruct-Turbo", provider="together", capabilities=["chat", "vision"], roles=["assistant", "vision_analyst"]),
            ModelInfo(id="together/mistralai/Mixtral-8x22B-Instruct-v0.1", provider="together", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="together/Qwen/Qwen2.5-72B-Instruct-Turbo", provider="together", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="together/deepseek-ai/DeepSeek-V3", provider="together", capabilities=["chat", "code"], roles=["assistant", "code_assistant"]),
            ModelInfo(id="together/black-forest-labs/FLUX.1-schnell", provider="together", capabilities=["image_gen"], roles=["image_generator"]),
        ]

    async def _discover_fireworks(self) -> List[ModelInfo]:
        """Discover models from Fireworks AI API ($1 free credits)."""
        settings = self._settings
        if not settings.fireworks_api_key:
            return []

        models: List[ModelInfo] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.fireworks_base_url}/models",
                    headers={"Authorization": f"Bearer {settings.fireworks_api_key}"}
                )
                response.raise_for_status()
                data = response.json()
        except httpx.RequestError as exc:
            logger.warning("Failed to discover Fireworks AI models: %s", exc)
            return self._fireworks_fallback_models()
        except httpx.HTTPStatusError as exc:
            logger.warning("Fireworks AI API returned HTTP %s: %s", exc.response.status_code, safe_http_error(exc))
            return self._fireworks_fallback_models()

        for model in data.get("data", data.get("models", [])):
            model_id = model.get("id", model.get("name", ""))
            if not model_id:
                continue

            capabilities, roles, api_method = detect_capabilities(model_id)

            models.append(ModelInfo(
                id=f"fireworks/{model_id}",
                provider="fireworks",
                capabilities=list(set(capabilities)),
                roles=roles,
                api_method=api_method
            ))

        if models:
            logger.info("Discovered %d Fireworks AI models from API", len(models))
            return models
        return self._fireworks_fallback_models()

    def _fireworks_fallback_models(self) -> List[ModelInfo]:
        """Fallback Fireworks AI models if API discovery fails."""
        return [
            ModelInfo(id="fireworks/accounts/fireworks/models/llama-v3p3-70b-instruct", provider="fireworks", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="fireworks/accounts/fireworks/models/llama-v3p2-90b-vision-instruct", provider="fireworks", capabilities=["chat", "vision"], roles=["assistant", "vision_analyst"]),
            ModelInfo(id="fireworks/accounts/fireworks/models/mixtral-8x22b-instruct", provider="fireworks", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="fireworks/accounts/fireworks/models/qwen2p5-72b-instruct", provider="fireworks", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="fireworks/accounts/fireworks/models/deepseek-v3", provider="fireworks", capabilities=["chat", "code"], roles=["assistant", "code_assistant"]),
        ]

    async def _discover_cloudflare(self) -> List[ModelInfo]:
        """Discover models from Cloudflare Workers AI (10,000 neurons/day free)."""
        settings = self._settings
        if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
            return []

        # Skip if credentials are placeholders
        if "your_" in settings.cloudflare_account_id or "your_" in settings.cloudflare_api_token:
            return []

        models: List[ModelInfo] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"https://api.cloudflare.com/client/v4/accounts/{settings.cloudflare_account_id}/ai/models/search",
                    params={"per_page": 1000},
                    headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"}
                )
                response.raise_for_status()
                data = response.json()
        except httpx.RequestError as exc:
            logger.warning("Failed to discover Cloudflare models: %s", exc)
            return self._cloudflare_fallback_models()
        except httpx.HTTPStatusError as exc:
            logger.warning("Cloudflare API returned HTTP %s: %s", exc.response.status_code, safe_http_error(exc))
            return self._cloudflare_fallback_models()

        for model in data.get("result", []):
            model_id = model.get("name", "")
            if not model_id:
                continue

            # Determine capabilities from task
            task = model.get("task", {})
            task_name = task.get("name", "") if isinstance(task, dict) else task
            capabilities = []
            roles = []

            if task_name == "Text Generation":
                capabilities.append("chat")
                roles.append("assistant")
            elif task_name == "Text Embeddings":
                capabilities.append("embedding")
                roles.append("embedder")
            elif task_name == "Image Classification":
                capabilities.append("vision")
                roles.append("vision_analyst")
            elif task_name == "Text-to-Image":
                capabilities.append("image_gen")
                roles.append("image_generator")
            elif task_name == "Speech Recognition":
                capabilities.append("audio")
                roles.append("audio_processor")
            elif task_name == "Translation":
                capabilities.append("translation")
                roles.append("translator")
            else:
                # Unknown/specialized Workers AI tasks are not chat-compatible.
                continue

            models.append(ModelInfo(
                id=f"cloudflare/{model_id}",
                provider="cloudflare",
                capabilities=list(set(capabilities)),
                roles=roles
            ))

        if models:
            logger.info("Discovered %d Cloudflare models from API", len(models))
            return models
        return self._cloudflare_fallback_models()



    async def _discover_github_models(self) -> List[ModelInfo]:
        """Discover the current GitHub Models catalog (free PAT quota)."""
        settings = self._settings
        if not settings.github_token:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    "https://models.github.ai/catalog/models",
                    headers={
                        "Authorization": f"Bearer {settings.github_token}",
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning("Failed to discover GitHub Models: %s", safe_http_error(exc))
            return []

        models = []
        for item in data if isinstance(data, list) else []:
            model_id = item.get("id", "")
            if not model_id:
                continue
            input_mods = set(item.get("supported_input_modalities") or [])
            output_mods = set(item.get("supported_output_modalities") or [])
            github_caps = set(item.get("capabilities") or [])
            capabilities, roles, api_method = detect_capabilities(model_id)
            if "text" in output_mods:
                if "chat" not in capabilities:
                    capabilities.append("chat")
                    roles.append("assistant")
            else:
                capabilities = [c for c in capabilities if c != "chat"]
                roles = [r for r in roles if r != "assistant"]
            if "image" in input_mods and "vision" not in capabilities:
                capabilities.append("vision")
                roles.append("vision_analyst")
            if "tool-calling" in github_caps and "function_calling" not in capabilities:
                capabilities.append("function_calling")
                roles.append("tool_user")
            models.append(ModelInfo(
                id=f"github/{model_id}",
                provider="github",
                capabilities=capabilities,
                roles=roles,
                api_method=api_method,
            ))
        logger.info("Discovered %d GitHub Models from catalog", len(models))
        return models

    def _cloudflare_fallback_models(self) -> List[ModelInfo]:
        """Fallback Cloudflare models if API discovery fails."""
        return [
            ModelInfo(id="cloudflare/@cf/moonshotai/kimi-k2.6", provider="cloudflare", capabilities=["chat", "vision", "reasoning"], roles=["assistant", "vision_analyst", "reasoning_engine"]),
            ModelInfo(id="cloudflare/@cf/zai-org/glm-4.7-flash", provider="cloudflare", capabilities=["chat", "reasoning"], roles=["assistant", "reasoning_engine"]),
            ModelInfo(id="cloudflare/@cf/openai/gpt-oss-120b", provider="cloudflare", capabilities=["chat", "reasoning"], roles=["assistant", "reasoning_engine"]),
            ModelInfo(id="cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct", provider="cloudflare", capabilities=["chat", "vision"], roles=["assistant", "vision_analyst"]),
            ModelInfo(id="cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast", provider="cloudflare", capabilities=["chat"], roles=["assistant"]),
            ModelInfo(id="cloudflare/@cf/meta/llama-3.2-11b-vision-instruct", provider="cloudflare", capabilities=["chat", "vision"], roles=["assistant", "vision_analyst"]),
            ModelInfo(id="cloudflare/@cf/deepseek-ai/deepseek-r1-distill-qwen-32b", provider="cloudflare", capabilities=["chat", "reasoning"], roles=["assistant", "reasoning_engine"]),
            ModelInfo(id="cloudflare/@cf/black-forest-labs/flux-1-schnell", provider="cloudflare", capabilities=["image_gen"], roles=["image_generator"]),
            ModelInfo(id="cloudflare/@cf/black-forest-labs/flux-2-klein-9b", provider="cloudflare", capabilities=["image_gen"], roles=["image_generator"]),
            ModelInfo(id="cloudflare/@cf/openai/whisper", provider="cloudflare", capabilities=["audio"], roles=["audio_processor"]),
            ModelInfo(id="cloudflare/gpt-image-1.5", provider="cloudflare", capabilities=["image_gen"], roles=["image_generator"]),
            ModelInfo(id="cloudflare/imagen-4", provider="cloudflare", capabilities=["image_gen"], roles=["image_generator"]),
            ModelInfo(id="cloudflare/hailuo-2.3", provider="cloudflare", capabilities=["video_gen"], roles=["video_generator"]),
            ModelInfo(id="cloudflare/hailuo-2.3-fast", provider="cloudflare", capabilities=["video_gen"], roles=["video_generator"]),
            ModelInfo(id="cloudflare/@cf/baai/bge-base-en-v1.5", provider="cloudflare", capabilities=["embedding"], roles=["embedder"]),
        ]

    # =========================================================================
    # STATIC HOSTED MODELS
    # =========================================================================

    async def _discover_huggingface(self) -> List[ModelInfo]:
        """Discover all chat models on HF's OpenAI-compatible provider router."""
        api_key = self._settings.huggingface_api_key
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    "https://router.huggingface.co/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning("Failed to discover Hugging Face models: %s", safe_http_error(exc))
            return self._cached_provider("huggingface")

        models = []
        for item in data.get("data", []):
            model_id = item.get("id", "")
            if not model_id:
                continue
            capabilities, roles, api_method = detect_capabilities(model_id)
            if "chat" not in capabilities:
                capabilities.append("chat")
                roles.append("assistant")
            models.append(ModelInfo(
                id=f"huggingface/{model_id}:fastest",
                provider="huggingface",
                capabilities=sorted(set(capabilities)),
                roles=sorted(set(roles)),
                api_method=api_method,
            ))
        return models or self._huggingface_fallback_models()

    @staticmethod
    def _huggingface_fallback_models() -> List[ModelInfo]:
        return [
            ModelInfo(
                id="huggingface/openai/gpt-oss-120b:fastest",
                provider="huggingface",
                capabilities=["chat", "reasoning", "function_calling"],
                roles=["assistant", "reasoning_engine", "tool_user"],
            ),
            ModelInfo(
                id="huggingface/openai/gpt-oss-20b:fastest",
                provider="huggingface",
                capabilities=["chat", "reasoning", "function_calling"],
                roles=["assistant", "reasoning_engine", "tool_user"],
            ),
        ]


    def _discover_static_hosted(self) -> Iterable[ModelInfo]:
        """Compatibility hook: live catalogs now come only from configured APIs."""
        return []

    def _openai_static_models(self) -> List[ModelInfo]:
        return [
            ModelInfo(id="openai/gpt-5.5", provider="openai", capabilities=["chat", "vision", "reasoning", "code", "function_calling"], roles=["assistant", "vision_analyst", "reasoning_engine", "code_assistant", "tool_user"]),
            ModelInfo(id="openai/gpt-5.4", provider="openai", capabilities=["chat", "vision", "reasoning", "code", "function_calling"], roles=["assistant", "vision_analyst", "reasoning_engine", "code_assistant", "tool_user"]),
            ModelInfo(id="openai/gpt-5.4-mini", provider="openai", capabilities=["chat", "vision", "reasoning", "code", "function_calling"], roles=["assistant", "vision_analyst", "reasoning_engine", "code_assistant", "tool_user"]),
            ModelInfo(id="openai/gpt-5.4-nano", provider="openai", capabilities=["chat", "vision", "reasoning", "function_calling"], roles=["assistant", "vision_analyst", "reasoning_engine", "tool_user"]),
            ModelInfo(id="openai/gpt-5.2", provider="openai", capabilities=["chat", "vision", "reasoning", "code"], roles=["assistant", "vision_analyst", "reasoning_engine", "code_assistant"]),
            ModelInfo(id="openai/gpt-5.2-pro", provider="openai", capabilities=["chat", "vision", "reasoning", "code"], roles=["assistant", "vision_analyst", "reasoning_engine", "code_assistant"]),
            ModelInfo(id="openai/gpt-5.1", provider="openai", capabilities=["chat", "vision", "reasoning", "code"], roles=["assistant", "vision_analyst", "reasoning_engine", "code_assistant"]),
            ModelInfo(id="openai/gpt-5", provider="openai", capabilities=["chat", "vision", "reasoning", "code"], roles=["assistant", "vision_analyst", "reasoning_engine", "code_assistant"]),
            ModelInfo(id="openai/gpt-5-mini", provider="openai", capabilities=["chat", "vision", "reasoning"], roles=["assistant", "vision_analyst", "reasoning_engine"]),
            ModelInfo(id="openai/gpt-5-nano", provider="openai", capabilities=["chat", "vision", "reasoning"], roles=["assistant", "vision_analyst", "reasoning_engine"]),
            ModelInfo(id="openai/gpt-4.1", provider="openai", capabilities=["chat", "vision", "code"], roles=["assistant", "vision_analyst", "code_assistant"]),
            ModelInfo(id="openai/gpt-4.1-mini", provider="openai", capabilities=["chat", "vision"], roles=["assistant", "vision_analyst"]),
            ModelInfo(id="openai/gpt-4.1-nano", provider="openai", capabilities=["chat", "vision"], roles=["assistant", "vision_analyst"]),
            ModelInfo(id="openai/o3", provider="openai", capabilities=["chat", "reasoning"], roles=["assistant", "reasoning_engine"]),
            ModelInfo(id="openai/o3-pro", provider="openai", capabilities=["chat", "reasoning"], roles=["assistant", "reasoning_engine"]),
            ModelInfo(id="openai/o4-mini", provider="openai", capabilities=["chat", "reasoning"], roles=["assistant", "reasoning_engine"]),
            ModelInfo(id="openai/gpt-5.2-codex", provider="openai", capabilities=["chat", "code", "reasoning"], roles=["assistant", "code_assistant", "reasoning_engine"]),
            ModelInfo(id="openai/gpt-5.1-codex", provider="openai", capabilities=["chat", "code", "reasoning"], roles=["assistant", "code_assistant", "reasoning_engine"]),
            ModelInfo(id="openai/gpt-image-1.5", provider="openai", capabilities=["image_gen"], roles=["image_generator"]),
            ModelInfo(id="openai/gpt-image-1", provider="openai", capabilities=["image_gen"], roles=["image_generator"]),
            ModelInfo(id="openai/gpt-image-1-mini", provider="openai", capabilities=["image_gen"], roles=["image_generator"]),
            ModelInfo(id="openai/chatgpt-image-latest", provider="openai", capabilities=["image_gen"], roles=["image_generator"]),
            ModelInfo(id="openai/dall-e-3", provider="openai", capabilities=["image_gen"], roles=["image_generator"]),
            ModelInfo(id="openai/dall-e-2", provider="openai", capabilities=["image_gen"], roles=["image_generator"]),
            ModelInfo(id="openai/sora-2", provider="openai", capabilities=["video_gen"], roles=["video_generator"]),
            ModelInfo(id="openai/sora-2-pro", provider="openai", capabilities=["video_gen"], roles=["video_generator"]),
            ModelInfo(id="openai/gpt-realtime", provider="openai", capabilities=["chat", "audio"], roles=["assistant", "audio_processor"]),
            ModelInfo(id="openai/gpt-realtime-mini", provider="openai", capabilities=["chat", "audio"], roles=["assistant", "audio_processor"]),
            ModelInfo(id="openai/gpt-audio", provider="openai", capabilities=["chat", "audio"], roles=["assistant", "audio_processor"]),
            ModelInfo(id="openai/gpt-audio-mini", provider="openai", capabilities=["chat", "audio"], roles=["assistant", "audio_processor"]),
            ModelInfo(id="openai/gpt-4o-transcribe", provider="openai", capabilities=["audio"], roles=["audio_processor"]),
            ModelInfo(id="openai/gpt-4o-mini-transcribe", provider="openai", capabilities=["audio"], roles=["audio_processor"]),
            ModelInfo(id="openai/gpt-4o-mini-tts", provider="openai", capabilities=["audio"], roles=["audio_processor"]),
            ModelInfo(id="openai/tts-1", provider="openai", capabilities=["audio"], roles=["audio_processor"]),
            ModelInfo(id="openai/tts-1-hd", provider="openai", capabilities=["audio"], roles=["audio_processor"]),
            ModelInfo(id="openai/whisper-1", provider="openai", capabilities=["audio"], roles=["audio_processor"]),
            ModelInfo(id="openai/text-embedding-3-large", provider="openai", capabilities=["embedding"], roles=["embedder"]),
            ModelInfo(id="openai/text-embedding-3-small", provider="openai", capabilities=["embedding"], roles=["embedder"]),
            ModelInfo(id="openai/text-embedding-ada-002", provider="openai", capabilities=["embedding"], roles=["embedder"]),
            ModelInfo(id="openai/omni-moderation-latest", provider="openai", capabilities=["moderation"], roles=["content_moderator"]),
        ]

    def _anthropic_static_models(self) -> List[ModelInfo]:
        premium = ["chat", "vision", "code", "reasoning"]
        premium_roles = ["assistant", "vision_analyst", "code_assistant", "reasoning_engine"]
        vision = ["chat", "vision"]
        vision_roles = ["assistant", "vision_analyst"]
        return [
            # Current/pinned direct Anthropic API IDs. OpenRouter IDs remain separate under provider=openrouter.
            ModelInfo(id="anthropic/claude-opus-4-8", provider="anthropic", capabilities=premium, roles=premium_roles),
            ModelInfo(id="anthropic/claude-opus-4-7", provider="anthropic", capabilities=premium, roles=premium_roles),
            ModelInfo(id="anthropic/claude-opus-4-6", provider="anthropic", capabilities=premium, roles=premium_roles),
            ModelInfo(id="anthropic/claude-opus-4-1-20250805", provider="anthropic", capabilities=premium, roles=premium_roles),
            ModelInfo(id="anthropic/claude-opus-4-20250514", provider="anthropic", capabilities=premium, roles=premium_roles),
            ModelInfo(id="anthropic/claude-sonnet-4-6", provider="anthropic", capabilities=premium, roles=premium_roles),
            ModelInfo(id="anthropic/claude-sonnet-4-5", provider="anthropic", capabilities=premium, roles=premium_roles),
            ModelInfo(id="anthropic/claude-sonnet-4-20250514", provider="anthropic", capabilities=premium, roles=premium_roles),
            ModelInfo(id="anthropic/claude-haiku-4-5-20251001", provider="anthropic", capabilities=vision, roles=vision_roles),
            ModelInfo(id="anthropic/claude-3-7-sonnet-20250219", provider="anthropic", capabilities=premium, roles=premium_roles),
            ModelInfo(id="anthropic/claude-3-5-sonnet-20241022", provider="anthropic", capabilities=["chat", "vision", "code"], roles=["assistant", "vision_analyst", "code_assistant"]),
            ModelInfo(id="anthropic/claude-3-5-haiku-20241022", provider="anthropic", capabilities=vision, roles=vision_roles),
            ModelInfo(id="anthropic/claude-3-haiku-20240307", provider="anthropic", capabilities=vision, roles=vision_roles),
        ]

    def _ollama_cloud_models(self) -> List[ModelInfo]:
        # One source of truth with the authenticated free catalog.
        from .user_tiers import OLLAMA_MODELS
        cloud_ids = [
            model[len("ollama/"):] if model.startswith("ollama/") else model
            for model in OLLAMA_MODELS
        ]
        models: List[ModelInfo] = []
        for model_id in cloud_ids:
            capabilities, roles, api_method = detect_capabilities(model_id)
            if "chat" not in capabilities:
                capabilities.append("chat")
                if "assistant" not in roles:
                    roles.append("assistant")
            if any(token in model_id for token in ("gpt-oss", "deepseek", "thinking", "glm-", "qwen3", "nemotron")):
                if "reasoning" not in capabilities:
                    capabilities.append("reasoning")
                    roles.append("reasoning_engine")
            if any(token in model_id for token in ("coder", "devstral", "deepseek", "glm-", "kimi")):
                if "code" not in capabilities:
                    capabilities.append("code")
                    roles.append("code_assistant")
            if "vl" in model_id or "gemini" in model_id:
                if "vision" not in capabilities:
                    capabilities.append("vision")
                    roles.append("vision_analyst")
            models.append(ModelInfo(
                id=f"ollama/{model_id}",
                provider="ollama",
                capabilities=sorted(set(capabilities)),
                roles=sorted(set(roles)),
                api_method=api_method,
            ))
        return models


registry = ModelRegistry()


def resolve_gemini_api_key() -> str | None:
    """Resolve the canonical Gemini / AI Studio credential."""
    from .google_genai import resolve_api_key
    return resolve_api_key()
