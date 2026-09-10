"""Public MCP bridge to a browser-selected local workspace.

Authenticated/admin MCP behavior is intentionally unchanged. Credential-less
``public_guest`` sessions keep the safe TriForce cloud surface and may pair one
browser tab. Browser-local file/code calls are forwarded only when that tab
advertised the exact capability during pairing.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from fastapi import Request

from .mcp_workspace_sessions import get_workspace, workspace_status

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
        "description": "Check whether this MCP session is paired with a browser-selected local workspace.",
        "inputSchema": {"type": "object", "properties": {}},
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
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "file_read",
        "description": "Read a UTF-8 text file inside the paired browser workspace.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1}}, "required": ["path"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "file_tree",
        "description": "List a bounded directory tree inside the paired browser workspace.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "default": "."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 8},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "code_read",
        "description": "Read source code inside the paired browser workspace.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1}}, "required": ["path"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "code_tree",
        "description": "Inspect the source tree inside the paired browser workspace.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "default": "."},
            "depth": {"type": "integer", "minimum": 1, "maximum": 8},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "code_search",
        "description": "Search text/source files inside the paired browser workspace.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"}, "path": {"type": "string", "default": "."},
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
            "pattern": {"type": "string"}, "path": {"type": "string", "default": "."},
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
            "path": {"type": "string"},
            "operation": {"type": "string", "enum": ["create", "write", "append", "replace"]},
            "content": {"type": "string"}, "old_text": {"type": "string"},
            "new_text": {"type": "string"}}, "required": ["path", "operation"]},
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "directory_create",
        "description": "Create a directory inside the paired browser workspace when Write access is enabled.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        "annotations": {"readOnlyHint": False},
    },
]


def is_public_guest(request: Request | None) -> bool:
    state = getattr(request, "state", None) if request is not None else None
    return bool(state is not None and getattr(state, "mcp_auth_method", None) == "public_guest")


def session_id(request: Request | None) -> str:
    state = getattr(request, "state", None) if request is not None else None
    return str(getattr(state, "mcp_session_id", "") or "")


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
    sid = session_id(request)
    status = get_workspace(sid)
    if status:
        capabilities = ", ".join(status.get("capabilities") or []) or "none"
        return (
            "This is the public TriForce MCP. Safe TriForce cloud tools execute on the server. "
            "The user paired a browser-selected local workspace; only its advertised browser capabilities execute locally. "
            f"Workspace mode: {status['mode']}. Local capabilities: {capabilities}. "
            f"User task: {status.get('task') or 'follow the current user request'}."
        )
    return (
        "This is the public TriForce MCP. To work with local files, ask the user to open "
        "https://api.ailinux.me/v1/mcp in a browser, choose a folder there, and then paste the one-time pairing ID into this chat. "
        "When the user pastes that ID, call workspace_pair with it. No desktop helper or account login is required."
    )


def _workspace_required(request: Request) -> Dict[str, Any]:
    if not session_id(request):
        return {
            "content": [{"type": "text", "text": "Local workspace requires an initialized MCP session."}],
            "structuredContent": {"ok": False, "code": "MCP_SESSION_REQUIRED"},
            "isError": True,
        }
    text = (
        "No local workspace is paired. Open https://api.ailinux.me/v1/mcp in a browser, choose the local folder and access mode, "
        "then paste the one-time ID shown by that page into this chat. I can bind it with workspace_pair."
    )
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {
            "ok": False, "code": "WORKSPACE_REQUIRED", "connected": False,
            "setup_url": "https://api.ailinux.me/v1/mcp",
            "procedure": "Open setup page -> choose folder/mode -> connect in browser -> paste ID -> workspace_pair.",
        },
        "isError": False,
    }


def _tool_error(code: str, text: str, **extra: Any) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"ok": False, "code": code, **extra},
        "isError": True,
    }


async def call_workspace_tool(request: Request, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    sid = session_id(request)

    if name == "workspace_pair":
        if not sid:
            return _workspace_required(request)
        code = str((arguments or {}).get("code") or "").strip().upper()
        if not code:
            return _tool_error("PAIR_CODE_REQUIRED", "workspace_pair requires the one-time ID shown by the TriForce MCP browser page.")
        try:
            from app.services.mcp_workspace_sessions import claim_waiting_workspace
            binding = claim_waiting_workspace(code, sid)
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
        return {
            "content": [{"type": "text", "text": f"Browser workspace paired successfully in {binding['mode']} mode."}],
            "structuredContent": {
                "ok": True, "connected": True, "mode": binding["mode"],
                "task": binding.get("task", ""), "client_id": binding.get("client_id", ""),
                "lease_id": binding.get("lease_id", ""),
                "capabilities": list(binding.get("capabilities") or []),
            },
            "isError": False,
        }

    if name == "workspace_status":
        if not sid:
            return _workspace_required(request)
        status = workspace_status(sid)
        if not status.get("connected"):
            return _workspace_required(request)
        return {
            "content": [{"type": "text", "text": f"Browser workspace connected in {status['mode']} mode."}],
            "structuredContent": {"ok": True, **status},
            "isError": False,
        }

    binding = get_workspace(sid)
    if not binding:
        return _workspace_required(request)

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
        {"tool": name, "arguments": dict(arguments or {}), "mode": mode},
        timeout=180.0,
    )
    if not isinstance(result, dict):
        raise RuntimeError("Browser workspace returned an invalid MCP tool result")
    return result


# Backwards-compatible import name. Workspace routing is now shared by public
# and authenticated sessions; RBAC for non-workspace tools remains unchanged.
async def call_public_local_tool(request: Request, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await call_workspace_tool(request, name, arguments)
