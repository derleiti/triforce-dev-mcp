"""RC1 account-scoped Discord pairing API. Disabled for Discord voice mutations until OAuth."""
from __future__ import annotations

import os
from urllib.parse import urlencode

import httpx
from fastapi.responses import HTMLResponse, FileResponse
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .client_auth import decode_authorization_header, USER_REGISTRY
from ..services.discord_mcp_registry import DiscordRegistry

router = APIRouter(prefix="/v1/discord-mcp", tags=["Discord MCP RC1"])


@router.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(Path(__file__).parent.parent / "static" / "discord-mcp.html",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def account_identity(authorization: str | None = Header(default=None)) -> str:
    payload = decode_authorization_header(authorization)
    subject = str(payload.get("email") or payload.get("sub") or "").lower().strip()
    if not subject or subject not in USER_REGISTRY:
        raise HTTPException(status_code=401, detail="Valid AILinux user session required")
    return subject


def registry() -> DiscordRegistry:
    location = Path(os.getenv("DISCORD_MCP_RC1_DB", "/var/lib/triforce/discord-mcp/registry.sqlite3"))
    location.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return DiscordRegistry(location)


class PairRequest(BaseModel):
    ttl_seconds: int = Field(default=300, ge=30, le=600)


@router.get("/me")
def me(account: str = Depends(account_identity)):
    return {"ok": True, "account": account, "login": "https://login.ailinux.me",
            "discord_oauth_connected": bool(registry().linked_ids(account)), "rc1_status": "staging", "legacy_voice_unchanged": True}


@router.post("/pair")
def pair(request: PairRequest, account: str = Depends(account_identity)):
    code = registry().create_pair(account, request.ttl_seconds)
    return {"ok": True, "pairing_code": code, "expires_in": request.ttl_seconds,
            "warning": "Code is one-time. No Discord permissions granted until OAuth verified."}


@router.post("/revoke")
def revoke(account: str = Depends(account_identity)):
    registry().revoke(account)
    return {"ok": True, "revoked": True}


_DISCORD_CALLBACK = "https://discord.ailinux.me/api/discord/oauth/callback"


@router.post("/oauth/start")
def discord_oauth_start(account: str = Depends(account_identity)):
    client_id = os.getenv("AILINUX_DISCORD_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("AILINUX_DISCORD_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(503, "Discord OAuth is not configured")
    state = registry().begin_oauth(account)
    params = {"response_type": "code", "client_id": client_id,
              "scope": "identify", "redirect_uri": _DISCORD_CALLBACK, "state": state}
    return {"ok": True, "authorization_url": "https://discord.com/oauth2/authorize?" + urlencode(params)}


@router.get("/oauth/callback", response_class=HTMLResponse)
async def discord_oauth_callback(code: str = "", state: str = ""):
    # The state is consumed atomically before any external HTTP request; retry/replay fails.
    account = registry().consume_oauth(state)
    if not account or not code or len(code) > 2048:
        raise HTTPException(400, "Invalid or expired Discord OAuth response")
    client_id = os.getenv("AILINUX_DISCORD_OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("AILINUX_DISCORD_OAUTH_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise HTTPException(503, "Discord OAuth is not configured")
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            token_response = await client.post("https://discord.com/api/v10/oauth2/token", data={
                "client_id": client_id, "client_secret": client_secret,
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": _DISCORD_CALLBACK,
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})
            token_response.raise_for_status()
            token = token_response.json()
            if token.get("token_type", "").lower() != "bearer" or not token.get("access_token"):
                raise ValueError("Invalid Discord access token")
            user_response = await client.get("https://discord.com/api/v10/users/@me", headers={
                "Authorization": "Bearer " + token["access_token"]
            })
            user_response.raise_for_status()
            discord_user = user_response.json()["id"]
        registry().link(account, discord_user)
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(502, "Discord identity verification failed") from None
    except __import__("sqlite3").IntegrityError:
        raise HTTPException(409, "Discord identity already linked to another AILinux account") from None
    return HTMLResponse("<!doctype html><html lang='de'><meta charset='utf-8'><title>AILinux Discord</title><body><h1>Discord verbunden</h1><p>Du kannst zum AILinux Discord-Dashboard zurückkehren.</p></body></html>",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
