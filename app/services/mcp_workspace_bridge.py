"""Public MCP local-workspace bridge.

Authenticated/admin MCP behavior remains untouched. Only public_guest requests
use this bridge: normal TriForce public cloud tools stay server-side while local
workspace tools are transparently forwarded to the workspace paired with the
current MCP session.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from fastapi import Request

from .mcp_workspace_sessions import get_workspace, workspace_status

READ_ONLY_TOOLS = {
    "workspace_status", "workspace_info",
    "file_read", "file_tree", "code_read", "code_tree", "code_search", "code_grep", "git",
}
WRITE_TOOLS = READ_ONLY_TOOLS | {
    "file_edit", "directory_create", "shell", "binary_exec", "task_runner", "lint", "test",
}
LOCAL_TOOL_NAMES = WRITE_TOOLS

# Credential-less internet surface. Deliberately excludes account/private data
# (mail, memory, notifications, forum drafts, group chats) and backend diagnostics.
PUBLIC_GUEST_CLOUD_TOOLS = {
    "chat", "list_models", "ask_specialist", "list_specialists", "models", "specialist",
    "analyze_image",
    "crawl", "crawl_url", "crawl_site", "crawl_status",
    "conversation", "prompt_template", "prompts",
    "api_docs", "web_search", "search", "search_health", "multi_search",
    "smart_search", "quick_smart_search", "google_deep_search",
    "ailinux_search", "grokipedia_search", "image_search",
    "weather", "crypto_prices", "stock_indices", "market_overview",
    "current_time", "list_timezones",
}

_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "workspace_status",
        "description": (
            "Check whether this MCP session is paired with a local TriForce workspace. "
            "If not paired, returns a short pairing code for the user to enter in the TriForce Local Workspace helper."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "workspace_info",
        "description": "Analyze the paired local workspace and show task, mode and project summary.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "file_read",
        "description": "Read a UTF-8 file from the user's paired local workspace.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1}}, "required": ["path"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "file_tree",
        "description": "Show a bounded directory tree from the user's paired local workspace.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "default": "."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 8},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "code_read",
        "description": "Read source code from the user's paired local workspace.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1}}, "required": ["path"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "code_tree",
        "description": "Inspect the source tree in the user's paired local workspace.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "default": "."}, "depth": {"type": "integer", "minimum": 1, "maximum": 8},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "code_search",
        "description": "Search source files in the user's paired local workspace.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"}, "path": {"type": "string", "default": "."},
            "file_pattern": {"type": "string", "default": "*"}, "case_sensitive": {"type": "boolean"},
            "regex": {"type": "boolean"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 500}},
            "required": ["query"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "code_grep",
        "description": "Regex-search text files in the user's paired local workspace.",
        "inputSchema": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "path": {"type": "string", "default": "."},
            "glob": {"type": "string", "default": "*"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 500}},
            "required": ["pattern"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "git",
        "description": "Read-only Git inspection in the user's paired local workspace.",
        "inputSchema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["status", "diff", "log", "show", "branch", "blame"]},
            "args": {"type": "array", "items": {"type": "string"}}, "cwd": {"type": "string", "default": "."}},
            "required": ["action"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "file_edit",
        "description": "Create or modify a UTF-8 file in the user's paired local workspace. Requires Write mode.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"}, "operation": {"type": "string", "enum": ["create", "write", "append", "replace"]},
            "content": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
            "required": ["path", "operation"]},
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "directory_create",
        "description": "Create a directory in the user's paired local workspace. Requires Write mode.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "shell",
        "description": "Run a sandboxed shell command on the user's computer inside the paired workspace. Requires Write mode.",
        "inputSchema": {"type": "object", "properties": {
            "command": {"type": "string"}, "cwd": {"type": "string", "default": "."},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 120}}, "required": ["command"]},
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "binary_exec",
        "description": "Run a sandboxed local program inside the paired workspace. Requires Write mode.",
        "inputSchema": {"type": "object", "properties": {
            "program": {"type": "string"}, "arguments": {"type": "array", "items": {"type": "string"}},
            "work_dir": {"type": "string", "default": "."}, "timeout": {"type": "integer", "minimum": 1, "maximum": 120},
            "stdin_data": {"type": "string"}}, "required": ["program"]},
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "task_runner",
        "description": "Run a sandboxed compound task inside the paired local workspace. Requires Write mode.",
        "inputSchema": {"type": "object", "properties": {
            "command": {"type": "string"}, "cwd": {"type": "string", "default": "."},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 300}}, "required": ["command"]},
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "lint",
        "description": "Run a linter locally inside the paired workspace. Requires Write mode.",
        "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string", "default": "."}}, "required": ["command"]},
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "test",
        "description": "Run project tests locally inside the paired workspace. Requires Write mode.",
        "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string", "default": "."}}, "required": ["command"]},
        "annotations": {"readOnlyHint": False},
    },
]


def is_public_guest(request: Request | None) -> bool:
    state = getattr(request, "state", None) if request is not None else None
    return bool(state is not None and getattr(state, "mcp_auth_method", None) == "public_guest")


def session_id(request: Request | None) -> str:
    state = getattr(request, "state", None) if request is not None else None
    return str(getattr(state, "mcp_session_id", "") or "")


def merge_public_workspace_tools(tools: List[Dict[str, Any]], request: Request) -> List[Dict[str, Any]]:
    """Keep normal public TriForce tools and overlay local workspace tool names."""
    if not is_public_guest(request):
        return tools
    merged = {
        str(tool.get("name") or ""): deepcopy(tool)
        for tool in tools
        if isinstance(tool, dict) and str(tool.get("name") or "") in PUBLIC_GUEST_CLOUD_TOOLS
    }
    for tool in _TOOL_SCHEMAS:
        merged[tool["name"]] = deepcopy(tool)
    return list(merged.values())


def public_instructions(request: Request) -> str:
    sid = session_id(request)
    status = get_workspace(sid)
    if status:
        return (
            "This is the public TriForce MCP. Normal TriForce public tools execute in the cloud. "
            "Local file/code/shell/test tools execute only on the user's paired computer inside the selected workspace. "
            f"Local workspace mode: {status['mode']}. User task: {status.get('task') or 'follow the user request'}."
        )
    return (
        "This is the public TriForce MCP. Normal TriForce public tools are available without login. "
        "Local file/code/shell/test tools require a user-selected local workspace. Call workspace_status to obtain a pairing code, "
        "then ask the user to open https://api.ailinux.me/v1/mcp in a browser and pair the TriForce Local Workspace helper."
    )


def _workspace_required(request: Request) -> Dict[str, Any]:
    sid = session_id(request)
    if not sid:
        return {
            "content": [{"type": "text", "text": "Local workspace requires an initialized MCP session."}],
            "structuredContent": {"ok": False, "code": "MCP_SESSION_REQUIRED"},
            "isError": True,
        }
    status = workspace_status(sid)
    code = status.get("pair_code")
    text = (
        "No local workspace is paired with this MCP session. "
        f"Pairing code: {code}. Open https://api.ailinux.me/v1/mcp in a browser, start the TriForce Local Workspace helper, "
        "choose a folder and Read only or Write, then enter this code."
    )
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"ok": False, "code": "WORKSPACE_REQUIRED", **status, "setup_url": "https://api.ailinux.me/v1/mcp"},
        "isError": False,
    }


async def call_public_local_tool(request: Request, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    sid = session_id(request)
    if name == "workspace_status":
        if not sid:
            return _workspace_required(request)
        status = workspace_status(sid)
        if not status.get("connected"):
            return _workspace_required(request)
        return {
            "content": [{"type": "text", "text": f"Local workspace connected in {status['mode']} mode."}],
            "structuredContent": {"ok": True, **status},
            "isError": False,
        }

    binding = get_workspace(sid)
    if not binding:
        return _workspace_required(request)

    mode = str(binding.get("mode") or "read_only")
    if mode != "write" and name not in READ_ONLY_TOOLS:
        return {
            "content": [{"type": "text", "text": f"Local workspace is Read only; tool '{name}' requires Write mode."}],
            "structuredContent": {"ok": False, "code": "WORKSPACE_READ_ONLY", "tool": name},
            "isError": True,
        }

    connection = binding.get("connection")
    if connection is None or bool(getattr(connection, "closed", True)):
        return _workspace_required(request)
    if "client_workspace_tool" not in set(getattr(connection, "supported_tools", []) or []):
        raise RuntimeError("Connected TriForce Workspace helper does not provide client_workspace_tool")

    result = await connection.send_tool_call(
        "client_workspace_tool",
        {"tool": name, "arguments": dict(arguments or {}), "mode": mode},
        timeout=180.0,
    )
    if not isinstance(result, dict):
        raise RuntimeError("Local workspace returned an invalid MCP tool result")
    return result
