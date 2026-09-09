from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.settings_store import (
    ConfigConflict,
    load_snapshot,
    redact,
    save_updates,
    validate_supported_values,
)


def clean_env() -> dict[str, str]:
    # Deterministic validation: no production/process settings override test data.
    return {"PATH": os.environ.get("PATH", "")}


def test_dotenv_is_data_not_shell(tmp_path: Path):
    marker = tmp_path / "owned"
    cfg = tmp_path / "triforce.env"
    cfg.write_text(f"REQUEST_TIMEOUT=$(touch {marker})\nTRIFORCE_API_PORT=9100\n")
    snap = load_snapshot(cfg)
    assert snap.values["REQUEST_TIMEOUT"] == f"$(touch {marker})"
    assert not marker.exists()


def test_roundtrip_preserves_comments_unknowns_and_quotes(tmp_path: Path):
    cfg = tmp_path / "triforce.env"
    cfg.write_text('# keep me\nUNKNOWN_FUTURE="a value"\nREQUEST_TIMEOUT=30\n')
    snap = load_snapshot(cfg)
    after = save_updates(
        {"REQUEST_TIMEOUT": 42.5, "TRIFORCE_BIND_HOST": "127.0.0.1"},
        path=cfg,
        expected_digest=snap.digest,
        environ=clean_env(),
    )
    text = cfg.read_text()
    assert "# keep me" in text
    assert 'UNKNOWN_FUTURE="a value"' in text
    assert "REQUEST_TIMEOUT=42.5" in text
    assert after.values["UNKNOWN_FUTURE"] == "a value"


def test_save_detects_parallel_change(tmp_path: Path):
    cfg = tmp_path / "triforce.env"
    cfg.write_text("REQUEST_TIMEOUT=30\n")
    snap = load_snapshot(cfg)
    cfg.write_text("REQUEST_TIMEOUT=31\n")
    with pytest.raises(ConfigConflict):
        save_updates({"REQUEST_TIMEOUT": 32}, path=cfg, expected_digest=snap.digest, environ=clean_env())


def test_validation_rejects_invalid_memory_limit():
    with pytest.raises(Exception):
        validate_supported_values({"TRIFORCE_MEMORY_MAX_RESULTS": "99"}, environ=clean_env())


def test_file_mode_and_single_last_good_are_protected(tmp_path: Path):
    cfg = tmp_path / "triforce.env"
    cfg.write_text("REQUEST_TIMEOUT=30\n")
    cfg.chmod(0o640)
    first = load_snapshot(cfg)
    save_updates({"REQUEST_TIMEOUT": 31}, path=cfg, expected_digest=first.digest, environ=clean_env())
    second = load_snapshot(cfg)
    save_updates({"REQUEST_TIMEOUT": 32}, path=cfg, expected_digest=second.digest, environ=clean_env())
    backup = tmp_path / "triforce.env.last-good"
    assert backup.exists()
    assert oct(cfg.stat().st_mode & 0o777) == "0o640"
    assert oct(backup.stat().st_mode & 0o777) == "0o640"
    assert not list(tmp_path.glob("*.bak*"))


def test_secret_redaction_is_explicit():
    result = redact({"OPENROUTER_API_KEY": "secret", "OPENROUTER_BASE_URL": "https://example.invalid"})
    assert result["OPENROUTER_API_KEY"] == "***"
    assert result["OPENROUTER_BASE_URL"] == "https://example.invalid"

def test_claude_agent_compatibility_aliases_do_not_collide():
    from app.config import Settings
    legacy = Settings.model_validate({"CLAUDE_AGENT_ID": "legacy"}, by_alias=True, by_name=True)
    assert legacy.claude_agent_id == "legacy"
    assert legacy.nova_claude_agent_id == "legacy"
    nova = Settings.model_validate({"NOVA_CLAUDE_AGENT_ID": "nova"}, by_alias=True, by_name=True)
    assert nova.nova_claude_agent_id == "nova"
    assert nova.claude_agent_id is None

def test_inventory_exposes_type_limits_choices_and_storage():
    from app.settings_store import settings_inventory
    inventory = {m.name: m for m in settings_inventory()}
    port = inventory["server_port"]
    assert port.value_type == "integer"
    assert port.storage == "config-file"
    assert port.required_restart is True
    assert port.minimum == 1
    assert port.maximum == 65535
    assert isinstance(port.choices, tuple)

def test_inventory_has_required_control_center_categories():
    from app.settings_store import settings_inventory
    categories = {m.category for m in settings_inventory()}
    assert {"Provider & Modelle", "MCP & Sicherheit", "Agenten", "Memory", "Server & Integrationen"}.issubset(categories)

def test_redacted_raw_roundtrip_preserves_secret_and_comments(tmp_path):
    from app.settings_store import redact_dotenv_text, restore_masked_secrets, save_raw_text, load_snapshot
    p = tmp_path / "triforce.env"
    original = "# keep me\nMCP_OAUTH_PASS=secret-value\nUNKNOWN_FLAG=old\n"
    p.write_text(original)
    snap = load_snapshot(p)
    redacted = redact_dotenv_text(original)
    assert "secret-value" not in redacted
    edited = redacted.replace("UNKNOWN_FLAG=old", "UNKNOWN_FLAG=new")
    restored = restore_masked_secrets(edited, original)
    assert "MCP_OAUTH_PASS=secret-value" in restored
    save_raw_text(restored, path=p, expected_digest=snap.digest, environ={})
    assert p.read_text() == "# keep me\nMCP_OAUTH_PASS=secret-value\nUNKNOWN_FLAG=new\n"


def test_raw_save_detects_parallel_change(tmp_path):
    from app.settings_store import ConfigConflict, load_snapshot, save_raw_text
    p = tmp_path / "triforce.env"; p.write_text("REQUEST_TIMEOUT=10\n")
    snap = load_snapshot(p); p.write_text("REQUEST_TIMEOUT=11\n")
    import pytest
    with pytest.raises(ConfigConflict):
        save_raw_text("REQUEST_TIMEOUT=12\n", path=p, expected_digest=snap.digest, environ={})
