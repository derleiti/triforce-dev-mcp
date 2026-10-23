"""Safety and routing regressions for the Discord voice MCP tool broker."""
from __future__ import annotations

import uuid

import pytest

from app.services.discord_voice_history import VoiceHistoryStore
from app.services.discord_voice_tools import (
    READ_TOOLS,
    choose_read_tool,
    run_read_tool,
)
from scripts import discord_voice_agent as agent

GID = "1333886137030283296"
UID = "965583496435367956"


@pytest.mark.parametrize(("utterance", "expected"), [
    ("Nova, prüf mal den AILinux Serverstatus", "triforce_server_status"),
    ("Wie läuft der TriForce Backend Server?", "triforce_server_status"),
    ("Sind die TriForce KI-Modelle im Katalog?", "triforce_model_catalog"),
    ("Welche LLM Modelle sind verfügbar?", "triforce_model_catalog"),
    ("Bitte gib den Status meines WoW Hexers", None),
    ("Was kann man mit einem Kernel machen?", None),
    ("Bitte den Server neu starten und Status prüfen", None),
    ("Zeig mir alle MCP API Keys", None),
    ("Lösch alles auf dem AILinux Server", None),
    ("", None),
])
def test_intent_dispatch_is_narrow(utterance, expected):
    assert choose_read_tool(utterance) == expected


@pytest.mark.asyncio
async def test_unlisted_tools_are_rejected():
    with pytest.raises(PermissionError):
        await run_read_tool("server_control_exec")
    with pytest.raises(PermissionError):
        await run_read_tool("triforce_system_restart")
    assert READ_TOOLS == ("triforce_server_status", "triforce_model_catalog")


def test_owner_and_guild_mandatory(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVA_VOICE_HISTORY_DB", str(tmp_path / "history.sqlite3"))
    for invalid in [
        {"guild_id": GID, "user_id": "111111111111111111"},
        {"guild_id": "111111111111111111", "user_id": UID},
    ]:
        invalid.update(action="chat", message="Prüf den TriForce Serverstatus",
                       request_id=str(uuid.uuid4()))
        with pytest.raises(PermissionError):
            agent.run(invalid)
    assert not (tmp_path / "history.sqlite3").exists()


def test_read_tool_is_called_and_persisted_once(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVA_VOICE_HISTORY_DB", str(tmp_path / "history.sqlite3"))
    calls = []

    async def fake_tool(tool):
        calls.append(tool)
        return "TriForce ist online.", {"tool": tool, "read_only": True, "ok": True}

    monkeypatch.setattr(agent, "run_read_tool", fake_tool)
    request = dict(guild_id=GID, user_id=UID, action="chat",
                   message="Kannst du den AILinux Serverstatus prüfen?",
                   request_id=str(uuid.uuid4()))
    response = agent.run(request)
    assert response["response"] == "TriForce ist online."
    assert response["model"] == "mcp/triforce_server_status"
    assert response["history_count"] == 1
    assert response["mcp_tool"]["read_only"] is True

    retry = agent.run(request)
    assert retry["cached"] is True
    assert retry["history_count"] == 1
    assert calls == ["triforce_server_status"]

    memory = VoiceHistoryStore(tmp_path / "history.sqlite3")
    assert memory.recent(GID, UID, limit=1)[0]["user"] == request["message"]


def test_normal_questions_fall_through_to_chat(monkeypatch):
    sentinel = {"ok": True, "response": "Das ist der normale KI-Chat."}
    calls = []

    def baseline(request):
        calls.append(request)
        return sentinel

    monkeypatch.setattr(agent.baseline, "run", baseline)
    request = dict(guild_id=GID, user_id=UID, action="chat",
                   message="Brumo, wo ist mein Honig?", request_id=str(uuid.uuid4()))
    assert agent.run(request) is sentinel
    assert calls == [request]


def test_unavailable_read_tool_never_claims_success(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVA_VOICE_HISTORY_DB", str(tmp_path / "failed.sqlite3"))

    async def offline(_tool):
        raise RuntimeError("Synthetic offline MCP")

    monkeypatch.setattr(agent, "run_read_tool", offline)
    request = dict(guild_id=GID, user_id=UID, action="chat",
                   message="Wie läuft der TriForce Serverstatus?",
                   request_id=str(uuid.uuid4()))
    with pytest.raises(RuntimeError, match="Synthetic offline"):
        agent.run(request)
    assert VoiceHistoryStore(tmp_path / "failed.sqlite3").count(GID, UID) == 0
