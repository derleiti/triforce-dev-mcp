"""Shared Google Gemini / AI Studio integration using the supported google-genai SDK.

`GEMINI_API_KEY` is the canonical Gemini Developer API credential. Historical
TriForce aliases remain accepted only for compatibility with existing installs.
No credential value is ever logged.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from ..config import get_settings

logger = logging.getLogger("ailinux.google_genai")

CANONICAL_KEY_ENV = "GEMINI_API_KEY"
LEGACY_KEY_ENVS = ("GOOGLE_GEMINI_KEY",)


def resolve_api_key() -> str | None:
    """Resolve one Gemini Developer API key without exposing it.

    New installs should use GEMINI_API_KEY. Legacy aliases are retained so an
    upgrade never silently loses a working AI Studio credential.
    """
    canonical = os.getenv(CANONICAL_KEY_ENV)
    if canonical:
        return canonical

    settings = get_settings()
    configured = getattr(settings, "gemini_api_key", None)
    if configured:
        return str(configured)

    return next((value for name in LEGACY_KEY_ENVS if (value := os.getenv(name))), None)


def configured_key_names() -> tuple[str, ...]:
    return tuple(name for name in (CANONICAL_KEY_ENV, *LEGACY_KEY_ENVS) if os.getenv(name))


def warn_on_ambiguous_environment() -> None:
    names = configured_key_names()
    if len(names) > 1:
        logger.warning(
            "Multiple Gemini API key environment aliases are configured (%s); "
            "prefer GEMINI_API_KEY only",
            ", ".join(names),
        )



def resolve_ai_studio_key() -> str | None:
    """Resolve the explicitly separate Google AI Studio credential."""
    value = os.getenv("GOOGLE_AI_STUDIO_KEY")
    if value:
        return value
    settings = get_settings()
    configured = getattr(settings, "google_ai_studio_key", None)
    return str(configured) if configured else None

def create_client(api_key: str | None = None):
    """Create a Gemini Developer API client using the supported Google Gen AI SDK."""
    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover - installation contract
        raise RuntimeError("google-genai is not installed") from exc

    key = api_key or resolve_api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    warn_on_ambiguous_environment()
    return genai.Client(api_key=key)


def text_contents(messages: list[dict[str, Any]]):
    """Convert OpenAI-style text messages to google.genai Content objects.

    Returns `(system_instruction, contents)` so system prompts use the native
    `system_instruction` channel instead of being disguised as user messages.
    """
    from google.genai import types

    system_chunks: list[str] = []
    contents: list[types.Content] = []
    for message in messages:
        role = str(message.get("role") or "user")
        text = message.get("content") or ""
        if not isinstance(text, str) or not text:
            continue
        if role == "system":
            system_chunks.append(text)
            continue
        contents.append(
            types.Content(
                role="model" if role == "assistant" else "user",
                parts=[types.Part.from_text(text=text)],
            )
        )
    return "\n\n".join(system_chunks) or None, contents
