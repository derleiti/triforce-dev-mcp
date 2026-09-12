"""
Swarm Broadcast Service — 631 Model Collective Intelligence
=============================================================

Sendet einen Prompt an ALLE registrierten Modelle (631+),
sammelt Antworten, bewertet Qualität, und gibt die besten
Ergebnisse an Gemini Lead zur Konsolidierung.

Architecture:
  Phase 1: BROADCAST — Kurzer Prompt an alle Modelle (batched per Provider)
  Phase 2: COLLECT   — Antworten sammeln, Timeouts/Errors loggen
  Phase 3: SCORE     — Quality-Scoring (Relevanz, Länge, Kohärenz)
  Phase 4: RANK      — Top N Ergebnisse für Lead-Konsolidierung

Rate Limits (per Provider):
  groq:        30 req/min  → batch 2, delay 2s
  cerebras:    30 req/min  → batch 2, delay 2s
  anthropic:   50 req/min  → batch 5, delay 6s
  mistral:     60 req/min  → batch 5, delay 5s
  gemini:      60 req/min  → batch 5, delay 5s
  openrouter: 200 req/min  → batch 20, delay 6s
  cloudflare: 300 req/min  → batch 25, delay 5s
  github:     150 req/min  → batch 15, delay 6s
  ollama:       ∞ (local)  → batch 5, delay 0s (CPU-bound)

Version: 1.0.0
Author: Markus Leitermann (derleiti)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.paths import TRISTAR_DIR
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger("ailinux.swarm_broadcast")

SWARM_DIR = TRISTAR_DIR / "swarm"
SWARM_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# Provider Rate Limit Config
# =============================================================================

PROVIDER_LIMITS: Dict[str, Dict[str, Any]] = {
    "groq":       {"rpm": 30,  "batch": 2,  "delay": 2.0, "timeout": 30},
    "cerebras":   {"rpm": 30,  "batch": 2,  "delay": 2.0, "timeout": 30},
    "anthropic":  {"rpm": 50,  "batch": 5,  "delay": 6.0, "timeout": 60},
    "mistral":    {"rpm": 60,  "batch": 5,  "delay": 5.0, "timeout": 30},
    "gemini":     {"rpm": 60,  "batch": 5,  "delay": 5.0, "timeout": 30},
    "openrouter": {"rpm": 200, "batch": 20, "delay": 6.0, "timeout": 45},
    "cloudflare": {"rpm": 300, "batch": 25, "delay": 5.0, "timeout": 30},
    "github":     {"rpm": 150, "batch": 15, "delay": 6.0, "timeout": 45},
    "ollama":     {"rpm": 60,  "batch": 5,  "delay": 3.0, "timeout": 45}   # Cloud-only, API-backed,
}


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class SwarmResponse:
    model_id: str
    provider: str
    response: str
    latency_ms: int
    quality_score: float = 0.0
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "response": self.response[:500],  # Truncate for overview
            "full_response_length": len(self.response),
            "latency_ms": self.latency_ms,
            "quality_score": self.quality_score,
            "error": self.error,
        }


@dataclass
class SwarmSession:
    id: str
    prompt: str
    user_question: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_models: int = 0
    responses: List[SwarmResponse] = field(default_factory=list)
    errors: List[SwarmResponse] = field(default_factory=list)
    top_results: List[SwarmResponse] = field(default_factory=list)
    phase: str = "created"  # created, broadcasting, scoring, completed, failed
    elapsed_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt[:200],
            "user_question": self.user_question[:200],
            "phase": self.phase,
            "total_models": self.total_models,
            "responses_count": len(self.responses),
            "errors_count": len(self.errors),
            "top_results_count": len(self.top_results),
            "elapsed_ms": self.elapsed_ms,
            "created_at": self.created_at,
        }


# =============================================================================
# Swarm Broadcast Engine
# =============================================================================

class SwarmBroadcast:
    """
    Broadcasts a prompt to all 631+ registered models and collects responses.
    """

    def __init__(self):
        self.sessions: Dict[str, SwarmSession] = {}
        self._http_timeout = aiohttp.ClientTimeout(total=120)

    # -------------------------------------------------------------------------
    # Main Entry Point
    # -------------------------------------------------------------------------

    async def broadcast(
        self,
        user_question: str,
        max_tokens: int = 200,
        top_n: int = 20,
        skip_providers: Optional[List[str]] = None,
        only_providers: Optional[List[str]] = None,
    ) -> SwarmSession:
        """
        Broadcast a prompt to all models, collect & score responses.

        Args:
            user_question: The original user question
            max_tokens: Max tokens per model response (keep small for speed)
            top_n: Number of top results to return
            skip_providers: Providers to skip (e.g. ["anthropic"] if no credits)
            only_providers: If set, only these providers are used
        """
        session_id = f"swarm-{uuid.uuid4().hex[:8]}"
        broadcast_prompt = self._build_broadcast_prompt(user_question)

        session = SwarmSession(
            id=session_id,
            prompt=broadcast_prompt,
            user_question=user_question,
        )
        self.sessions[session_id] = session

        start = time.monotonic()

        try:
            # Step 1: Get all models
            models = await self._fetch_all_models()
            session.total_models = len(models)

            # Filter by provider
            if skip_providers:
                skip_set = set(p.lower() for p in skip_providers)
                models = [m for m in models if m["provider"] not in skip_set]
            if only_providers:
                only_set = set(p.lower() for p in only_providers)
                models = [m for m in models if m["provider"] in only_set]

            # Exclude non-chat/audio/TTS/transcription models from chat broadcast.
            models = [m for m in models if self._is_model_chat_eligible(m.get("id", ""))]

            logger.info(f"Swarm {session_id}: Broadcasting to {len(models)} models")
            session.phase = "broadcasting"

            # Step 2: Group by provider
            by_provider: Dict[str, List[Dict]] = {}
            for m in models:
                by_provider.setdefault(m["provider"], []).append(m)

            # Step 3: Broadcast per provider (respecting rate limits)
            tasks = []
            for provider, provider_models in by_provider.items():
                tasks.append(
                    self._broadcast_provider(
                        session, provider, provider_models,
                        broadcast_prompt, max_tokens
                    )
                )

            await asyncio.gather(*tasks, return_exceptions=True)

            # Step 4: Score all responses
            session.phase = "scoring"
            self._score_responses(session, user_question)

            # Step 5: Rank and select top N
            scored = sorted(
                (r for r in session.responses if r.quality_score > 0),
                key=lambda r: r.quality_score,
                reverse=True,
            )
            session.top_results = scored[:top_n]

            session.phase = "completed"
            session.elapsed_ms = int((time.monotonic() - start) * 1000)

            # Save
            self._save_session(session)

            logger.info(
                f"Swarm {session_id}: Complete. "
                f"{len(session.responses)} responses, "
                f"{len(session.errors)} errors, "
                f"top {len(session.top_results)} selected, "
                f"{session.elapsed_ms}ms total"
            )

        except Exception as e:
            session.phase = "failed"
            session.elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error(f"Swarm broadcast failed: {e}")

        return session

    # -------------------------------------------------------------------------
    # Provider Broadcasting (rate-limit aware)
    # -------------------------------------------------------------------------

    async def _broadcast_provider(
        self,
        session: SwarmSession,
        provider: str,
        models: List[Dict],
        prompt: str,
        max_tokens: int,
    ):
        """Send prompt to all models of a provider, respecting rate limits."""
        limits = PROVIDER_LIMITS.get(provider, {"batch": 5, "delay": 5.0, "timeout": 30})
        batch_size = limits["batch"]
        delay = limits["delay"]
        timeout = limits["timeout"]

        logger.info(f"  Provider {provider}: {len(models)} models, batch={batch_size}, delay={delay}s")

        for i in range(0, len(models), batch_size):
            batch = models[i:i + batch_size]

            tasks = [
                self._call_model(provider, m["id"], prompt, max_tokens, timeout)
                for m in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for model_info, result in zip(batch, results):
                if isinstance(result, Exception):
                    session.errors.append(SwarmResponse(
                        model_id=model_info["id"],
                        provider=provider,
                        response="",
                        latency_ms=0,
                        error=str(result),
                    ))
                elif result.error or self._looks_like_error_response(result.response):
                    result.error = result.error or "error-like model response"
                    result.quality_score = 0.0
                    session.errors.append(result)
                else:
                    session.responses.append(result)

            # Rate limit delay between batches
            if i + batch_size < len(models) and delay > 0:
                await asyncio.sleep(delay)

    async def _call_model(
        self, provider: str, model_id: str, prompt: str,
        max_tokens: int, timeout: int,
    ) -> SwarmResponse:
        """Call a single model via TriForce /v1/chat endpoint."""
        start = time.monotonic()

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as http:
                async with http.post(
                    "http://localhost:9000/v1/chat",
                    json={
                        "model": model_id,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": 0.7,
                        "stream": False,
                        "no_fallback": True,
                    },
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Basic em9tYmllOmU5RjhEdUtiSC0=",
                    },
                ) as resp:
                    latency = int((time.monotonic() - start) * 1000)

                    if resp.status != 200:
                        error_text = await resp.text()
                        return SwarmResponse(
                            model_id=model_id, provider=provider,
                            response="", latency_ms=latency,
                            error=f"HTTP {resp.status}: {error_text[:200]}",
                        )

                    data = await resp.json()

                    # Extract response text (multiple formats)
                    content = ""
                    # TriForce native: {"text": "..."}
                    if "text" in data:
                        content = data["text"]
                    # OpenAI-compat: {"choices": [{"message": {"content": "..."}}]}
                    elif "choices" in data and data["choices"]:
                        msg = data["choices"][0].get("message", {})
                        content = msg.get("content", "")
                    # Ollama: {"message": {"content": "..."}}
                    elif "message" in data:
                        content = data["message"].get("content", "")
                    # Fallback
                    elif "response" in data:
                        content = data["response"]

                    return SwarmResponse(
                        model_id=model_id, provider=provider,
                        response=content, latency_ms=latency,
                    )

        except asyncio.TimeoutError:
            latency = int((time.monotonic() - start) * 1000)
            return SwarmResponse(
                model_id=model_id, provider=provider,
                response="", latency_ms=latency,
                error=f"Timeout after {timeout}s",
            )
        except Exception as e:
            latency = int((time.monotonic() - start) * 1000)
            return SwarmResponse(
                model_id=model_id, provider=provider,
                response="", latency_ms=latency,
                error=str(e),
            )

    # -------------------------------------------------------------------------
    # Quality Scoring
    # -------------------------------------------------------------------------

    def _is_model_chat_eligible(self, model_id: str) -> bool:
        """Return False for audio, transcription, TTS, and other non-chat model IDs."""
        mid = (model_id or "").lower()
        blocked_fragments = (
            "whisper",
            "audio",
            "transcribe",
            "transcription",
            "tts",
            "speech",
            "voice",
        )
        return not any(fragment in mid for fragment in blocked_fragments)

    def _looks_like_error_response(self, response: str) -> bool:
        """Detect transport/provider errors that arrived as text responses."""
        text = (response or "").strip().lower()
        if not text:
            return True

        error_markers = (
            "[fehler",
            "[error",
            "http 400",
            "http 401",
            "http 403",
            "http 404",
            "http 429",
            "http 500",
            "http 502",
            "http 503",
            "does not support chat completions",
            "requires terms acceptance",
            "backend exploded",
            "error-like model response",
            "fehler beim streaming",
        )
        return any(marker in text for marker in error_markers)

    def _score_responses(self, session: SwarmSession, user_question: str):
        """Score each response for quality/relevance."""
        question_words = set(user_question.lower().split())

        for resp in session.responses:
            score = 0.0
            text = resp.response.strip()

            if not text or resp.error or self._looks_like_error_response(text):
                resp.quality_score = 0.0
                continue

            hidden_reasoning_penalty = 0.0
            lower_text = text.lower()
            if "<think>" in lower_text or "</think>" in lower_text:
                hidden_reasoning_penalty = 0.15

            # Length score (prefer 50-500 chars, penalize too short/long)
            length = len(text)
            if length < 20:
                score += 0.1
            elif length < 50:
                score += 0.3
            elif length <= 500:
                score += 0.5
            elif length <= 1000:
                score += 0.4
            else:
                score += 0.3

            # Relevance score (keyword overlap)
            response_words = set(text.lower().split())
            overlap = len(question_words & response_words)
            relevance = min(overlap / max(len(question_words), 1), 1.0)
            score += relevance * 0.3

            # Coherence: has structure (code blocks, lists, paragraphs)
            if "```" in text:
                score += 0.1  # Contains code
            if "\n" in text:
                score += 0.05  # Multi-line
            if any(w in text.lower() for w in ["beispiel", "example", "implementation", "code"]):
                score += 0.05

            # Speed bonus (faster = better, max 0.1)
            if resp.latency_ms > 0:
                speed_score = max(0, 0.1 - (resp.latency_ms / 30000) * 0.1)
                score += speed_score

            score = max(0.0, score - hidden_reasoning_penalty)
            score = max(0.0, score - hidden_reasoning_penalty)
            resp.quality_score = round(min(score, 1.0), 3)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _build_broadcast_prompt(self, user_question: str) -> str:
        """Short prompt for all models — max ~100 tokens."""
        return (
            f"Kurze Antwort (max 200 Worte). "
            f"Gib Feature-Ideen, Architektur-Vorschlag, oder Code-Beispiel fuer:\n\n"
            f"{user_question}\n\n"
            f"Fokus: konkret, umsetzbar, mit Beispiel."
        )

    async def _fetch_all_models(self) -> List[Dict[str, str]]:
        """Fetch all registered models from /v1/models."""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as http:
                async with http.get("http://localhost:9000/v1/models", 
                    headers={"Authorization": "Basic em9tYmllOmU5RjhEdUtiSC0="}) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    models = []
                    for m in data.get("data", []):
                        model_id = m.get("id", "")
                        provider = model_id.split("/")[0] if "/" in model_id else "unknown"
                        # Skip local Ollama models — only keep cloud variants
                        if provider == "ollama":
                            model_name = model_id.split("/", 1)[-1] if "/" in model_id else model_id
                            if "cloud" not in model_name.lower():
                                continue  # Skip local model (would timeout)
                        models.append({"id": model_id, "provider": provider})
                    return models
        except Exception as e:
            logger.error(f"Failed to fetch models: {e}")
            return []

    def get_session(self, session_id: str) -> Optional[SwarmSession]:
        return self.sessions.get(session_id)

    def get_consolidated_prompt(self, session: SwarmSession) -> str:
        """Build a consolidated prompt from top results for Gemini Lead."""
        parts = [
            f"# Swarm-Broadcast Ergebnisse",
            f"## Original-Frage: {session.user_question}",
            f"## Statistik: {len(session.responses)} Antworten, "
            f"{len(session.errors)} Fehler, Top {len(session.top_results)} ausgewaehlt",
            "",
            "## Top Antworten (nach Quality-Score):",
        ]

        for i, r in enumerate(session.top_results, 1):
            parts.append(
                f"\n### #{i} — {r.model_id} (Score: {r.quality_score}, {r.latency_ms}ms)\n"
                f"{r.response[:800]}"
            )

        parts.append(
            "\n\n## Auftrag an Gemini Lead:\n"
            "Konsolidiere die besten Ideen aus allen Antworten.\n"
            "Erstelle einen konkreten Implementierungsplan mit Coding-Tasks.\n"
            "Verteile die Tasks auf die verfuegbaren Coding-Agents."
        )

        return "\n".join(parts)

    def _save_session(self, session: SwarmSession):
        filepath = SWARM_DIR / f"{session.id}.json"
        data = {
            "id": session.id,
            "prompt": session.prompt,
            "user_question": session.user_question,
            "phase": session.phase,
            "total_models": session.total_models,
            "responses": [r.to_dict() for r in session.responses],
            "errors": [r.to_dict() for r in session.errors],
            "top_results": [r.to_dict() for r in session.top_results],
            "elapsed_ms": session.elapsed_ms,
            "created_at": session.created_at,
        }
        filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# =============================================================================
# Singleton
# =============================================================================

swarm = SwarmBroadcast()
