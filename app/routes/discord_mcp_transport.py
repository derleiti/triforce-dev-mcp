"""Stateless Streamable HTTP MCP endpoint, strictly scoped to Discord accounts.

No OAuth token for this resource is valid for any other TriForce MCP endpoint.
No arbitrary shell, model, filesystem or message-send capability is exported.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .discord_mcp_rc1 import registry
from .client_auth import USER_REGISTRY
from ..services.discord_mcp_oauth_store import DiscordMcpOAuthStore, RESOURCE

router = APIRouter(prefix="/v1/discord-mcp", tags=["Discord MCP Transport"])

_METADATA = "https://discord.ailinux.me/.well-known/oauth-protected-resource/mcp"
_ALLOWED_ORIGINS = {
    "https://chatgpt.com", "https://chat.openai.com", "https://discord.ailinux.me",
}
_TOOLS = [
    {
        "name": "discord_connection_status",
        "description": "Read whether your verified AILinux account is linked to Discord and the number of approved server grants. Does not change Discord settings.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "Discord connection status", "readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "discord_authorized_guilds",
        "description": "List Discord servers explicitly authorized for your AILinux account, including their granted permission scopes. Does not list unrelated servers.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "Authorized Discord servers", "readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "discord_voice_status",
        "description": "Read the Nova voice bridge state only when your linked Discord account has an explicit read grant for its current server. No join, listen, or speaking action is performed.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "Discord Nova voice status", "readOnlyHint": True, "openWorldHint": False},
    },
    {'name': 'discord_voice_connect', 'description': 'Connect the verified owner to their current Discord voice channel and enable free conversation. The server-side bot remains connected, but ChatGPT only responds during active tool calls.', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'annotations': {'title': 'Discord Voice Connect', 'readOnlyHint': False, 'openWorldHint': False}},
    {'name': 'discord_voice_talk', 'description': 'Enable owner-only hands-free conversation on the existing voice connection, with persistent server-side restart restoration.', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'annotations': {'title': 'Discord Voice Talk', 'readOnlyHint': False, 'openWorldHint': False}},
    {'name': 'discord_voice_listen', 'description': 'Enable wake-word listening or pause listening on the owner voice connection.', 'inputSchema': {'type': 'object', 'properties': {'enabled': {'type': 'boolean'}}, 'additionalProperties': False, 'required': ['enabled']}, 'annotations': {'title': 'Discord Voice Listen', 'readOnlyHint': False, 'openWorldHint': False}},
    {'name': 'discord_voice_say', 'description': 'Speak a short user-authorized message in the connected Discord voice room.', 'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string', 'minLength': 1, 'maxLength': 400}}, 'additionalProperties': False, 'required': ['text']}, 'annotations': {'title': 'Discord Voice Say', 'readOnlyHint': False, 'openWorldHint': False}},
    {'name': 'discord_voice_exchange', 'description': 'Speak one ChatGPT-authored utterance and capture one owner reply. No autonomous model loop is created.', 'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string', 'minLength': 1, 'maxLength': 400}, 'wait_seconds': {'type': 'integer', 'minimum': 4, 'maximum': 22}}, 'additionalProperties': False, 'required': ['text']}, 'annotations': {'title': 'Discord Voice Exchange', 'readOnlyHint': False, 'openWorldHint': False}},
    {'name': 'discord_voice_capture', 'description': 'Capture the next authorized owner utterance once, without speaking.', 'inputSchema': {'type': 'object', 'properties': {'wait_seconds': {'type': 'integer', 'minimum': 4, 'maximum': 22}}, 'additionalProperties': False}, 'annotations': {'title': 'Discord Voice Capture', 'readOnlyHint': False, 'openWorldHint': False}},
    {'name': 'discord_voice_leave', 'description': 'Explicitly disconnect the server-side Nova voice bot and clear auto-rejoin.', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'annotations': {'title': 'Discord Voice Leave', 'readOnlyHint': False, 'openWorldHint': False}},
]


def _auth(request: Request):
    value = request.headers.get("authorization", "")
    if not value.startswith("Bearer "):
        return None
    token = value[7:].strip()
    claims = DiscordMcpOAuthStore(registry().database).verify(token)
    if not claims or "discord:read" not in claims["scopes"].split():
        return None
    if claims["account"] not in USER_REGISTRY:
        return None
    return claims


def _unauthorized():
    return JSONResponse(
        {"error": "invalid_token", "error_description": "Sign in with your AILinux account to connect the Discord MCP."},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer resource_metadata="' + _METADATA + '", scope="discord:read"',
                 "Cache-Control": "no-store"},
    )


def _origin_rejected(request):
    return bool(request.headers.get("origin") and
                request.headers["origin"] not in _ALLOWED_ORIGINS)


def _rpc(response_id, result=None, *, error=None):
    payload = {"jsonrpc": "2.0", "id": response_id}
    if error is None:
        payload["result"] = result
    else:
        payload["error"] = error
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


async def _tool(name, account, args=None, claims=None):
    args = args or {}
    db = registry()
    if name == "discord_connection_status":
        links = db.linked_ids(account)
        return {"discord_linked": bool(links), "discord_identity_count": len(links),
                "approved_servers": len(db.authorized_guilds(account)),
                "status": "connected" if links else "discord_oauth_required"}
    if name == "discord_authorized_guilds":
        return {"servers": db.authorized_guilds(account)}
    if name.startswith("discord_voice_"):
        from .discord_voice_bridge import GUILD_ID, OWNER_ID, voice_rpc
        if OWNER_ID not in db.linked_ids(account):
            return {"available": False, "reason": "owner_identity_required"}
        is_status = name == "discord_voice_status"
        scopes = set((claims or {}).get("scopes", "").split())
        if not is_status and "discord:voice" not in scopes:
            return {"available": False, "reason": "voice_oauth_scope_required"}
        required_grants = ["mcp:read"] if is_status else {
            "discord_voice_connect": ["voice:join", "voice:listen"],
            "discord_voice_talk": ["voice:listen"],
            "discord_voice_listen": ["voice:listen"],
            "discord_voice_say": ["voice:speak"],
            "discord_voice_exchange": ["voice:listen", "voice:speak"],
            "discord_voice_capture": ["voice:listen"],
            "discord_voice_leave": ["voice:join"],
        }.get(name, [])
        if any(not db.can(account, OWNER_ID, GUILD_ID, scope) for scope in required_grants):
            return {"available": False, "reason": "server_voice_grant_required"}
        if is_status:
            data = await voice_rpc({"op": "status"})
            return {"available": True, **data,
                    "chatgpt_scope": "active MCP invocations only"}
        request = {
            "discord_voice_connect": {"op": "connect"},
            "discord_voice_talk": {"op": "talk"},
            "discord_voice_listen": {"op": "listen", "enabled": args.get("enabled")},
            "discord_voice_say": {"op": "say", "text": args.get("text")},
            "discord_voice_exchange": {"op": "exchange", "text": args.get("text"), "wait_seconds": args.get("wait_seconds", 15)},
            "discord_voice_capture": {"op": "capture", "wait_seconds": args.get("wait_seconds", 15)},
            "discord_voice_leave": {"op": "leave"},
        }.get(name)
        if request is None:
            raise ValueError("Unknown voice tool")
        return await voice_rpc(request)
    raise ValueError("Unknown tool")


@router.head("/mcp")
async def mcp_head(request: Request):
    if _origin_rejected(request):
        return Response(status_code=403)
    if not _auth(request):
        unauthorized = _unauthorized()
        return Response(status_code=401, headers={"WWW-Authenticate": unauthorized.headers["WWW-Authenticate"]})
    return Response(status_code=405, headers={"Allow": "GET, POST, DELETE"})


@router.get("/mcp")
async def mcp_get(request: Request):
    if _origin_rejected(request):
        return Response(status_code=403)
    if not _auth(request):
        return _unauthorized()
    # A stateless MCP server MAY decline an unrelated GET SSE stream.
    return Response(status_code=405, headers={"Allow": "POST, GET, DELETE"})


@router.delete("/mcp")
async def mcp_delete(request: Request):
    if _origin_rejected(request):
        return Response(status_code=403)
    if not _auth(request):
        return _unauthorized()
    return Response(status_code=405, headers={"Allow": "POST, GET, DELETE"})


@router.post("/mcp")
async def mcp_post(request: Request):
    if _origin_rejected(request):
        return Response(status_code=403)
    claims = _auth(request)
    if not claims:
        return _unauthorized()
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return Response(status_code=415)
    raw = await request.body()
    if len(raw) > 65536:
        return Response(status_code=413)
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _rpc(None, error={"code": -32700, "message": "Parse error"})
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc(None, error={"code": -32600, "message": "Invalid request"})
    method = message.get("method")
    if method == "notifications/initialized":
        return Response(status_code=202)
    request_id = message.get("id")
    if request_id is None or not isinstance(method, str):
        return _rpc(None, error={"code": -32600, "message": "Invalid request"})
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _rpc(request_id, error={"code": -32602, "message": "Invalid params"})
    if method == "initialize":
        offered = params.get("protocolVersion", "2025-11-25")
        supported = {"2025-11-25", "2025-06-18", "2025-03-26"}
        protocol = offered if offered in supported else "2025-11-25"
        return _rpc(request_id, {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "ailinux-discord-mcp", "version": "0.2.0"},
            "instructions": "Account-scoped Discord tools. Use only approved AILinux-linked Discord servers. "
                            "A Discord bot's persistent connection is not the same as a persistent ChatGPT model session.",
        })
    if method == "ping":
        return _rpc(request_id, {})
    if method == "tools/list":
        return _rpc(request_id, {"tools": _TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        tool = next((item for item in _TOOLS if item["name"] == name), None)
        if tool is None:
            return _rpc(request_id, error={"code": -32602, "message": "Unknown tool"})
        schema = tool["inputSchema"]
        if not isinstance(arguments, dict) or set(arguments) - set(schema["properties"]) or any(
            key not in arguments for key in schema.get("required", [])
        ):
            return _rpc(request_id, error={"code": -32602, "message": "Invalid tool arguments"})
        for key, value in arguments.items():
            field = schema["properties"][key]
            kind = field["type"]
            if (kind == "boolean" and type(value) is not bool) or (
                kind == "integer" and type(value) is not int
            ) or (kind == "string" and not isinstance(value, str)):
                return _rpc(request_id, error={"code": -32602, "message": "Invalid tool argument type"})
            if kind == "string" and not field.get("minLength", 0) <= len(value) <= field.get("maxLength", 65536):
                return _rpc(request_id, error={"code": -32602, "message": "Invalid tool argument length"})
            if kind == "integer" and not field.get("minimum", -999999) <= value <= field.get("maximum", 999999):
                return _rpc(request_id, error={"code": -32602, "message": "Invalid wait time"})
        try:
            payload = await _tool(name, claims["account"], arguments, claims)
        except Exception:
            # Never leak path, token, or internal bridge error details.
            return _rpc(request_id, {"content": [{"type": "text", "text": "Discord voice operation unavailable."}],
                                     "isError": True})
        return _rpc(request_id, {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload,
            "isError": False,
        })
    return _rpc(request_id, error={"code": -32601, "message": "Method not found"})
