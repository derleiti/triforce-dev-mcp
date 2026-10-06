
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

_RECORD_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_TYPES = {"A","AAAA","CNAME","TXT","MX","CAA","SRV","NS","PTR"}
_PROTECTED = {"NS","SOA"}
_HELPER = Path(__file__).resolve().parents[2] / "scripts" / "cloudflare_dns_helper.php"


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
    allowed = {"id","type","name","content","proxied","ttl","priority","comment","created_on","modified_on"}
    out = {k:v for k,v in record.items() if k in allowed}
    if str(out.get("type") or "").upper() == "TXT" and "content" in out:
        out["content"] = "<redacted TXT content; length=%d>" % len(str(out["content"]))
    return out


class CloudflareDnsService:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def _run_sync(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        helper_code = _HELPER.read_text(encoding="utf-8")
        if helper_code.lstrip().startswith("<?php"):
            helper_code = helper_code.lstrip()[5:].lstrip()
        proc = subprocess.run(
            [
                "docker", "exec",
                "--env", "AILINUX_CF_DNS_PAYLOAD=" + payload_json,
                "wordpress_fpm",
                "wp", "--allow-root", "--quiet",
                "--path=/var/www/html",
                "eval", helper_code,
            ],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        marker = "AILINUX_CF_DNS_JSON:"
        line = next((x.split(marker,1)[1] for x in proc.stdout.splitlines() if marker in x), "")
        if not line:
            raise CloudflareDnsError("Cloudflare DNS backend returned no structured response")
        result = json.loads(line)
        if not result.get("ok"):
            raise CloudflareDnsError(str(result.get("code") or "DNS_ERROR") + ": " + str(result.get("error") or "request failed"))
        if isinstance(result.get("record"), dict):
            result["record"] = _redact(result["record"])
        if isinstance(result.get("records"), list):
            result["records"] = [_redact(x) for x in result["records"] if isinstance(x, dict)]
            result["count"] = len(result["records"])
        return result

    async def _run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._run_sync, payload)

    async def list_records(self, zone: str, name: str = "", record_type: str = "", per_page: int = 100) -> Dict[str, Any]:
        z = _name(zone)
        n = _name(name) if name else ""
        if n:
            _inside(n, z)
        return await self._run({"op":"list","zone":z,"name":n,"type":_rtype(record_type),"per_page":max(1,min(100,int(per_page or 100)))})

    async def get_record(self, zone: str, record_id: str) -> Dict[str, Any]:
        return await self._run({"op":"get","zone":_name(zone),"record_id":_rid(record_id)})

    async def delete_record(self, zone: str, record_id: str, expected_name: str, expected_type: str, confirm: bool) -> Dict[str, Any]:
        z = _name(zone)
        n = _name(expected_name)
        _inside(n, z)
        t = _rtype(expected_type, True)
        if t in _PROTECTED:
            raise ValueError("protected DNS record type")
        return await self._run({"op":"delete","zone":z,"record_id":_rid(record_id),"expected_name":n,"expected_type":t,"confirm":bool(confirm)})


cloudflare_dns_service = CloudflareDnsService()
