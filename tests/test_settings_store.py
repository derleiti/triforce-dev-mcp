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
