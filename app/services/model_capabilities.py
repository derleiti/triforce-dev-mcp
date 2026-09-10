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
AVAIL_QUOTA = "quota_exceeded"   # 429 / billing / rate limit
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
        return self.availability in (AVAIL_OK, AVAIL_UNKNOWN)


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


def classify_http_status(status: int) -> tuple[str, str]:
    """Map an HTTP status onto an availability state."""
    if status in (200, 201):
        return AVAIL_OK, ""
    if status in (401, 403):
        return AVAIL_UNAUTHORISED, f"HTTP {status}"
    if status == 404:
        return AVAIL_MISSING, "HTTP 404"
    if status == 429:
        return AVAIL_QUOTA, "HTTP 429 rate limit or quota"
    if status in (402, 413):
        return AVAIL_QUOTA, f"HTTP {status}"
    if status >= 500:
        return AVAIL_ERROR, f"HTTP {status}"
    return AVAIL_ERROR, f"HTTP {status}"
