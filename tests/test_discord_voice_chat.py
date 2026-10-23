"""Offline contract tests for the private TriForce Discord chat CLI."""
import pytest
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/discord_voice_chat.py'
spec = importlib.util.spec_from_file_location('discord_voice_chat', SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def test_normalize_spoken_answer_removes_thinking_and_markup():
    text = bridge.normalize_answer('<think>internal</think> **Servus** Zombie!')
    assert text == 'Servus Zombie!'


def test_request_validation_rejects_unknown_actions_and_identities():
    import pytest
    with pytest.raises(ValueError):
        bridge.validate_request({'guild_id': bridge.GUILD_ID,
                                 'user_id': bridge.OWNER_ID, 'action': 'exec'})
    with pytest.raises(PermissionError):
        bridge.validate_request({'guild_id': bridge.GUILD_ID,
                                 'user_id': '111111111111111111', 'action': 'history'})
    valid, action = bridge.validate_request({'guild_id': bridge.GUILD_ID,
                                              'user_id': bridge.OWNER_ID,
                                              'action': 'history'})
    assert action == 'history'


def test_wrong_guild_does_not_trigger_chat_api():
    request = {'guild_id': 'wrong', 'user_id': bridge.OWNER_ID,
               'message': 'test', 'history': []}
    p = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(request),
                       text=True, capture_output=True, timeout=10)
    assert p.returncode != 0
    assert json.loads(p.stdout)['error'] == 'PermissionError'

@pytest.mark.asyncio
async def test_voice_model_fallback_preserves_history(monkeypatch):
    from app.services.chat_router import api_proxy
    from app.config import get_settings
    import pytest
    calls = []
    async def fake_chat(model, messages, **kwargs):
        calls.append((model, messages))
        if model.endswith('gpt-oss-20b'):
            raise RuntimeError('synthetic upstream outage')
        return 'Du hast vorhin die Farbe Azurblau genannt.'
    monkeypatch.setattr(api_proxy, 'chat', fake_chat)
    history = [
        {'role': 'user', 'content': 'Meine Lieblingsfarbe ist Azurblau.'},
        {'role': 'assistant', 'content': 'Alles klar.'},
    ]
    answer, model = await bridge.respond('Welche Farbe habe ich genannt?', history)
    assert 'Azurblau' in answer
    assert model == 'groq/openai/gpt-oss-120b'
    assert len(calls) == 2
    assert calls[0][1][-3]['content'] == 'Meine Lieblingsfarbe ist Azurblau.'
    assert calls[1][1][-1]['content'] == 'Welche Farbe habe ich genannt?'
