
from __future__ import annotations

from typing import Any, Dict

from app.services.cloudflare_dns import cloudflare_dns_service


CLOUDFLARE_DNS_TOOLS = [
    {
        "name": "cloudflare_dns_list",
        "description": (
            "List DNS records in a server-configured Cloudflare zone. "
            "Cloudflare credentials stay server-side and are never returned. "
            "TXT record contents are redacted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "zone": {"type": "string", "description": "Configured DNS zone, e.g. ailinux.me"},
                "name": {"type": "string", "description": "Optional exact DNS name within the zone"},
                "type": {
                    "type": "string",
                    "enum": ["A", "AAAA", "CNAME", "TXT", "MX", "CAA", "SRV", "NS", "PTR"],
                },
                "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 100},
            },
            "required": ["zone"],
            "additionalProperties": False,
        },
        "x_inventory": "cloudflare",
    },
    {
        "name": "cloudflare_dns_get",
        "description": (
            "Get one DNS record by Cloudflare record id from a server-configured zone. "
            "Credentials stay server-side; TXT content is redacted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "zone": {"type": "string"},
                "record_id": {"type": "string", "description": "32-character Cloudflare DNS record id"},
            },
            "required": ["zone", "record_id"],
            "additionalProperties": False,
        },
        "x_inventory": "cloudflare",
    },
    {
        "name": "cloudflare_dns_delete",
        "description": (
            "Delete exactly one DNS record from a server-configured Cloudflare zone. "
            "Requires record id, expected name, expected type, and confirm=true. "
            "The server re-reads the record and refuses deletion on identity mismatch. "
            "NS/SOA cannot be deleted. Credentials are never exposed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "zone": {"type": "string"},
                "record_id": {"type": "string"},
                "expected_name": {"type": "string"},
                "expected_type": {
                    "type": "string",
                    "enum": ["A", "AAAA", "CNAME", "TXT", "MX", "CAA", "SRV", "PTR"],
                },
                "confirm": {
                    "type": "boolean",
                    "description": "false performs a dry run; true permits deletion",
                },
            },
            "required": ["zone", "record_id", "expected_name", "expected_type", "confirm"],
            "additionalProperties": False,
        },
        "x_inventory": "cloudflare",
    },
]


async def handle_cloudflare_dns_list(params: Dict[str, Any]) -> Dict[str, Any]:
    return await cloudflare_dns_service.list_records(
        zone=str(params.get("zone") or ""),
        name=params.get("name"),
        record_type=params.get("type"),
        per_page=int(params.get("per_page") or 100),
    )


async def handle_cloudflare_dns_get(params: Dict[str, Any]) -> Dict[str, Any]:
    return await cloudflare_dns_service.get_record(
        zone=str(params.get("zone") or ""),
        record_id=str(params.get("record_id") or ""),
    )


async def handle_cloudflare_dns_delete(params: Dict[str, Any]) -> Dict[str, Any]:
    return await cloudflare_dns_service.delete_record(
        zone=str(params.get("zone") or ""),
        record_id=str(params.get("record_id") or ""),
        expected_name=str(params.get("expected_name") or ""),
        expected_type=str(params.get("expected_type") or ""),
        confirm=bool(params.get("confirm")),
    )


CLOUDFLARE_DNS_HANDLERS = {
    "cloudflare_dns_list": handle_cloudflare_dns_list,
    "cloudflare_dns_get": handle_cloudflare_dns_get,
    "cloudflare_dns_delete": handle_cloudflare_dns_delete,
}
