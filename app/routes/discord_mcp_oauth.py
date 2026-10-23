"""Independent OAuth2 Authorization Server for discord.ailinux.me/mcp.

Uses the real signed AILinux login cookie, explicit user consent, DCR, S256
PKCE and opaque audience-restricted tokens. Not the legacy MCP shared password.
"""
from __future__ import annotations

import html
import json
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .client_auth import decode_authorization_header, USER_REGISTRY
from .discord_mcp_rc1 import registry
from ..services.discord_mcp_oauth_store import DiscordMcpOAuthStore, RESOURCE, SCOPES

PUBLIC = "https://discord.ailinux.me"
router = APIRouter(prefix="/v1/discord-mcp/oauth2", tags=["Discord MCP OAuth"])


def store():
    return DiscordMcpOAuthStore(registry().database)


def _ailinux_account(request: Request) -> str | None:
    token = request.cookies.get("ailinux_token")
    if not token or len(token) > 4096:
        return None
    try:
        payload = decode_authorization_header("Bearer " + token)
        account = str(payload.get("email") or payload.get("sub") or "").lower().strip()
        return account if account in USER_REGISTRY else None
    except Exception:
        return None


def _no_cache(obj, status=200):
    return JSONResponse(obj, status_code=status,
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _oauth_error(error: str, status=400):
    return _no_cache({"error": error}, status)


async def _urlencoded(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Expected form data")
    raw = await request.body()
    if len(raw) > 8192:
        raise HTTPException(413, "Form too large")
    pairs = parse_qs(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=False, max_num_fields=30)
    if any(len(values) != 1 for values in pairs.values()):
        raise HTTPException(400, "Duplicate parameter")
    return {key: values[0] for key, values in pairs.items()}


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource():
    return {"resource": RESOURCE, "authorization_servers": [PUBLIC],
            "scopes_supported": sorted(SCOPES), "bearer_methods_supported": ["header"]}


@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata():
    return {
        "issuer": PUBLIC,
        "authorization_endpoint": PUBLIC + "/authorize",
        "token_endpoint": PUBLIC + "/token",
        "registration_endpoint": PUBLIC + "/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": sorted(SCOPES),
        "service_documentation": PUBLIC + "/",
    }


@router.post("/register")
async def register(request: Request):
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return _oauth_error("invalid_client_metadata")
    raw = await request.body()
    if len(raw) > 8192:
        return _oauth_error("invalid_client_metadata")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Invalid metadata")
        if data.get("token_endpoint_auth_method", "none") != "none":
            raise ValueError("Only public S256-PKCE clients supported")
        if data.get("response_types", ["code"]) != ["code"]:
            raise ValueError("Invalid response type")
        grants = data.get("grant_types", ["authorization_code", "refresh_token"])
        if not isinstance(grants, list) or not grants or any(
            x not in {"authorization_code", "refresh_token"} for x in grants
        ):
            raise ValueError("Invalid grant type")
        caller = request.client.host if request.client else "unknown"
        created = store().register_client(data.get("client_name", "MCP client"),
                                          data.get("redirect_uris"), caller)
    except PermissionError:
        return _oauth_error("too_many_requests", 429)
    except (ValueError, TypeError, KeyError):
        return _oauth_error("invalid_client_metadata")
    return _no_cache(created, 201)


@router.get("/authorize")
async def authorize(request: Request):
    q = request.query_params
    client_id = q.get("client_id", "")
    redirect = q.get("redirect_uri", "")
    challenge = q.get("code_challenge", "")
    state = q.get("state", "")
    resource = q.get("resource", RESOURCE)
    scope = q.get("scope", "discord:read").strip() or "discord:read"
    if (
        q.get("response_type") != "code" or q.get("code_challenge_method") != "S256"
        or not 43 <= len(challenge) <= 128 or not all(c.isalnum() or c in "-_" for c in challenge)
        or not 16 <= len(state) <= 1024 or resource != RESOURCE
        or not set(scope.split()).issubset(SCOPES) or "discord:read" not in scope.split()
    ):
        return HTMLResponse("Invalid OAuth request", status_code=400)
    client = store().client_for_redirect(client_id, redirect)
    if not client:
        return HTMLResponse("Unknown OAuth client or redirect URI", status_code=400)
    account = _ailinux_account(request)
    if not account:
        url = PUBLIC + "/authorize?" + str(q)
        return RedirectResponse(
            "https://login.ailinux.me/?" + urlencode({"redirect": url, "google": "1"}),
            status_code=302,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    csrf = store().begin_authorize(account, client_id, redirect, state, challenge, scope)
    safe_name = html.escape(client["name"])
    safe_account = html.escape(account)
    safe_redirect = html.escape(urlsplit(redirect).hostname or "")
    long_access = ("<br>Dauerhafte Verbindung: <b>ja, widerrufbar</b>" if
                   "offline_access" in scope.split() else "")
    page = f"""<!doctype html><html lang="de"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="referrer" content="same-origin"><title>AILinux MCP erlauben</title>
    <style>body{{background:#101725;color:#f1f5ff;font:16px system-ui;margin:10vh auto;max-width:540px;padding:24px}}
    section{{border:1px solid #35445f;border-radius:14px;padding:24px;background:#192438}}
    button{{background:#5865f2;color:white;border:0;border-radius:8px;padding:12px 18px;cursor:pointer;margin:8px 8px 0 0}}
    button[value=no]{{background:#35445f}}p{{line-height:1.6;color:#bac7dc}}</style>
    <section><h1>MCP-Verbindung freigeben</h1>
    <p><b>{safe_name}</b> möchte über dein AILinux-Konto Discord-Statusinformationen lesen.</p>
    <p>Angemeldet bei AILinux: <b>{safe_account}</b><br>
    Google-Anmeldung erfolgt ausschließlich über <b>login.ailinux.me</b>;
    weder Google-Zugangsdaten noch dein AILinux-Login-Token werden an ChatGPT übertragen.<br>
    Client-Redirect: <b>{safe_redirect}</b><br>
    Berechtigung: <b>discord:read</b> (nur lesend){long_access}<br>Die Verbindung kann ein erneuerbares Zugriffstoken erhalten und jederzeit im AILinux-Konto widerrufen werden.</p>
    <form method="POST" action="/authorize/approve">
    <input type="hidden" name="csrf" value="{html.escape(csrf)}">
    <button name="allow" value="yes" type="submit">Verbindung erlauben</button>
    <button name="allow" value="no" type="submit">Ablehnen</button>
    </form></section></html>"""
    # Referrer-Policy: no-referrer sends Origin:null on normal Chrome form POSTs.
    # Use same-origin: preserves strict origin checking and never leaks OAuth
    # authorization codes, state or consent URLs to external redirects.
    response = HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin",
                                           "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
    response.set_cookie("dmcp_consent", csrf, max_age=300, httponly=True,
                        secure=True, samesite="lax", path="/")
    return response


@router.post("/authorize/approve")
async def approve(request: Request):
    origin = request.headers.get("origin")
    if origin and origin != PUBLIC:
        return HTMLResponse("Invalid request origin", status_code=403)
    account = _ailinux_account(request)
    if not account:
        return HTMLResponse("Session expired. Sign in again.", status_code=401)
    data = await _urlencoded(request)
    csrf = data.get("csrf", "")
    if not csrf or csrf != request.cookies.get("dmcp_consent"):
        return HTMLResponse("Consent verification failed", status_code=403)
    record = store().finish_authorize(csrf, account, data.get("allow") == "yes")
    if not record:
        return HTMLResponse("Consent expired or already used", status_code=403)
    payload = {"state": record["state"]}
    if "code" in record:
        payload["code"] = record["code"]
    else:
        payload["error"] = "access_denied"
    redirect = record["redirect_uri"]
    glue = "&" if "?" in redirect else "?"
    response = RedirectResponse(redirect + glue + urlencode(payload), status_code=303,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.delete_cookie("dmcp_consent", path="/")
    return response


@router.post("/token")
async def token(request: Request):
    data = await _urlencoded(request)
    client_id = data.get("client_id", "")
    grant = data.get("grant_type", "")
    if data.get("resource", RESOURCE) != RESOURCE:
        return _oauth_error("invalid_target")
    if data.get("client_secret"):
        return _oauth_error("invalid_client", 401)
    if grant == "authorization_code":
        if not store().client_for_redirect(client_id, data.get("redirect_uri", "")):
            return _oauth_error("invalid_client", 401)
        result = store().exchange_code(data.get("code", ""), client_id,
                                       data.get("redirect_uri", ""), data.get("code_verifier", ""))
    elif grant == "refresh_token":
        result = store().refresh(data.get("refresh_token", ""), client_id)
    else:
        return _oauth_error("unsupported_grant_type")
    return _no_cache(result) if result else _oauth_error("invalid_grant")
