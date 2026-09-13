import base64
import hashlib

import pytest

from app.routes import client_auth


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(client_auth, "USER_REGISTRY", {
        "browser@example.test": {"tier": "pro", "name": "Browser User", "nova_entitlements": {}}
    })
    monkeypatch.setattr(client_auth, "CLIENT_REGISTRY", {})
    monkeypatch.setattr(client_auth, "ACTIVE_SESSIONS", {})
    monkeypatch.setattr(client_auth, "_browser_redis", False)
    monkeypatch.setattr(client_auth, "_BROWSER_CODE_FALLBACK", {})


@pytest.mark.asyncio
async def test_app_browser_code_pkce_exchange_and_replay_rejection():
    verifier = "v" * 64
    state = "state-" + "x" * 32
    redirect = "http://127.0.0.1:49152/callback"
    token = client_auth.create_jwt_token("browser-source", "pro", email="browser@example.test")

    issued = await client_auth.create_browser_code(
        client_auth.BrowserCodeRequest(
            purpose="app",
            app_id="ailinux-ai-coder",
            redirect_uri=redirect,
            code_challenge=_challenge(verifier),
            state=state,
        ),
        authorization=f"Bearer {token}",
    )
    assert issued["state"] == state
    login = await client_auth.exchange_browser_code(
        client_auth.BrowserCodeExchangeRequest(
            code=issued["code"], purpose="app", app_id="ailinux-ai-coder",
            redirect_uri=redirect, code_verifier=verifier,
        )
    )
    assert login.email == "browser@example.test"
    assert login.token

    with pytest.raises(client_auth.HTTPException) as replay:
        await client_auth.exchange_browser_code(
            client_auth.BrowserCodeExchangeRequest(
                code=issued["code"], purpose="app", app_id="ailinux-ai-coder",
                redirect_uri=redirect, code_verifier=verifier,
            )
        )
    assert replay.value.status_code == 400


@pytest.mark.asyncio
async def test_bad_pkce_consumes_code_and_does_not_leak_session():
    verifier = "a" * 64
    token = client_auth.create_jwt_token("browser-source", "pro", email="browser@example.test")
    issued = await client_auth.create_browser_code(
        client_auth.BrowserCodeRequest(
            purpose="app", app_id="ailinux-copa",
            redirect_uri="http://127.0.0.1:49153/callback",
            code_challenge=_challenge(verifier), state="s" * 32,
        ),
        authorization=f"Bearer {token}",
    )
    with pytest.raises(client_auth.HTTPException) as exc:
        await client_auth.exchange_browser_code(
            client_auth.BrowserCodeExchangeRequest(
                code=issued["code"], purpose="app", app_id="ailinux-copa",
                redirect_uri="http://127.0.0.1:49153/callback", code_verifier="b" * 64,
            )
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_wordpress_bridge_is_one_time_without_raw_browser_token():
    token = client_auth.create_jwt_token("browser-source", "pro", email="browser@example.test")
    issued = await client_auth.create_browser_code(
        client_auth.BrowserCodeRequest(purpose="wordpress"),
        authorization=f"Bearer {token}",
    )
    login = await client_auth.exchange_browser_code(
        client_auth.BrowserCodeExchangeRequest(code=issued["code"], purpose="wordpress")
    )
    assert login.email == "browser@example.test"
    assert login.token != token


def test_app_redirect_rejects_non_loopback_and_unknown_apps():
    with pytest.raises(client_auth.HTTPException):
        client_auth._validate_browser_app_redirect("ailinux-ai-coder", "https://evil.example/callback")
    with pytest.raises(client_auth.HTTPException):
        client_auth._validate_browser_app_redirect("unknown-app", "http://127.0.0.1:49152/callback")


def test_app_redirect_accepts_exact_verified_https_app_link_only():
    expected = "https://api.ailinux.me/v1/auth/browser/app-callback/ailinux-client"
    assert client_auth._validate_browser_app_redirect("ailinux-client", expected) == expected
    with pytest.raises(client_auth.HTTPException):
        client_auth._validate_browser_app_redirect(
            "ailinux-client", "https://api.ailinux.me/v1/auth/browser/app-callback/ailinux-ai-coder"
        )
    with pytest.raises(client_auth.HTTPException):
        client_auth._validate_browser_app_redirect("ailinux-client", expected + "?next=x")


@pytest.mark.asyncio
async def test_android_app_link_browser_code_uses_fragment_and_pkce_exchange():
    verifier = "z" * 64
    state = "android-state-" + "x" * 32
    redirect = "https://api.ailinux.me/v1/auth/browser/app-callback/ailinux-client"
    token = client_auth.create_jwt_token("browser-source", "pro", email="browser@example.test")
    issued = await client_auth.create_browser_code(
        client_auth.BrowserCodeRequest(
            purpose="app", app_id="ailinux-client", redirect_uri=redirect,
            code_challenge=_challenge(verifier), state=state,
        ),
        authorization=f"Bearer {token}",
    )
    assert issued["handoff_url"].startswith(redirect + "#code=")
    assert "&state=" in issued["handoff_url"]
    assert "?code=" not in issued["handoff_url"]
    login = await client_auth.exchange_browser_code(
        client_auth.BrowserCodeExchangeRequest(
            code=issued["code"], purpose="app", app_id="ailinux-client",
            redirect_uri=redirect, code_verifier=verifier,
        )
    )
    assert login.email == "browser@example.test"
