#!/usr/bin/env python3
"""Owner-scoped Nova voice RPC with a narrowly allowlisted MCP read toolbox.

The normal persistent conversation remains in scripts/discord_voice_chat.py;
only explicitly recognized, read-only questions are diverted to safe handlers.
This never exposes generic MCP method calls, shell, mutations, or credentials.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.discord_voice_history import VoiceHistoryStore
from app.services.discord_voice_tools import choose_read_tool, run_read_tool
from scripts import discord_voice_chat as baseline


def run(request: dict) -> dict:
    request, action = baseline.validate_request(request)
    if action != "chat":
        return baseline.run(request)

    message = request.get("message")
    request_id = request.get("request_id")
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 550:
        raise ValueError("Voice question must contain 1 to 550 characters")
    if not isinstance(request_id, str) or len(request_id) != 36:
        raise ValueError("Missing stable request_id")
    try:
        if str(uuid.UUID(request_id)) != request_id.lower():
            raise ValueError("Invalid request_id")
    except ValueError:
        raise ValueError("Invalid request_id") from None

    tool = choose_read_tool(message)
    if tool is None:
        return baseline.run(request)

    history = VoiceHistoryStore()
    previous = history.find_request(request_id, baseline.GUILD_ID, baseline.OWNER_ID)
    if previous:
        return {
            "ok": True, "response": previous["response"],
            "history_count": history.count(baseline.GUILD_ID, baseline.OWNER_ID),
            "cached": True, "turn_id": previous["turn_id"],
        }

    spoken, evidence = asyncio.run(run_read_tool(tool))
    answer = baseline.normalize_answer(spoken)
    if not answer:
        raise RuntimeError("MCP read tool returned no speech")
    saved = history.save(
        request_id,
        baseline.GUILD_ID,
        baseline.OWNER_ID,
        message.strip(),
        answer,
    )
    return {
        "ok": True,
        "response": saved["response"],
        "model": "mcp/" + evidence["tool"],
        "mcp_tool": evidence,
        "history_count": history.count(baseline.GUILD_ID, baseline.OWNER_ID),
        "cached": False,
        "turn_id": saved["turn_id"],
    }


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read(9000))
        result = run(request)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": type(error).__name__}))
        print(f"Restricted voice MCP dispatcher failed: {type(error).__name__}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
