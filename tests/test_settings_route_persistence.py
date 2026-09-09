from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routes.settings import _update_to_env, update_settings_endpoint
from app.schemas.settings import SettingsUpdate
from app.settings_store import load_snapshot


def test_update_schema_maps_to_canonical_environment_aliases():
    updates = SettingsUpdate(request_timeout=42.5, auto_crawler_enabled=False, openai_model_aliases={"x": "y"})
    assert _update_to_env(updates) == {
        "REQUEST_TIMEOUT": 42.5,
        "AUTO_CRAWLER_ENABLED": False,
        "OPENAI_MODEL_ALIASES": {"x": "y"},
    }


def test_settings_api_persists_without_mutating_process_environment(tmp_path: Path, monkeypatch):
    config = tmp_path / "triforce.env"
    config.write_text("REQUEST_TIMEOUT=10\nUNKNOWN_BETA_VALUE=keep\n")
    monkeypatch.setenv("TRIFORCE_CONFIG_FILE", str(config))
    monkeypatch.delenv("REQUEST_TIMEOUT", raising=False)
    before = load_snapshot(config)

    asyncio.run(update_settings_endpoint(
        SettingsUpdate(request_timeout=33, expected_digest=before.digest),
        _admin={"role": "admin"},
    ))

    text = config.read_text()
    assert "REQUEST_TIMEOUT=33" in text
    assert "UNKNOWN_BETA_VALUE=keep" in text
    assert "REQUEST_TIMEOUT" not in __import__("os").environ


def test_settings_api_detects_stale_digest(tmp_path: Path, monkeypatch):
    config = tmp_path / "triforce.env"
    config.write_text("REQUEST_TIMEOUT=10\n")
    monkeypatch.setenv("TRIFORCE_CONFIG_FILE", str(config))

    with pytest.raises(HTTPException) as caught:
        asyncio.run(update_settings_endpoint(
            SettingsUpdate(request_timeout=20, expected_digest="0" * 64),
            _admin={"role": "admin"},
        ))
    assert caught.value.status_code == 409
