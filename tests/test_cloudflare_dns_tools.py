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
