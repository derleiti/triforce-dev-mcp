"""Portable AI-first MCP capability contract.

These schemas describe *what* an AI may ask a paired client to do. Execution is
always target-local (browser/native helper/AICoder) and capability-gated; the
contract contains no host names, usernames or absolute paths.
"""
from __future__ import annotations

PORTABLE_DEVICE_TOOL_NAMES = (
    "device_info",
    "process_ops",
    "service_ops",
    "app_ops",
    "window_ops",
    "computer_input",
)

_CTX = {
    "workspace_token": {"type": "string"},
    "workspace_context": {
        "type": "string",
        "description": "Optional non-secret context selector supplied by an authenticated bridge.",
    },
}

PORTABLE_DEVICE_TOOLS = [
    {
        "name": "device_info",
        "description": "Inspect the paired device and the capabilities the user explicitly shared. Returns OS, architecture, displays and available local adapters without exposing secrets.",
        "inputSchema": {"type": "object", "properties": dict(_CTX)},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "process_ops",
        "description": "Inspect or control processes on the paired device. Prefer list/get before control. Mutating actions are executed only when the native Helper exposes system-control capability and may require local confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "get", "signal"]},
                "pid": {"type": "integer", "minimum": 1},
                "signal": {"type": "string", "enum": ["terminate", "kill", "interrupt"], "default": "terminate"},
                "query": {"type": "string", "description": "Optional process-name filter for list."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                **_CTX,
            },
            "required": ["action"],
        },
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "service_ops",
        "description": "Inspect or control an operating-system service using a platform adapter (systemd, Windows Service Control Manager, or launchd). Mutating actions require the native Helper's system-control share and local consent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "get", "start", "stop", "restart"]},
                "service": {"type": "string", "maxLength": 256},
                "query": {"type": "string", "maxLength": 256},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                **_CTX,
            },
            "required": ["action"],
        },
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "app_ops",
        "description": "Inspect, launch, focus or close a desktop application on the paired device. Launch/focus/close are capability-gated and may require local confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "launch", "focus", "close"]},
                "app": {"type": "string", "maxLength": 512},
                "args": {"type": "array", "items": {"type": "string"}, "maxItems": 64},
                **_CTX,
            },
            "required": ["action"],
        },
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "window_ops",
        "description": "Inspect or manage desktop windows using the platform's available automation adapter. Unsupported actions fail explicitly instead of falling back to an unrestricted shell.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "focus", "close", "minimize", "maximize"]},
                "window_id": {"type": "string", "maxLength": 256},
                "title": {"type": "string", "maxLength": 512},
                **_CTX,
            },
            "required": ["action"],
        },
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "computer_input",
        "description": "Send bounded keyboard, pointer or scroll input to the paired desktop only when the user explicitly enabled computer control. Use computer_observe before and after actions to verify state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["click", "double_click", "move", "scroll", "type", "key"]},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "middle", "right"], "default": "left"},
                "delta_x": {"type": "integer", "minimum": -10000, "maximum": 10000, "default": 0},
                "delta_y": {"type": "integer", "minimum": -10000, "maximum": 10000, "default": 0},
                "text": {"type": "string", "maxLength": 65536},
                "keys": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
                **_CTX,
            },
            "required": ["action"],
        },
        "annotations": {"readOnlyHint": False},
    },
]
