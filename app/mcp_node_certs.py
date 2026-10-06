"""TLS credentials for the legacy MCP mesh client.

Private keys must never be embedded in source code. Configure filesystem paths
with MCP_CLIENT_CERT_FILE, MCP_CLIENT_KEY_FILE and MCP_CA_CERT_FILE.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path


def _required_path(env_name: str) -> Path:
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        raise RuntimeError(f"{env_name} is required for MCP mesh mTLS")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{env_name} does not point to a readable file")
    return path


def get_ssl_context() -> ssl.SSLContext:
    """Build a fail-closed mTLS client context from filesystem credentials."""
    cert_file = _required_path("MCP_CLIENT_CERT_FILE")
    key_file = _required_path("MCP_CLIENT_KEY_FILE")
    ca_file = _required_path("MCP_CA_CERT_FILE")

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert_file), str(key_file))
    ctx.load_verify_locations(str(ca_file))
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def get_cert_info() -> dict:
    """Return non-secret configuration metadata only."""
    return {
        "client_cert_configured": bool((os.getenv("MCP_CLIENT_CERT_FILE") or "").strip()),
        "client_key_configured": bool((os.getenv("MCP_CLIENT_KEY_FILE") or "").strip()),
        "ca_cert_configured": bool((os.getenv("MCP_CA_CERT_FILE") or "").strip()),
        "type": "mTLS client certificate",
    }
