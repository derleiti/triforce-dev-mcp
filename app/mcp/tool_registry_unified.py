from __future__ import annotations

from typing import Any, Dict, List, Optional
from copy import deepcopy

from ..utils.tool_normalizer import is_readonly_tool, normalize_tool_name
# v4 shim - no longer needed, all aliases in V5_ALIASES
from .tool_registry_v5 import get_all_tools as v5_get_all_tools, V5_ALIASES
from .workspace_tool_contract import WORKSPACE_CONTROL_TOOLS, AIHELPER_LEGACY_ALIASES
from .portable_tool_contract import PORTABLE_DEVICE_TOOLS


DEFAULT_EXAMPLES: Dict[str, Dict[str, Any]] = {
    "code_read": {"path": "app/routes/mcp.py"},
    "code_search": {"query": "wp_update_post", "path": "app"},
    "config": {},
    "config_set": {"key": "MCP_WS_PORT", "value": "58642"},
    "wp_list_drafts": {"per_page": 10},
    "wp_update_post": {"post_id": 92503, "title": "Registry probe"},
    "flarum_discussion_create": {"title": "Registry update", "content": "Post via MCP", "tag_ids": [8]},
    "mail_send": {"to": "admin@ailinux.me", "subject": "Nova report", "body": "Done."},
    "agent_call": {"agent_id": "codex-mcp", "message": "review this patch"},
    "search": {"query": "OpenAI MCP write temporarily disabled"},
    "status": {},
    "debug": {"method": "tools/list", "params": {}},
}

INVENTORY_OVERRIDES: Dict[str, str] = {
    "config": "settings",
    "config_set": "settings",
    "prompts": "settings",
    "prompt_set": "settings",
    "wp_list_drafts": "wordpress",
    "wp_create_draft": "wordpress",
    "wp_update_post": "wordpress",
    "mail_inbox": "mail",
    "mail_read": "mail",
    "mail_send": "mail",
    "mail_mark_seen": "mail",
    "flarum_discussions": "forum",
    "flarum_discussion": "forum",
    "flarum_discussion_get": "forum",
    "flarum_discussion_create": "forum",
    "flarum_post_create": "forum",
    "flarum_post_edit": "forum",
    "flarum_posts": "forum",
    "flarum_tags": "forum",
    "flarum_users": "forum",
    "notify_list": "observability",
    "notify_send": "observability",
    "notify_read": "observability",
    "notify_clear": "observability",
    "notify_status": "observability",
    "idle_assign": "agents",
    "mcp_analytics": "observability",
    "mcp_telemetry": "observability",
    "logs": "observability",
    "log_viewer": "observability",
    "health": "observability",
    "status": "admin",
    "debug": "admin",
    "hot_reload": "admin",
    "restart": "admin",
    "service_control": "admin",
    "container_control": "admin",
    "docker_stack": "admin",
    "safe_probe": "admin",
    "service_status": "admin",
    "container_status": "admin",
    "remote_hosts": "network",
    "remote_task": "network",
    "mesh_status": "network",
    "mesh_task": "network",
    "remote_status": "network",
    "remote_exec": "network",
    "remote_admin": "network",
    "network_info": "network",
    "vault_status": "security",
    "vault_keys": "security",
    "vault_add": "security",
    "git": "code",
    "evolve": "code",
    "file_read": "filesystem",
    "file_ops": "filesystem",
    "code_read": "filesystem",
    "code_tree": "filesystem",
    "code_search": "filesystem",
    "code_edit": "filesystem",
    "code_patch": "filesystem",
    "task_runner": "execution",
    "binary_exec": "execution",
    "custom_binary": "execution",
    "custom_exec": "execution",
    "shell": "execution",
    "chat": "ai",
    "models": "ai",
    "specialist": "ai",
    "ollama_run": "ai",
    "ollama_list": "ai",
    "ollama_status": "ai",
    "ollama_pull": "ai",
    "ollama_delete": "ai",
    "agents": "agents",
    "agent_call": "agents",
    "agent_broadcast": "agents",
    "agent_start": "agents",
    "agent_stop": "agents",
    "agent_review": "agents",
    "agent_task_create": "agents",
    "agent_task_status": "agents",
    "agent_skill_list": "agents",
    "agent_skill_update": "agents",
    "memory_store": "memory",
    "memory_search": "memory",
    "memory_clear": "memory",
    "search": "search",
    "crawl": "search",
    "multi_search": "search",
    "smart_search": "search",
    "fetch": "search",
    "image_search": "search",
    "current_time": "search",
    "init": "initialization",
    "bootstrap": "initialization",
    "acknowledge_policy": "initialization",
    "tla_plan": "planning",
    "tla_verify": "planning",
    "tla_status": "planning",
    "tla_advance": "planning",
    "tla_abort": "planning",
    "workspace_status": "workspace",
    "workspace_pair": "workspace",
    "workspace_info": "workspace",
    "workspace_clear": "workspace",
    "aihelper_pair": "aihelper",
    "aihelper_compute_execute": "aihelper",
    "file_read": "filesystem",
    "file_tree": "filesystem",
    "code_read": "filesystem",
    "code_grep": "filesystem",
    "file_edit": "filesystem",
    "directory_create": "filesystem",
    "aihelper_observe": "aihelper",
    "aihelper_input": "aihelper",
    "aihelper_window_ops": "aihelper",
    "aihelper_app_ops": "aihelper",
    "aihelper_service_ops": "aihelper",
    "aihelper_process_ops": "aihelper",
    "aihelper_device_info": "aihelper",
    "aihelper_screenshot": "aihelper",
    "aihelper_vision_start": "aihelper",
    "aihelper_vision_status": "aihelper",
    "aihelper_vision_observe": "aihelper",
    "aihelper_vision_stop": "aihelper",
    "aihelper_clipboard_read": "aihelper",
    "aihelper_clipboard_write": "aihelper",
    # Group Chat (Multi-AI Orchestration) — Added 2026-03-15
    "group_chat_create": "group_chat",
    "group_chat_ask": "group_chat",
    "group_chat_message": "group_chat",
    "group_chat_read": "group_chat",
    "group_chat_status": "group_chat",
    "group_chat_list": "group_chat",
    "group_chat_consolidate": "group_chat",
    "group_chat_assign": "group_chat",
    # Swarm Broadcast
    "swarm_broadcast": "swarm",
    "swarm_status": "swarm",
    "swarm_top_results": "swarm",
    "swarm_consolidated": "swarm",
}



# Tool ownership/surface scopes. This is orthogonal to semantic inventories:
# inventory answers "what task is this for?" while scope answers "who owns it?".
TOOL_SCOPE_GLOBAL = "global"
TOOL_SCOPE_TRIFORCE_ADMIN = "triforce_admin"
TOOL_SCOPE_TRIFORCE_AUTH = "triforce_auth"
TOOL_SCOPE_AIHELPER = "aihelper"

TRIFORCE_GLOBAL_TOOLS = frozenset({"status"})
TRIFORCE_AUTH_INVENTORIES = frozenset({"mail", "forum", "wordpress"})
TRIFORCE_AUTH_TOOLS = frozenset({
    "nova_chat_agent", "n8n_mcp_call",
    "notify_list", "notify_read", "notify_send", "notify_clear",
})
TRIFORCE_ADMIN_INVENTORIES = frozenset({
    "admin", "agents", "group_chat", "network", "settings", "observability",
})
TRIFORCE_ADMIN_TOOLS = frozenset({
    "shell", "service_control", "container_control", "docker_stack", "hot_reload",
    "config", "config_set", "debug", "evolve", "prompt_set",
    "ollama_status", "ollama_pull", "ollama_delete",
    "mesh_status", "mesh_task", "remote_hosts", "remote_task", "remote_exec", "remote_admin",
    "vault_status", "vault_keys", "vault_add",
    "browser_navigate", "browser_click", "browser_type", "browser_screenshot", "browser_close",
    "memory_clear", "memory_history",
})

def tool_scope(name: str, inventory: str = "") -> str:
    """Return product ownership/authorization scope for one canonical tool.

    Callers that only have a tool name still receive the same classification as
    tools/list; derive the canonical primary inventory instead of silently
    falling back to ``global``.
    """
    name = str(name or "")
    inventory = str(inventory or _inventory_for_tool(name))
    if name.startswith("aihelper_"):
        return TOOL_SCOPE_AIHELPER
    if name in TRIFORCE_GLOBAL_TOOLS:
        return TOOL_SCOPE_GLOBAL
    if inventory in TRIFORCE_AUTH_INVENTORIES or name in TRIFORCE_AUTH_TOOLS or name.startswith(("mail_", "wp_", "flarum_")):
        return TOOL_SCOPE_TRIFORCE_AUTH
    if inventory in TRIFORCE_ADMIN_INVENTORIES or name in TRIFORCE_ADMIN_TOOLS or name.startswith(("agent_", "group_chat_")):
        return TOOL_SCOPE_TRIFORCE_ADMIN
    return TOOL_SCOPE_GLOBAL


# Canonical advertised MCP surface. Legacy/duplicate handlers may remain callable
# for compatibility, but are intentionally hidden from model discovery.
CANONICAL_TOOL_NAMES = frozenset({
    # Core operations
    "shell", "binary_exec", "task_runner", "status", "service_control", "container_control", "docker_stack", "hot_reload",
    "log_viewer", "mcp_analytics", "config", "config_set",
    # Files/code
    "file_ops", "code_search", "code_edit", "code_tree", "git",
    # AI/agents
    "chat", "models", "specialist", "agents", "agent_call", "agent_broadcast",
    "agent_start", "agent_stop", "evolve", "nova_chat_agent",
    # Search/browser
    "search", "crawl", "current_time", "browser_navigate", "browser_click", "browser_type",
    "browser_screenshot", "browser_close",
    # Ollama/model operations
    "ollama_status", "ollama_pull", "ollama_delete",
    # Mesh/remote
    "mesh_status", "mesh_task", "remote_hosts", "remote_task", "remote_exec", "remote_admin",
    # Vault
    "vault_status", "vault_keys", "vault_add",
    # Memory
    "memory_store", "memory_search", "memory_clear", "memory_history",
    # Settings/debug
    "prompts", "prompt_set", "debug",
    # Mail
    "mail_inbox", "mail_read", "mail_send", "mail_mark_seen",
    # WordPress
    "wp_list_posts", "wp_create_draft", "wp_update_post", "wp_delete_post",
    "wp_create_page", "wp_multi_ai_post",
    # Forum
    "flarum_discussions", "flarum_discussion_get", "flarum_post_get",
    "flarum_post_create", "flarum_post_edit", "flarum_discussion_create",
    "flarum_tags", "flarum_users", "flarum_posts",
    # Notifications
    "notify_list", "notify_read", "notify_send", "notify_clear",
    # Group chat
    "group_chat_create", "group_chat_ask", "group_chat_message", "group_chat_read",
    "group_chat_list", "group_chat_consolidate", "group_chat_assign",
    # Workspace / device controls (one canonical pool; execution remains target-specific)
    "aihelper_pair", "workspace_info", "workspace_clear",
    "file_read", "file_tree", "code_read", "code_grep", "file_edit", "directory_create",
    "aihelper_observe", "aihelper_screenshot", "aihelper_vision_start", "aihelper_vision_status", "aihelper_vision_observe", "aihelper_vision_stop", "aihelper_clipboard_read", "aihelper_clipboard_write", "aihelper_compute_execute",
    "aihelper_device_info", "aihelper_process_ops", "aihelper_service_ops", "aihelper_app_ops", "aihelper_window_ops", "aihelper_input",
    # Integrations
    "n8n_mcp_call",
})

# Small default surface for LLMs. Specialized capabilities stay available through
# inventory-specific tools/list calls without flooding every model context.
CORE_TOOL_NAMES = frozenset({
    # Global AI primitives.
    "chat", "models", "specialist", "search", "crawl", "current_time", "memory_store", "memory_search",
    # Portable workspace/code semantics. Execution remains local/share-scoped when paired.
    "workspace_info", "file_read", "file_tree", "file_edit", "file_ops", "directory_create",
    "workspace_clear", "code_read", "code_tree", "code_search", "code_grep", "code_edit", "git",
    # Pairing bootstrap and all currently important AILinux Helper capabilities.
    # Static/cached MCP clients must learn these schemas before a share exists.
    "aihelper_pair", "aihelper_observe", "aihelper_screenshot", "aihelper_vision_start",
    "aihelper_vision_status", "aihelper_vision_observe", "aihelper_vision_stop",
    "aihelper_clipboard_read", "aihelper_clipboard_write", "aihelper_compute_execute",
    "aihelper_device_info", "aihelper_process_ops", "aihelper_service_ops", "aihelper_app_ops",
    "aihelper_window_ops", "aihelper_input",
    # Stable Local-MCP compatibility names for hosts that cache an older schema.
    "workspace_status", "workspace_pair", "computer_observe", "computer_screenshot",
    "vision_start", "vision_status", "vision_observe", "vision_stop", "app_ops", "computer_input",
})


SEMANTIC_INVENTORY_PROFILES: Dict[str, Dict[str, Any]] = {
    "debug": {
        "description": "Failure analysis, logs, telemetry, MCP diagnostics and bounded status evidence.",
        "tools": {"debug", "log_viewer", "mcp_analytics", "status"},
    },
    "code": {
        "description": "Source inspection, search, structured code edits and version-control work.",
        "tools": {"code_read", "code_search", "code_tree", "code_edit", "code_grep", "git"},
    },
    "files": {
        "description": "General file and directory inspection or mutation without implying code semantics.",
        "tools": {"file_read", "file_tree", "file_edit", "file_ops", "directory_create"},
    },
    "vision": {
        "description": "Screen/browser observation and screenshots; input control remains a separate device-control capability.",
        "tools": {"aihelper_observe", "aihelper_screenshot", "aihelper_vision_start", "aihelper_vision_status", "aihelper_vision_observe", "aihelper_vision_stop", "browser_screenshot"},
    },
    "system": {
        "description": "Portable host/device state, processes, services, applications and container/system control.",
        "tools": {"aihelper_device_info", "aihelper_process_ops", "aihelper_service_ops", "aihelper_app_ops", "status", "service_control", "container_control", "docker_stack"},
    },
    "research": {
        "description": "Current web/document research plus scoped memory recall for evidence-backed work.",
        "tools": {"search", "crawl", "memory_search", "memory_history"},
    },
    "automation": {
        "description": "Execution and workflow automation primitives. Prefer typed tools over shell-like execution.",
        "inventories": {"execution", "integration"},
    },
    "communication": {
        "description": "Mail, notifications, forum and publishing surfaces.",
        "inventories": {"mail", "forum", "wordpress", "observability"},
        "exclude_tools": {"log_viewer", "mcp_analytics"},
    },
    "collaboration": {
        "description": "TriForce agents and group orchestration tools.",
        "inventories": {"agents", "group_chat", "swarm"},
    },
    "aihelper": {
        "description": "AILinux Helper pairing, vision, device control, clipboard and shared compute.",
        "tools": {name for name in CANONICAL_TOOL_NAMES if name.startswith("aihelper_")},
    },
    "workspace": {
        "description": "Pairing plus shared workspace/file operations.",
        "tools": {"aihelper_pair", "workspace_info", "workspace_clear", "file_read", "file_tree", "file_edit", "file_ops", "directory_create", "code_read", "code_tree", "code_search", "code_grep", "code_edit"},
    },
    "models": {
        "description": "Model discovery/chat plus TriForce model-runtime administration when authorized.",
        "tools": {"chat", "models", "specialist", "nova_chat_agent", "ollama_status", "ollama_pull", "ollama_delete"},
    },
    "network": {
        "description": "TriForce mesh and remote-node operations.",
        "inventories": {"network"},
        "tools": {"mesh_status", "mesh_task"},
    },
    "security": {
        "description": "TriForce vault and security-owned capabilities.",
        "tools": {"vault_status", "vault_keys", "vault_add"},
    },
    "admin": {
        "description": "TriForce engine/service administration and diagnostics.",
        "inventories": {"admin", "settings", "integration"},
        "tools": {"debug", "hot_reload", "log_viewer", "mcp_analytics"},
    },
}


def _task_inventory(name: str, inventory: str) -> str:
    if name == "aihelper_pair": return "workspace"
    if name.startswith("aihelper_vision_") or name in {"aihelper_observe", "aihelper_screenshot"}: return "vision"
    if name.startswith("aihelper_"): return "device"
    if name.startswith("code_") or name in {"git", "evolve"}: return "code"
    if name.startswith("file_") or name in {"directory_create", "workspace_info", "workspace_clear"}: return "files"
    if name.startswith("memory_"): return "memory"
    if name in {"search", "crawl"}: return "research"
    if inventory in {"mail", "forum", "wordpress", "observability"} and name.startswith(("mail_", "flarum_", "wp_", "notify_")): return "communication"
    if inventory in {"agents", "group_chat"}: return "collaboration"
    if inventory == "browser": return "browser"
    if inventory == "network" or name.startswith("mesh_"): return "network"
    if name.startswith("vault_"): return "security"
    if name.startswith("ollama_"): return "models"
    if inventory == "settings": return "settings"
    if inventory in {"admin"} or name in {"debug", "hot_reload", "log_viewer", "mcp_analytics"}: return "admin"
    if inventory == "integration": return "automation"
    if inventory == "execution": return "execution"
    if inventory == "ai": return "ai"
    return inventory or "misc"


def _semantic_inventory_groups(tool: Dict[str, Any]) -> List[str]:
    name = str(tool.get("name") or "")
    inventory = str(tool.get("x_inventory") or _inventory_for_tool(name))
    groups: List[str] = []
    for profile, rule in SEMANTIC_INVENTORY_PROFILES.items():
        tools = set(rule.get("tools") or ())
        inventories = set(rule.get("inventories") or ())
        excluded = set(rule.get("exclude_tools") or ())
        if name in excluded:
            continue
        if name in tools or inventory in inventories:
            groups.append(profile)
    task_inventory = _task_inventory(name, inventory)
    if task_inventory:
        groups.append(task_inventory)
    return sorted(set(groups))


def usage_hint_for_tool(tool: Dict[str, Any]) -> str:
    name = str(tool.get("name") or "")
    annotations = tool.get("annotations") or {}
    if annotations.get("readOnlyHint") is True or (
        "readOnlyHint" not in annotations and is_readonly_tool(name)
    ):
        effect = "read-only"
    elif annotations.get("destructiveHint") is True:
        effect = "destructive; verify target and backup first"
    else:
        effect = "may change state; use approval/backup workflow"
    groups = _semantic_inventory_groups(tool)
    group_hint = ", ".join(groups[:3]) or str(tool.get("x_inventory") or "misc")
    return f"{group_hint}; {effect}"


def tooltip_for_tool(tool: Dict[str, Any]) -> str:
    name = str(tool.get("name") or "")
    display = str(tool.get("x_display_name") or name)
    task = str(tool.get("x_task_inventory") or tool.get("x_inventory") or "misc")
    scope = str(tool.get("x_scope") or tool_scope(name, str(tool.get("x_inventory") or "")))
    access = str(tool.get("x_access") or "policy")
    usage = usage_hint_for_tool(tool)
    description = " ".join(str(tool.get("description") or "").split())
    if len(description) > 220:
        description = description[:217].rstrip() + "..."
    return f"{display} | task={task} | scope={scope} | access={access} | {usage} | {description}".strip()


def _decorate_tool_metadata(tool: Dict[str, Any]) -> Dict[str, Any]:
    name = str(tool.get("name") or "")
    inventory = str(tool.get("x_inventory") or _inventory_for_tool(name))
    scope = tool_scope(name, inventory)
    namespace = "aihelper" if scope == TOOL_SCOPE_AIHELPER else ("triforce" if scope.startswith("triforce_") else "global")
    tool["x_inventory"] = inventory
    tool["x_task_inventory"] = _task_inventory(name, inventory)
    tool["x_inventory_groups"] = _semantic_inventory_groups(tool)
    tool["x_scope"] = scope
    tool["x_namespace"] = namespace
    tool["x_access"] = {
        TOOL_SCOPE_AIHELPER: "share",
        TOOL_SCOPE_TRIFORCE_AUTH: "authenticated",
        TOOL_SCOPE_TRIFORCE_ADMIN: "admin",
    }.get(scope, "policy")
    tool["x_usage_hint"] = usage_hint_for_tool(tool)
    tool["x_display_name"] = f"aihelper.{name.removeprefix('aihelper_')}" if namespace == "aihelper" else name
    tool["x_tooltip"] = tooltip_for_tool(tool)
    return tool


def get_inventory_catalog(tools: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Task-oriented discovery index for progressive/scrollable tool selection."""
    catalog: Dict[str, Dict[str, Any]] = {}
    canonical = get_inventory_map(tools)

    def item(tool: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": str(tool.get("name") or ""),
            "display_name": str(tool.get("x_display_name") or tool.get("name") or ""),
            "scope": str(tool.get("x_scope") or "global"),
            "access": str(tool.get("x_access") or "policy"),
            "tooltip": str(tool.get("x_tooltip") or tool.get("x_usage_hint") or ""),
        }

    for name, members in canonical.items():
        rows = sorted((tool for tool in tools if str(tool.get("x_inventory") or _inventory_for_tool(str(tool.get("name") or ""))) == name), key=lambda t: str(t.get("x_display_name") or t.get("name") or ""))
        catalog[name] = {
            "count": len(members),
            "description": f"Canonical {name} ownership inventory.",
            "tools": [item(tool) for tool in rows],
        }
    for profile, rule in SEMANTIC_INVENTORY_PROFILES.items():
        members = sorted(filter_tools_by_inventory(tools, profile), key=lambda t: str(t.get("x_display_name") or t.get("name") or ""))
        catalog[profile] = {
            "count": len(members),
            "description": str(rule.get("description") or ""),
            "tools": [item(tool) for tool in members],
        }
    scope_descriptions = {
        TOOL_SCOPE_GLOBAL: "Portable/global AI tools shared across MCP consumers.",
        TOOL_SCOPE_AIHELPER: "AILinux Helper share/device tools; require an explicit user share/lease.",
        TOOL_SCOPE_TRIFORCE_AUTH: "TriForce account/integration tools; require authenticated service access.",
        TOOL_SCOPE_TRIFORCE_ADMIN: "TriForce engine administration; admin/internal authority required.",
    }
    for scope, description in scope_descriptions.items():
        members = sorted(
            (tool for tool in tools if str(tool.get("x_scope") or "") == scope),
            key=lambda t: str(t.get("x_display_name") or t.get("name") or ""),
        )
        catalog[scope] = {"count": len(members), "description": description, "tools": [item(tool) for tool in members]}
    return dict(sorted(catalog.items()))


INVENTORY_SYNONYMS: Dict[str, str] = {
    "fs": "filesystem",
    "ops": "admin",
    "config": "settings",
    "prompts": "settings",
    "observability": "observability",
    "telemetry": "observability",
    "analytics": "observability",
    "wordpress": "wordpress",
    "wp": "wordpress",
    "forum": "forum",
    "flarum": "forum",
    "mail": "mail",
    "email": "mail",
    "agents": "agents",
    "agent": "agents",
    "network": "network",
    "remote": "network",
    "execution": "execution",
    "exec": "execution",
    "search": "search",
    "ai": "ai",
    "memory": "memory",
    "init": "initialization",
    "bootstrap": "initialization",
    "planning": "planning",
    "group_chat": "group_chat",
    "gc": "group_chat",
    "swarm": "swarm",
    "browser": "browser",
    "n8n": "integration",
    "integration": "integration",
    "workspace": "workspace",
    "device": "device",
    "aihelper": "aihelper",
}


def _dedupe_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for tool in tools:
        name = tool.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(deepcopy(tool))
    return out


def _inventory_for_tool(name: str) -> str:
    if name in INVENTORY_OVERRIDES:
        return INVENTORY_OVERRIDES[name]
    if name.startswith(("wp_",)):
        return "wordpress"
    if name.startswith(("flarum_",)):
        return "forum"
    if name.startswith(("mail_",)):
        return "mail"
    if name.startswith(("notify_", "mcp_", "log_")):
        return "observability"
    if name.startswith(("agent_",)):
        return "agents"
    if name.startswith(("code_", "file_")):
        return "filesystem"
    if name.startswith(("remote_",)):
        return "network"
    if name.startswith(("memory_",)):
        return "memory"
    if name.startswith(("group_chat_",)):
        return "group_chat"
    if name.startswith(("swarm_",)):
        return "swarm"
    if name.startswith(("browser_",)):
        return "browser"
    if name.startswith(("n8n_",)):
        return "integration"
    return "misc"


LEGACY_CANONICAL_ALIASES: Dict[str, str] = dict(AIHELPER_LEGACY_ALIASES)

def resolve_tool_name_for_call(name: str) -> str:
    normalized = normalize_tool_name(name or "")
    normalized = LEGACY_CANONICAL_ALIASES.get(normalized, normalized)
    # Once a compatibility alias becomes a first-class canonical capability, its
    # canonical name wins. This keeps file_read distinct from code_read while old
    # non-canonical aliases continue resolving through V5_ALIASES.
    if normalized in CANONICAL_TOOL_NAMES:
        return normalized
    return V5_ALIASES.get(normalized, normalized)


def get_unified_tools(extra_tools: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    raw_tools: List[Dict[str, Any]] = []
    # v4 schemas removed — v5 is canonical source (2026-03-16)
    raw_tools.extend(v5_get_all_tools())
    raw_tools.extend(WORKSPACE_CONTROL_TOOLS)
    raw_tools.extend(PORTABLE_DEVICE_TOOLS)
    from .handlers_memory_history import HISTORY_TOOLS
    raw_tools.extend(HISTORY_TOOLS)

    # 2026-04-27: include STRUCTURED_ADMIN_TOOLS (log_viewer, service_status,
    # mcp_telemetry, binary_exec, custom_exec, safe_probe, file_ops, ...).
    # Their handlers are bridged into v4 HandlerRegistry via
    # handlers_v4._register_structured_admin_handlers(); this exposes them
    # in tools/list so MCP clients can discover them.
    # _dedupe_tools is "first wins", so V5 definitions take precedence on overlap.
    try:
        from .structured_admin import STRUCTURED_ADMIN_TOOLS
        raw_tools.extend(STRUCTURED_ADMIN_TOOLS)
    except Exception:
        pass

    if extra_tools:
        raw_tools.extend(extra_tools)

    tools: List[Dict[str, Any]] = []
    seen = set()
    for tool in _dedupe_tools(raw_tools):
        cloned = deepcopy(tool)
        name = resolve_tool_name_for_call(cloned.get("name", ""))
        if not name or name in seen:
            continue
        cloned["name"] = name
        seen.add(name)
        tools.append(cloned)

    # Only advertise one canonical tool per capability. Compatibility handlers and
    # aliases remain callable by name but do not consume LLM tool context.
    tools = [tool for tool in tools if tool.get("name") in CANONICAL_TOOL_NAMES]

    for tool in tools:
        _decorate_tool_metadata(tool)
    return tools


def get_canonical_all_tools() -> List[Dict[str, Any]]:
    """Return the single canonical full MCP inventory used by all surfaces."""
    from .handlers_wordpress import WORDPRESS_TOOL_SCHEMAS
    from .handlers_browser import BROWSER_TOOL_SCHEMAS
    from ..services.n8n_mcp import N8N_TOOLS

    tools = get_unified_tools(
        extra_tools=(WORDPRESS_TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS + N8N_TOOLS)
    )
    existing = {tool.get("name") for tool in tools}
    if "nova_chat_agent" not in existing:
        tools.append({
            "name": "nova_chat_agent",
            "description": "Chat through Nova's configured account-backed agent bridge.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string", "default": "auto"},
                    "message": {"type": "string"},
                    "messages": {"type": "array", "items": {"type": "object"}},
                    "system": {"type": "string"},
                    "model": {"type": "string"},
                    "temperature": {"type": "number"},
                    "max_tokens": {"type": "integer"},
                },
                "required": ["message"],
            },
            "x_inventory": "ai",
        })
    tools = _dedupe_tools(tools)
    for tool in tools:
        _decorate_tool_metadata(tool)
    return tools


def get_inventory_map(tools: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for tool in tools:
        inv = tool.get("x_inventory") or _inventory_for_tool(tool.get("name", ""))
        result.setdefault(inv, []).append(tool.get("name", ""))
    for key in result:
        result[key] = sorted(set(result[key]))
    return dict(sorted(result.items(), key=lambda kv: kv[0]))


def filter_tools_by_inventory(tools: List[Dict[str, Any]], inventory: str) -> List[Dict[str, Any]]:
    wanted = (inventory or "").strip().lower()
    wanted = INVENTORY_SYNONYMS.get(wanted, wanted)
    if not wanted or wanted in ("all", "*"):
        return tools
    scope_aliases = {
        "global": TOOL_SCOPE_GLOBAL,
        "triforce_admin": TOOL_SCOPE_TRIFORCE_ADMIN,
        "admin_only": TOOL_SCOPE_TRIFORCE_ADMIN,
        "triforce_auth": TOOL_SCOPE_TRIFORCE_AUTH,
        "authenticated": TOOL_SCOPE_TRIFORCE_AUTH,
        "aihelper": TOOL_SCOPE_AIHELPER,
    }
    scope = scope_aliases.get(wanted)
    return [
        tool for tool in tools
        if (scope is not None and str(tool.get("x_scope") or tool_scope(str(tool.get("name") or ""), str(tool.get("x_inventory") or ""))) == scope)
        or (tool.get("x_inventory") or _inventory_for_tool(tool.get("name", ""))) == wanted
        or wanted in set(tool.get("x_inventory_groups") or _semantic_inventory_groups(tool))
    ]


def filter_tools_for_profile(tools: List[Dict[str, Any]], profile: str) -> List[Dict[str, Any]]:
    """Return the small default core, canonical full surface, or one inventory."""
    wanted = (profile or "core").strip().lower()
    if wanted in {"core", "minimal", "default"}:
        return [tool for tool in tools if tool.get("name") in CORE_TOOL_NAMES]
    if wanted in {"all", "full", "*"}:
        return tools
    return filter_tools_by_inventory(tools, wanted)


def decorate_tools(
    tools: List[Dict[str, Any]],
    include_links: bool = False,
    include_aliases: bool = False,
    include_examples: bool = False,
) -> List[Dict[str, Any]]:
    reverse_aliases: Dict[str, List[str]] = {}
    for old, new in {**V5_ALIASES, **LEGACY_CANONICAL_ALIASES}.items():
        reverse_aliases.setdefault(new, []).append(old)

    decorated: List[Dict[str, Any]] = []
    for tool in tools:
        t = _decorate_tool_metadata(deepcopy(tool))
        name = t.get("name", "")
        if include_links:
            t["x_call"] = {
                "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            }
        if include_aliases:
            t["x_aliases"] = sorted(set(reverse_aliases.get(name, [])))
        if include_examples and name in DEFAULT_EXAMPLES:
            t["x_example"] = DEFAULT_EXAMPLES[name]
        decorated.append(t)
    return decorated
