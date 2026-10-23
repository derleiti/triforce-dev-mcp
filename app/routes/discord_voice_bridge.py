"""Signed Discord slash commands -> restricted SSH bridge -> local Nova voice socket.

Only Discord's verified, configured guild owner may operate the voice bridge.
No arbitrary remote commands, no direct token access and no ambient listening.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

LOGGER = logging.getLogger(__name__)

# Application/guild owner identities verified through the Discord Bot API.
APPLICATION_ID = "1562939604829470741"
GUILD_ID = "1333886137030283296"
OWNER_ID = "965583496435367956"
REMOTE_TARGET = "zombie-pc"
REMOTE_CLIENT = "/home/zombie/workspace/triforce/integrations/ailinux-discord-voice-mcp/remote_client.py"

SUPPORTED = {"join", "say", "leave", "status"}


def private_message(content: str) -> JSONResponse:
    return JSONResponse({"type": 4, "data": {"content": content[:1900], "flags": 64}})


def parse_nova(payload: dict[str, Any]) -> tuple[str, str, str] | str:
    """Return (subcommand, user ID, text), or a private error (string)."""
    if str(payload.get("guild_id", "")) != GUILD_ID:
        return "Nova Voice ist nur auf dem freigegebenen AILinux-Server verfügbar."
    if payload.get("application_id") and str(payload.get("application_id")) != APPLICATION_ID:
        return "Die Discord-Anwendung stimmt nicht überein."

    member = payload.get("member") or {}
    user = member.get("user") or payload.get("user") or {}
    user_id = str(user.get("id", ""))
    if user_id != OWNER_ID:
        return "Nova Voice ist derzeit ausschließlich für den Serverinhaber freigeschaltet."

    # Discord handles default command permissions, but never rely on that alone.
    options = (payload.get("data") or {}).get("options") or []
    if not isinstance(options, list) or len(options) != 1:
        return "Verwende /nova join, /nova say, /nova status oder /nova leave."
    subcommand = options[0]
    if not isinstance(subcommand, dict) or subcommand.get("type") != 1:
        return "Ungültiger Unterbefehl."
    action = str(subcommand.get("name") or "")
    if action not in SUPPORTED:
        return "Unbekannter Nova-Befehl."

    text = ""
    if action == "say":
        options = subcommand.get("options") or []
        if not isinstance(options, list):
            return "Ungültiger Textparameter."
        entries = [opt.get("value") for opt in options
                   if isinstance(opt, dict) and opt.get("name") == "text"]
        if len(entries) != 1 or not isinstance(entries[0], str):
            return "Bitte einen Text für /nova say angeben."
        text = entries[0].strip()
        if not 1 <= len(text) <= 400:
            return "Der Text muss zwischen 1 und 400 Zeichen lang sein."

    return action, user_id, text


async def voice_rpc(request: dict[str, Any]) -> dict[str, Any]:
    """Invoke the pinned remote client with a JSON payload over stdin."""
    cmd = [
        "/usr/bin/ssh", "-T", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes",
        "-o", "LogLevel=ERROR",
        REMOTE_TARGET, "/usr/bin/python3", REMOTE_CLIENT
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, _stderr = await asyncio.wait_for(
            process.communicate(json.dumps(request, ensure_ascii=False).encode("utf8")),
            timeout=59
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("Discord-Voice-Bridge hat nicht rechtzeitig geantwortet.") from None
    if len(output) > 8192 or not output:
        raise RuntimeError("Ungültige Antwort der Voice-Bridge.")
    try:
        response = json.loads(output)
    except (ValueError, TypeError):
        raise RuntimeError("Voice-Bridge hat keine gültige JSON-Antwort geliefert.") from None
    if not isinstance(response, dict):
        raise RuntimeError("Voice-Bridge-Protokollfehler.")
    if not response.get("ok"):
        raise RuntimeError(str(response.get("error") or "Unbekannter Voice-Fehler")[:200])
    return response


async def do_nova_action(action: str, user_id: str, text: str) -> str:
    if action == "status":
        data = await voice_rpc({"op": "status"})
        if data.get("connected"):
            return f"Nova ist in #{data.get('channel_name', 'Sprachkanal')}. Stimme: Seraphina. Zuhören: aus."
        return "Nova ist online und bereit. Ich bin noch in keinem Sprachkanal. Nutze /nova join."

    if action == "join":
        data = await voice_rpc({"op": "join_user", "user_id": user_id})
        channel = str(data.get("channel_name") or "deinem Sprachkanal")
        if not data.get("reused"):
            try:
                await voice_rpc({
                    "op": "say",
                    "text": "Servus Zombie! Nova ist jetzt im Discord-Sprachkanal. Brumo, Soundcheck!"
                })
            except Exception as error:
                return f"Nova ist in #{channel}, aber die Begrüßung konnte nicht abgespielt werden: {str(error)[:135]}"
        return f"Nova ist jetzt in #{channel}! Zum Sprechen /nova say verwenden. /nova leave beendet die Verbindung."

    if action == "say":
        data = await voice_rpc({"op": "say", "text": text})
        return f"Nova hat gesprochen ({data.get('spoken_characters', len(text))} Zeichen, Stimme: Seraphina)."

    if action == "leave":
        data = await voice_rpc({"op": "leave"})
        return "Nova hat den Sprachkanal verlassen." if not data.get("already_left") else "Nova war bereits außerhalb des Sprachkanals."

    raise RuntimeError("Unbekannter Befehl")


async def update_discord_response(interaction_token: str, content: str) -> None:
    # Discord's interaction token is single-purpose and never printed or logged.
    target = (
        f"https://discord.com/api/v10/webhooks/{APPLICATION_ID}/"
        f"{quote(interaction_token, safe='')}/messages/@original"
    )
    async with httpx.AsyncClient(timeout=12.0) as session:
        response = await session.patch(target, json={"content": content[:1900]})
        response.raise_for_status()


async def finish_nova_command(action: str, user_id: str, text: str, token: str) -> None:
    try:
        content = await do_nova_action(action, user_id, text)
    except Exception as error:
        LOGGER.warning("Nova Discord voice action failed: %s", type(error).__name__)
        content = f"Nova Voice: {str(error)[:175]}"
    try:
        await update_discord_response(token, content)
    except Exception as error:
        LOGGER.warning("Nova Discord response delivery failed: %s", type(error).__name__)


async def handle_nova_interaction(payload: dict[str, Any]) -> JSONResponse:
    parsed = parse_nova(payload)
    if isinstance(parsed, str):
        return private_message(parsed)
    token = payload.get("token")
    if not isinstance(token, str) or not 10 <= len(token) <= 512:
        return private_message("Die Discord-Interaktion enthält keinen gültigen Antworttoken.")
    action, user_id, text = parsed
    task = BackgroundTask(finish_nova_command, action, user_id, text, token)
    # Deferred, ephemeral response within Discord's 3-second deadline.
    return JSONResponse({"type": 5, "data": {"flags": 64}}, background=task)
