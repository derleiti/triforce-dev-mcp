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


def test_inaccurate_voice_claims_are_detected():
    examples = [
        'Ich bin nur textbasiert und habe keine Sprachfunktion.',
        'Ich kann dir in kurzen Textnachrichten helfen.',
        'Ich habe keine Sprachausgabe.',
        'Ich kann leider keine Texte laut vorlesen.',
        'Dann passe ich die Lautstärke an.',
    ]
    for text in examples:
        assert bridge.inaccurate_voice_output(text), text
    assert not bridge.inaccurate_voice_output('Ich kann deine Stimme über Discord hören und antworte mit Seraphina.')
    assert not bridge.inaccurate_voice_output('Deinen Bildschirm kann ich aktuell nicht sehen.')


def test_old_false_history_is_corrected_only_in_model_context():
    incorrect = 'Ich bin nur textbasiert, ich kann leider nicht sprechen.'
    history = [
        {'role': 'user', 'content': 'Kannst du mich hören?'},
        {'role': 'assistant', 'content': incorrect},
    ]
    prompts = bridge.prepare_messages('Und jetzt?', history)
    assert prompts[1]['content'] == 'Kannst du mich hören?'
    assert 'Korrektur' in prompts[2]['content']
    assert incorrect not in [p['content'] for p in prompts]
    assert prompts[-1]['content'] == 'Und jetzt?'
    assert history[1]['content'] == incorrect  # Database archive remains unchanged.


@pytest.mark.asyncio
async def test_invalid_first_model_is_replaced_by_grounded_second_model(monkeypatch):
    from app.services.chat_router import api_proxy
    calls = []
    async def chat(model, messages, **kwargs):
        calls.append(model)
        if model.endswith('gpt-oss-20b'):
            return 'Ich bin leider nur textbasiert und kann nicht sprechen.'
        return 'Ja, wir reden über Discord und ich antworte mit Seraphina.'
    monkeypatch.setattr(api_proxy, 'chat', chat)
    answer, model = await bridge.respond('Kannst du sprechen?', [])
    assert 'wir reden über Discord' in answer
    assert model.endswith('120b')
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_no_false_voice_claim_escapes_if_models_repeat_it(monkeypatch):
    from app.services.chat_router import api_proxy
    async def chat(*args, **kwargs):
        return 'Ich kann dir nur in kurzen Textnachrichten helfen.'
    monkeypatch.setattr(api_proxy, 'chat', chat)
    answer, model = await bridge.respond('Kannst du beim WoW-Zocken zuschauen?', [])
    assert 'Bildschirm' in answer
    assert 'Seraphina' in answer
    assert model == 'voice-grounding-fallback'
    assert not bridge.inaccurate_voice_output(answer)
