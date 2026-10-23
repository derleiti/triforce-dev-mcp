#!/usr/bin/env python3
"""Restricted Nova Discord conversation RPC, backed by a private SQLite history.

JSON on stdin/stdout. Credentials stay on the TriForce server; audio stays local
to zombie-pc. Only successful questions and answers are written to the DB.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.discord_voice_history import VoiceHistoryStore

GUILD_ID = "1333886137030283296"
OWNER_ID = "965583496435367956"
MODELS = ("groq/openai/gpt-oss-20b", "groq/openai/gpt-oss-120b")
SYSTEM = (
    "Du bist Nova, die technische, freundliche Sprachassistentin von AILinux. "
    "Dein Gesprächspartner Zombie entwickelt AILinux und TriForce und mag Linux, KI, "
    "Musik und direkte, ruhige Antworten. Brumo ist gelegentlich sein scherzhafter "
    "Qualitätsbeauftragter. Sprich locker auf Deutsch, leicht bayerischer Schmäh ist okay. "
    "WICHTIG: Beantworte die KONKRETE letzte Frage zuerst sachlich und individuell. "
    "Vermeide wiederholte Begrüßungen und allgemeine Antworten wie 'Ja, ich höre dich'. "
    "Nutze die vorherigen Gesprächsnachrichten bei Anschlussfragen und erinnere dich "
    "an Informationen, die darin vorkommen. Frage nach, wenn eine Frage unverständlich ist. "
    "Antworte normalerweise in 1 bis 3 Sätzen, höchstens etwa 65 Wörtern, "
    "ohne Markdown, Emojis oder Aufzählungen. "
    "Dies ist eine separate TriForce-Discord-Sitzung und NICHT der laufende "
    "ChatGPT-Chat. Behaupte niemals, den aktuellen ChatGPT-Verlauf zu kennen. "
    "Behaupte keine ausgeführten Computeraktionen ohne Tool-Beleg."
)


def normalize_answer(answer: str) -> str:
    text = str(answer or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    text = re.sub(r"(?m)^[-*#]+\s*", "", text)
    text = text.replace(chr(96), "").replace("*", "").replace("_", "")
    text = re.sub(r"\s+", " ", text)
    return text[:380].strip()


async def respond(message: str, history: list[dict]) -> tuple[str, str]:
    from app.config import get_settings
    from app.services.chat_router import api_proxy

    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError("Groq provider is not configured")
    os.environ["GROQ_API_KEY"] = settings.groq_api_key

    messages = [{"role": "system", "content": SYSTEM}, *history, {"role": "user", "content": message}]
    last_error: Exception | None = None
    for model in MODELS:
        try:
            response = await asyncio.wait_for(
                api_proxy.chat(model, messages, temperature=0.45, max_tokens=300),
                timeout=25,
            )
            answer = normalize_answer(response)
            if answer:
                return answer, model
            raise RuntimeError("Provider returned an empty answer")
        except Exception as error:
            # Never print raw upstream errors: provider responses might include
            # credentials, internal paths or the user's personal question.
            print(f"Voice chat model {model.split('/')[-1]} failed: {type(error).__name__}",
                  file=sys.stderr)
            last_error = error
    raise RuntimeError("All configured voice chat models failed") from last_error


def validate_request(request: Any) -> tuple[dict, str]:
    if not isinstance(request, dict):
        raise ValueError("Expected JSON object")
    if request.get("guild_id") != GUILD_ID or request.get("user_id") != OWNER_ID:
        raise PermissionError("Unauthorized Discord voice conversation")
    action = request.get("action", "chat")
    if action not in {"chat", "history", "count", "reset"}:
        raise ValueError("Unsupported conversation action")
    return request, action


def run(request: dict) -> dict:
    request, action = validate_request(request)
    history = VoiceHistoryStore()

    if action == "history":
        return {"ok": True, "history": history.recent(GUILD_ID, OWNER_ID, limit=6),
                "history_count": history.count(GUILD_ID, OWNER_ID)}
    if action == "count":
        return {"ok": True, "history_count": history.count(GUILD_ID, OWNER_ID)}
    if action == "reset":
        removed = history.clear(GUILD_ID, OWNER_ID)
        return {"ok": True, "removed": removed, "history_count": 0}

    question = request.get("message")
    request_id = request.get("request_id")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 550:
        raise ValueError("Voice question must contain 1 to 550 characters")
    if not isinstance(request_id, str) or len(request_id) != 36:
        raise ValueError("Missing stable request_id")
    try:
        if str(uuid.UUID(request_id)) != request_id.lower():
            raise ValueError("Invalid request_id")
    except ValueError:
        raise ValueError("Invalid request_id") from None

    existing = history.find_request(request_id, GUILD_ID, OWNER_ID)
    if existing:
        return {"ok": True, "response": existing["response"],
                "history_count": history.count(GUILD_ID, OWNER_ID),
                "cached": True, "turn_id": existing["turn_id"]}

    model_context = history.messages(GUILD_ID, OWNER_ID, limit=10)
    answer, model = asyncio.run(respond(question.strip(), model_context))
    saved = history.save(request_id, GUILD_ID, OWNER_ID, question.strip(), answer)
    return {"ok": True, "response": saved["response"], "model": model,
            "history_count": history.count(GUILD_ID, OWNER_ID),
            "cached": False, "turn_id": saved["turn_id"]}


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read(9000))
        result = run(request)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": type(error).__name__}))
        print(f"Nova voice RPC failed: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
