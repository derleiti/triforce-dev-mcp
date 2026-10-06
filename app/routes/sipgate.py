from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STATE_DIR = _REPO_ROOT / ".state"
_PENDING_FILE = _STATE_DIR / "sipgate_oauth_pending.json"
_TOKEN_FILE = _STATE_DIR / "sipgate_oauth.json"
_CALLBACK = os.getenv("SIPGATE_REDIRECT_URI", "https://api.ailinux.me/v1/sipgate/oauth/callback")
_SCOPES = (
    "openid account:read users:read numbers:read settings:read "
    "phonelines:read phonelines:numbers:read phonelines:devices:read "
    "phonelines:forwardings:read phonelines:sipgateio:read settings:sipgateio:read"
)


def _client_config() -> tuple[str, str, str]:
    client_id = os.getenv("SIPGATE_CLIENT_ID", "").strip()
    client_secret = os.getenv("SIPGATE_CLIENT_SECRET", "").strip()
    realm = os.getenv("SIPGATE_REALM", "third-party").strip() or "third-party"
    if not client_id or not client_secret:
        raise RuntimeError("Sipgate OAuth client is not configured")
    return client_id, client_secret, realm


def _pat_config() -> tuple[str, str] | None:
    token_id = os.getenv("SIPGATE_TOKEN_ID", "").strip()
    token = os.getenv("SIPGATE_TOKEN", "").strip()
    return (token_id, token) if token_id and token else None


async def _api_get(path: str) -> httpx.Response:
    url = "https://api.sipgate.com/v2/" + path.lstrip("/")
    pat = _pat_config()
    async with httpx.AsyncClient(timeout=20) as client:
        if pat:
            return await client.get(url, auth=pat, headers={"Accept": "application/json"})
        tokens = await _refresh_if_needed(_load_tokens())
        access = str(tokens.get("access_token") or "")
        if not access:
            raise HTTPException(503, "Sipgate authentication is not connected")
        return await client.get(url, headers={"Authorization": f"Bearer {access}", "Accept": "application/json"})


def _auth_endpoint(realm: str) -> str:
    return f"https://login.sipgate.com/auth/realms/{realm}/protocol/openid-connect/auth"


def _token_endpoint(realm: str) -> str:
    return f"https://login.sipgate.com/auth/realms/{realm}/protocol/openid-connect/token"


def _write_private_json(path: Path, payload: dict) -> None:
    _STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def prepare_authorization_url() -> str:
    client_id, _, realm = _client_config()
    state = secrets.token_urlsafe(32)
    _write_private_json(_PENDING_FILE, {"state": state, "created_at": int(time.time())})
    return _auth_endpoint(realm) + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": _CALLBACK,
        "scope": _SCOPES,
        "response_type": "code",
        "state": state,
    })


def _load_tokens() -> dict:
    if not _TOKEN_FILE.exists():
        return {}
    try:
        return json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def _refresh_if_needed(tokens: dict) -> dict:
    if not tokens:
        return {}
    if int(tokens.get("expires_at", 0)) > int(time.time()) + 30:
        return tokens
    refresh = str(tokens.get("refresh_token") or "")
    if not refresh:
        return tokens
    client_id, client_secret, realm = _client_config()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(_token_endpoint(realm), data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        })
    if resp.status_code != 200:
        return tokens
    data = resp.json()
    now = int(time.time())
    updated = {
        "access_token": data.get("access_token", ""),
        "refresh_token": data.get("refresh_token") or refresh,
        "token_type": data.get("token_type", "Bearer"),
        "scope": data.get("scope", tokens.get("scope", "")),
        "expires_at": now + int(data.get("expires_in", 300)),
        "updated_at": now,
    }
    _write_private_json(_TOKEN_FILE, updated)
    return updated


@router.get("/v1/sipgate/oauth/callback", response_class=HTMLResponse)
async def sipgate_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    if error:
        return HTMLResponse(f"<h1>Sipgate authorization failed</h1><p>{error}</p>", status_code=400)
    if not code or not state:
        raise HTTPException(400, "Missing OAuth code or state")
    if not _PENDING_FILE.exists():
        raise HTTPException(400, "No pending Sipgate authorization")
    try:
        pending = json.loads(_PENDING_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(400, "Invalid pending authorization") from exc
    if int(time.time()) - int(pending.get("created_at", 0)) > 900:
        _PENDING_FILE.unlink(missing_ok=True)
        raise HTTPException(400, "Sipgate authorization expired")
    if not secrets.compare_digest(str(pending.get("state") or ""), state):
        raise HTTPException(400, "Invalid OAuth state")

    client_id, client_secret, realm = _client_config()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(_token_endpoint(realm), data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": _CALLBACK,
            "grant_type": "authorization_code",
        })
    if resp.status_code != 200:
        raise HTTPException(502, "Sipgate token exchange failed")
    data = resp.json()
    if not data.get("access_token") or not data.get("refresh_token"):
        raise HTTPException(502, "Sipgate returned incomplete OAuth tokens")
    now = int(time.time())
    _write_private_json(_TOKEN_FILE, {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "token_type": data.get("token_type", "Bearer"),
        "scope": data.get("scope", _SCOPES),
        "expires_at": now + int(data.get("expires_in", 300)),
        "updated_at": now,
    })
    _PENDING_FILE.unlink(missing_ok=True)

    account_ok = False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            check = await client.get(
                "https://api.sipgate.com/v2/account",
                headers={"Authorization": f"Bearer {data['access_token']}", "Accept": "application/json"},
            )
        account_ok = check.status_code == 200
    except Exception:
        pass

    detail = "Account API verified." if account_ok else "OAuth connected; account check pending."
    return HTMLResponse(
        "<html><body style='font-family:system-ui;background:#0d1015;color:#f4f4f4;padding:3rem'>"
        "<h1>✓ Sipgate connected to TriForce</h1>"
        f"<p>{detail}</p><p>You can close this tab.</p></body></html>"
    )


@router.get("/v1/sipgate/status")
async def sipgate_status():
    pat = _pat_config()
    oauth_configured = bool(os.getenv("SIPGATE_CLIENT_ID") and os.getenv("SIPGATE_CLIENT_SECRET"))
    connected = False
    account_http = None
    try:
        check = await _api_get("account")
        account_http = check.status_code
        connected = check.status_code == 200
    except Exception:
        pass
    tokens = {} if pat else (await _refresh_if_needed(_load_tokens()) if oauth_configured else {})
    return {
        "configured": bool(pat) or oauth_configured,
        "connected": connected,
        "auth_method": "pat" if pat else ("oauth2" if tokens.get("refresh_token") else "none"),
        "account_http": account_http,
        "realm": os.getenv("SIPGATE_REALM", "third-party"),
        "scope": tokens.get("scope", ""),
        "expires_at": tokens.get("expires_at"),
        "updated_at": tokens.get("updated_at"),
    }


@router.get("/v1/sipgate/overview")
async def sipgate_overview():
    account = await _api_get("account")
    if account.status_code != 200:
        raise HTTPException(account.status_code, "Sipgate account request failed")
    users_resp = await _api_get("users")
    users_data = users_resp.json() if users_resp.status_code == 200 else {}
    users = users_data.get("items", []) if isinstance(users_data, dict) else []
    user_resources = []
    for user in users:
        uid = str(user.get("id") or "")
        if not uid:
            continue
        row = {"user_id": uid}
        for name in ("devices", "numbers", "faxlines"):
            resp = await _api_get(f"{uid}/{name}")
            row[name + "_http"] = resp.status_code
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                row[name + "_count"] = len(items)
                if name == "faxlines":
                    row["faxlines"] = [{"id": x.get("id"), "alias": x.get("alias")} for x in items]
        user_resources.append(row)
    io_resp = await _api_get("settings/sipgateio")
    io_data = io_resp.json() if io_resp.status_code == 200 else {}
    return {
        "connected": True,
        "auth_method": "pat" if _pat_config() else "oauth2",
        "account_verified": bool(account.json().get("verified")),
        "main_product_type": account.json().get("mainProductType"),
        "users_count": len(users),
        "users": user_resources,
        "sipgateio_http": io_resp.status_code,
        "sipgateio_incoming_configured": bool(io_data.get("incomingUrl")) if isinstance(io_data, dict) else False,
        "sipgateio_outgoing_configured": bool(io_data.get("outgoingUrl")) if isinstance(io_data, dict) else False,
        "sipgateio_log": bool(io_data.get("log")) if isinstance(io_data, dict) else False,
    }
