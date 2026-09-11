import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.routes import client_auth


@pytest.fixture(autouse=True)
def isolated_client_auth_state(monkeypatch):
    monkeypatch.setattr(client_auth, "USER_REGISTRY", {})
    monkeypatch.setattr(client_auth, "CLIENT_REGISTRY", {})
    monkeypatch.setattr(client_auth, "ACTIVE_SESSIONS", {})


@pytest.mark.asyncio
async def test_login_verify_and_handshake_include_copa_entitlements():
    client_auth.USER_REGISTRY["copa@example.test"] = {
        "password_hash": client_auth.hash_secret("secret"),
        "tier": "pro",
        "name": "Copa User",
        "nova_entitlements": {"copa_ocr": True},
    }

    login = await client_auth.user_login(
        client_auth.UserLoginRequest(email="copa@example.test", password="secret")
    )

    assert login.token
    assert login.tier == "pro"
    assert login.nova_entitlements == {"copa_ocr": True}

    auth_header = f"Bearer {login.token}"
    verified = await client_auth.verify_auth(auth_header)
    assert verified["valid"] is True
    assert verified["email"] == "copa@example.test"
    assert verified["nova_entitlements"] == {"copa_ocr": True}

    handshake = await client_auth.client_handshake(auth_header)
    assert handshake["valid"] is True
    assert handshake["endpoints"]["chat"] == "/v1/client/chat"
    assert handshake["endpoints"]["mcp"] == "/v1/mcp"
    assert handshake["capabilities"]["ocr"] is True


@pytest.mark.asyncio
async def test_legacy_free_tier_is_reported_as_guest():
    token = client_auth.create_jwt_token(
        "client-free",
        "free",
        email="free@example.test",
    )

    verified = await client_auth.verify_auth(f"Bearer {token}")

    assert verified["valid"] is True
    assert verified["tier"] == "guest"


@pytest.mark.asyncio
async def test_refresh_renews_still_valid_token_and_preserves_identity():
    client_auth.USER_REGISTRY["refresh@example.test"] = {
        "password_hash": client_auth.hash_secret("secret"),
        "tier": "pro",
        "name": "Refresh User",
    }
    original = client_auth.create_jwt_token(
        "client-refresh", "pro", email="refresh@example.test", expires_hours=1
    )

    refreshed = await client_auth.refresh_auth(f"Bearer {original}")
    payload = client_auth.decode_jwt_token(refreshed["token"])

    assert payload["client_id"] == "client-refresh"
    assert payload["email"] == "refresh@example.test"
    assert payload["tier"] == "pro"
    assert refreshed["expires_in"] == client_auth.JWT_EXPIRY_HOURS * 3600


@pytest.mark.asyncio
async def test_refresh_rejects_expired_token():
    expired = client_auth.create_jwt_token(
        "client-expired", "pro", email="expired@example.test", expires_hours=-1
    )

    with pytest.raises(client_auth.HTTPException) as exc:
        await client_auth.refresh_auth(f"Bearer {expired}")

    assert exc.value.status_code == 401


def test_resolve_authority_primary_owner_and_fresh_wordpress(monkeypatch):
    from datetime import datetime, timezone, timedelta
    monkeypatch.setenv("ADMIN_EMAIL", "owner@example.test")
    role, level, source = client_auth.resolve_user_authority("owner@example.test", {})
    assert role.value == "human_owner" and level == 100 and source == "primary_owner"

    user = {
        "wordpress_roles": ["administrator"],
        "wordpress_can_admin": True,
        "wordpress_authority_verified_at": datetime.now(timezone.utc).isoformat(),
    }
    role, level, source = client_auth.resolve_user_authority("admin@example.test", user)
    assert role.value == "human_admin" and level == 90 and source == "wordpress"

    stale = dict(user)
    stale["wordpress_authority_verified_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    role, level, source = client_auth.resolve_user_authority("admin@example.test", stale)
    assert role.value == "human_member" and level == 20


def test_enterprise_subscription_is_not_organizational_admin(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    role, level, _ = client_auth.resolve_user_authority(
        "paid@example.test", {"tier": "enterprise"}
    )
    assert role.value == "human_member"
    assert level == 20


@pytest.mark.asyncio
async def test_wordpress_admin_login_becomes_mcp_admin(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.setattr(client_auth, "save_user_to_file", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(client_auth, "verify_wordpress_login", lambda email, password: {
        "email": email,
        "name": "WP Admin",
        "tier": "free",
        "wordpress_roles": ["administrator"],
        "wordpress_can_admin": True,
        "authority_role": "",
        "nova_entitlements": {},
    })
    login = await client_auth.user_login(
        client_auth.UserLoginRequest(email="wpadmin@example.test", password="secret")
    )
    assert login.authority_role == "human_admin"
    assert login.authority_level == 90
    assert login.mcp_admin is True
    verified = await client_auth.verify_auth(f"Bearer {login.token}")
    assert verified["authority_role"] == "human_admin"
    assert verified["mcp_admin"] is True

@pytest.mark.asyncio
async def test_google_login_existing_user_does_not_require_password_or_wordpress(monkeypatch):
    email = "google@example.test"
    client_auth.USER_REGISTRY[email] = {
        "password_hash": client_auth.hash_secret("legacy-secret"),
        "tier": "pro",
        "name": "Google User",
    }
    monkeypatch.setattr(
        client_auth,
        "verify_google_credential",
        lambda credential: {
            "email": email,
            "email_verified": True,
            "name": "Google User",
            "sub": "google-sub-123",
        },
    )
    monkeypatch.setattr(client_auth, "save_user_to_file", lambda *_args, **_kwargs: True)

    login = await client_auth.google_login(client_auth.GoogleLoginRequest(credential="x" * 64))

    assert login.email == email
    assert login.token
    assert client_auth.USER_REGISTRY[email]["google_sub"] == "google-sub-123"
