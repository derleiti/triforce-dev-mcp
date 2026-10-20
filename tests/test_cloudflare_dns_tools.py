import asyncio

import pytest

from app.mcp.cloudflare_dns_tools import CLOUDFLARE_DNS_TOOLS
from app.mcp.tool_registry_unified import (
    CANONICAL_TOOL_NAMES,
    TOOL_SCOPE_TRIFORCE_AUTH,
    get_canonical_all_tools,
    tool_scope,
)
from app.services.cloudflare_dns import (
    CloudflareDnsService,
    _redact,
)


def test_cloudflare_tools_are_canonical_authenticated_inventory():
    names = {tool["name"]: tool for tool in get_canonical_all_tools()}
    for name in ("cloudflare_dns_list", "cloudflare_dns_get", "cloudflare_dns_delete"):
        assert name in CANONICAL_TOOL_NAMES
        assert name in names
        assert names[name]["x_inventory"] == "cloudflare"
        assert tool_scope(name) == TOOL_SCOPE_TRIFORCE_AUTH


def test_cloudflare_tool_schemas_are_small_and_explicit():
    by_name = {tool["name"]: tool for tool in CLOUDFLARE_DNS_TOOLS}
    assert by_name["cloudflare_dns_list"]["inputSchema"]["required"] == ["zone"]
    assert set(by_name["cloudflare_dns_delete"]["inputSchema"]["required"]) == {
        "zone", "record_id", "expected_name", "expected_type", "confirm"
    }
    assert by_name["cloudflare_dns_delete"]["inputSchema"]["additionalProperties"] is False


def test_txt_content_is_redacted_but_normal_records_are_preserved():
    txt = _redact({"id": "a" * 32, "type": "TXT", "name": "x.example", "content": "secret-value"})
    assert "secret-value" not in txt["content"]
    assert "length=" in txt["content"]

    a = _redact({"id": "b" * 32, "type": "A", "name": "x.example", "content": "192.0.2.10"})
    assert a["content"] == "192.0.2.10"


def test_delete_rejects_out_of_zone_and_protected_types_before_backend():
    svc = CloudflareDnsService()

    with pytest.raises(ValueError):
        asyncio.run(svc.delete_record("ailinux.me", "a" * 32, "evil.example", "A", False))

    with pytest.raises(ValueError):
        asyncio.run(svc.delete_record("ailinux.dev", "a" * 32, "ailinux.dev", "NS", False))


def test_delete_rejects_malformed_record_id_before_backend():
    svc = CloudflareDnsService()
    with pytest.raises(ValueError):
        asyncio.run(svc.delete_record("ailinux.me", "not-an-id", "x.ailinux.me", "A", False))

def test_new_dns_client_uses_bearer_token_and_redacts_txt(monkeypatch):
    import json
    import urllib.request
    import app.services.cloudflare_dns as cf

    token = "test_dns_token_not_real"
    monkeypatch.setattr(cf, "_credential_settings",
                        lambda: ("ailinux.me", "a" * 32, token))
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, limit):
            assert limit >= 100
            return json.dumps({
                "success": True,
                "result": [{"id": "b" * 32, "type": "TXT",
                            "name": "txt.ailinux.me", "content": "secret-test-value"}],
            }).encode()

    def urlopen(req, timeout):
        requests.append(req)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    response = cf.CloudflareDnsService()._run_sync({"op": "list", "zone": "ailinux.me", "per_page": 1})
    assert response["ok"] is True
    assert response["count"] == 1
    assert "secret-test-value" not in str(response)
    assert requests[0].get_header("Authorization") == "Bearer " + token
    assert requests[0].get_method() == "GET"
    assert "per_page=1" in requests[0].full_url


def test_new_dns_client_enforces_configured_zone_before_network(monkeypatch):
    import app.services.cloudflare_dns as cf

    monkeypatch.setattr(cf, "_credential_settings", lambda: ("ailinux.me", "a" * 32, "test"))
    service = cf.CloudflareDnsService()
    monkeypatch.setattr(service, "_request", lambda *args: pytest.fail("request must not be sent"))
    with pytest.raises(cf.CloudflareDnsError, match="ZONE_NOT_CONFIGURED"):
        service._run_sync({"op": "list", "zone": "ailinux.dev"})


def test_new_dns_client_delete_requires_confirmation_and_identity_match(monkeypatch):
    import app.services.cloudflare_dns as cf

    monkeypatch.setattr(cf, "_credential_settings", lambda: ("ailinux.me", "a" * 32, "test"))
    calls = []

    def request(method, url, token):
        calls.append(method)
        if method == "GET":
            return {"success": True, "result": {
                "id": "b" * 32, "type": "A", "name": "api.ailinux.me", "content": "192.0.2.2"
            }}
        return {"success": True, "result": {"id": "b" * 32}}

    service = cf.CloudflareDnsService()
    monkeypatch.setattr(service, "_request", request)
    payload = {"op": "delete", "zone": "ailinux.me", "record_id": "b" * 32,
               "expected_name": "api.ailinux.me", "expected_type": "A", "confirm": False}
    preview = service._run_sync(payload)
    assert preview["deleted"] is False
    assert calls == ["GET"]

    with pytest.raises(cf.CloudflareDnsError, match="IDENTITY_MISMATCH"):
        service._run_sync({**payload, "expected_name": "www.ailinux.me", "confirm": True})
    assert "DELETE" not in calls

    deleted = service._run_sync({**payload, "confirm": True})
    assert deleted["deleted"] is True
    assert calls[-2:] == ["GET", "DELETE"]


def test_dns_credentials_use_latest_config_without_global_key_fallback(monkeypatch, tmp_path):
    import app.services.cloudflare_dns as cf

    path = tmp_path / "triforce.env"
    path.write_text("CLOUDFLARE_DNS_API_TOKEN=rotated-token\n"
                    "CLOUDFLARE_ZONE_ID=" + "a" * 32 + "\n"
                    "CLOUDFLARE_API_TOKEN=legacy-token\n")
    monkeypatch.setattr(cf, "_CONFIG", path)
    monkeypatch.setenv("CLOUDFLARE_DNS_API_TOKEN", "old-env-token")
    zone, zone_id, token = cf._credential_settings()
    assert (zone, zone_id, token) == ("ailinux.me", "a" * 32, "rotated-token")

    path.write_text("CLOUDFLARE_ZONE_ID=" + "a" * 32 + "\n"
                    "CLOUDFLARE_API_TOKEN=legacy-token\n")
    monkeypatch.delenv("CLOUDFLARE_DNS_API_TOKEN")
    with pytest.raises(cf.CloudflareDnsError, match="CLOUDFLARE_DNS_API_TOKEN"):
        cf._credential_settings()
