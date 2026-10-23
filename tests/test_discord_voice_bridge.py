"""Nova slash command must be Discord-signed, owner-scoped and deferred."""
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routes import discord_interactions
from app.routes import discord_voice_bridge as bridge


def build_request(private_key, payload, *, tamper=False):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = private_key.sign(timestamp.encode() + raw)
    if tamper:
        signature = bytes(64)
    return raw, {
        "X-Signature-Ed25519": signature.hex(),
        "X-Signature-Timestamp": timestamp,
        "Content-Type": "application/json",
    }


def nova_payload(name, user_id=bridge.OWNER_ID, *, text=None):
    option = {"type": 1, "name": name}
    if text is not None:
        option["options"] = [{"name": "text", "type": 3, "value": text}]
    return {
        "type": 2, "application_id": bridge.APPLICATION_ID,
        "guild_id": bridge.GUILD_ID, "token": "test-token-for-background-handler",
        "member": {"user": {"id": user_id}, "permissions": "8"},
        "data": {"name": "nova", "options": [option]},
    }


@pytest.fixture
def signed_app(monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(discord_interactions, "_public_key", key.public_key())
    app = FastAPI()
    app.include_router(discord_interactions.router)
    return key, app


@pytest.mark.asyncio
async def test_owner_status_deferred_then_processed(signed_app, monkeypatch):
    key, app = signed_app
    calls = []
    async def fake_finish(action, user_id, text, token):
        calls.append((action, user_id, text, token))
    monkeypatch.setattr(bridge, "finish_nova_command", fake_finish)
    raw, headers = build_request(key, nova_payload("status"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/discord", content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"type": 5, "data": {"flags": 64}}
    assert len(calls) == 1 and calls[0][0] == "status"


@pytest.mark.asyncio
async def test_nonowner_cannot_use_join(signed_app, monkeypatch):
    key, app = signed_app
    calls = []
    async def fake_finish(*args):
        calls.append(args)
    monkeypatch.setattr(bridge, "finish_nova_command", fake_finish)
    raw, headers = build_request(key, nova_payload("join", "3333886137030283296"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/discord", content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json()["type"] == 4
    assert response.json()["data"]["flags"] == 64
    assert not calls


@pytest.mark.asyncio
async def test_invalid_signature_blocks_voice(signed_app, monkeypatch):
    key, app = signed_app
    calls = []
    async def fake_finish(*args):
        calls.append(args)
    monkeypatch.setattr(bridge, "finish_nova_command", fake_finish)
    raw, headers = build_request(key, nova_payload("join"), tamper=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/discord", content=raw, headers=headers)
    assert response.status_code == 401
    assert not calls


@pytest.mark.asyncio
async def test_say_validates_character_count(signed_app, monkeypatch):
    key, app = signed_app
    calls = []
    async def fake_finish(action, user_id, text, token):
        calls.append((action,text))
    monkeypatch.setattr(bridge, "finish_nova_command", fake_finish)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        valid, vh = build_request(key, nova_payload("say", text="Servus, Zombie!"))
        v = await client.post("/discord", content=valid, headers=vh)
        invalid, ih = build_request(key, nova_payload("say", text="x" * 401))
        i = await client.post("/discord", content=invalid, headers=ih)
    assert v.json()["type"] == 5
    assert i.json()["type"] == 4
    assert calls == [("say", "Servus, Zombie!")]


@pytest.mark.asyncio
async def test_wrong_guild_rejected(signed_app, monkeypatch):
    key, app = signed_app
    payload = nova_payload("leave")
    payload["guild_id"] = "1333886137030283297"
    raw, headers = build_request(key, payload)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/discord", content=raw, headers=headers)
    assert result.json()["type"] == 4
    assert result.json()["data"]["flags"] == 64

@pytest.mark.asyncio
async def test_listening_controls_only_allow_owner(signed_app, monkeypatch):
    key, app = signed_app
    seen = []
    async def fake_finish(action, user_id, text, token):
        seen.append((action, user_id))
    monkeypatch.setattr(bridge, 'finish_nova_command', fake_finish)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        for action in ('listen', 'quiet'):
            raw, headers = build_request(key, nova_payload(action))
            response = await client.post('/discord', content=raw, headers=headers)
            assert response.json()['type'] == 5
        raw, headers = build_request(key, nova_payload('listen', '111111111111111111'))
        response = await client.post('/discord', content=raw, headers=headers)
        assert response.json()['type'] == 4
    assert seen == [('listen', bridge.OWNER_ID), ('quiet', bridge.OWNER_ID)]


@pytest.mark.asyncio
async def test_listening_state_calls_rpc(monkeypatch):
    calls=[]
    async def fake_rpc(data):
        calls.append(data)
        return {'ok': True, 'listening': data.get('enabled', False)}
    monkeypatch.setattr(bridge, 'voice_rpc', fake_rpc)
    enabled=await bridge.do_nova_action('listen', bridge.OWNER_ID, '')
    disabled=await bridge.do_nova_action('quiet', bridge.OWNER_ID, '')
    assert calls == [{'op': 'listen', 'enabled': True}, {'op': 'listen', 'enabled': False}]
    assert 'hört' in enabled and 'ausgeschaltet' in disabled

@pytest.mark.asyncio
async def test_history_exposed_only_through_owner_scoped_command(monkeypatch, tmp_path):
    from app.services.discord_voice_history import VoiceHistoryStore
    import uuid
    monkeypatch.setenv('NOVA_VOICE_HISTORY_DB', str(tmp_path / 'voice.sqlite3'))
    storage = VoiceHistoryStore()
    storage.save(str(uuid.uuid4()), bridge.GUILD_ID, bridge.OWNER_ID,
                 'Ich benutze KDE Plasma.', 'Verstanden, du nutzt KDE Plasma.')
    message = await bridge.do_nova_action('history', bridge.OWNER_ID, '')
    assert 'Ich benutze KDE Plasma.' in message
    assert 'Verstanden, du nutzt KDE Plasma.' in message

    async def fake_status(_request):
        return {'ok': True, 'connected': True, 'channel_name': 'Allgemein', 'listening': True}
    monkeypatch.setattr(bridge, 'voice_rpc', fake_status)
    status = await bridge.do_nova_action('status', bridge.OWNER_ID, '')
    assert 'Gespeicherte Gesprächsrunden: 1' in status
    assert 'Zuhören: an' in status
    after = await bridge.do_nova_action('forget', bridge.OWNER_ID, '')
    assert '1 Runde(n)' in after
    assert storage.count(bridge.GUILD_ID, bridge.OWNER_ID) == 0


@pytest.mark.asyncio
async def test_signed_history_requires_guild_owner(signed_app, monkeypatch):
    key, app = signed_app
    called = []
    async def fake_finish(action, user_id, text, token):
        called.append(action)
    monkeypatch.setattr(bridge, 'finish_nova_command', fake_finish)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        owner_raw, owner_headers = build_request(key, nova_payload('history'))
        owner = await client.post('/discord', content=owner_raw, headers=owner_headers)
        bad_raw, bad_headers = build_request(key, nova_payload('forget', user_id='111111111111111111'))
        rejected = await client.post('/discord', content=bad_raw, headers=bad_headers)
    assert owner.json()['type'] == 5
    assert rejected.json()['type'] == 4
    assert called == ['history']

@pytest.mark.asyncio
async def test_free_talk_discord_command_is_owner_only(signed_app, monkeypatch):
    key, app = signed_app
    requested = []
    async def fake_finish(action, user_id, text, token):
        requested.append((action, user_id))
    monkeypatch.setattr(bridge, 'finish_nova_command', fake_finish)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        content, headers = build_request(key, nova_payload('talk'))
        response = await client.post('/discord', content=content, headers=headers)
        assert response.json()['type'] == 5
        bad_content, bad_headers = build_request(key, nova_payload('talk', '111111111111111111'))
        denied = await client.post('/discord', content=bad_content, headers=bad_headers)
        assert denied.json()['type'] == 4
    assert requested == [('talk', bridge.OWNER_ID)]


@pytest.mark.asyncio
async def test_free_talk_calls_voice_bridge(monkeypatch):
    requests = []
    async def fake_rpc(request):
        requests.append(request)
        return {'ok': True, 'listening': True, 'conversation_mode': True}
    monkeypatch.setattr(bridge, 'voice_rpc', fake_rpc)
    msg = await bridge.do_nova_action('talk', bridge.OWNER_ID, '')
    assert requests == [{'op': 'talk'}]
    assert 'ohne Aktivierungswort' in msg
