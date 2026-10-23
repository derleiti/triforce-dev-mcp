import sqlite3
import pytest
from app.services.discord_mcp_registry import DiscordRegistry

USER = "123456789012345678"
OTHER = "223456789012345678"
GUILD = "323456789012345678"

@pytest.fixture
def registry(tmp_path):
    return DiscordRegistry(tmp_path / "discord-test.sqlite3")

def test_default_deny_and_scoped_grants(registry):
    assert not registry.can("alice", USER, GUILD, "voice:join")
    registry.link("alice", USER)
    registry.grant("alice", USER, GUILD, {"voice:join", "mcp:read"})
    assert registry.can("alice", USER, GUILD, "voice:join")
    assert not registry.can("alice", USER, GUILD, "text:send")
    assert not registry.can("bob", USER, GUILD, "voice:join")
    assert not registry.can("alice", USER, OTHER, "voice:join")
    assert not registry.can("alice", USER, OTHER, "mcp:read")

def test_unlinked_and_invalid_scopes_rejected(registry):
    with pytest.raises(PermissionError):
        registry.grant("alice", USER, GUILD, {"voice:join"})
    registry.link("alice", USER)
    with pytest.raises(ValueError):
        registry.grant("alice", USER, GUILD, {"shell:execute"})

def test_discord_identity_exclusive(registry):
    registry.link("alice", USER)
    with pytest.raises(sqlite3.IntegrityError):
        registry.link("bob", USER)

def test_pair_one_time_and_revocable(registry):
    token = registry.create_pair("alice")
    assert registry.redeem_pair(token) == "alice"
    assert registry.redeem_pair(token) is None
    token = registry.create_pair("alice")
    registry.revoke("alice")
    assert registry.redeem_pair(token) is None

def test_revocation_clears_access(registry):
    registry.link("alice", USER)
    registry.grant("alice", USER, GUILD, {"voice:join"})
    registry.revoke("alice")
    assert not registry.can("alice", USER, GUILD, "voice:join")
