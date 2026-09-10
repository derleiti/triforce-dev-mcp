"""Public MCP bridge to a browser-selected local workspace.

Authenticated/admin MCP behavior is intentionally unchanged. Credential-less
``public_guest`` sessions keep the safe TriForce cloud surface and may pair one
browser tab. Browser-local file/code calls are forwarded only when that tab
advertised the exact capability during pairing.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Dict, List

from fastapi import Request

from .mcp_workspace_sessions import (
    get_workspace, get_workspace_by_token, get_workspace_lease, mark_transport_detached, workspace_status as lease_status,
)

CONTROL_TOOLS = {"workspace_status", "workspace_pair"}
BROWSER_READ_TOOLS = {
    "workspace_info", "file_read", "file_tree", "code_read", "code_tree",
    "code_search", "code_grep",
}
BROWSER_WRITE_TOOLS = BROWSER_READ_TOOLS | {"file_edit", "directory_create"}
READ_ONLY_TOOLS = CONTROL_TOOLS | BROWSER_READ_TOOLS
WRITE_TOOLS = CONTROL_TOOLS | BROWSER_WRITE_TOOLS
LOCAL_TOOL_NAMES = WRITE_TOOLS

PUBLIC_GUEST_CLOUD_TOOLS = {
    "chat", "list_models", "ask_specialist", "list_specialists", "models", "specialist",
    "analyze_image", "crawl", "crawl_url", "crawl_site", "crawl_status",
    "conversation", "prompt_template", "prompts", "api_docs", "web_search", "search",
    "search_health", "multi_search", "smart_search", "quick_smart_search",
    "google_deep_search", "ailinux_search", "grokipedia_search", "image_search",
    "weather", "crypto_prices", "stock_indices", "market_overview", "current_time",
    "list_timezones",
}

_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "workspace_status",
        "description": (
            "Check whether this MCP session is paired with a browser-selected local workspace. "
            "If the user message contains a TriForce workspace ID in the form XXXX-XXXX-XXXX-XXXX-XXXX-XXXX, "
            "pass it as workspace_id; this tool validates the waiting browser and pairs it automatically."
        ),
        "inputSchema": {"type": "object", "properties": {
            "workspace_id": {"type": "string", "description": "Optional one-time TriForce workspace ID pasted by the user."},
            "workspace_token": {"type": "string"}
        }},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "workspace_pair",
        "description": (
            "Bind this MCP session to the browser workspace waiting under the one-time ID. "
            "Use this when the user pastes an ID shown by https://api.ailinux.me/v1/mcp."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "workspace_info",
        "description": "Analyze the paired browser workspace: file counts, size, extensions and sample paths.",
        "inputSchema": {"type": "object", "properties": {"workspace_token": {"type": "string"}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "file_read",
        "description": "Read a UTF-8 text file inside the paired browser workspace.",
        "inputSchema": {"type": "object", "properties": {
            "workspace_token": {"type": "string"}, "path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1}}, "required": ["path"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "file_tree",
        "description": "List a bounded directory tree inside the paired browser workspace.",
        "inputSchema": {"type": "object", "properties": {
            "workspace_token": {"type": "string"}, "path": {"type": "string", "default": "."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 8},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "code_read",
        "description": "Read source code inside the paired browser workspace.",
        "inputSchema": {"type": "object", "properties": {
            "workspace_token": {"type": "string"}, "path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1}}, "required": ["path"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "code_tree",
        "description": "Inspect the source tree inside the paired browser workspace.",
        "inputSchema": {"type": "object", "properties": {
            "workspace_token": {"type": "string"}, "path": {"type": "string", "default": "."},
            "depth": {"type": "integer", "minimum": 1, "maximum": 8},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "code_search",
        "description": "Search text/source files inside the paired browser workspace.",
        "inputSchema": {"type": "object", "properties": {
            "workspace_token": {"type": "string"}, "query": {"type": "string"}, "path": {"type": "string", "default": "."},
            "file_pattern": {"type": "string", "default": "*"},
            "case_sensitive": {"type": "boolean"}, "regex": {"type": "boolean"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 500}},
            "required": ["query"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "code_grep",
        "description": "Regex-search text files inside the paired browser workspace.",
        "inputSchema": {"type": "object", "properties": {
            "workspace_token": {"type": "string"}, "pattern": {"type": "string"}, "path": {"type": "string", "default": "."},
            "glob": {"type": "string", "default": "*"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 500}},
            "required": ["pattern"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "file_edit",
        "description": (
            "Create or modify a UTF-8 text file inside the paired browser workspace. "
            "Available only when the user granted browser Write access."
        ),
        "inputSchema": {"type": "object", "properties": {
            "workspace_token": {"type": "string"}, "path": {"type": "string"},
            "operation": {"type": "string", "enum": ["create", "write", "append", "replace"]},
            "content": {"type": "string"}, "old_text": {"type": "string"},
            "new_text": {"type": "string"}}, "required": ["path", "operation"]},
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "directory_create",
        "description": "Create a directory inside the paired browser workspace when Write access is enabled.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "workspace_token": {"type": "string"}}, "required": ["path"]},
        "annotations": {"readOnlyHint": False},
    },
]


def is_public_guest(request: Request | None) -> bool:
    state = getattr(request, "state", None) if request is not None else None
    return bool(state is not None and getattr(state, "mcp_auth_method", None) == "public_guest")


def session_id(request: Request | None) -> str:
    state = getattr(request, "state", None) if request is not None else None
    return str(getattr(state, "mcp_session_id", "") or "")


def workspace_affinity_id(request: Request | None) -> str:
    """Stable workspace identity for one OpenAI connector transport context.

    Both high-entropy OpenAI headers are required.  Using x-openai-subject alone
    would be stable but too broad: separate chats/transports owned by the same
    connector subject could accidentally inherit one local workspace lease.
    """
    if request is None:
        return ""
    headers = getattr(request, "headers", {})
    ua = str(headers.get("user-agent") or "").lower()
    subject = str(headers.get("x-openai-subject") or "").strip()
    transport = str(headers.get("x-openai-session") or "").strip()
    if not subject or not transport or "openai-mcp" not in ua:
        return ""
    import hashlib
    digest = hashlib.sha256((subject + "\0" + transport).encode("utf-8")).hexdigest()
    return "openai-workspace-" + digest[:40]


def merge_workspace_tools(tools: List[Dict[str, Any]], request: Request) -> List[Dict[str, Any]]:
    """Overlay browser-workspace tools onto the canonical MCP surface.

    Public guests retain the intentionally small cloud allowlist. Authenticated
    clients keep their normal RBAC-filtered catalog and gain the same browser
    workspace controls. Authentication therefore adds capabilities; it never
    selects a different MCP product surface or downgrades privileges.
    """
    if is_public_guest(request):
        merged = {
            str(tool.get("name") or ""): deepcopy(tool)
            for tool in tools
            if isinstance(tool, dict) and str(tool.get("name") or "") in PUBLIC_GUEST_CLOUD_TOOLS
        }
    else:
        merged = {
            str(tool.get("name") or ""): deepcopy(tool)
            for tool in tools
            if isinstance(tool, dict) and str(tool.get("name") or "")
        }
    for tool in _TOOL_SCHEMAS:
        merged[tool["name"]] = deepcopy(tool)
    return list(merged.values())


# Backwards-compatible import name used by older tests/extensions.
def merge_public_workspace_tools(tools: List[Dict[str, Any]], request: Request) -> List[Dict[str, Any]]:
    return merge_workspace_tools(tools, request)


def public_instructions(request: Request) -> str:
    sid = workspace_affinity_id(request) or session_id(request)
    status = get_workspace_lease(sid)
    if status:
        capabilities = ", ".join(status.get("capabilities") or []) or "none"
        state = status.get("state", "connected")
        return (
            "This is the public TriForce MCP. Safe TriForce cloud tools execute on the server. "
            "The user paired a browser-selected local workspace; only its advertised browser capabilities execute locally. "
            f"Workspace state: {state}. Access mode: {status['mode']}. Local capabilities: {capabilities}. "
            + ("The browser transport is temporarily suspended but the lease is still valid and reconnectable. " if state == "suspended" else "")
            + f"User task: {status.get('task') or 'follow the current user request'}."
        )
    return (
        "This is the public TriForce MCP. To work with local files, ask the user to open "
        "https://api.ailinux.me/v1/mcp in a browser, choose a folder there, and then paste the one-time pairing ID into this chat. "
        "When the user pastes that ID, call workspace_pair with it. No desktop helper or account login is required."
    )


def _workspace_required(request: Request) -> Dict[str, Any]:
    effective_sid = workspace_affinity_id(request) or session_id(request)
    if not effective_sid:
        return {
            "content": [{"type": "text", "text": "Local workspace requires an initialized MCP session."}],
            "structuredContent": {"ok": False, "code": "MCP_SESSION_REQUIRED", "state": "unpaired"},
            "isError": True,
        }
    status = lease_status(effective_sid)
    if status.get("state") == "suspended":
        return {
            "content": [{"type": "text", "text": "The local workspace lease is still paired, but the browser transport is temporarily suspended. Reopen the workspace page; it can reconnect with the same pairing ID."}],
            "structuredContent": {"ok": False, "code": "WORKSPACE_SUSPENDED", **status},
            "isError": False,
        }
    text = (
        "No local workspace is paired. Open https://api.ailinux.me/v1/mcp in a browser, choose the local folder and access mode, "
        "then paste the one-time ID shown by that page into this chat."
    )
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {
            "ok": False, "code": "WORKSPACE_REQUIRED", "state": "unpaired", "connected": False,
            "setup_url": "https://api.ailinux.me/v1/mcp",
            "procedure": "Open setup page -> choose folder/access mode -> connect in browser -> paste ID.",
        },
        "isError": False,
    }


def _tool_error(code: str, text: str, **extra: Any) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"ok": False, "code": code, **extra},
        "isError": True,
    }


def _workspace_binding_response(binding: Dict[str, Any], *, token: str = "") -> Dict[str, Any]:
    connection = binding.get("connection")
    live = connection is not None and not bool(getattr(connection, "closed", True))
    state = "connected" if live else "suspended"
    data = {
        "ok": True,
        "state": state,
        "connected": live,
        "suspended": not live,
        "reconnectable": True,
        "access_mode": binding.get("mode", "read_only"),
        "mode": binding.get("mode", "read_only"),  # compatibility
        "task": binding.get("task", ""),
        "client_id": binding.get("client_id", ""),
        "lease_id": binding.get("lease_id", ""),
        "capabilities": list(binding.get("capabilities") or []),
    }
    if token:
        data["workspace_token"] = token
    text = (
        f"Browser workspace connected with {data['access_mode']} access."
        if live else
        f"Workspace lease is paired with {data['access_mode']} access; browser transport is suspended and may reconnect with the same ID."
    )
    return {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": False}


_WORKSPACE_ID_RE = re.compile(r"^[0-9A-F]{4}(?:-[0-9A-F]{4}){5}$")


def _workspace_id_from_legacy_arguments(arguments: Dict[str, Any]) -> str:
    """Recognize an exact workspace ID carried through an older cached tool schema.

    Some MCP hosts keep a pre-update schema for the lifetime of a chat.  If the
    user pasted a workspace ID, the assistant can carry it in any existing
    string argument of a browser workspace tool.  TriForce consumes the exact
    ID only while no workspace is bound and never forwards it to the browser.
    """
    for value in arguments.values():
        if not isinstance(value, str):
            continue
        candidate = value.strip().upper()
        if _WORKSPACE_ID_RE.fullmatch(candidate):
            return candidate
    return ""


async def _claim_workspace_for_session(request: Request, workspace_id: str) -> Dict[str, Any]:
    sid = session_id(request)
    affinity_sid = workspace_affinity_id(request)
    effective_sid = affinity_sid or sid or f"workspace-lease-{__import__('uuid').uuid4().hex}"
    try:
        from app.services.mcp_workspace_sessions import claim_waiting_workspace
        binding = claim_waiting_workspace(workspace_id, effective_sid)
        if not sid:
            mark_transport_detached(effective_sid)
    except Exception as exc:
        return _tool_error(
            "WORKSPACE_PAIR_FAILED",
            f"Could not connect browser workspace: {exc}",
            detail=str(exc),
        )
    connection = binding.get("connection")
    if connection is not None and not bool(getattr(connection, "closed", True)):
        try:
            await connection.websocket.send_json({
                "jsonrpc": "2.0",
                "method": "workspace/paired",
                "params": {
                    "ok": True, "state": "connected", "connected": True,
                    "access_mode": binding["mode"], "mode": binding["mode"],
                    "lease_id": binding.get("lease_id", ""),
                },
            })
        except Exception:
            pass
    return _workspace_binding_response(binding, token=workspace_id)


async def call_workspace_tool(request: Request, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    sid = session_id(request)
    arguments = dict(arguments or {})
    workspace_token = str(arguments.pop("workspace_token", "") or "").strip().upper()
    workspace_id = str(arguments.pop("workspace_id", "") or "").strip().upper()

    if name == "workspace_pair":
        code = str(arguments.get("code") or "").strip().upper()
        if not code:
            return _tool_error("PAIR_CODE_REQUIRED", "workspace_pair requires the one-time ID shown by the TriForce MCP browser page.")
        # Some Streamable HTTP consumers (notably current ChatGPT connectors)
        # initialize a session but omit Mcp-Session-Id on later tools/call POSTs.
        # Bind those calls to a logical lease session keyed only by the secret pair
        # code; never infer identity from IP, User-Agent or another shared signal.
        effective_sid = workspace_affinity_id(request) or sid or f"workspace-lease-{__import__('uuid').uuid4().hex}"
        try:
            from app.services.mcp_workspace_sessions import claim_waiting_workspace
            binding = claim_waiting_workspace(code, effective_sid)
            if not sid:
                mark_transport_detached(effective_sid)
        except Exception as exc:
            return _tool_error("WORKSPACE_PAIR_FAILED", f"Could not pair browser workspace: {exc}", detail=str(exc))
        connection = binding.get("connection")
        if connection is not None and not bool(getattr(connection, "closed", True)):
            try:
                await connection.websocket.send_json({
                    "jsonrpc": "2.0",
                    "method": "workspace/paired",
                    "params": {
                        "ok": True, "connected": True, "mode": binding["mode"],
                        "lease_id": binding.get("lease_id", ""),
                    },
                })
            except Exception:
                pass
        return _workspace_binding_response(binding, token=code)

    affinity_sid = workspace_affinity_id(request)
    binding = get_workspace_lease(affinity_sid) if affinity_sid else None
    if binding is None and sid:
        binding = get_workspace_lease(sid)
    if binding is None and workspace_token:
        binding = get_workspace_by_token(workspace_token)

    # Compatibility for MCP hosts that cached the old parameterless
    # workspace_status schema.  The assistant may carry an exact workspace ID
    # through an existing string argument (for example code_search.query).
    # Consume it only while unpaired and never forward it to the browser tool.
    if binding is None and not workspace_id and name in (BROWSER_READ_TOOLS | BROWSER_WRITE_TOOLS):
        legacy_workspace_id = _workspace_id_from_legacy_arguments(arguments)
        if legacy_workspace_id:
            return await _claim_workspace_for_session(request, legacy_workspace_id)

    if name == "workspace_status":
        if workspace_id:
            return await _claim_workspace_for_session(request, workspace_id)

        if binding:
            return _workspace_binding_response(binding, token=workspace_token)
        pair_session_id = affinity_sid or sid
        if pair_session_id:
            from app.services.mcp_workspace_sessions import get_or_create_pair_code
            code = get_or_create_pair_code(pair_session_id)
            return {
                "content": [{"type": "text", "text": (
                    "No local workspace is paired. Open the TriForce MCP setup page, choose the folder and mode, "
                    f"then connect it with this pairing ID: {code}"
                )}],
                "structuredContent": {
                    "ok": False, "code": "WORKSPACE_REQUIRED", "connected": False,
                    "pair_code": code,
                    "setup_url": f"https://api.ailinux.me/v1/mcp?pair_code={code}",
                    "procedure": "Open setup_url -> choose folder/mode -> connect. The page binds directly to this MCP session.",
                },
                "isError": False,
            }
        return _workspace_required(request)

    if not binding:
        return _workspace_required(request)
    if binding.get("state") == "suspended" or binding.get("connection") is None:
        return {
            "content": [{"type": "text", "text": "Workspace lease is paired but the browser transport is suspended. Reopen the workspace page; the same lease will reconnect."}],
            "structuredContent": {
                "ok": False, "code": "WORKSPACE_SUSPENDED", "state": "suspended",
                "connected": False, "suspended": True, "reconnectable": True,
                "access_mode": binding.get("mode", "read_only"), "mode": binding.get("mode", "read_only"),
                "lease_id": binding.get("lease_id", ""),
                "capabilities": list(binding.get("capabilities") or []),
            },
            "isError": False,
        }

    mode = str(binding.get("mode") or "read_only")
    if mode != "write" and name in (BROWSER_WRITE_TOOLS - BROWSER_READ_TOOLS):
        return _tool_error("WORKSPACE_READ_ONLY", f"Browser workspace is Read only; tool '{name}' requires Write mode.", tool=name)

    capabilities = set(binding.get("capabilities") or [])
    if name not in capabilities:
        return _tool_error(
            "WORKSPACE_TOOL_UNAVAILABLE",
            f"The paired browser did not advertise local tool '{name}'.",
            tool=name,
            capabilities=sorted(capabilities),
        )

    connection = binding.get("connection")
    if connection is None or bool(getattr(connection, "closed", True)):
        return _workspace_required(request)
    if "client_workspace_tool" not in set(getattr(connection, "supported_tools", []) or []):
        raise RuntimeError("Connected browser workspace does not provide client_workspace_tool")

    result = await connection.send_tool_call(
        "client_workspace_tool",
        {"tool": name, "arguments": arguments, "mode": mode},
        timeout=180.0,
    )
    if not isinstance(result, dict):
        raise RuntimeError("Browser workspace returned an invalid MCP tool result")
    return result


# Backwards-compatible import name. Workspace routing is now shared by public
# and authenticated sessions; RBAC for non-workspace tools remains unchanged.
async def call_public_local_tool(request: Request, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await call_workspace_tool(request, name, arguments)
