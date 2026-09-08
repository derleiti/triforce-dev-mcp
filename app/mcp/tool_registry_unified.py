from __future__ import annotations

from typing import Any, Dict, List, Optional
from copy import deepcopy

from ..utils.tool_normalizer import normalize_tool_name
# v4 shim - no longer needed, all aliases in V5_ALIASES
from .tool_registry_v5 import get_all_tools as v5_get_all_tools, V5_ALIASES


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
    "mcp_analytics": "observability",
    "mcp_telemetry": "observability",
    "logs": "observability",
    "log_viewer": "observability",
    "health": "observability",
    "status": "admin",
    "restart": "admin",
    "service_control": "admin",
    "container_control": "admin",
    "safe_probe": "admin",
    "service_status": "admin",
    "container_status": "admin",
    "remote_hosts": "network",
    "remote_task": "network",
    "remote_status": "network",
    "remote_exec": "network",
    "remote_admin": "network",
    "network_info": "network",
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

INVENTORY_SYNONYMS: Dict[str, str] = {
    "code": "filesystem",
    "files": "filesystem",
    "fs": "filesystem",
    "system": "admin",
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
    return "misc"


def resolve_tool_name_for_call(name: str) -> str:
    normalized = normalize_tool_name(name or "")
    return V5_ALIASES.get(normalized, normalized)


def get_unified_tools(extra_tools: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    raw_tools: List[Dict[str, Any]] = []
    # v4 schemas removed — v5 is canonical source (2026-03-16)
    raw_tools.extend(v5_get_all_tools())
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

    for tool in tools:
        name = tool.get("name", "")
        tool.setdefault("x_inventory", _inventory_for_tool(name))
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
    return [tool for tool in tools if (tool.get("x_inventory") or _inventory_for_tool(tool.get("name", ""))) == wanted]


def decorate_tools(
    tools: List[Dict[str, Any]],
    include_links: bool = False,
    include_aliases: bool = False,
    include_examples: bool = False,
) -> List[Dict[str, Any]]:
    reverse_aliases: Dict[str, List[str]] = {}
    for old, new in {**V5_ALIASES}.items():
        reverse_aliases.setdefault(new, []).append(old)

    decorated: List[Dict[str, Any]] = []
    for tool in tools:
        t = deepcopy(tool)
        name = t.get("name", "")
        t["x_inventory"] = t.get("x_inventory") or _inventory_for_tool(name)
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
