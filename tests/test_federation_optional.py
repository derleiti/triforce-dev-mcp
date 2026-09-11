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


def test_federation_websocket_uses_backend_port():
    from app.services.federation_websocket import _peer_backend_port

    assert _peer_backend_port({"backend_port": 9100, "port": 9000}) == 9100
    assert _peer_backend_port({"port": 9000}) == 9000
    assert _peer_backend_port({"port": 9100}) == 9100
    assert _peer_backend_port({"backend_port": "bad"}) == 9100
    assert _peer_backend_port({"backend_port": 70000}) == 9100


def test_federation_proxy_ip_recovery_is_local_only(monkeypatch):
    import app.routes.federation as route

    monkeypatch.setattr(route, "_local_ipv4_addresses", lambda: {"127.0.0.1", "138.201.50.230"})
    assert route._federation_auth_client_ip("zombie-pc", "138.201.50.230") == "10.10.0.2"
    assert route._federation_auth_client_ip("backup", "127.0.0.1") == "10.10.0.3"
    assert route._federation_auth_client_ip("zombie-pc", "203.0.113.50") == "203.0.113.50"
    assert route._federation_auth_client_ip("unknown", "138.201.50.230") == "138.201.50.230"
