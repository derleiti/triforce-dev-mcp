"""Public MCP bridge to a browser-selected local workspace.

Authenticated/admin MCP behavior is intentionally unchanged. Credential-less
``public_guest`` sessions keep the safe TriForce cloud surface and may pair one
browser tab. Browser-local file/code calls are forwarded only when that tab
advertised the exact capability during pairing.
"""
from __future__ import annotations

from copy import deepcopy
import asyncio
import base64
import binascii
import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request

from app.mcp.workspace_tool_contract import WORKSPACE_TOOL_NAMES
from .share_manifest import (
    CLIPBOARD_READ_TOOLS,
    CLIPBOARD_WRITE_TOOLS,
    COMPUTE_TOOLS,
    DISPLAY_OBSERVE_TOOLS, DISPLAY_CONTROL_TOOLS, DEVICE_READ_TOOLS as SHARE_DEVICE_READ_TOOLS, DEVICE_CONTROL_TOOLS, DEVICE_MUTATING_TOOLS,
    RESOURCE_CLIPBOARD,
    RESOURCE_COMPUTE,
    RESOURCE_DISPLAY,
    RESOURCE_DEVICE,
    RESOURCE_WORKSPACE,
    WORKSPACE_READ_TOOLS,
    WORKSPACE_WRITE_TOOLS,
    build_share_manifest,
    manifest_has_grant,
    manifest_resource,
)
from .mcp_workspace_sessions import (
    claim_workspace_with_resume_token, get_workspace, get_workspace_by_token, get_workspace_lease, mark_transport_detached,
    resolve_web_pair_code, workspace_status as lease_status,
)

CONTROL_TOOLS = {"workspace_status", "workspace_pair"}
BROWSER_READ_TOOLS = {
    "workspace_info", "file_read", "file_tree", "code_read", "code_tree",
    "code_search", "code_grep", "file_ops", "git",
}
BROWSER_WRITE_TOOLS = BROWSER_READ_TOOLS | {
    "file_edit", "directory_create", "workspace_clear", "code_edit", "shell",
}
DEVICE_READ_TOOLS = {"computer_observe", "computer_screenshot", "vision_start", "vision_status", "vision_observe", "vision_stop", "clipboard_read", "device_info", "process_ops", "service_ops"}
DEVICE_WRITE_TOOLS = {"clipboard_write", "process_ops", "service_ops", "app_ops", "window_ops", "computer_input"}
DEVICE_TOOLS = DEVICE_READ_TOOLS | DEVICE_WRITE_TOOLS
READ_ONLY_TOOLS = CONTROL_TOOLS | BROWSER_READ_TOOLS | DEVICE_TOOLS
WRITE_TOOLS = CONTROL_TOOLS | BROWSER_WRITE_TOOLS | DEVICE_TOOLS | set(COMPUTE_TOOLS)
LOCAL_TOOL_NAMES = WRITE_TOOLS

# Local MCP mirrors the canonical TriForce inventory automatically. These three
# product domains intentionally stay invisible because they operate privileged
# shared services rather than the user's paired workspace.
LOCAL_ADMIN_ONLY_INVENTORIES = frozenset({"forum", "wordpress", "mail"})
# Static MCP clients may cache tools/list before the user pairs a native Helper.
# Keep the AI-facing vision/input schemas discoverable, but mark them locked;
# execution still requires a live lease, advertised capability and manifest grant.
DISCOVERABLE_LOCKED_LOCAL_TOOLS = frozenset({"computer_observe", "computer_screenshot", "vision_start", "vision_status", "vision_observe", "vision_stop", "computer_input", "app_ops"})
WORKSPACE_EXECUTOR_WAIT_SECONDS = 25.0
WORKSPACE_EXECUTOR_POLL_SECONDS = 0.25

# Versioned canonical contract for every workspace-facing MCP projection.
# Fingerprints deliberately cover only the semantic input schema (plus name),
# so policy/visibility/annotations may differ without creating false drift.
WORKSPACE_SCHEMA_VERSION = "1.0.0"

def _stable_schema_json(value: Any) -> str:
    """Serialize schema material deterministically for cross-surface checks."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def workspace_tool_schema_fingerprint(tool: Dict[str, Any]) -> str:
    """Return the semantic schema fingerprint for one workspace tool."""
    material = {
        "name": str(tool.get("name") or ""),
        "inputSchema": tool.get("inputSchema") or {"type": "object", "properties": {}},
    }
    return hashlib.sha256(_stable_schema_json(material).encode("utf-8")).hexdigest()


def _workspace_contract_base_tools() -> List[Dict[str, Any]]:
    """Project workspace tools exclusively from TriForce's canonical registry."""
    from app.mcp.tool_registry_unified import get_canonical_all_tools

    canonical = {
        str(tool.get("name") or ""): tool
        for tool in get_canonical_all_tools()
        if isinstance(tool, dict) and str(tool.get("name") or "")
    }
    missing = [name for name in WORKSPACE_TOOL_NAMES if name not in canonical]
    if missing:
        raise RuntimeError(f"canonical workspace tools missing from registry: {missing}")
    return [deepcopy(canonical[name]) for name in WORKSPACE_TOOL_NAMES]


def workspace_contract_fingerprint() -> str:
    """Return one deterministic fingerprint for the advertised workspace surface."""
    material = [
        {
            "name": str(tool.get("name") or ""),
            "inputSchema": tool.get("inputSchema") or {"type": "object", "properties": {}},
        }
        for tool in sorted(_workspace_contract_base_tools(), key=lambda item: str(item.get("name") or ""))
    ]
    return hashlib.sha256(_stable_schema_json(material).encode("utf-8")).hexdigest()


def canonical_workspace_tools() -> List[Dict[str, Any]]:
    """Return decorated copies of the one canonical workspace tool contract."""
    contract_fp = workspace_contract_fingerprint()
    tools: List[Dict[str, Any]] = []
    for tool in _workspace_contract_base_tools():
        cloned = deepcopy(tool)
        cloned["x_schema_version"] = WORKSPACE_SCHEMA_VERSION
        cloned["x_schema_fingerprint"] = workspace_tool_schema_fingerprint(cloned)
        cloned["x_workspace_contract_fingerprint"] = contract_fp
        tools.append(cloned)
    return tools


def is_public_guest(request: Request | None) -> bool:
    state = getattr(request, "state", None) if request is not None else None
    return bool(state is not None and getattr(state, "mcp_auth_method", None) == "public_guest")


def session_id(request: Request | None) -> str:
    state = getattr(request, "state", None) if request is not None else None
    return str(getattr(state, "mcp_session_id", "") or "")


def authenticated_workspace_subject(request: Request | None) -> str:
    """Return only server-asserted workspace identity metadata from valid auth."""
    state = getattr(request, "state", None) if request is not None else None
    return str(getattr(state, "mcp_workspace_subject", "") or "").strip()


def workspace_affinity_id(request: Request | None, workspace_context: str = "") -> str:
    """Return a stable lease alias independent of ephemeral MCP transports.

    Authenticated bridge/service credentials may carry a server-side
    ``workspace_subject``.  A non-secret workspace_context then isolates several
    Telegram chats behind one connector credential.  Public/OpenAI connectors
    retain their existing transport-scoped affinity behavior.
    """
    if request is None:
        return ""
    import hashlib

    auth_subject = authenticated_workspace_subject(request)
    if auth_subject:
        context = str(workspace_context or "").strip()[:256]
        material = auth_subject + ("\0" + context if context else "")
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return "auth-workspace-" + digest[:40]

    headers = getattr(request, "headers", {})
    ua = str(headers.get("user-agent") or "").lower()
    subject = str(headers.get("x-openai-subject") or "").strip()
    transport = str(headers.get("x-openai-session") or "").strip()
    if not subject or not transport or "openai-mcp" not in ua:
        return ""
    digest = hashlib.sha256((subject + "\0" + transport).encode("utf-8")).hexdigest()
    return "openai-workspace-" + digest[:40]


def _share_binding(request: Request | None) -> Optional[Dict[str, Any]]:
    """Return the active logical share lease for discovery, if one exists."""
    sid = workspace_affinity_id(request) or session_id(request)
    return get_workspace_lease(sid) if sid else None


def _binding_share_manifest(binding: Dict[str, Any]) -> Dict[str, Any]:
    """Use the live manifest when available, otherwise rebuild grants from the persisted lease."""
    connection = binding.get("connection")
    live_manifest = getattr(connection, "share_manifest", None) if connection is not None else None
    if isinstance(live_manifest, dict):
        return live_manifest
    return build_share_manifest({
        "mode": binding.get("mode") or "read_only",
        "capabilities": list(binding.get("capabilities") or []),
    })


def _local_tool_visible(name: str, binding: Optional[Dict[str, Any]]) -> bool:
    """Project local discovery from announced capability AND its active share grant."""
    if name in CONTROL_TOOLS:
        return True
    if not binding:
        return False
    capabilities = set(binding.get("capabilities") or [])
    if name not in capabilities:
        return False
    manifest = _binding_share_manifest(binding)
    if name in WORKSPACE_WRITE_TOOLS:
        return manifest_has_grant(manifest, RESOURCE_WORKSPACE, "write")
    if name in WORKSPACE_READ_TOOLS:
        return manifest_has_grant(manifest, RESOURCE_WORKSPACE, "read")
    if name in DISPLAY_OBSERVE_TOOLS:
        return manifest_has_grant(manifest, RESOURCE_DISPLAY, "observe")
    if name in DISPLAY_CONTROL_TOOLS:
        return manifest_has_grant(manifest, RESOURCE_DISPLAY, "control")
    if name in SHARE_DEVICE_READ_TOOLS:
        return manifest_has_grant(manifest, RESOURCE_DEVICE, "read")
    if name in DEVICE_CONTROL_TOOLS:
        return manifest_has_grant(manifest, RESOURCE_DEVICE, "control")
    if name in CLIPBOARD_WRITE_TOOLS:
        return manifest_has_grant(manifest, RESOURCE_CLIPBOARD, "write")
    if name in CLIPBOARD_READ_TOOLS:
        return manifest_has_grant(manifest, RESOURCE_CLIPBOARD, "read")
    if name in COMPUTE_TOOLS:
        return manifest_has_grant(manifest, RESOURCE_COMPUTE, "execute")
    return False


def merge_workspace_tools(tools: List[Dict[str, Any]], request: Request) -> List[Dict[str, Any]]:
    """Overlay the persistent local-workspace surface on canonical TriForce tools.

    Public Local-MCP clients mirror the canonical full TriForce inventory so the
    catalog cannot drift. Forum, WordPress and mail remain admin-only and are
    omitted completely. Host/workspace-sensitive tool names are marked for local
    execution; server-side RBAC remains authoritative for online tools.
    """
    binding = _share_binding(request)
    if is_public_guest(request):
        from app.mcp.tool_registry_unified import get_canonical_all_tools
        from app.utils.mcp_security import PRIVILEGED_TOOLS

        merged: Dict[str, Dict[str, Any]] = {}
        for tool in get_canonical_all_tools():
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name") or "")
            inventory = str(tool.get("x_inventory") or "misc")
            if not name or inventory in LOCAL_ADMIN_ONLY_INVENTORIES:
                continue
            locked_local = name in LOCAL_TOOL_NAMES and not _local_tool_visible(name, binding)
            if locked_local and not (binding is None and name in DISCOVERABLE_LOCKED_LOCAL_TOOLS):
                continue
            cloned = deepcopy(tool)
            execution = "local_workspace" if name in LOCAL_TOOL_NAMES else "triforce_server"
            cloned["x_execution"] = execution
            if locked_local:
                cloned["x_requires_workspace"] = True
            if execution == "triforce_server" and name in PRIVILEGED_TOOLS:
                cloned["x_requires_admin"] = True
            merged[name] = cloned
    else:
        merged = {
            str(tool.get("name") or ""): deepcopy(tool)
            for tool in tools
            if isinstance(tool, dict) and str(tool.get("name") or "")
        }
    authenticated_bridge = bool(authenticated_workspace_subject(request))
    for cloned in canonical_workspace_tools():
        name = str(cloned.get("name") or "")
        visible_now = _local_tool_visible(name, binding)
        # Authenticated service connectors such as Nova/Telegram perform MCP
        # tool discovery before any individual chat has a bound workspace.
        # Expose the canonical schemas so the model can learn the tools once;
        # execution authority is still decided later from workspace_context,
        # the resolved lease, advertised capabilities and share grants.
        if not visible_now and not authenticated_bridge:
            continue
        cloned["x_execution"] = "local_workspace"
        if authenticated_bridge and not visible_now:
            cloned["x_requires_workspace"] = True
        merged[name] = cloned
    return list(merged.values())


# Backwards-compatible import name used by older tests/extensions.
def merge_public_workspace_tools(tools: List[Dict[str, Any]], request: Request) -> List[Dict[str, Any]]:
    return merge_workspace_tools(tools, request)


def public_instructions(request: Request) -> str:
    sid = workspace_affinity_id(request) or session_id(request)
    status = get_workspace_lease(sid)
    if status:
        capabilities = ", ".join(status.get("capabilities") or []) or "none"
        state = status.get("state", "ready")
        return (
            "This is the public TriForce MCP. Safe TriForce cloud tools execute on the server. "
            "The user paired a browser-selected local workspace; only its advertised browser capabilities execute locally. "
            f"Workspace lease state: {state}. Executor transport: {status.get('transport_state', 'offline')}. Access mode: {status['mode']}. Local capabilities: {capabilities}. "
            + ("The logical workspace session remains ready; only the local browser executor is offline. " if status.get("transport_state") == "offline" else "")
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
    if status.get("state") == "ready" and status.get("transport_state") == "offline":
        return {
            "content": [{"type": "text", "text": "The workspace session is ready, but the local browser executor is offline. Reopen the workspace page to resume the executor transport."}],
            "structuredContent": {"ok": False, "code": "WORKSPACE_TRANSPORT_OFFLINE", **status},
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


def workspace_tool_requires_write(name: str, arguments: Dict[str, Any] | None = None) -> bool:
    args = arguments or {}
    if name in {"file_edit", "directory_create", "workspace_clear", "code_edit", "shell", "computer_input"}:
        return True
    if name == "file_ops":
        return str(args.get("action") or "read").lower() in {"write", "append", "delete", "remove"}
    if name in {"process_ops", "service_ops", "app_ops", "window_ops"}:
        return str(args.get("action") or "list").lower() not in {"list", "get"}
    if name == "git":
        mode = str(args.get("mode") or args.get("action") or "status").lower()
        return mode not in {"status", "diff", "log", "show", "blame"}
    return False


async def wait_for_workspace_executor(
    request: Request,
    binding: Dict[str, Any],
    *,
    timeout: float = WORKSPACE_EXECUTOR_WAIT_SECONDS,
) -> Dict[str, Any]:
    """Wait briefly for the same persistent lease to regain a local executor.

    Android browsers may suspend the physical WebSocket while the user switches
    to ChatGPT/Telegram. The lease remains valid; a local tool call should wait
    for the resume transport instead of failing immediately.
    """
    lease_id = str(binding.get("lease_id") or "")
    affinity_sid = str(binding.get("session_id") or "") or workspace_affinity_id(request) or session_id(request)
    deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout))
    current = binding
    while asyncio.get_running_loop().time() < deadline:
        connection = current.get("connection")
        if (
            connection is not None
            and not bool(getattr(connection, "closed", True))
            and current.get("transport_state") != "offline"
        ):
            return current
        await asyncio.sleep(WORKSPACE_EXECUTOR_POLL_SECONDS)
        refreshed = get_workspace_lease(affinity_sid) if affinity_sid else None
        if refreshed is None:
            break
        if lease_id and str(refreshed.get("lease_id") or "") != lease_id:
            break
        current = refreshed
    return current


def should_route_tool_locally(request: Request | None, name: str) -> bool:
    """Route workspace-sensitive tools locally without hijacking admin MCP calls."""
    if name not in LOCAL_TOOL_NAMES:
        return False
    if name in CONTROL_TOOLS:
        return True
    if request is None:
        return False
    if is_public_guest(request):
        return True
    if authenticated_workspace_subject(request):
        return True
    sid = workspace_affinity_id(request) or session_id(request)
    return bool(sid and get_workspace_lease(sid))


_EXECUTION_TIMEOUT_TEXT = (
    "The local executor started the tool call but did not return a final result "
    "before the timeout. The operation is not retried automatically because a write "
    "may already have completed. Check workspace state before retrying."
)


def _tool_error(code: str, text: str, **extra: Any) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"ok": False, "code": code, **extra},
        "isError": True,
    }


_MAX_WORKSPACE_IMAGE_BYTES = 32 * 1024 * 1024
_IMAGE_DATA_URL_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.+)$", re.DOTALL)


def _normalize_display_tool_result(name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a verified local screenshot payload into native MCP image content.

    Helpers historically returned screenshot metadata as JSON with either a
    ``data_url`` (desktop) or raw base64 ``data`` plus an image MIME type.  The
    bridge keeps that wire contract compatible but strips the encoded image from
    structured metadata after producing the MCP ``image`` content block.
    """
    if name not in DISPLAY_OBSERVE_TOOLS or bool(result.get("isError")):
        return result

    content = result.get("content")
    if isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "image" for block in content
    ):
        return result

    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        return result

    mime = str(
        structured.get("mimeType")
        or structured.get("mime_type")
        or structured.get("mime")
        or ""
    ).strip().lower()
    encoded = structured.get("data")
    data_url = structured.get("data_url") or structured.get("dataUrl")

    if isinstance(data_url, str) and data_url:
        match = _IMAGE_DATA_URL_RE.fullmatch(data_url.strip())
        if not match:
            return _tool_error(
                "WORKSPACE_IMAGE_INVALID",
                "The local screenshot executor returned an invalid image data URL.",
                tool=name,
            )
        url_mime, encoded = match.group(1).lower(), match.group(2)
        if mime and mime != url_mime:
            return _tool_error(
                "WORKSPACE_IMAGE_INVALID",
                "The local screenshot executor returned conflicting image MIME types.",
                tool=name,
            )
        mime = url_mime

    if not isinstance(encoded, str) or not encoded or not mime.startswith("image/"):
        # A display tool may intentionally return non-image status/metadata. Keep
        # those legacy results untouched rather than guessing at arbitrary data.
        return result

    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return _tool_error(
            "WORKSPACE_IMAGE_INVALID",
            "The local screenshot executor returned malformed base64 image data.",
            tool=name,
        )
    if not decoded or len(decoded) > _MAX_WORKSPACE_IMAGE_BYTES:
        return _tool_error(
            "WORKSPACE_IMAGE_INVALID",
            "The local screenshot executor returned an empty or oversized image.",
            tool=name,
        )

    metadata = {
        key: value
        for key, value in structured.items()
        if key not in {"data", "data_url", "dataUrl"}
    }
    metadata["mimeType"] = mime
    return {
        "content": [{"type": "image", "data": encoded, "mimeType": mime}],
        "structuredContent": metadata,
        "isError": False,
    }


def _workspace_binding_response(binding: Dict[str, Any], *, token: str = "") -> Dict[str, Any]:
    connection = binding.get("connection")
    live = connection is not None and not bool(getattr(connection, "closed", True))
    data = {
        "ok": True,
        "state": "ready",
        "lease_state": "ready",
        "connected": True,
        "transport_state": "online" if live else "offline",
        "executor_online": live,
        "suspended": False,
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
    elif binding.get("resume_token"):
        data["workspace_token"] = binding["resume_token"]
    text = (
        f"Workspace session ready with {data['access_mode']} access; local executor transport is online."
        if live else
        f"Workspace session ready with {data['access_mode']} access; local executor transport is offline and may resume."
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


async def _claim_workspace_for_session(
    request: Request, workspace_id: str, workspace_context: str = ""
) -> Dict[str, Any]:
    sid = session_id(request)
    affinity_sid = workspace_affinity_id(request, workspace_context)
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
                    "resume_token": binding.get("resume_token", ""),
                },
            })
        except Exception:
            pass
    return _workspace_binding_response(binding, token=str(binding.get("resume_token") or ""))


async def call_workspace_tool(request: Request, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    sid = session_id(request)
    arguments = dict(arguments or {})
    workspace_token = str(arguments.pop("workspace_token", "") or "").strip()
    workspace_id = str(arguments.pop("workspace_id", "") or "").strip().upper()
    workspace_context = str(arguments.pop("workspace_context", "") or "").strip()[:256]

    if name == "workspace_pair":
        code = str(arguments.get("code") or "").strip().upper()
        if not code:
            return _tool_error("PAIR_CODE_REQUIRED", "workspace_pair requires the one-time ID shown by the TriForce MCP browser page.")
        # Some Streamable HTTP consumers (notably current ChatGPT connectors)
        # initialize a session but omit Mcp-Session-Id on later tools/call POSTs.
        # Bind those calls to a logical lease session keyed only by the secret pair
        # code; never infer identity from IP, User-Agent or another shared signal.
        effective_sid = workspace_affinity_id(request, workspace_context) or sid or f"workspace-lease-{__import__('uuid').uuid4().hex}"
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
                        "resume_token": binding.get("resume_token", ""),
                    },
                })
            except Exception:
                pass
        return _workspace_binding_response(binding, token=str(binding.get("resume_token") or ""))

    affinity_sid = workspace_affinity_id(request, workspace_context)
    binding = get_workspace_lease(affinity_sid) if affinity_sid else None
    if binding is None and sid:
        binding = get_workspace_lease(sid)
    if binding is None and workspace_token:
        effective_sid = affinity_sid or sid
        if effective_sid:
            try:
                binding = claim_workspace_with_resume_token(workspace_token, effective_sid)
            except Exception:
                binding = None
        else:
            binding = get_workspace_by_token(workspace_token)

    # Compatibility for MCP hosts that cached an older workspace schema. The
    # assistant may carry an exact workspace ID through an existing string
    # argument (for example code_search.query). A *currently valid* browser
    # pairing ID is explicit authorization to switch/rebind workspaces, even if
    # this MCP identity still has an older suspended lease. Invalid code-shaped
    # strings keep normal tool semantics when a live workspace already exists.
    if not workspace_id and name in (BROWSER_READ_TOOLS | BROWSER_WRITE_TOOLS):
        legacy_workspace_id = _workspace_id_from_legacy_arguments(arguments)
        if legacy_workspace_id and (resolve_web_pair_code(legacy_workspace_id) or binding is None):
            return await _claim_workspace_for_session(request, legacy_workspace_id, workspace_context)

    if name == "workspace_status":
        if workspace_id:
            return await _claim_workspace_for_session(request, workspace_id, workspace_context)

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
    if binding.get("connection") is None or binding.get("transport_state") == "offline":
        binding = await wait_for_workspace_executor(request, binding)
        if binding.get("connection") is None or binding.get("transport_state") == "offline":
            return {
                "content": [{"type": "text", "text": "Workspace lease is ready, but no local executor resumed within the handoff window. The lease and write permission remain active; reopen the workspace page and retry the tool call."}],
                "structuredContent": {
                    "ok": False, "code": "WORKSPACE_EXECUTOR_UNAVAILABLE",
                    "state": "ready", "lease_state": "ready", "connected": True,
                    "transport_state": "offline", "executor_online": False,
                    "suspended": False, "reconnectable": True,
                    "retryable": True, "waited_seconds": WORKSPACE_EXECUTOR_WAIT_SECONDS,
                    "access_mode": binding.get("mode", "read_only"), "mode": binding.get("mode", "read_only"),
                    "lease_id": binding.get("lease_id", ""),
                    "capabilities": list(binding.get("capabilities") or []),
                },
                "isError": False,
            }

    mode = str(binding.get("mode") or "read_only")
    if mode != "write" and name in BROWSER_WRITE_TOOLS and workspace_tool_requires_write(name, arguments):
        return _tool_error("WORKSPACE_READ_ONLY", f"Browser workspace is Read only; tool '{name}' requires Write mode.", tool=name)

    capabilities = set(binding.get("capabilities") or [])
    if name not in capabilities:
        return _tool_error(
            "WORKSPACE_TOOL_UNAVAILABLE",
            f"The paired browser did not advertise local tool '{name}'.",
            tool=name,
            capabilities=sorted(capabilities),
        )
    if name in DISPLAY_OBSERVE_TOOLS and not manifest_has_grant(
        _binding_share_manifest(binding), RESOURCE_DISPLAY, "observe"
    ):
        return _tool_error(
            "WORKSPACE_DISPLAY_GRANT_REQUIRED",
            "The paired helper has not granted display observation for this workspace.",
            tool=name,
        )
    if name in DISPLAY_CONTROL_TOOLS and not manifest_has_grant(
        _binding_share_manifest(binding), RESOURCE_DISPLAY, "control"
    ):
        return _tool_error(
            "WORKSPACE_DISPLAY_CONTROL_GRANT_REQUIRED",
            "The paired helper has not granted desktop control for this workspace.",
            tool=name,
        )
    if name in SHARE_DEVICE_READ_TOOLS and not manifest_has_grant(
        _binding_share_manifest(binding), RESOURCE_DEVICE, "read"
    ):
        return _tool_error(
            "WORKSPACE_DEVICE_GRANT_REQUIRED",
            "The paired helper has not granted device inspection for this workspace.",
            tool=name,
        )
    if (
        (name in DEVICE_CONTROL_TOOLS or (
            name in DEVICE_MUTATING_TOOLS
            and name not in DISPLAY_CONTROL_TOOLS
            and workspace_tool_requires_write(name, arguments)
        ))
        and not manifest_has_grant(_binding_share_manifest(binding), RESOURCE_DEVICE, "control")
    ):
        return _tool_error(
            "WORKSPACE_DEVICE_CONTROL_GRANT_REQUIRED",
            "The paired helper has not granted device control for this workspace.",
            tool=name,
        )
    if name in COMPUTE_TOOLS and not manifest_has_grant(
        _binding_share_manifest(binding), RESOURCE_COMPUTE, "execute"
    ):
        return _tool_error(
            "WORKSPACE_COMPUTE_GRANT_REQUIRED",
            "The paired helper has not granted disposable compute execution for this session.",
            tool=name,
        )

    connection = binding.get("connection")
    if connection is None or bool(getattr(connection, "closed", True)):
        return _workspace_required(request)
    if "client_workspace_tool" not in set(getattr(connection, "supported_tools", []) or []):
        raise RuntimeError("Connected browser workspace does not provide client_workspace_tool")

    if name in COMPUTE_TOOLS:
        compute = manifest_resource(_binding_share_manifest(binding), RESOURCE_COMPUTE)
        runtime = str(compute.get("runtime") or "").strip().lower().replace("-", "_")
        if runtime == "triforce_docker":
            from .workspace_compute_sandbox import execute_remote_compute
            identity = affinity_sid or sid or str(binding.get("session_id") or binding.get("lease_id") or "workspace")
            try:
                return await execute_remote_compute(
                    connection=connection, arguments=arguments, mode=mode,
                    capabilities=capabilities, identity=identity,
                )
            except (ValueError, RuntimeError) as exc:
                return _tool_error(
                    "WORKSPACE_COMPUTE_SANDBOX_FAILED", str(exc),
                    tool=name, retryable=False, sandboxed=True,
                )

    try:
        result = await connection.send_tool_call(
            "client_workspace_tool",
            {"tool": name, "arguments": arguments, "mode": mode},
            timeout=180.0,
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        return _tool_error(
            "WORKSPACE_EXECUTION_UNCERTAIN", _EXECUTION_TIMEOUT_TEXT,
            tool=name, retryable=False, execution_started=True, detail=str(exc),
        )
    except HTTPException as exc:
        # ClientConnection.send_tool_call reports a tool timeout as HTTP 504
        # instead of a TimeoutError. The call still reached the executor, so it
        # must reach the caller as uncertain rather than as a transport failure.
        if exc.status_code != 504:
            raise
        return _tool_error(
            "WORKSPACE_EXECUTION_UNCERTAIN", _EXECUTION_TIMEOUT_TEXT,
            tool=name, retryable=False, execution_started=True,
            detail=str(getattr(exc, "detail", "") or "tool call timed out"),
        )
    except (ConnectionError, RuntimeError) as exc:
        if bool(getattr(connection, "closed", False)):
            return _tool_error(
                "WORKSPACE_EXECUTION_UNCERTAIN",
                "The local executor disconnected during the tool call. The operation is not retried automatically because a write may already have completed. Check workspace state before retrying.",
                tool=name, retryable=False, detail=str(exc),
            )
        raise
    if not isinstance(result, dict):
        raise RuntimeError("Browser workspace returned an invalid MCP tool result")
    return _normalize_display_tool_result(name, result)


# Backwards-compatible import name. Workspace routing is now shared by public
# and authenticated sessions; RBAC for non-workspace tools remains unchanged.
async def call_public_local_tool(request: Request, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await call_workspace_tool(request, name, arguments)
