"""Provider-neutral MCP compatibility negotiation.

This module deliberately does *not* authorize tools.  It only normalizes protocol
and transport quirks observed in MCP hosts.  Authentication/RBAC remains owned by
app.utils.mcp_auth and app.utils.mcp_security.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

HANDSHAKE_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
LEGACY_DEFAULT_PROTOCOL_VERSION = "2024-11-05"
LATEST_HANDSHAKE_PROTOCOL_VERSION = "2025-11-25"


@dataclass(frozen=True)
class MCPCompatibilityProfile:
    name: str
    streamable_http: bool = True
    legacy_sse: bool = True
    stateless_preferred: bool = False
    session_header_optional: bool = False
    simple_tool_schemas: bool = True


PROFILES: dict[str, MCPCompatibilityProfile] = {
    "generic": MCPCompatibilityProfile("generic"),
    "openai": MCPCompatibilityProfile(
        "openai",
        streamable_http=True,
        legacy_sse=True,
        stateless_preferred=False,
        session_header_optional=True,
    ),
    "anthropic": MCPCompatibilityProfile(
        "anthropic",
        streamable_http=True,
        legacy_sse=True,
    ),
    "google": MCPCompatibilityProfile(
        "google",
        streamable_http=True,
        legacy_sse=False,
        stateless_preferred=True,
    ),
    "xai": MCPCompatibilityProfile(
        "xai",
        streamable_http=True,
        legacy_sse=True,
        stateless_preferred=True,
    ),
}


def _lower_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def detect_profile(
    client_info: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> MCPCompatibilityProfile:
    """Detect transport quirks conservatively.

    Detection is advisory only. It must never be used as an authorization signal.
    Unknown clients always fall back to the standards-oriented generic profile.
    """
    info = client_info or {}
    name = str(info.get("name") or "").strip().lower()
    title = str(info.get("title") or "").strip().lower()
    haystack = f"{name} {title}"
    h = _lower_headers(headers)
    ua = h.get("user-agent", "").lower()

    if h.get("x-openai-session") or h.get("x-openai-subject") or any(
        token in haystack or token in ua for token in ("openai", "chatgpt", "codex")
    ):
        return PROFILES["openai"]
    if any(token in haystack or token in ua for token in ("anthropic", "claude")):
        return PROFILES["anthropic"]
    if any(token in haystack or token in ua for token in ("google", "gemini", "antigravity")):
        return PROFILES["google"]
    if any(token in haystack or token in ua for token in ("xai", "x.ai", "grok")):
        return PROFILES["xai"]
    return PROFILES["generic"]


def negotiate_protocol_version(requested: Any) -> str:
    requested_text = str(requested or "").strip()
    if not requested_text:
        return LEGACY_DEFAULT_PROTOCOL_VERSION
    if requested_text in HANDSHAKE_PROTOCOL_VERSIONS:
        return requested_text
    # initialize only negotiates handshake-era revisions. Unknown/future/modern
    # revisions receive the latest handshake revision as a counter-offer.
    return LATEST_HANDSHAKE_PROTOCOL_VERSION


def build_initialize_result(
    params: Mapping[str, Any] | None,
    *,
    server_name: str,
    server_version: str,
    instructions: str,
    server_info_extra: Mapping[str, Any] | None = None,
    tool_capabilities_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one consistent MCP initialize result for every transport."""
    p = params or {}
    server_info: dict[str, Any] = {
        "name": server_name,
        "version": server_version,
    }
    if server_info_extra:
        server_info.update(dict(server_info_extra))
    tool_capabilities: dict[str, Any] = {"listChanged": True}
    if tool_capabilities_extra:
        tool_capabilities.update(dict(tool_capabilities_extra))
    return {
        "protocolVersion": negotiate_protocol_version(p.get("protocolVersion")),
        "serverInfo": server_info,
        "capabilities": {
            "tools": tool_capabilities,
            "prompts": {"listChanged": True},
            "resources": {"listChanged": True},
        },
        "instructions": instructions,
    }


def logical_session_id(
    *,
    explicit_session_id: str | None,
    headers: Mapping[str, str] | None,
    client_info: Mapping[str, Any] | None = None,
) -> str:
    """Return a stable logical session id for hosts that omit MCP session headers."""
    if explicit_session_id:
        return str(explicit_session_id)

    h = _lower_headers(headers)
    profile = detect_profile(client_info=client_info, headers=h)
    if profile.name == "openai":
        openai_session = h.get("x-openai-session", "").strip()
        openai_subject = h.get("x-openai-subject", "").strip()
        if openai_session and openai_subject:
            digest = hashlib.sha256(
                (openai_subject + "\0" + openai_session).encode("utf-8")
            ).hexdigest()
            return "openai-" + digest[:40]
    return ""
