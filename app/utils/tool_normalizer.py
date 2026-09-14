"""
MCP Tool Name Normalizer — Zentrale Kanonisierung
===================================================
Single Source of Truth für:
- Tool-Namen-Normalisierung (Pfade, Präfixe, Aliases)
- Read-Only-Klassifizierung (für readOnlyHint)
- Handler-Key-Auflösung (v4-Name -> interner Key)

Verwendet von: mcp_remote.py, mcp.py (handle_tools_call), handlers_v4.py
"""
from __future__ import annotations
import re
from typing import FrozenSet

# ---------------------------------------------------------------------------
# Read-Only Tool Whitelist (Single Source of Truth)
# Alle Tools die sicher readOnlyHint=true bekommen dürfen
# ---------------------------------------------------------------------------
READONLY_TOOLS: FrozenSet[str] = frozenset({
    # v4 kanonische Namen
    "status", "health", "models", "search", "specialist",
    "agents", "agent_output",
    "code_tree", "code_read", "code_search",
    "dev_analyze", "dev_lint", "dev_debug", "dev_summarize",
    "dev_links", "dev_refactor",
    "doc_scan", "doc_read", "doc_search", "doc_tree", "doc_stats",
    "memory_search", "debug", "logs", "logs_errors", "logs_stats",
    "config", "prompts",
    "init", "bootstrap",

    # v3 / Legacy-Namen
    "list_models", "ask_specialist", "list_specialists",
    "web_search", "multi_search", "smart_search", "quick_smart_search",
    "search_health", "fetch", "ailinux_search", "grokipedia_search", "image_search",
    "google_deep_search",
    "tristar_models", "tristar_memory_search", "tristar_status",
    "tristar_settings", "tristar_prompts_list",
    "codebase_structure", "codebase_file", "codebase_search",
    "codebase_routes", "codebase_services", "codebase_backup",
    "cli-agents_list", "cli-agents_get", "cli-agents_output", "cli-agents_stats",
    "ollama_list", "ollama_status", "ollama_health", "ollama_ps", "ollama_show",
    "queue_status", "queue_agents", "queue_get",
    "memory_index_search",
    "vault_keys", "vault_status",
    "mesh_status", "mesh_agents",
    "mesh_filter_check", "mesh_filter_audit",
    "check_compatibility", "debug_mcp_request",
    "api_docs", "api_search",
    "weather", "crypto_prices", "stock_indices", "market_overview",
    "current_time", "list_timezones",
    "crawl_status",
    "search_llm_config",
    "health_check",  # v3 Name
    "network_info", "system_info",
    # Canonical AILinux Helper read-only surface. Mutating Helper tools (pair,
    # input, app/process/service/window ops, clipboard write, vision start/stop,
    # compute) deliberately stay out of this set.
    "aihelper_observe", "aihelper_screenshot", "aihelper_vision_status",
    "aihelper_vision_observe", "aihelper_clipboard_read", "aihelper_device_info",

    # Agent-Endpoint Methoden
    "agent/status", "agent/output",

    # Tristar internas die lesend sind
    "tristar_memory_search", "triforce_logs_recent", "triforce_logs_errors",
    "triforce_logs_stats", "tristar_logs",

    # v4 Composite
    "gemini_research", "gemini_quick",
    "ollama_embed",

    # v4 Admin/Infra - read-only Tools die gefehlt haben (NOVA-PATCH)
    "log_viewer",       # liest Logs, ändert nichts
    "process_control",  # list/find Prozesse, kein Kill
    "remote_hosts",     # listet Hosts, keine Aktion
    "remote_status",    # liest Task-Status, keine Aktion
    "remote_info",      # listet Federation-Nodes, keine Aktion
    "evolve_history",   # liest Evolutions-History, keine Aktion

    # v2.82 Read-Only Tools (PATCH v2.82)
    "safe_probe",         # System-Diagnostik: hostname, uptime, free, df, journal (kein sudo)
    "agent_review",       # Agent-Status/Output/Health lesen (kein start/stop)
    "service_status",     # systemctl status/logs (kein start/stop/restart)
    "container_status",   # docker ps/status/logs/stats (kein start/stop/restart)
    "file_read",          # Dateien lesen/listen/finden (kein write/append)
    # remote_status already listed above

    # v2.82 Telemetry
    "mcp_telemetry",      # Read-only performance metrics for MCP tool calls

    # v2.82 Read-Only Split-Tools + neue Diagnostik (NOVA-PATCH v2.82)
    "safe_probe",         # read-only System-Diagnostik (hostname, uptime, df, etc.)
    "agent_review",       # read-only Agent-Inspektion (status, output, health_check)
    "service_status",     # read-only Wrapper für service_control (nur status+logs)
    "container_status",   # read-only Wrapper für container_control (nur list/status/logs/stats)
    "file_read",          # read-only Wrapper für file_ops (nur read/list/find/size)
    "mcp_analytics",      # read-only MCP Tool-Performance-Metriken

    # v4 ChatGPT-Kompatibilität — fehlende READONLY-Klassifikation (NOVA-PATCH 2)
    "chat",                     # LLM-Chat-Aufruf, kein Server-State-Write
    "ollama_run",               # LLM Inference, kein Persistenz-Write
    "opnsense_status",          # OPNsense Status-Abfrage (read)
    "opnsense_firewall_rules",  # OPNsense Firewall-Regeln lesen (read)
    "opnsense_interfaces",      # OPNsense Interfaces lesen (read)
    "opnsense_logs",            # OPNsense Logs lesen (read)
    "opnsense_vpn_status",      # OPNsense VPN-Status (read)

    # v2.82 Read-Only Split-Tools (PATCH v2.82)
    "safe_probe",               # System-Diagnostik, kein sudo, keine Side Effects
    "agent_review",             # Agent-Status/Output lesen, kein start/stop
    "service_status",           # systemd status/logs lesen, kein restart/stop
    "container_status",         # docker list/status/logs/stats, kein start/stop
    "file_read",                # Dateien lesen/listen, kein write/append
    "remote_status",            # Federation-Node-Status lesen, kein restart
    "mcp_analytics",            # MCP-Tool-Call-Statistiken lesen
    # v2.86 Read-Only Wrappers
    "remote_hosts",             # listet Federation-Hosts
    "binary_list",              # listet verfuegbare Binaries
    "template_list",            # listet Command-Templates
    "task_reference",           # quick_reference/encode/decode
})

# ---------------------------------------------------------------------------
# v4-Name -> interner Handler-Schlüssel (kanonisches Mapping)
# ACHTUNG: Nur eintragen wenn v4-Name ANDERS als interner Handler-Name!
# Kein Many-to-One, nur explizite Forward-Mappings
# ---------------------------------------------------------------------------
CANONICAL_MAP: dict[str, str] = {
    "status"          : "tristar_status",
    # "health" → handlers_v4 registriert es direkt als "health", kein Remapping
    "models"          : "list_models",
    "web_search"      : "search",
    "multi_search"    : "search",
    "smart_search"    : "search",
    "quick_smart_search" : "search",
    "specialist"      : "ask_specialist",
    "agents"          : "cli-agents_list",
    "agent_call"      : "cli-agents_call",
    "agent_broadcast" : "cli-agents_broadcast",
    "agent_start"     : "cli-agents_start",
    "agent_stop"      : "cli-agents_stop",
    "agent_output"    : "cli-agents_output",
    "code_tree"       : "codebase_structure",
    "code_read"       : "codebase_file",
    "code_search"     : "codebase_search",
    "code_edit"       : "codebase_edit",
    "code_patch"      : "ram_patch_apply",
    "memory_store"    : "tristar_memory_store",
    "memory_search"   : "tristar_memory_search",
    "memory_clear"    : "tristar_memory_clear",
    "debug"           : "debug_mcp_request",
    "logs"            : "tristar_logs",
    "logs_errors"     : "triforce_logs_errors",
    "logs_stats"      : "triforce_logs_stats",
    "config"          : "tristar_settings",
    "config_set"      : "tristar_settings_set",
    "prompts"         : "tristar_prompts_list",
    "prompt_set"      : "tristar_prompts_set",
    "crawl"           : "crawl_url",
    "bootstrap"       : "bootstrap_all",
    "restart"         : "restart_backend",
    "vault_keys"      : "vault_list_keys",
    "vault_add"       : "vault_add_key",
    "remote_hosts"    : "remote_hosts",          # v2.86: eigener Handler (war remote_host_list → nicht existent)
    "remote_task"     : "remote_task_submit",
    "remote_status"   : "remote_status",          # v2.86: zeigt auf registrierten read-only Wrapper
}

# ---------------------------------------------------------------------------
# Path-Präfix-Pattern: "/api.ailinux.me/link_xxx/toolname" → "toolname"
# ---------------------------------------------------------------------------
_PATH_PREFIX_RE = re.compile(r'^/[^/]+/[^/]+/([a-zA-Z0-9_/-]+)$')
VALID_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
RESERVED_SLASH_NAMES: FrozenSet[str] = frozenset({
    "initialize",
    "tools/list",
    "tools/call",
    "prompts/list",
    "prompts/get",
    "resources/list",
    "resources/read",
    "agent/status",
    "agent/output",
})


def is_valid_tool_name(name: str) -> bool:
    """Return True if a tool name matches the external MCP export pattern."""
    return bool(VALID_TOOL_NAME_RE.fullmatch(name or ""))


def normalize_tool_name(raw_name: str) -> str:
    """Normalize any MCP tool name/path to canonical form.
    
    Handles:
    - /domain/link_xxx/tool_name -> tool_name
    - tools/call:tool_name -> tool_name
    - /v1/mcp/link_abc123/tool_name -> tool_name
    - Whitespace/case normalization
    """
    if not raw_name:
        return ""
    
    name = raw_name.strip()

    if name in RESERVED_SLASH_NAMES:
        return name
    
    # Strip "tools/call:" prefix (from log entries)
    if name.startswith("tools/call:"):
        name = name[len("tools/call:"):]

    if name in RESERVED_SLASH_NAMES:
        return name
    
    # Strip path-based prefixes: /anything/link_xxx/tool -> tool
    # Also handles /domain/anything/tool
    if "/" in name and name not in RESERVED_SLASH_NAMES:
        # Take last segment after any path
        segments = [s for s in name.split("/") if s]
        if segments:
            # If any segment looks like link_xxx, take everything after it
            for i, seg in enumerate(segments):
                if re.match(r'^link_[a-zA-Z0-9]+$', seg):
                    name = "/".join(segments[i+1:]) if i+1 < len(segments) else segments[-1]
                    break
            else:
                # No link_ segment found, take last segment
                name = segments[-1]

    if name in RESERVED_SLASH_NAMES:
        return name

    if is_valid_tool_name(name):
        return name

    # Normalize legacy dotted/slashed names to external-safe MCP tool IDs.
    name = name.replace(".", "_").replace("/", "_")
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^a-zA-Z0-9_-]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")

    if len(name) > 64:
        name = name[:64].rstrip("_-")

    return name


def resolve_to_handler_key(tool_name: str) -> str:
    """
    Löst einen (ggf. bereits normalisierten) Tool-Namen zum
    internen Handler-Schlüssel auf.
    Gibt den Namen unverändert zurück, wenn kein Mapping existiert.
    """
    normalized = normalize_tool_name(tool_name)
    return CANONICAL_MAP.get(normalized, normalized)


def is_readonly_tool(tool_name: str) -> bool:
    """True wenn das Tool definitiv read-only ist (readOnlyHint=true)."""
    normalized = normalize_tool_name(tool_name)
    return normalized in READONLY_TOOLS
