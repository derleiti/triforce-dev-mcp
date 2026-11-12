"""Regression tests for persistent Copa OCR account entitlements."""
import json

from app.routes import client_auth


def test_persist_entitlement_without_losing_other_accounts(tmp_path, monkeypatch):
    path = tmp_path / "users.json"
    path.write_text(json.dumps({
        "another@example.test": {"nova_entitlements": {"other": True}}
    }))
    monkeypatch.setattr(client_auth, "USERS_FILE_PATH", path)

    assert client_auth.save_user_to_file(
        "ADMIN@AILINUX.ME",
        {"nova_entitlements": {"copa_ocr": True}},
    )
    users = json.loads(path.read_text())
    assert users["admin@ailinux.me"]["nova_entitlements"]["copa_ocr"]
    assert users["another@example.test"]["nova_entitlements"]["other"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_failed_write_keeps_existing_entitlement_file(tmp_path, monkeypatch):
    path = tmp_path / "users.json"
    original = '{"admin@ailinux.me":{"nova_entitlements":{"copa_ocr":true}}}\n'
    path.write_text(original)
    monkeypatch.setattr(client_auth, "USERS_FILE_PATH", path)

    assert not client_auth.save_user_to_file(
        "other@example.test", {"non_serializable": object()}
    )
    assert path.read_text() == original
    assert not list(tmp_path.glob(".users.*.tmp"))
