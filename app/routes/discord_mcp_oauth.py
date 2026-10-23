"""Independent OAuth2 Authorization Server for discord.ailinux.me/mcp.

Uses the real signed AILinux login cookie, explicit user consent, DCR, S256
PKCE and opaque audience-restricted tokens. Not the legacy MCP shared password.
"""
from __future__ import annotations

import html
import json
import re
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
    # Chrome validates the final 303 target after a form POST against the
    # consent page's CSP form-action directive. Permit the registered callback
    # origin only. Validate it before interpolating into the CSP header.
    parts = urlsplit(redirect)
    callback_origin = f"{parts.scheme}://{parts.netloc}"
    https_ok = bool(re.fullmatch(r"https://(?:[A-Za-z0-9.-]+|\[[A-Fa-f0-9:]+\])(?::[0-9]{1,5})?", callback_origin))
    loopback_ok = (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}
                   and parts.port and bool(re.fullmatch(r"http://(?:localhost|127\.0\.0\.1|\[::1\]):[0-9]{1,5}", callback_origin)))
    if not (https_ok or loopback_ok):
        return HTMLResponse("Invalid callback origin", status_code=400)
    long_access = ("<br>Dauerhafte Verbindung: <b>ja, widerrufbar</b>" if
                   "offline_access" in scope.split() else "")
    import secrets
    script_nonce = secrets.token_urlsafe(20)
    page = f"""<!doctype html><html lang="de"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="referrer" content="same-origin"><title>AILinux MCP erlauben</title>
    <style>body{{background:#101725;color:#f1f5ff;font:16px system-ui;margin:10vh auto;max-width:540px;padding:24px}}
    section{{border:1px solid #35445f;border-radius:14px;padding:24px;background:#192438}}
    button{{background:#5865f2;color:white;border:0;border-radius:8px;padding:12px 18px;cursor:pointer;margin:8px 8px 0 0}}
    button.deny{{background:#35445f}}p{{line-height:1.6;color:#bac7dc}}
    .consent-actions{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
    .consent-actions form{{display:inline-block;margin:0}}
    button:disabled{{opacity:.55;cursor:wait}}
    #consent-progress{{font-size:14px;color:#8df2ba}}
    </style>
    <section><h1>MCP-Verbindung freigeben</h1>
    <p><b>{safe_name}</b> möchte über dein AILinux-Konto Discord-Statusinformationen lesen.</p>
    <p>Angemeldet bei AILinux: <b>{safe_account}</b><br>
    Google-Anmeldung erfolgt ausschließlich über <b>login.ailinux.me</b>;
    weder Google-Zugangsdaten noch dein AILinux-Login-Token werden an ChatGPT übertragen.<br>
    Client-Redirect: <b>{safe_redirect}</b><br>
    Berechtigung: <b>discord:read</b> (nur lesend){long_access}<br>Die Verbindung kann ein erneuerbares Zugriffstoken erhalten und jederzeit im AILinux-Konto widerrufen werden.</p>
    <div class="consent-actions">
    <form method="POST" action="/authorize/approve" class="consent-form">
    <input type="hidden" name="csrf" value="{html.escape(csrf)}">
    <input type="hidden" name="allow" value="yes">
    <button type="submit">Verbindung erlauben</button>
    </form>
    <form method="POST" action="/authorize/approve" class="consent-form">
    <input type="hidden" name="csrf" value="{html.escape(csrf)}">
    <input type="hidden" name="allow" value="no">
    <button type="submit" class="deny">Ablehnen</button>
    </form>
    </div>
    <p id="consent-progress" role="status" aria-live="polite"></p>
    </section>
    <script nonce="{script_nonce}">
    (() => {{
      let submitted = false;
      for (const form of document.querySelectorAll(".consent-form")) {{
        form.addEventListener("submit", event => {{
          if (submitted) {{ event.preventDefault(); return; }}
          submitted = true;
          const action = form.querySelector('input[name="allow"]').value;
          document.getElementById("consent-progress").textContent =
            action === "yes" ? "Freigabe gesendet. Weiterleitung zu ChatGPT läuft …" :
            "Ablehnung gesendet. Weiterleitung zu ChatGPT läuft …";
          // Hidden fields keep both the consent nonce and decision in form data,
          // even when all submit buttons become disabled on submission.
          for (const button of document.querySelectorAll('button[type="submit"]')) {{
            button.disabled = true;
          }}
        }});
      }}
    }})();
    </script></html>"""
    # Referrer-Policy: no-referrer sends Origin:null on normal Chrome form POSTs.
    # Use same-origin: preserves strict origin checking and never leaks OAuth
    # authorization codes, state or consent URLs to external redirects.
    response = HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin",
                                           "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{script_nonce}'; style-src 'unsafe-inline'; form-action 'self' {callback_origin}; base-uri 'none'; frame-ancestors 'none'"})
    # An authorization has its own high-entropy, account-bound, single-use
    # consent nonce stored server-side. Do not use one mutable cookie across
    # independent OAuth tabs (old dmcp_consent cookies might also survive).
    response.delete_cookie("dmcp_consent", path="/", secure=True, samesite="lax")
    response.delete_cookie("dmcp_consent", path="/authorize", secure=True, samesite="lax")
    return response


@router.post("/authorize/approve")
async def approve(request: Request):
    origin = request.headers.get("origin")
    if origin and origin != PUBLIC:
        return HTMLResponse("Invalid request origin", status_code=403)
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site and fetch_site != "same-origin":
        return HTMLResponse("Invalid request site", status_code=403)
    account = _ailinux_account(request)
    if not account:
        return HTMLResponse("Session expired. Sign in again.", status_code=401)
    data = await _urlencoded(request)
    csrf = data.get("csrf", "")
    if not csrf or data.get("allow") not in {"yes", "no"}:
        return HTMLResponse("Consent verification failed", status_code=403)
    # The nonce is stored hashed and bound to the verified AILinux identity,
    # OAuth client, redirect, S256 challenge, and expiry. Atomic consume
    # prevents replay without depending on a browser-wide mutable cookie.
    record = store().finish_authorize(csrf, account, data["allow"] == "yes")
    if not record:
        return HTMLResponse(
            '<!doctype html><html lang="de"><meta charset="utf-8">'
            '<title>Freigabe bereits verwendet</title>'
            '<body><h1>Die Freigabe ist nicht mehr gültig</h1>'
            '<p>Diese Bestätigung wurde bereits verwendet oder ist abgelaufen.'
            ' Bitte kehre zum ChatGPT-Tab zurück. Falls dort keine Verbindung'
            ' zustande gekommen ist, starte die MCP-Anmeldung in ChatGPT neu.'
            ' Nicht erneut auf derselben Seite bestätigen.</p></body></html>',
            status_code=403, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    payload = {"state": record["state"]}
    if "code" in record:
        payload["code"] = record["code"]
    else:
        payload["error"] = "access_denied"
    redirect = record["redirect_uri"]
    glue = "&" if "?" in redirect else "?"
    response = RedirectResponse(redirect + glue + urlencode(payload), status_code=303,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
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
