"""Capability intelligence for the TriForce model registry.

Three stages, cheapest first:

1. declared  - read capabilities the provider already publishes
               (OpenRouter modalities, Gemini supportedGenerationMethods,
               Cloudflare task types, HuggingFace pipeline tags). Free.
2. probed    - actually talk to the model with a minimal request when the
               provider publishes nothing useful. Costs tokens, so it is
               gated by CAPABILITY_PROBE_MODE and cached permanently.
3. availability - track whether a model answers, is out of quota, is
               unauthorised or gone, so callers never offer a dead model.

Results live in a JSON store on disk and survive restarts. A model is only
re-probed when it is new or when the operator forces it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import httpx

logger = logging.getLogger("ailinux.model_capabilities")

STORE_PATH = Path(os.getenv("CAPABILITY_STORE_PATH", "/var/tristar/model_capabilities.json"))
STORE_VERSION = 1

# ── Capability vocabulary ────────────────────────────────────────────────────
CHAT = "chat"
VISION = "vision"
IMAGE_GEN = "image_gen"
VIDEO_GEN = "video_gen"
AUDIO_IN = "audio_in"
AUDIO_OUT = "audio_out"
OCR = "ocr"
EMBEDDING = "embedding"
TOOLS = "tools"
REASONING = "reasoning"
CODE = "code"
MODERATION = "moderation"

ALL_CAPABILITIES = (
    CHAT, VISION, IMAGE_GEN, VIDEO_GEN, AUDIO_IN, AUDIO_OUT,
    OCR, EMBEDDING, TOOLS, REASONING, CODE, MODERATION,
)

# ── Availability states ──────────────────────────────────────────────────────
AVAIL_OK = "ok"                  # answered successfully
AVAIL_QUOTA = "quota_exceeded"   # credits/quota exhausted - stays broken until topped up
AVAIL_RATE_LIMITED = "rate_limited"  # temporary 429 - worth retrying later
AVAIL_UNAUTHORISED = "unauthorised"  # 401 / 403
AVAIL_MISSING = "not_found"      # 404 - model no longer exists
AVAIL_ERROR = "error"            # 5xx / network
AVAIL_UNKNOWN = "unknown"        # never checked


# ── Probe modes ──────────────────────────────────────────────────────────────
MODE_OFF = "off"           # never probe, declared metadata only
MODE_DECLARED = "declared"  # alias of off, clearer name
MODE_FREE = "free"         # probe only providers that do not bill per token
MODE_FULL = "full"         # probe everything

# Providers that do not bill per request (free tiers / local).
FREE_PROVIDERS: Set[str] = {
    "ollama", "groq", "cerebras", "cloudflare", "gemini",
    "huggingface", "nvidia", "github",
}

# Providers whose catalogue already carries machine-readable capabilities.
DECLARATIVE_PROVIDERS: Set[str] = {
    "openrouter", "gemini", "cloudflare", "huggingface",
}


def probe_mode() -> str:
    raw = (os.getenv("CAPABILITY_PROBE_MODE") or MODE_FREE).strip().lower()
    if raw in (MODE_OFF, MODE_DECLARED):
        return MODE_OFF
    if raw in (MODE_FREE, MODE_FULL):
        return raw
    logger.warning("Unknown CAPABILITY_PROBE_MODE %r, falling back to %s", raw, MODE_FREE)
    return MODE_FREE


def provider_is_probeable(provider: str, mode: Optional[str] = None) -> bool:
    mode = mode or probe_mode()
    if mode == MODE_OFF:
        return False
    if mode == MODE_FULL:
        return True
    return provider in FREE_PROVIDERS


@dataclass
class CapabilityRecord:
    """What we know about one model, and how we learned it."""
    model_id: str
    provider: str = ""
    capabilities: List[str] = field(default_factory=list)
    source: str = "heuristic"        # declared | probed | heuristic
    availability: str = AVAIL_UNKNOWN
    availability_detail: str = ""
    context_length: Optional[int] = None
    checked_at: float = 0.0
    probed_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapabilityRecord":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def usable(self) -> bool:
        return self.availability in (AVAIL_OK, AVAIL_UNKNOWN, AVAIL_RATE_LIMITED)


class CapabilityStore:
    """Persistent JSON store, keyed by model id."""

    def __init__(self, path: Path = STORE_PATH) -> None:
        self.path = path
        self._records: Dict[str, CapabilityRecord] = {}
        self._dirty = False
        self._lock = asyncio.Lock()
        self.load()

    # ── persistence ──────────────────────────────────────────────────────
    def load(self) -> None:
        try:
            if not self.path.exists():
                return
            raw = json.loads(self.path.read_text())
            if raw.get("version") != STORE_VERSION:
                logger.warning("Capability store version mismatch, starting fresh")
                return
            for mid, data in (raw.get("models") or {}).items():
                self._records[mid] = CapabilityRecord.from_dict(data)
            logger.info("Loaded %d capability records", len(self._records))
        except Exception as exc:
            logger.warning("Could not load capability store: %s", exc)

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": STORE_VERSION,
                "saved_at": time.time(),
                "models": {m: r.to_dict() for m, r in self._records.items()},
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=1, sort_keys=True))
            tmp.replace(self.path)
            self._dirty = False
            logger.info("Saved %d capability records", len(self._records))
        except Exception as exc:
            logger.warning("Could not save capability store: %s", exc)

    # ── access ───────────────────────────────────────────────────────────
    def get(self, model_id: str) -> Optional[CapabilityRecord]:
        return self._records.get(model_id)

    def put(self, record: CapabilityRecord) -> None:
        self._records[record.model_id] = record
        self._dirty = True

    def all(self) -> List[CapabilityRecord]:
        return list(self._records.values())

    def needs_probe(self, model_id: str) -> bool:
        rec = self._records.get(model_id)
        return rec is None or rec.probed_at == 0.0

    def by_capability(self, capability: str, usable_only: bool = True) -> List[str]:
        return sorted(
            r.model_id for r in self._records.values()
            if capability in r.capabilities and (r.usable or not usable_only)
        )

    def index(self, usable_only: bool = True) -> Dict[str, List[str]]:
        """Inverted index: capability -> model ids."""
        out: Dict[str, List[str]] = {c: [] for c in ALL_CAPABILITIES}
        for rec in self._records.values():
            if usable_only and not rec.usable:
                continue
            for cap in rec.capabilities:
                out.setdefault(cap, []).append(rec.model_id)
        return {k: sorted(v) for k, v in out.items()}

    def stats(self) -> Dict[str, Any]:
        by_source: Dict[str, int] = {}
        by_avail: Dict[str, int] = {}
        for r in self._records.values():
            by_source[r.source] = by_source.get(r.source, 0) + 1
            by_avail[r.availability] = by_avail.get(r.availability, 0) + 1
        return {
            "total": len(self._records),
            "by_source": by_source,
            "by_availability": by_avail,
            "probe_mode": probe_mode(),
        }


# ── Stage 1: declared metadata ───────────────────────────────────────────────
def capabilities_from_openrouter(entry: Dict[str, Any]) -> List[str]:
    """OpenRouter publishes modalities and supported parameters outright."""
    arch = entry.get("architecture") or {}
    inputs = {m.lower() for m in (arch.get("input_modalities") or [])}
    outputs = {m.lower() for m in (arch.get("output_modalities") or [])}
    params = {p.lower() for p in (entry.get("supported_parameters") or [])}

    caps: Set[str] = set()
    if "text" in outputs:
        caps.add(CHAT)
    if "image" in inputs:
        caps.add(VISION)
    if "image" in outputs:
        caps.add(IMAGE_GEN)
    if "audio" in inputs:
        caps.add(AUDIO_IN)
    if "audio" in outputs:
        caps.add(AUDIO_OUT)
    if "video" in outputs:
        caps.add(VIDEO_GEN)
    if "embeddings" in outputs or "embedding" in outputs:
        caps.add(EMBEDDING)
        caps.discard(CHAT)
    if "tools" in params or "tool_choice" in params:
        caps.add(TOOLS)
    if "reasoning" in params or "include_reasoning" in params:
        caps.add(REASONING)
    if "file" in inputs:
        caps.add(OCR)
    return sorted(caps)


def capabilities_from_gemini(supported_methods: Iterable[str]) -> List[str]:
    """Gemini publishes supportedGenerationMethods per model."""
    methods = {m.lower() for m in (supported_methods or [])}
    caps: Set[str] = set()
    if "generatecontent" in methods or "streamgeneratecontent" in methods:
        caps.add(CHAT)
    if "embedcontent" in methods or "batchembedcontents" in methods:
        caps.add(EMBEDDING)
        caps.discard(CHAT)
    if "predict" in methods:
        caps.add(IMAGE_GEN)
    if "predictlongrunning" in methods:
        caps.add(VIDEO_GEN)
    if "counttokens" in methods and CHAT in caps:
        caps.add(TOOLS)
    return sorted(caps)


def capabilities_from_huggingface(pipeline_tag: str) -> List[str]:
    """HuggingFace exposes a single pipeline_tag that maps cleanly."""
    tag = (pipeline_tag or "").lower()
    mapping = {
        "text-generation": [CHAT],
        "text2text-generation": [CHAT],
        "conversational": [CHAT],
        "image-text-to-text": [CHAT, VISION],
        "visual-question-answering": [VISION],
        "image-to-text": [VISION, OCR],
        "text-to-image": [IMAGE_GEN],
        "text-to-video": [VIDEO_GEN],
        "image-to-image": [IMAGE_GEN],
        "automatic-speech-recognition": [AUDIO_IN],
        "text-to-speech": [AUDIO_OUT],
        "text-to-audio": [AUDIO_OUT],
        "feature-extraction": [EMBEDDING],
        "sentence-similarity": [EMBEDDING],
        "text-classification": [MODERATION],
    }
    return sorted(mapping.get(tag, []))


# Substrings that mark an exhausted balance rather than a passing rate limit.
QUOTA_MARKERS = (
    "insufficient_quota", "credit_balance_exhausted", "no credits remaining",
    "credit balance is too low", "credit balance", "billing",
    "exceeded your current quota", "payment required", "quota_exceeded",
    "out of credits", "plans & billing",
)


def classify_http_status(status: int, body: str = "") -> tuple[str, str]:
    """Map an HTTP status (and error body) onto an availability state.

    429 is deliberately split: an exhausted credit balance stays broken
    until someone tops it up, while a plain rate limit clears by itself.
    Treating both the same would either hide a dead provider or retire a
    healthy one.
    """
    text = (body or "").lower()
    if status in (200, 201):
        return AVAIL_OK, ""
    if status in (401, 403):
        return AVAIL_UNAUTHORISED, f"HTTP {status}"
    if status == 404:
        return AVAIL_MISSING, "HTTP 404 - model not offered by provider"
    if status == 402:
        return AVAIL_QUOTA, "HTTP 402 payment required"
    if status == 429:
        if any(marker in text for marker in QUOTA_MARKERS):
            return AVAIL_QUOTA, "HTTP 429 - credit balance or quota exhausted"
        return AVAIL_RATE_LIMITED, "HTTP 429 - rate limited, retry later"
    if status >= 500:
        return AVAIL_ERROR, f"HTTP {status} - provider side error"
    if 400 <= status < 500 and any(marker in text for marker in QUOTA_MARKERS):
        # Anthropic answers an exhausted balance with 400, not 402/429.
        return AVAIL_QUOTA, f"HTTP {status} - credit balance or quota exhausted"
    return AVAIL_ERROR, f"HTTP {status}"


# ── Stage 2: empirical probing ───────────────────────────────────────────────
# Endpoints verified against the discovery code already in this repo.
# "style" selects the request shape: openai-compatible or anthropic messages.
PROBE_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "openai":    {"base": "https://api.openai.com/v1",        "env": "OPENAI_API_KEY",    "style": "openai"},
    "groq":      {"base": "https://api.groq.com/openai/v1",   "env": "GROQ_API_KEY",      "style": "openai"},
    "cerebras":  {"base": "https://api.cerebras.ai/v1",       "env": "CEREBRAS_API_KEY",  "style": "openai"},
    "nvidia":    {"base": "https://integrate.api.nvidia.com/v1", "env": "NVIDIA_API_KEY", "style": "openai"},
    "mistral":   {"base": "https://api.mistral.ai/v1",        "env": "MISTRAL_API_KEY",   "style": "openai"},
    "kimi":      {"base": "https://api.moonshot.ai/v1",       "env": "KIMI_API_KEY",      "style": "openai"},
    "ollama":    {"base": "http://localhost:11434/v1",        "env": "",                  "style": "openai"},
    "anthropic": {"base": "https://api.anthropic.com/v1",     "env": "ANTHROPIC_API_KEY", "style": "anthropic"},
}

# Smallest valid PNG (1x1, transparent) - enough to ask "do you accept images?"
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

PROBE_TOOL_SCHEMA = [{
    "type": "function",
    "function": {
        "name": "ping",
        "description": "probe",
        "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
    },
}]


def _strip_provider(model_id: str, provider: str) -> str:
    """'groq/llama-3.3-70b' -> 'llama-3.3-70b'. OpenRouter ids keep their slash."""
    prefix = f"{provider}/"
    return model_id[len(prefix):] if model_id.startswith(prefix) else model_id


def _auth_headers(provider: str, key: str) -> Dict[str, str]:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01",
                "content-type": "application/json"}
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    return headers


class ProbeResult:
    __slots__ = ("capabilities", "availability", "detail")

    def __init__(self) -> None:
        self.capabilities: Set[str] = set()
        self.availability: str = AVAIL_UNKNOWN
        self.detail: str = ""


class ModelProber:
    """Sends minimal requests to find out what a model actually accepts.

    Every probe caps output at one token. A 200 means the capability is
    real; a 4xx that is not auth/quota means the model rejected that kind
    of input, which is itself a useful answer.
    """

    def __init__(self, concurrency: int = 4, timeout: float = 25.0) -> None:
        self._sem = asyncio.Semaphore(concurrency)
        self._timeout = timeout
        # Providers that returned 401/429 - stop hammering them.
        self._blocked: Dict[str, str] = {}

    def _payload(self, style: str, model: str, kind: str) -> tuple[str, Dict[str, Any]]:
        """Build (path, body) for one probe kind."""
        if style == "anthropic":
            body: Dict[str, Any] = {"model": model, "max_tokens": 1,
                                    "messages": [{"role": "user", "content": "hi"}]}
            if kind == "vision":
                body["messages"] = [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/png", "data": TINY_PNG_B64}},
                    {"type": "text", "text": "hi"},
                ]}]
            elif kind == "tools":
                body["tools"] = [{"name": "ping", "description": "probe",
                                  "input_schema": {"type": "object",
                                                   "properties": {"x": {"type": "string"}}}}]
            return "/messages", body

        body = {"model": model, "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}]}
        if kind == "vision":
            body["messages"] = [{"role": "user", "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{TINY_PNG_B64}"}},
            ]}]
        elif kind == "tools":
            body["tools"] = PROBE_TOOL_SCHEMA
        elif kind == "embedding":
            return "/embeddings", {"model": model, "input": "probe"}
        return "/chat/completions", body

    async def _request(self, client: httpx.AsyncClient, provider: str, cfg: Dict[str, Any],
                       model: str, kind: str) -> tuple[int, str]:
        path, body = self._payload(cfg["style"], model, kind)
        url = cfg["base"].rstrip("/") + path
        try:
            r = await client.post(url, json=body,
                                  headers=_auth_headers(provider, os.getenv(cfg["env"], "")),
                                  timeout=self._timeout)
            return r.status_code, r.text[:200]
        except httpx.TimeoutException:
            return 0, "timeout"
        except httpx.RequestError as exc:
            return 0, f"{exc.__class__.__name__}"

    async def probe(self, model_id: str, provider: str) -> ProbeResult:
        result = ProbeResult()
        cfg = PROBE_ENDPOINTS.get(provider)
        if not cfg:
            result.detail = "no probe endpoint for provider"
            return result
        if provider in self._blocked:
            result.availability = self._blocked[provider]
            result.detail = "provider blocked earlier in this run"
            return result
        if cfg["env"] and not os.getenv(cfg["env"]):
            result.availability = AVAIL_UNAUTHORISED
            result.detail = f"{cfg['env']} not set"
            return result

        model = _strip_provider(model_id, provider)

        async with self._sem:
            async with httpx.AsyncClient() as client:
                # 1) text - also establishes availability
                status, detail = await self._request(client, provider, cfg, model, "text")
                state, note = classify_http_status(status, detail) if status else (AVAIL_ERROR, detail)
                result.availability = state
                result.detail = note

                if state in (AVAIL_UNAUTHORISED, AVAIL_QUOTA, AVAIL_RATE_LIMITED):
                    # Whole provider is unusable right now - do not probe further.
                    self._blocked[provider] = state
                    return result
                if state == AVAIL_OK:
                    result.capabilities.add(CHAT)
                elif state == AVAIL_MISSING:
                    return result

                # 2) embedding - only worth trying when chat failed
                if CHAT not in result.capabilities and cfg["style"] == "openai":
                    st, _ = await self._request(client, provider, cfg, model, "embedding")
                    if st in (200, 201):
                        result.capabilities.add(EMBEDDING)
                        result.availability = AVAIL_OK
                        return result

                if CHAT not in result.capabilities:
                    return result

                # 3) vision
                st, _ = await self._request(client, provider, cfg, model, "vision")
                if st in (200, 201):
                    result.capabilities.add(VISION)
                elif st == 429:
                    self._blocked[provider] = AVAIL_QUOTA

                # 4) tool calling
                st, _ = await self._request(client, provider, cfg, model, "tools")
                if st in (200, 201):
                    result.capabilities.add(TOOLS)
                elif st == 429:
                    self._blocked[provider] = AVAIL_QUOTA

        return result

    @property
    def blocked_providers(self) -> Dict[str, str]:
        return dict(self._blocked)


# ── Orchestration ────────────────────────────────────────────────────────────
async def fetch_declared_maps() -> Dict[str, Dict[str, List[str]]]:
    """Pull provider catalogues that publish capabilities directly.

    Returns {provider: {bare_model_id: [capabilities]}}. Failures are
    non-fatal - a provider we cannot reach simply contributes nothing and
    its models fall through to probing or heuristics.
    """
    out: Dict[str, Dict[str, List[str]]] = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # OpenRouter: richest source, covers the bulk of the catalogue.
        key = os.getenv("OPENROUTER_API_KEY", "")
        if key:
            try:
                r = await client.get(
                    "https://openrouter.ai/api/v1/models?output_modalities=all",
                    headers={"Authorization": f"Bearer {key}"},
                )
                if r.status_code == 200:
                    table: Dict[str, List[str]] = {}
                    for entry in r.json().get("data", []):
                        mid = entry.get("id")
                        if mid:
                            table[mid] = capabilities_from_openrouter(entry)
                    out["openrouter"] = table
                    logger.info("Declared capabilities for %d OpenRouter models", len(table))
            except Exception as exc:
                logger.warning("OpenRouter capability fetch failed: %s", exc)

        # Gemini: supportedGenerationMethods per model.
        gkey = os.getenv("GOOGLE_AI_STUDIO_KEY") or os.getenv("GEMINI_API_KEY") or ""
        if gkey:
            try:
                r = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    headers={"x-goog-api-key": gkey},
                )
                if r.status_code == 200:
                    table = {}
                    for entry in r.json().get("models", []):
                        name = entry.get("name", "")
                        if name.startswith("models/"):
                            name = name[7:]
                        if name:
                            table[name] = capabilities_from_gemini(
                                entry.get("supportedGenerationMethods", []))
                    out["gemini"] = table
                    logger.info("Declared capabilities for %d Gemini models", len(table))
            except Exception as exc:
                logger.warning("Gemini capability fetch failed: %s", exc)

    return out


async def refresh_capabilities(
    models: Iterable[Any],
    store: "CapabilityStore",
    *,
    mode: Optional[str] = None,
    probe_limit: int = 0,
    force: bool = False,
) -> Dict[str, Any]:
    """Bring the store up to date for the given models.

    ``models`` are objects with ``.id``, ``.provider`` and ``.capabilities``
    (i.e. registry ModelInfo). Declared metadata is applied to everything;
    probing is limited to what the mode allows and to models not yet probed.
    ``probe_limit`` of 0 means no limit.
    """
    mode = mode or probe_mode()
    declared = await fetch_declared_maps()
    now = time.time()

    stats = {"declared": 0, "probed": 0, "heuristic": 0, "skipped_probe": 0,
             "mode": mode, "errors": 0}

    to_probe: List[tuple] = []

    for m in models:
        mid = getattr(m, "id", None)
        if not mid:
            continue
        provider = getattr(m, "provider", "") or ""
        existing = store.get(mid)
        record = existing or CapabilityRecord(model_id=mid, provider=provider)
        record.provider = provider or record.provider
        record.checked_at = now

        # Stage 1: declared
        bare = _strip_provider(mid, provider)
        table = declared.get(provider) or {}
        caps = table.get(bare) or table.get(mid)
        if caps:
            record.capabilities = caps
            record.source = "declared"
            stats["declared"] += 1
        elif not existing or existing.source == "heuristic":
            # Fall back to whatever the registry guessed from the name.
            record.capabilities = sorted(set(getattr(m, "capabilities", []) or []))
            record.source = "heuristic"
            stats["heuristic"] += 1

        store.put(record)

        # Stage 2: probe candidates
        if provider in PROBE_ENDPOINTS and provider_is_probeable(provider, mode):
            if force or store.needs_probe(mid):
                to_probe.append((mid, provider))
            else:
                stats["skipped_probe"] += 1

    if probe_limit and len(to_probe) > probe_limit:
        to_probe = to_probe[:probe_limit]

    if to_probe:
        prober = ModelProber()
        logger.info("Probing %d models (mode=%s)", len(to_probe), mode)
        results = await asyncio.gather(
            *(prober.probe(mid, prov) for mid, prov in to_probe),
            return_exceptions=True,
        )
        for (mid, prov), res in zip(to_probe, results):
            rec = store.get(mid) or CapabilityRecord(model_id=mid, provider=prov)
            if isinstance(res, Exception):
                rec.availability = AVAIL_ERROR
                rec.availability_detail = str(res)[:120]
                stats["errors"] += 1
            else:
                rec.availability = res.availability
                rec.availability_detail = res.detail
                stats["probed"] += 1
                if res.capabilities:
                    # Probed facts win over declared/heuristic guesses.
                    rec.capabilities = sorted(set(rec.capabilities) | res.capabilities
                                              if rec.source == "declared"
                                              else res.capabilities)
                    rec.source = "probed"
                elif res.availability in (AVAIL_MISSING, AVAIL_UNAUTHORISED):
                    # The model does not answer at all - its inherited
                    # heuristic capabilities are worthless, drop them.
                    rec.capabilities = []
                    rec.source = "probed"
            rec.probed_at = time.time()
            store.put(rec)

    store.save()
    stats["store_total"] = len(store.all())
    return stats


# Module-level singleton so the registry and MCP tools share one store.
_store: Optional[CapabilityStore] = None


def get_store() -> CapabilityStore:
    global _store
    if _store is None:
        _store = CapabilityStore()
    return _store
