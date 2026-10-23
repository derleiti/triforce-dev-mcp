"""RC1 account-scoped Discord pairing API. Disabled for Discord voice mutations until OAuth."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .client_auth import decode_authorization_header, USER_REGISTRY
from ..services.discord_mcp_registry import DiscordRegistry

router = APIRouter(prefix="/v1/discord-mcp", tags=["Discord MCP RC1"])


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
            "discord_oauth_connected": False, "rc1_status": "staging", "legacy_voice_unchanged": True}


@router.post("/pair")
def pair(request: PairRequest, account: str = Depends(account_identity)):
    code = registry().create_pair(account, request.ttl_seconds)
    return {"ok": True, "pairing_code": code, "expires_in": request.ttl_seconds,
            "warning": "Code is one-time. No Discord permissions granted until OAuth verified."}


@router.post("/revoke")
def revoke(account: str = Depends(account_identity)):
    registry().revoke(account)
    return {"ok": True, "revoked": True}
