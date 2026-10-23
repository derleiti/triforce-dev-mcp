"""Persistent Nova Discord voice memory regression tests."""
import importlib.util
import os
import stat
import uuid
from pathlib import Path

import pytest

from app.services.discord_voice_history import MAX_TURNS, VoiceHistoryStore

GUILD = "1333886137030283296"
USER = "965583496435367956"


def test_conversation_survives_new_process_and_is_private(tmp_path):
    path = tmp_path / "state" / "voice.sqlite3"
    first = VoiceHistoryStore(path)
    ref = str(uuid.uuid4())
    turn = first.save(ref, GUILD, USER, "Meine Testzahl ist 47.", "Ich merke mir 47.")
    assert turn["response"] == "Ich merke mir 47."
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    second = VoiceHistoryStore(path)
    assert second.count(GUILD, USER) == 1
    assert second.messages(GUILD, USER) == [
        {"role": "user", "content": "Meine Testzahl ist 47."},
        {"role": "assistant", "content": "Ich merke mir 47."},
    ]
    assert second.save(ref, GUILD, USER, "IGNORE THIS", "IGNORE THIS") == turn
    assert second.count(GUILD, USER) == 1
    assert second.clear(GUILD, USER) == 1
    assert VoiceHistoryStore(path).recent(GUILD, USER) == []


def test_history_is_scoped_and_bounded(tmp_path):
    db = VoiceHistoryStore(tmp_path / "scope.sqlite3")
    for n in range(MAX_TURNS + 4):
        db.save(str(uuid.uuid4()), GUILD, USER, "F{}".format(n), "A{}".format(n))
    assert db.count(GUILD, USER) == MAX_TURNS
    assert db.recent(GUILD, USER, 1)[0]["user"] == "F203"
    assert db.messages(GUILD, USER, 2)[0]["content"] == "F202"
    assert db.count(GUILD, "111111111111111111") == 0
    with pytest.raises(ValueError):
        db.count("bad-guild", USER)


def test_chat_errors_do_not_persist(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "scripts/discord_voice_chat.py"
    spec = importlib.util.spec_from_file_location("test_nova_voice_rpc", source)
    rpc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rpc)
    monkeypatch.setenv("NOVA_VOICE_HISTORY_DB", str(tmp_path / "failed.sqlite3"))

    async def fail(*args):
        raise RuntimeError("synthetic provider failure")

    monkeypatch.setattr(rpc, "respond", fail)
    with pytest.raises(RuntimeError, match="synthetic provider"):
        rpc.run({
            "action": "chat", "guild_id": GUILD, "user_id": USER,
            "message": "Eine gültige Frage", "request_id": str(uuid.uuid4()),
        })
    assert VoiceHistoryStore(tmp_path / "failed.sqlite3").count(GUILD, USER) == 0
