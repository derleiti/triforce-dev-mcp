from __future__ import annotations

import importlib
import os


def test_federation_module_imports_without_secret(monkeypatch):
    monkeypatch.delenv("FEDERATION_SECRET", raising=False)
    import app.services.server_federation as mod
    mod = importlib.reload(mod)
    assert mod.FEDERATION_PSK == ""
    assert mod.verify_signed_request({"data": {}, "timestamp": "0", "signature": ""}) is None


def test_federation_signing_fails_closed_without_secret(monkeypatch):
    monkeypatch.delenv("FEDERATION_SECRET", raising=False)
    import app.services.server_federation as mod
    mod = importlib.reload(mod)
    try:
        mod.create_signed_request({"x": 1})
    except RuntimeError as exc:
        assert "disabled" in str(exc).lower()
    else:
        raise AssertionError("signing without a configured federation secret must fail")
