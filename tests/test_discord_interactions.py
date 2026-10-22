"""Discord Interactions: signed requests only, including the initial PING."""
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routes import discord_interactions


@pytest.fixture
def signed_client(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(discord_interactions, "_public_key", private_key.public_key())
    app = FastAPI()
    app.include_router(discord_interactions.router)
    return private_key, AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _post_signed(key, client, payload, *, stale=False, tamper=False):
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()) - (600 if stale else 0))
    signature = key.sign(timestamp.encode() + body)
    if tamper:
        signature = b"\x00" * 64
    return await client.post(
        "/discord",
        content=body,
        headers={
            "X-Signature-Ed25519": signature.hex(),
            "X-Signature-Timestamp": timestamp,
            "Content-Type": "application/json",
        },
    )


@pytest.mark.asyncio
async def test_signed_ping_returns_pong(signed_client):
    key, client = signed_client
    async with client:
        response = await _post_signed(key, client, {"type": 1})
    assert response.status_code == 200
    assert response.json() == {"type": 1}


@pytest.mark.asyncio
async def test_invalid_stale_missing_signatures_are_rejected(signed_client):
    key, client = signed_client
    async with client:
        bad = await _post_signed(key, client, {"type": 1}, tamper=True)
        stale = await _post_signed(key, client, {"type": 1}, stale=True)
        missing = await client.post("/discord", json={"type": 1})
    assert (bad.status_code, stale.status_code, missing.status_code) == (401, 401, 401)


@pytest.mark.asyncio
async def test_signed_command_is_private_and_nonprivileged(signed_client):
    key, client = signed_client
    async with client:
        response = await _post_signed(key, client, {"type": 2, "data": {"name": "unknown"}})
    assert response.status_code == 200
    assert response.json()["data"]["flags"] == 64


@pytest.mark.asyncio
async def test_signed_autocomplete_returns_empty_choices(signed_client):
    key, client = signed_client
    async with client:
        response = await _post_signed(key, client, {"type": 4})
    assert response.status_code == 200
    assert response.json() == {"type": 8, "data": {"choices": []}}
