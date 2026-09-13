"""Generic AILinux share manifest.

A share is the central unit of the AILinux capability fabric: it bundles
independent *resources* (workspace, clipboard, display, compute, mcp, device
metadata) with explicit *grants*. The legacy ``workspace/share`` payload is a
special case of this model, so this module is the single normalizer for both.

Design rules encoded here:

* Default is OFF. A resource is only enabled when the client actually
  advertised the corresponding capability in ``capabilities``.
* ``capabilities`` is the hard truth. A native share profile can only add
  metadata to a resource, never enable one on its own.
* ``public`` never implies write, execute or admin.
* Workspace is optional: a native-capability-only share is valid.

This module is dependency-light on purpose: routes, transport bridge and tests
all import it, so the share semantics have exactly one owner.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Optional

# --- resource types -------------------------------------------------------

RESOURCE_WORKSPACE = "workspace"
RESOURCE_CLIPBOARD = "clipboard"
RESOURCE_DISPLAY = "display"
RESOURCE_COMPUTE = "compute"
RESOURCE_MCP = "mcp"
RESOURCE_DEVICE = "device"

RESOURCE_TYPES = (
    RESOURCE_WORKSPACE,
    RESOURCE_CLIPBOARD,
    RESOURCE_DISPLAY,
    RESOURCE_COMPUTE,
    RESOURCE_MCP,
    RESOURCE_DEVICE,
)

# --- visibility / lifetime ------------------------------------------------

VISIBILITIES = ("private", "unlisted", "public")
LIFETIMES = ("session", "temporary", "persistent")

# --- workspace access modes ----------------------------------------------

WORKSPACE_OFF = "off"
WORKSPACE_READ_ONLY = "read_only"
WORKSPACE_READ_WRITE = "read_write"

#: Legacy wire values accepted from browsers/helpers and their canonical form.
_WORKSPACE_MODE_ALIASES = {
    "off": WORKSPACE_OFF,
    "none": WORKSPACE_OFF,
    "disabled": WORKSPACE_OFF,
    "read": WORKSPACE_READ_ONLY,
    "readonly": WORKSPACE_READ_ONLY,
    "read_only": WORKSPACE_READ_ONLY,
    "ro": WORKSPACE_READ_ONLY,
    "write": WORKSPACE_READ_WRITE,
    "readwrite": WORKSPACE_READ_WRITE,
    "read_write": WORKSPACE_READ_WRITE,
    "rw": WORKSPACE_READ_WRITE,
}

#: Canonical form -> value expected by the existing workspace session layer.
_LEGACY_WORKSPACE_MODE = {
    WORKSPACE_OFF: "read_only",
    WORKSPACE_READ_ONLY: "read_only",
    WORKSPACE_READ_WRITE: "write",
}

# --- capability -> resource mapping ---------------------------------------

WORKSPACE_READ_TOOLS = frozenset({
    "workspace_info", "file_read", "file_tree", "code_read", "code_tree",
    "code_search", "code_grep", "file_ops",
})
WORKSPACE_WRITE_TOOLS = frozenset({
    "file_edit", "directory_create", "workspace_clear", "code_edit",
})
DISPLAY_OBSERVE_TOOLS = frozenset({"computer_observe", "computer_screenshot"})
CLIPBOARD_READ_TOOLS = frozenset({"clipboard_read"})
CLIPBOARD_WRITE_TOOLS = frozenset({"clipboard_write"})
COMPUTE_TOOLS = frozenset({"compute_execute"})


def normalize_visibility(value: Any) -> str:
    """Return a supported visibility; anything unknown collapses to private."""
    text = str(value or "").strip().lower()
    if text in ("link", "unlisted"):
        return "unlisted"
    return text if text in VISIBILITIES else "private"


def normalize_lifetime(value: Any) -> str:
    """Return a supported lifetime; anything unknown collapses to session."""
    text = str(value or "").strip().lower()
    return text if text in LIFETIMES else "session"


def normalize_workspace_mode(value: Any) -> str:
    """Map any legacy or canonical access mode onto the canonical form."""
    text = str(value or "").strip().lower()
    return _WORKSPACE_MODE_ALIASES.get(text, WORKSPACE_READ_ONLY)


def legacy_workspace_mode(mode: str) -> str:
    """Map a canonical workspace mode back onto the legacy wire value."""
    return _LEGACY_WORKSPACE_MODE.get(mode, "read_only")


def _clean_capabilities(values: Any) -> List[str]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
        return []
    return sorted({str(x) for x in values if isinstance(x, str) and str(x).strip()})


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _resource(kind: str, enabled: bool, **detail: Any) -> Dict[str, Any]:
    item: Dict[str, Any] = {"type": kind, "enabled": bool(enabled)}
    item.update(detail)
    return item


def _bounded_number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, number))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    return int(_bounded_number(value, float(default), float(minimum), float(maximum)))


def _short_text(value: Any, limit: int = 128) -> str:
    return str(value or "").strip()[:limit]


def _string_list(value: Any, limit: int = 32) -> List[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({_short_text(item, 128) for item in value if _short_text(item, 128)})[:limit]


def build_share_manifest(
    params: Any,
    *,
    share_id: Optional[str] = None,
    owner: str = "",
    revision: int = 1,
    visibility: Any = None,
    lifetime: Any = None,
) -> Dict[str, Any]:
    """Normalize a raw ``workspace/share`` payload into a share manifest.

    ``params`` is whatever the browser or native helper sent. Unknown or
    missing fields never enable a resource; they only ever result in ``off``.
    """
    payload = _as_dict(params)
    capabilities = _clean_capabilities(payload.get("capabilities"))
    capability_set = set(capabilities)
    resources_in = _as_dict(payload.get("resources"))
    native = _as_dict(resources_in.get("native"))

    # --- workspace -------------------------------------------------------
    requested_mode = normalize_workspace_mode(
        payload.get("access_mode") or payload.get("mode")
    )
    has_read = bool(capability_set & WORKSPACE_READ_TOOLS)
    has_write = bool(capability_set & WORKSPACE_WRITE_TOOLS)
    if not has_read:
        workspace_mode = WORKSPACE_OFF
    elif requested_mode == WORKSPACE_READ_WRITE and has_write:
        workspace_mode = WORKSPACE_READ_WRITE
    else:
        workspace_mode = WORKSPACE_READ_ONLY

    # --- device-level resources (capabilities are authoritative) ---------
    clipboard_read = "clipboard_read" in capability_set
    clipboard_write = "clipboard_write" in capability_set
    display_observe = bool(capability_set & DISPLAY_OBSERVE_TOOLS)
    compute_enabled = bool(capability_set & COMPUTE_TOOLS)
    compute_native = _as_dict(native.get("compute"))
    compute_detail: Dict[str, Any] = {
        "runtime": _short_text(compute_native.get("runtime") or "docker", 32),
        "ephemeral": True,
        "available": compute_enabled and compute_native.get("available") is True,
    }
    if compute_enabled:
        compute_detail.update({
            "resource_id": _short_text(compute_native.get("resource_id"), 128),
            "node_id": _short_text(compute_native.get("node_id"), 128),
            "cpu_cores": _bounded_number(compute_native.get("cpu_cores"), 1.0, 0.1, 4096.0),
            "memory_mb": _bounded_int(compute_native.get("memory_mb"), 512, 1, 16 * 1024 * 1024),
            "gpu": _short_text(compute_native.get("gpu"), 256),
            "vram_mb": _bounded_int(compute_native.get("vram_mb"), 0, 0, 16 * 1024 * 1024),
            "models": _string_list(compute_native.get("models")),
            "capabilities": _string_list(compute_native.get("capabilities")),
            "max_concurrent": _bounded_int(compute_native.get("max_concurrent"), 1, 1, 1024),
            "load": _bounded_number(compute_native.get("load"), 0.0, 0.0, 1.0),
            "healthy": compute_native.get("healthy") is True,
        })
    device_shared = _as_dict(native.get("resources")).get("advertise") is True
    mcp_shared = _as_dict(native.get("mcp")).get("advertise") is True

    resources = [
        _resource(
            RESOURCE_WORKSPACE,
            workspace_mode != WORKSPACE_OFF,
            mode=workspace_mode,
            requested_mode=requested_mode,
        ),
        _resource(
            RESOURCE_CLIPBOARD,
            clipboard_read or clipboard_write,
            read=clipboard_read,
            write=clipboard_write,
        ),
        # Control is never derived; observing must not imply driving the host.
        _resource(RESOURCE_DISPLAY, display_observe, observe=display_observe, control=False),
        _resource(RESOURCE_COMPUTE, compute_enabled, **compute_detail),
        _resource(RESOURCE_MCP, mcp_shared, discover=mcp_shared, invoke=False),
        _resource(RESOURCE_DEVICE, device_shared, advertise=device_shared),
    ]

    grants: List[Dict[str, str]] = []
    if workspace_mode != WORKSPACE_OFF:
        grants.append({"resource": RESOURCE_WORKSPACE, "action": "read"})
    if workspace_mode == WORKSPACE_READ_WRITE:
        grants.append({"resource": RESOURCE_WORKSPACE, "action": "write"})
    if clipboard_read:
        grants.append({"resource": RESOURCE_CLIPBOARD, "action": "read"})
    if clipboard_write:
        grants.append({"resource": RESOURCE_CLIPBOARD, "action": "write"})
    if display_observe:
        grants.append({"resource": RESOURCE_DISPLAY, "action": "observe"})
    if compute_enabled:
        grants.append({"resource": RESOURCE_COMPUTE, "action": "execute"})
    if mcp_shared:
        grants.append({"resource": RESOURCE_MCP, "action": "discover"})
    if device_shared:
        grants.append({"resource": RESOURCE_DEVICE, "action": "read"})

    return {
        "share_id": str(share_id or uuid.uuid4().hex),
        "owner": str(owner or ""),
        "visibility": normalize_visibility(
            visibility if visibility is not None else native.get("visibility")
        ),
        "lifetime": normalize_lifetime(lifetime if lifetime is not None else payload.get("lifetime")),
        "verified": False,
        "task": str(payload.get("task") or ""),
        "resources": resources,
        "grants": grants,
        "capabilities": capabilities,
        "revision": max(1, int(revision or 1)),
        "origin": None,
        "upstream": None,
    }


def manifest_resource(manifest: Any, kind: str) -> Dict[str, Any]:
    """Return a resource entry by type, or a disabled placeholder."""
    for item in _as_dict(manifest).get("resources") or []:
        if isinstance(item, dict) and item.get("type") == kind:
            return item
    return {"type": kind, "enabled": False}


def manifest_has_grant(manifest: Any, resource: str, action: str) -> bool:
    """True when the manifest explicitly grants ``action`` on ``resource``."""
    for grant in _as_dict(manifest).get("grants") or []:
        if isinstance(grant, dict) and grant.get("resource") == resource and grant.get("action") == action:
            return True
    return False


def legacy_workspace_view(manifest: Any) -> Dict[str, Any]:
    """Project a manifest back onto the legacy workspace session arguments."""
    data = _as_dict(manifest)
    workspace = manifest_resource(data, RESOURCE_WORKSPACE)
    return {
        "mode": legacy_workspace_mode(str(workspace.get("mode") or WORKSPACE_OFF)),
        "task": str(data.get("task") or ""),
        "capabilities": list(data.get("capabilities") or []),
    }


def bump_revision(manifest: Any) -> Dict[str, Any]:
    """Return the manifest with an incremented revision (live share update)."""
    data = dict(_as_dict(manifest))
    data["revision"] = max(1, int(data.get("revision") or 1)) + 1
    return data
