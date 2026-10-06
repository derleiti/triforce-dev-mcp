"""Read-only P7 inventory for the canonical MCP tool surface.

This module deliberately does not reduce or mutate the canonical registry.  It
turns the currently advertised canonical surface into an auditable dataset so
MERGE/ALIAS/DEPRECATE decisions can be evidence-based.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .runtime_registry import RuntimeToolRegistry
from .tool_registry_unified import get_canonical_all_tools
from .workspace_tool_contract import AIHELPER_CANONICAL_TO_WIRE

TOOLSET_VERSION = "p7-v1"
VALID_DECISIONS = frozenset({
    "KEEP", "MERGE", "ALIAS", "DEPRECATE", "INTERNAL_ONLY", "REMOVE",
})
VALID_CATEGORY_PREFIXES = frozenset({
    "core", "share", "workspace", "compute", "dev", "system", "service",
    "process", "network", "security", "display", "input", "container",
    "logs", "package", "hardware", "custom",
})

# Deliberate compatibility decision from the canonical-registry contract.  Do
# not infer more aliases by similar descriptions/names; those need P7 evidence.
KNOWN_DUPLICATES: Dict[str, str] = {
    "code_read": "file_read",
}

# Explicit migration outcome for every v5 definition intentionally omitted from
# the canonical model-facing surface. This prevents silent tool loss when old
# registries are compared during future consolidations. ``replacement`` is a
# canonical capability/workflow, not necessarily a wire-compatible alias.
LEGACY_CONSOLIDATION: Dict[str, Dict[str, str]] = {
    "health": {"decision": "MERGE", "replacement": "status"},
    "logs": {"decision": "MERGE", "replacement": "log_viewer"},
    "logs_errors": {"decision": "MERGE", "replacement": "log_viewer"},
    "logs_stats": {"decision": "MERGE", "replacement": "mcp_analytics"},
    "restart": {"decision": "MERGE", "replacement": "service_control/agent_start"},
    "hive_compress": {"decision": "INTERNAL_ONLY", "replacement": "memory subsystem"},
    "hive_recall": {"decision": "INTERNAL_ONLY", "replacement": "memory subsystem"},
    "hive_stats": {"decision": "INTERNAL_ONLY", "replacement": "memory subsystem"},
    "code_patch": {"decision": "MERGE", "replacement": "code_edit (AICoder keeps local patch adapter)"},
    "dev_analyze": {"decision": "MERGE", "replacement": "specialist + code tools"},
    "dev_lint": {"decision": "MERGE", "replacement": "specialist + client-local lint/test"},
    "dev_debug": {"decision": "MERGE", "replacement": "specialist + debug/code tools"},
    "dev_summarize": {"decision": "MERGE", "replacement": "specialist + code_read/code_tree"},
    "dev_links": {"decision": "MERGE", "replacement": "specialist + code_search"},
    "dev_refactor": {"decision": "MERGE", "replacement": "specialist + code_edit"},
    "image_search": {"decision": "MERGE", "replacement": "search(mode=images)"},
    "ollama_run": {"decision": "MERGE", "replacement": "chat/models"},
    "ollama_list": {"decision": "MERGE", "replacement": "models"},
    "init": {"decision": "REMOVE", "replacement": "MCP initialize"},
    "wp_list_drafts": {"decision": "MERGE", "replacement": "wp_list_posts"},
    "doc_scan": {"decision": "MERGE", "replacement": "file_tree/code_tree"},
    "doc_read": {"decision": "MERGE", "replacement": "file_read/code_read"},
    "doc_search": {"decision": "MERGE", "replacement": "code_search/search(mode=docs)"},
    "doc_tree": {"decision": "MERGE", "replacement": "file_tree/code_tree"},
    "doc_stats": {"decision": "MERGE", "replacement": "specialist + file_tree"},
    "telegram_mcp_agent": {"decision": "MERGE", "replacement": "nova_chat_agent"},
    "flarum_refresh": {"decision": "MERGE", "replacement": "flarum_discussions"},
    "flarum_admin_request": {"decision": "INTERNAL_ONLY", "replacement": "typed flarum_* tools"},
    "flarum_discussion": {"decision": "MERGE", "replacement": "flarum_discussion_get"},
    "idle_assign": {"decision": "INTERNAL_ONLY", "replacement": "agent orchestration"},
    "notify_status": {"decision": "MERGE", "replacement": "notify_list/mcp_analytics"},
    "group_chat_enqueue": {"decision": "INTERNAL_ONLY", "replacement": "group_chat_message"},
    "group_chat_status": {"decision": "MERGE", "replacement": "group_chat_read/group_chat_list"},
    "agent_chat_list": {"decision": "MERGE", "replacement": "group_chat_list"},
    "agent_chat_read": {"decision": "MERGE", "replacement": "group_chat_read"},
    "agent_chat_stream": {"decision": "MERGE", "replacement": "group_chat_read"},
    "agent_chat_summary": {"decision": "MERGE", "replacement": "group_chat_consolidate"},
    "agent_chat_cleanup": {"decision": "INTERNAL_ONLY", "replacement": "group-chat retention"},
}

_CLIENT_PLATFORMS = ["android", "browser", "linux", "macos", "windows"]


def _source_map() -> Dict[str, str]:
    """Recreate the canonical registry's first-wins provenance deterministically."""
    from .tool_registry_v5 import get_all_tools as v5_get_all_tools
    from .workspace_tool_contract import WORKSPACE_CONTROL_TOOLS
    from .handlers_memory_history import HISTORY_TOOLS
    from .structured_admin import STRUCTURED_ADMIN_TOOLS
    from .handlers_wordpress import WORDPRESS_TOOL_SCHEMAS
    from .handlers_browser import BROWSER_TOOL_SCHEMAS
    from ..services.n8n_mcp import N8N_TOOLS

    providers = (
        ("tool_registry_v5", v5_get_all_tools()),
        ("workspace_tool_contract", WORKSPACE_CONTROL_TOOLS),
        ("memory_history", HISTORY_TOOLS),
        ("structured_admin", STRUCTURED_ADMIN_TOOLS),
        ("wordpress", WORDPRESS_TOOL_SCHEMAS),
        ("browser", BROWSER_TOOL_SCHEMAS),
        ("n8n", N8N_TOOLS),
    )
    result: Dict[str, str] = {}
    for source, tools in providers:
        for tool in tools:
            name = str(tool.get("name") or "").strip()
            if name:
                result.setdefault(name, source)
                canonical_helper = next(
                    (canonical for canonical, wire in AIHELPER_CANONICAL_TO_WIRE.items() if wire == name),
                    "",
                )
                if canonical_helper:
                    result.setdefault(canonical_helper, source + ":legacy-wire")
    result.setdefault("aihelper_pair", "workspace_tool_contract")
    result.setdefault("nova_chat_agent", "tool_registry_unified:synthetic")
    return result


def _category(name: str, inventory: str) -> str:
    wire_name = AIHELPER_CANONICAL_TO_WIRE.get(name, name)
    if name == "aihelper_pair" or name.startswith("workspace_"):
        return "workspace.core"
    if name in {"file_read", "file_tree", "file_edit", "directory_create", "code_read", "code_tree", "code_search", "code_grep", "code_edit", "file_ops"}:
        return "workspace.files"
    if wire_name in {"clipboard_read", "clipboard_write"}:
        return "share.clipboard"
    if wire_name in {"computer_observe", "computer_screenshot", "vision_start", "vision_status", "vision_observe", "vision_stop"}:
        return "display.capture"
    if wire_name in {"shell", "compute_execute"}:
        return "compute.exec"
    if name.startswith("service_") or name in {"restart", "hot_reload"}:
        return "service.control"
    if name.startswith("container_") or name == "docker_stack":
        return "container.control"
    if name.startswith("remote_") or name.startswith("mesh_"):
        return "network.remote"
    if name.startswith("browser_"):
        if name in {"browser_click", "browser_type"}:
            return "input.browser"
        if name == "browser_screenshot":
            return "display.browser"
        return "custom.browser"
    if name.startswith("log_") or name.startswith("mcp_analytics"):
        return "logs.observability"
    if name == "git" or name.startswith("code_"):
        return "dev.code"
    if name.startswith("vault_"):
        return "security.vault"

    by_inventory = {
        "ai": "core.ai",
        "agents": "core.agents",
        "memory": "core.memory",
        "search": "core.search",
        "settings": "system.settings",
        "admin": "system.admin",
        "execution": "process.execution",
        "network": "network.remote",
        "observability": "logs.observability",
        "filesystem": "dev.filesystem",
        "code": "dev.code",
        "workspace": "workspace.core",
        "device": "share.device",
        "aihelper": "share.device",
        "security": "security.vault",
        "browser": "custom.browser",
        "wordpress": "custom.wordpress",
        "forum": "custom.forum",
        "mail": "custom.mail",
        "group_chat": "custom.group_chat",
        "swarm": "custom.swarm",
        "integration": "custom.integration",
        "initialization": "core.initialization",
        "planning": "core.planning",
    }
    return by_inventory.get(inventory, "custom.misc")


def _required_capabilities(name: str) -> List[str]:
    # These names are the legacy wire capabilities used by share_manifest.py.
    # Canonical aihelper_* names are translated at the protocol boundary.
    wire_name = AIHELPER_CANONICAL_TO_WIRE.get(name, name)
    workspace_caps = {
        "workspace_status": ["workspace_info"],
        "workspace_info": ["workspace_info"],
        "workspace_clear": ["workspace_clear"],
        "file_read": ["file_read"],
        "file_tree": ["file_tree"],
        "file_edit": ["file_edit"],
        "directory_create": ["directory_create"],
        "file_ops": ["file_ops"],
        "code_read": ["code_read"],
        "code_tree": ["code_tree"],
        "code_search": ["code_search"],
        "code_grep": ["code_grep"],
        "code_edit": ["code_edit"],
        "computer_observe": ["computer_observe"],
        "computer_screenshot": ["computer_screenshot"],
        "vision_start": ["vision_start"],
        "vision_status": ["vision_status"],
        "vision_observe": ["vision_observe"],
        "vision_stop": ["vision_stop"],
        "clipboard_read": ["clipboard_read"],
        "clipboard_write": ["clipboard_write"],
        "compute_execute": ["compute_execute"],
    }
    return list(workspace_caps.get(wire_name, []))


def _platforms(name: str) -> List[str]:
    if name.startswith("workspace_") or _required_capabilities(name):
        return list(_CLIENT_PLATFORMS)
    return ["server"]


def _policy_metadata(name: str, inventory: str, source: str) -> Dict[str, str]:
    # Reuse the existing runtime policy classifier without loading handlers or
    # touching live state.  This prevents the audit from inventing a second
    # effect/privilege policy.
    registry = RuntimeToolRegistry()
    defaults = registry._policy_defaults(name, inventory, source)
    classification = registry._classify_tool(name, defaults)
    if classification in {"read_safe", "preview_safe"}:
        effect, privilege = "read", "none"
    elif classification == "write_scoped":
        effect, privilege = "write", "scoped"
    elif classification == "write_privileged":
        effect, privilege = "control", "admin"
    elif classification == "exec_privileged":
        effect, privilege = "execute", "admin"
    else:
        effect, privilege = "internal", "internal"
    return {
        "runtime_classification": classification,
        "effect": effect,
        "privilege": privilege,
    }


def _metrics(events: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    accum: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "usage": 0, "errors": 0, "total_latency_ms": 0.0,
    })
    for event in events:
        name = str(event.get("tool") or "").strip()
        if not name:
            continue
        item = accum[name]
        item["usage"] += 1
        try:
            item["total_latency_ms"] += float(event.get("latency_ms") or 0.0)
        except (TypeError, ValueError):
            pass
        if str(event.get("status") or "").lower() != "success":
            item["errors"] += 1

    out: Dict[str, Dict[str, Any]] = {}
    for name, item in accum.items():
        usage = int(item["usage"])
        out[name] = {
            "usage": usage,
            "latency_ms": round(item["total_latency_ms"] / usage, 2) if usage else None,
            "error_rate": round(item["errors"] / usage, 4) if usage else None,
        }
    return out


def _live_events() -> List[Mapping[str, Any]]:
    try:
        from .structured_admin import _MCP_CALL_LOG
        return list(_MCP_CALL_LOG)
    except Exception:
        return []


def build_canonical_inventory(
    *, events: Optional[Iterable[Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Return one complete P7 audit record for every canonical advertised tool."""
    sources = _source_map()
    metrics = _metrics(_live_events() if events is None else events)
    records: List[Dict[str, Any]] = []

    for tool in get_canonical_all_tools():
        name = str(tool.get("name") or "")
        inventory = str(tool.get("x_inventory") or "misc")
        source = sources.get(name, "canonical:unknown")
        policy = _policy_metadata(name, inventory, source)
        duplicate_of = KNOWN_DUPLICATES.get(name)
        measured = metrics.get(name, {})
        record = {
            "name": name,
            "schema": deepcopy(tool.get("inputSchema") or {"type": "object", "properties": {}}),
            "category": _category(name, inventory),
            "effect": policy["effect"],
            "privilege": policy["privilege"],
            "platforms": _platforms(name),
            "required_capabilities": _required_capabilities(name),
            "duplicate_of": duplicate_of,
            "latency_ms": measured.get("latency_ms"),
            "error_rate": measured.get("error_rate"),
            "usage": int(measured.get("usage") or 0),
            "source": source,
            "classification": "ALIAS" if duplicate_of else "KEEP",
            "runtime_classification": policy["runtime_classification"],
            "inventory": inventory,
        }
        records.append(record)
    return sorted(records, key=lambda item: item["name"])


def toolset_descriptor(records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Return stable version/hash for the canonical contract (excluding metrics)."""
    items = records if records is not None else build_canonical_inventory()
    stable = [
        {
            key: item[key]
            for key in (
                "name", "schema", "category", "effect", "privilege", "platforms",
                "required_capabilities", "duplicate_of", "source", "classification",
                "runtime_classification", "inventory",
            )
        }
        for item in items
    ]
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "version": TOOLSET_VERSION,
        "hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "count": len(items),
    }


def inventory_report(*, events: Optional[Iterable[Mapping[str, Any]]] = None) -> Dict[str, Any]:
    records = build_canonical_inventory(events=events)
    decisions: Dict[str, int] = defaultdict(int)
    for row in LEGACY_CONSOLIDATION.values():
        decisions[str(row.get("decision") or "UNKNOWN")] += 1
    return {
        "toolset": toolset_descriptor(records),
        "tools": records,
        "legacy_consolidation": deepcopy(LEGACY_CONSOLIDATION),
        "legacy_decisions": dict(sorted(decisions.items())),
    }


def advertised_toolset_descriptor(tools: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Hash the exact client-visible MCP contract after auth/share filtering."""
    contract_keys = (
        "name", "description", "inputSchema", "outputSchema", "annotations", "x_inventory",
    )
    stable = []
    for tool in tools:
        stable.append({key: deepcopy(tool.get(key)) for key in contract_keys if key in tool})
    stable.sort(key=lambda item: str(item.get("name") or ""))
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "version": TOOLSET_VERSION,
        "hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "count": len(stable),
    }
