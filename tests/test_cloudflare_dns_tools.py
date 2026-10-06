import asyncio

import pytest

from app.mcp.cloudflare_dns_tools import CLOUDFLARE_DNS_TOOLS
from app.mcp.tool_registry_unified import (
    TOOL_SCOPE_TRIFORCE_AUTH,
    get_canonical_all_tools,
    tool_scope,
)
from app.services.cloudflare_dns import CloudflareDnsService, _redact


def test_cloudflare_tools_are_canonical_authenticated_tools():
    names = {tool["name"] for tool in get_canonical_all_tools()}
    for name in {"cloudflare_dns_list", "cloudflare_dns_get", "cloudflare_dns_delete"}:
        assert name in names
        assert tool_scope(name) == TOOL_SCOPE_TRIFORCE_AUTH


def test_txt_content_is_redacted():
    record = {"id": "a" * 32, "type": "TXT", "name": "x.example", "content": "secret-value"}
    out = _redact(record)
    assert out["content"].startswith("<redacted TXT content; length=")
    assert "secret-value" not in out["content"]


def test_delete_rejects_protected_record_types_before_backend_call():
    service = CloudflareDnsService()
    with pytest.raises(ValueError):
        asyncio.run(
            service.delete_record(
                "ailinux.dev",
                "a" * 32,
                "ailinux.dev",
                "NS",
                False,
            )
        )


def test_delete_rejects_out_of_zone_name_before_backend_call():
    service = CloudflareDnsService()
    with pytest.raises(ValueError):
        asyncio.run(
            service.delete_record(
                "ailinux.me",
                "a" * 32,
                "example.org",
                "A",
                False,
            )
        )


def test_schemas_are_small_and_strict():
    tools = {tool["name"]: tool for tool in CLOUDFLARE_DNS_TOOLS}
    assert set(tools) == {
        "cloudflare_dns_list",
        "cloudflare_dns_get",
        "cloudflare_dns_delete",
    }
    for tool in tools.values():
        assert tool["inputSchema"].get("additionalProperties") is False
