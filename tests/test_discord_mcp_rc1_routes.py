import pytest
from fastapi import HTTPException
from app.routes.discord_mcp_rc1 import account_identity, PairRequest, pair
from app.services.discord_mcp_registry import DiscordRegistry


def test_missing_login_is_rejected():
    with pytest.raises(HTTPException) as exc:
        account_identity(None)
    assert exc.value.status_code == 401


def test_pair_request_limits():
    with pytest.raises(Exception):
        PairRequest(ttl_seconds=7200)


def test_pairing_is_account_scoped(tmp_path, monkeypatch):
    from app.routes import discord_mcp_rc1 as m
    database = DiscordRegistry(tmp_path / "rc1.db")
    monkeypatch.setattr(m, "registry", lambda: database)
    result = pair(PairRequest(), account="alice@example.org")
    assert database.redeem_pair(result["pairing_code"]) == "alice@example.org"
    assert database.redeem_pair(result["pairing_code"]) is None
