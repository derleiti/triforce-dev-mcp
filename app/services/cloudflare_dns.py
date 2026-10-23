"""Scoped Cloudflare DNS operations for the authenticated TriForce MCP tools.

Do not route DNS administration through WordPress's legacy Global API Key.
Only CLOUDFLARE_DNS_API_TOKEN may authorize these operations; no token values
are passed through a subprocess command line or returned to MCP clients.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

_RECORD_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_TYPES = {"A", "AAAA", "CNAME", "TXT", "MX", "CAA", "SRV", "NS", "PTR"}
_PROTECTED = {"NS", "SOA"}
_CONFIG = Path(__file__).resolve().parents[2] / "config" / "triforce.env"
_API = "https://api.cloudflare.com/client/v4"


class CloudflareDnsError(RuntimeError):
    pass


def _name(value: str) -> str:
    v = str(value or "").strip().rstrip(".").lower()
    if not v or len(v) > 253 or any(part == "" for part in v.split(".")):
        raise ValueError("invalid DNS name")
    return v


def _rtype(value: Optional[str], required: bool = False) -> str:
    v = str(value or "").strip().upper()
    if not v and not required:
        return ""
    if v not in _TYPES:
        raise ValueError("unsupported DNS record type")
    return v


def _rid(value: str) -> str:
    v = str(value or "").strip()
    if not _RECORD_ID.fullmatch(v):
        raise ValueError("record_id must be a 32-character Cloudflare record id")
    return v.lower()


def _inside(name: str, zone: str) -> None:
    if name != zone and not name.endswith("." + zone):
        raise ValueError("DNS name is outside the selected zone")


def _redact(record: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"id", "type", "name", "content", "proxied", "ttl",
               "priority", "comment", "created_on", "modified_on"}
    out = {k: v for k, v in record.items() if k in allowed}
    if str(out.get("type") or "").upper() == "TXT" and "content" in out:
        out["content"] = "<redacted TXT content; length=%d>" % len(str(out["content"]))
    return out


def _credential_settings() -> tuple[str, str, str]:
    """Use the current on-disk secret, so rotation needs no service restart."""
    values: dict[str, str] = {}
    if _CONFIG.is_file():
        for line in _CONFIG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k in {"CLOUDFLARE_DNS_API_TOKEN", "CLOUDFLARE_ZONE_ID", "CLOUDFLARE_DNS_ZONE"}:
                v = v.strip()
                if len(v) >= 2 and v[0] in "'\\\"" and v[-1] == v[0]:
                    v = v[1:-1]
                values[k] = v
    zone = _name(values.get("CLOUDFLARE_DNS_ZONE") or os.getenv("CLOUDFLARE_DNS_ZONE") or "ailinux.me")
    zone_id = values.get("CLOUDFLARE_ZONE_ID") or os.getenv("CLOUDFLARE_ZONE_ID", "")
    token = values.get("CLOUDFLARE_DNS_API_TOKEN") or os.getenv("CLOUDFLARE_DNS_API_TOKEN", "")
    if not _RECORD_ID.fullmatch(zone_id):
        raise CloudflareDnsError("CLOUDFLARE_ZONE_ID is missing or invalid")
    if not token:
        raise CloudflareDnsError("CLOUDFLARE_DNS_API_TOKEN is missing")
    return zone, zone_id.lower(), token


class CloudflareDnsService:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def _request(self, method: str, url: str, token: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            method=method,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/json",
                "User-Agent": "TriForce-Cloudflare-DNS/2.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                response_data = json.loads(response.read(2_000_000))
        except urllib.error.HTTPError as exc:
            raise CloudflareDnsError("Cloudflare DNS API rejected request (HTTP %d)" % exc.code) from None
        except urllib.error.URLError as exc:
            raise CloudflareDnsError("Cloudflare DNS API is unavailable") from None
        except (ValueError, TypeError) as exc:
            raise CloudflareDnsError("Cloudflare DNS API returned invalid JSON") from None
        if status < 200 or status >= 300 or not response_data.get("success"):
            raise CloudflareDnsError("Cloudflare DNS API rejected request (HTTP %d)" % status)
        return response_data

    def _run_sync(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        zone = _name(payload.get("zone", ""))
        configured_zone, zone_id, token = _credential_settings()
        if zone != configured_zone:
            raise CloudflareDnsError("ZONE_NOT_CONFIGURED: DNS access is restricted to " + configured_zone)
        base = _API + "/zones/" + zone_id + "/dns_records"
        op = str(payload.get("op") or "")
        if op == "list":
            query = {"per_page": max(1, min(100, int(payload.get("per_page", 100))))}
            if payload.get("name"):
                n = _name(str(payload["name"]))
                _inside(n, zone)
                query["name"] = n
            if payload.get("type"):
                query["type"] = _rtype(str(payload["type"]), True)
            data = self._request("GET", base + "?" + urllib.parse.urlencode(query), token)
            records = [_redact(rec) for rec in data.get("result", []) if isinstance(rec, dict)]
            return {"ok": True, "zone": zone, "count": len(records), "records": records}

        if op not in {"get", "delete"}:
            raise CloudflareDnsError("UNSUPPORTED_OPERATION")
        record_id = _rid(str(payload.get("record_id") or ""))
        data = self._request("GET", base + "/" + record_id, token)
        record = data.get("result")
        if not isinstance(record, dict):
            raise CloudflareDnsError("Cloudflare DNS API returned no record")
        if op == "get":
            return {"ok": True, "zone": zone, "record": _redact(record)}

        expected_name = _name(str(payload.get("expected_name") or ""))
        expected_type = _rtype(str(payload.get("expected_type") or ""), True)
        _inside(expected_name, zone)
        if expected_type in _PROTECTED:
            raise ValueError("protected DNS record type")
        actual_name = _name(str(record.get("name") or ""))
        actual_type = _rtype(str(record.get("type") or ""), True)
        if actual_name != expected_name or actual_type != expected_type:
            raise CloudflareDnsError("IDENTITY_MISMATCH: DNS record not deleted")
        if actual_type in _PROTECTED:
            raise CloudflareDnsError("PROTECTED_RECORD_TYPE")
        if not payload.get("confirm"):
            return {"ok": True, "zone": zone, "deleted": False,
                    "dry_run": True, "record": _redact(record)}
        self._request("DELETE", base + "/" + record_id, token)
        return {"ok": True, "zone": zone, "deleted": True,
                "record": {"id": record_id, "name": actual_name, "type": actual_type}}

    async def _run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._run_sync, payload)

    async def list_records(self, zone: str, name: str = "", record_type: str = "", per_page: int = 100) -> Dict[str, Any]:
        z = _name(zone)
        n = _name(name) if name else ""
        if n:
            _inside(n, z)
        return await self._run({"op": "list", "zone": z, "name": n,
                                "type": _rtype(record_type),
                                "per_page": max(1, min(100, int(per_page or 100)))})

    async def get_record(self, zone: str, record_id: str) -> Dict[str, Any]:
        return await self._run({"op": "get", "zone": _name(zone), "record_id": _rid(record_id)})

    async def delete_record(self, zone: str, record_id: str, expected_name: str,
                            expected_type: str, confirm: bool) -> Dict[str, Any]:
        z = _name(zone)
        n = _name(expected_name)
        _inside(n, z)
        t = _rtype(expected_type, True)
        if t in _PROTECTED:
            raise ValueError("protected DNS record type")
        return await self._run({"op": "delete", "zone": z, "record_id": _rid(record_id),
                                "expected_name": n, "expected_type": t, "confirm": bool(confirm)})


cloudflare_dns_service = CloudflareDnsService()
