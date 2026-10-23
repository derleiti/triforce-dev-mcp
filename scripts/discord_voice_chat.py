#!/usr/bin/env python3
"""TriForce Discord conversational bridge: bounded JSON on stdin/stdout."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GUILD_ID = "1333886137030283296"
OWNER_ID = "965583496435367956"
MODEL = "groq/openai/gpt-oss-20b"
SYSTEM = (
    "Du bist Nova, die freundliche, kompetente Sprachassistentin von AILinux. "
    "Du sprichst mit Zombie/Markus in einem Discord-Sprachkanal. "
    "Sprich locker, natürlich und direkt auf Deutsch, gerne mit etwas bayerischem Schmäh, "
    "aber ohne übertriebene Begrüßungen. Brumo ist gelegentlich der scherzhafte "
    "Qualitätsbeauftragte. Antworte knapp in 1 bis 3 Sätzen und maximal etwa 45 Wörtern. "
    "Schreibe keine Markdown-Formatierung, keine Aufzählungszeichen und keine Emojis. "
    "Das ist eine separate TriForce-Discord-Session; du siehst keine Live-ChatGPT-Chats. "
    "Behaupte nicht, Aufgaben am Computer erledigt zu haben, wenn du sie nicht ausgeführt hast."
)


def sanitize_history(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for entry in value[-8:]:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        text = entry.get("content")
        if role in ("user", "assistant") and isinstance(text, str) and text.strip():
            result.append({"role": role, "content": text.strip()[:600]})
    return result


def normalize_answer(response: str) -> str:
    text = str(response or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    text = re.sub(r"(?m)^[-*#]+\s*", "", text)
    text = text.replace(chr(96), "").replace("*", "").replace("_", "")
    text = re.sub(r"\s+", " ", text)
    return text[:380].strip()


async def respond(message: str, history: list[dict[str, str]]) -> str:
    from app.config import get_settings
    from app.services.chat_router import api_proxy

    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError("TriForce Groq provider is not configured")
    os.environ["GROQ_API_KEY"] = settings.groq_api_key
    messages = [{"role": "system", "content": SYSTEM}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})
    answer = await asyncio.wait_for(
        api_proxy.chat(MODEL, messages, temperature=0.55, max_tokens=175),
        timeout=32,
    )
    return normalize_answer(answer)


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(9000)
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("Invalid JSON object")
        if request.get("guild_id") != GUILD_ID or request.get("user_id") != OWNER_ID:
            raise PermissionError("Unauthorized voice conversation")
        message = request.get("message")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 550:
            raise ValueError("Message must contain 1 to 550 characters")
        history = sanitize_history(request.get("history"))
        text = asyncio.run(respond(message.strip(), history))
        if not text:
            raise RuntimeError("Model returned no speech")
        sys.stdout.write(json.dumps({"ok": True, "response": text, "model": MODEL}, ensure_ascii=False) + "\n")
        return 0
    except Exception as error:
        sys.stdout.write(json.dumps({"ok": False, "error": type(error).__name__}) + "\n")
        print(f"Nova chat bridge failure: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
