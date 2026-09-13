"""
MCP Tool Registry v4.0 - Consolidated & Optimized
==================================================

Von 134 Tools auf ~55 reduziert:
- Duplikate entfernt
- Ähnliche Tools zusammengelegt
- Klarere Namen ohne redundante Präfixe
- Kategorien vereinfacht

Version: 4.0.0
Author: AILinux/NOVA
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("ailinux.mcp.registry")

Handler = Callable[[Dict[str, Any]], Awaitable[Any]]


# =============================================================================
# CORE - Chat & Models (3 Tools)
# =============================================================================

CORE_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "chat",
        "description": "Send message to any AI model (Ollama, Gemini, Claude, GPT, Mistral). Auto-routes if no model specified.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The message to send"},
                "model": {"type": "string", "description": "Model ID (e.g., 'gemini-2.0-flash', 'claude-sonnet-4', 'llama3.2')"},
                "system_prompt": {"type": "string", "description": "Optional system prompt"},
                "temperature": {"type": "number", "description": "Sampling temperature (0.0-2.0)"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "models",
        "description": "List all available AI models with capabilities, status and provider info",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "specialist",
        "description": "Route task to best specialist model based on task type (code, math, creative, etc.)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task type: code, math, creative, analysis, research"},
                "message": {"type": "string", "description": "The actual message/prompt"},
            },
            "required": ["task", "message"],
        },
    },
]


# =============================================================================
# SEARCH & WEB (2 Tools)
# =============================================================================

SEARCH_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search",
        "description": "Search the web for current information",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "crawl",
        "description": "Crawl a website and extract content",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to crawl"},
                "max_pages": {"type": "integer", "description": "Maximum pages (default: 10)"},
            },
            "required": ["url"],
        },
    },
]


# =============================================================================
# MEMORY - Persistent Knowledge (3 Tools)
# =============================================================================

MEMORY_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "memory_store",
        "description": "Store knowledge/facts/decisions in persistent memory",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to store"},
                "type": {"type": "string", "enum": ["fact", "decision", "code", "summary", "todo"], "description": "Memory type"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1, "description": "Confidence score"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "memory_search",
        "description": "Search persistent memory for relevant information",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "type": {"type": "string", "description": "Filter by memory type"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter by tags"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
            },
        },
    },
    {
        "name": "memory_clear",
        "description": "Clear memory entries by type, tags, or age",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Clear only this type"},
                "older_than_days": {"type": "integer", "description": "Clear entries older than N days"},
            },
        },
    },
]


# =============================================================================
# AGENTS - CLI Agent Management (5 Tools, merged from cli-agents + queue)
# =============================================================================

AGENT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "agents",
        "description": "List all CLI agents (Claude, Codex, Gemini, OpenCode) with status and stats",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "agent_call",
        "description": "Send a message/task to a specific CLI agent and get response",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent ID: claude, codex, gemini, opencode"},
                "message": {"type": "string", "description": "Message to send"},
                "timeout": {"type": "integer", "description": "Timeout seconds (default: 120)"},
                "execution_mode": {
                    "type": "string",
                    "enum": ["auto", "direct", "aicoder", "team"],
                    "description": "Execution routing override; auto prefers direct execution for small tasks.",
                    "default": "auto",
                },
            },
            "required": ["agent", "message"],
        },
    },
    {
        "name": "agent_broadcast",
        "description": "Send message to all agents for parallel processing / consensus",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to broadcast"},
                "strategy": {"type": "string", "enum": ["parallel", "consensus", "sequential"], "description": "Execution strategy"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "agent_start",
        "description": "Start/restart a CLI agent",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent ID to start"},
            },
            "required": ["agent"],
        },
    },
    {
        "name": "agent_stop",
        "description": "Stop a running CLI agent",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent ID to stop"},
                "force": {"type": "boolean", "description": "Force kill"},
            },
            "required": ["agent"],
        },
    },
]


# =============================================================================
# CODE - Codebase Tools (5 Tools, merged from codebase + adaptive_code)
# =============================================================================

CODE_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "code_read",
        "description": "Read a file from the codebase",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "code_search",
        "description": "Search for patterns/text in the codebase (regex supported)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search pattern"},
                "path": {"type": "string", "description": "Limit to path (default: app)"},
                "regex": {"type": "boolean", "description": "Use regex (default: false)"},
                "max_results": {"type": "integer", "description": "Max results (default: 50)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "code_edit",
        "description": "Edit a file: replace, insert, append, or delete lines. Auto-backup.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "mode": {"type": "string", "enum": ["replace", "insert", "append", "delete"], "description": "Edit mode"},
                "old_text": {"type": "string", "description": "Text to find (for replace)"},
                "new_text": {"type": "string", "description": "New text"},
                "line": {"type": "integer", "description": "Line number (for insert/delete)"},
                "dry_run": {"type": "boolean", "description": "Preview without saving"},
            },
            "required": ["path", "mode"],
        },
    },
    {
        "name": "code_tree",
        "description": "Show directory structure with optional depth limit",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to scan (default: app)"},
                "depth": {"type": "integer", "description": "Max depth (default: 3)"},
            },
        },
    },
    {
        "name": "code_patch",
        "description": "Apply unified diff patch to codebase",
        "inputSchema": {
            "type": "object",
            "properties": {
                "diff": {"type": "string", "description": "Unified diff content"},
                "dry_run": {"type": "boolean", "description": "Preview only (default: true)"},
            },
            "required": ["diff"],
        },
    },
]


# =============================================================================
# OLLAMA - Local Model Management (6 Tools)
# =============================================================================

OLLAMA_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "ollama_list",
        "description": "List all local Ollama models with size and details",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ollama_pull",
        "description": "Download a model from Ollama registry",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name (e.g., llama3.2, qwen2.5:14b)"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "ollama_delete",
        "description": "Delete a local Ollama model",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name to delete"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "ollama_run",
        "description": "Run inference on local Ollama model",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name"},
                "prompt": {"type": "string", "description": "Prompt text"},
                "system": {"type": "string", "description": "System prompt"},
            },
            "required": ["model", "prompt"],
        },
    },
    {
        "name": "ollama_embed",
        "description": "Generate embeddings from text",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Embedding model"},
                "text": {"type": "string", "description": "Text to embed"},
            },
            "required": ["model", "text"],
        },
    },
    {
        "name": "ollama_status",
        "description": "Check Ollama server status and running models",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# =============================================================================
# LOGS - Logging & Monitoring (3 Tools, merged from triforce_logs + tristar_logs)
# =============================================================================

LOG_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "logs",
        "description": "Get recent system logs with optional filtering",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["api", "llm", "mcp", "error", "agent", "all"], "description": "Log category"},
                "limit": {"type": "integer", "description": "Max entries (default: 100)"},
                "level": {"type": "string", "enum": ["debug", "info", "warning", "error"], "description": "Min log level"},
            },
        },
    },
    {
        "name": "logs_errors",
        "description": "Get recent error logs only",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max entries (default: 50)"},
            },
        },
    },
    {
        "name": "logs_stats",
        "description": "Get logging statistics: counts, rates, uptime",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# =============================================================================
# CONFIG - Settings & Prompts (4 Tools, merged from tristar_settings + prompts)
# =============================================================================

CONFIG_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "config",
        "description": "Get all configuration settings",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "config_set",
        "description": "Set a configuration value",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Setting key"},
                "value": {"description": "Setting value (string, number, bool, or object)"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "prompts",
        "description": "List all system prompts",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "prompt_set",
        "description": "Create or update a system prompt",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Prompt name"},
                "content": {"type": "string", "description": "Prompt content"},
            },
            "required": ["name", "content"],
        },
    },
]


# =============================================================================
# SYSTEM - Control & Debug (5 Tools)
# =============================================================================

SYSTEM_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "status",
        "description": "Get full system status: services, agents, memory, uptime",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "shell",
        "description": "Execute shell command (admin only, dangerous)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command"},
                "timeout": {"type": "integer", "description": "Timeout seconds (default: 30)"},
                "sudo": {"type": "boolean", "description": "Run with sudo"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "restart",
        "description": "Restart backend service or specific agent",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "What to restart: backend, or agent ID"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "health",
        "description": "Quick health check of all services",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "debug",
        "description": "Trace an MCP request without executing",
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "MCP method to trace"},
                "params": {"type": "object", "description": "Parameters"},
            },
            "required": ["method"],
        },
    },
]


# =============================================================================
# VAULT - API Key Management (3 Tools)
# =============================================================================

VAULT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "vault_keys",
        "description": "List stored API keys (names only, not values)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "vault_add",
        "description": "Add/update an API key",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["openai", "anthropic", "google", "mistral", "groq"], "description": "Provider name"},
                "key": {"type": "string", "description": "API key value"},
            },
            "required": ["provider", "key"],
        },
    },
    {
        "name": "vault_status",
        "description": "Check vault status (locked/unlocked)",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# =============================================================================
# REMOTE - Remote Task Execution (3 Tools)
# =============================================================================

REMOTE_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "remote_hosts",
        "description": "List registered remote hosts for task execution",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "remote_task",
        "description": "Submit a task to run on remote host via SSH",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Host ID"},
                "task": {"type": "string", "description": "Task description"},
                "agent": {"type": "string", "description": "Agent to use (default: auto)"},
            },
            "required": ["host", "task"],
        },
    },
    {
        "name": "remote_status",
        "description": "Get status of remote tasks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Specific task ID (optional)"},
            },
        },
    },
]


# =============================================================================
# EVOLVE - Auto-Evolution & Analysis (2 Tools)
# =============================================================================

EVOLVE_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "evolve",
        "description": "Run auto-evolution analysis: find issues, propose improvements",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["analyze", "suggest", "implement"], "description": "Mode: analyze only, suggest fixes, or implement"},
                "focus": {"type": "array", "items": {"type": "string"}, "description": "Focus areas: security, performance, quality"},
            },
        },
    },
    {
        "name": "evolve_history",
        "description": "Get history of past evolution runs",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max entries (default: 10)"},
            },
        },
    },
]


# =============================================================================
# INIT - Bootstrap & Session (2 Tools, reduced from 10)
# =============================================================================

INIT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "init",
        "description": "Initialize agent session with system status and available tools",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent ID for specific prompt"},
            },
        },
    },
    {
        "name": "bootstrap",
        "description": "Start all CLI agents and initialize system",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lead_first": {"type": "boolean", "description": "Start lead agent first (default: true)"},
            },
        },
    },
]


# =============================================================================
# GEMINI - Gemini-specific Tools (3 Tools, reduced from 9)
# =============================================================================

GEMINI_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "gemini_research",
        "description": "Gemini conducts research using memory, web, and other LLMs",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Research query"},
                "store": {"type": "boolean", "description": "Store findings in memory (default: true)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gemini_coordinate",
        "description": "Gemini coordinates a task across multiple LLMs",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task to coordinate"},
                "strategy": {"type": "string", "enum": ["parallel", "sequential", "consensus"], "description": "Execution strategy"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "gemini_exec",
        "description": "Execute Python code in Gemini's sandbox",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "timeout": {"type": "integer", "description": "Timeout seconds (default: 30)"},
            },
            "required": ["code"],
        },
    },
]


# =============================================================================
# MESH - Mesh AI Coordination (3 Tools, reduced from 7)
# =============================================================================

MESH_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "mesh_status",
        "description": "Get mesh AI system status",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mesh_task",
        "description": "Submit task to mesh for distributed processing",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title"},
                "description": {"type": "string", "description": "Task description"},
            },
            "required": ["title", "description"],
        },
    },
    {
        "name": "mesh_agents",
        "description": "List mesh agents and their roles",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# =============================================================================
# CONSOLIDATED TOOL LIST
# =============================================================================

def get_all_tools() -> List[Dict[str, Any]]:
    """Returns all available MCP tools (optimized set)."""
    all_tools = []
    all_tools.extend(CORE_TOOLS)        # 3
    all_tools.extend(SEARCH_TOOLS)      # 2
    all_tools.extend(MEMORY_TOOLS)      # 3
    all_tools.extend(AGENT_TOOLS)       # 5
    all_tools.extend(CODE_TOOLS)        # 5
    all_tools.extend(OLLAMA_TOOLS)      # 6
    all_tools.extend(LOG_TOOLS)         # 3
    all_tools.extend(CONFIG_TOOLS)      # 4
    all_tools.extend(SYSTEM_TOOLS)      # 5
    all_tools.extend(VAULT_TOOLS)       # 3
    all_tools.extend(REMOTE_TOOLS)      # 3
    all_tools.extend(EVOLVE_TOOLS)      # 2
    all_tools.extend(INIT_TOOLS)        # 2
    all_tools.extend(GEMINI_TOOLS)      # 3
    all_tools.extend(MESH_TOOLS)        # 3
    return all_tools                    # = 52 Total


def get_tool_count() -> int:
    """Returns total tool count."""
    return len(get_all_tools())


def get_tool_names() -> List[str]:
    """Returns all tool names."""
    return [tool["name"] for tool in get_all_tools()]


def get_tool_by_name(tool_name: str) -> Optional[Dict[str, Any]]:
    """Get tool definition by name."""
    for tool in get_all_tools():
        if tool.get("name") == tool_name:
            return tool
    return None


def get_categories() -> Dict[str, List[str]]:
    """Returns categories with their tool names."""
    return {
        "core": [t["name"] for t in CORE_TOOLS],
        "search": [t["name"] for t in SEARCH_TOOLS],
        "memory": [t["name"] for t in MEMORY_TOOLS],
        "agents": [t["name"] for t in AGENT_TOOLS],
        "code": [t["name"] for t in CODE_TOOLS],
        "ollama": [t["name"] for t in OLLAMA_TOOLS],
        "logs": [t["name"] for t in LOG_TOOLS],
        "config": [t["name"] for t in CONFIG_TOOLS],
        "system": [t["name"] for t in SYSTEM_TOOLS],
        "vault": [t["name"] for t in VAULT_TOOLS],
        "remote": [t["name"] for t in REMOTE_TOOLS],
        "evolve": [t["name"] for t in EVOLVE_TOOLS],
        "init": [t["name"] for t in INIT_TOOLS],
        "gemini": [t["name"] for t in GEMINI_TOOLS],
        "mesh": [t["name"] for t in MESH_TOOLS],
    }


# =============================================================================
# HANDLER REGISTRY
# =============================================================================

_TOOL_HANDLERS: Dict[str, Handler] = {}


def register_handler(tool_name: str, handler: Handler) -> None:
    """Register a handler for a tool."""
    _TOOL_HANDLERS[tool_name] = handler
    logger.debug(f"Registered handler: {tool_name}")


def get_handler(tool_name: str) -> Optional[Handler]:
    """Get handler for a tool."""
    return _TOOL_HANDLERS.get(tool_name)


def register_handlers(handlers: Dict[str, Handler]) -> int:
    """Register multiple handlers from dict."""
    for name, handler in handlers.items():
        register_handler(name, handler)
    return len(handlers)


# =============================================================================
# ALIAS MAPPING - Maps old tool names to new ones
# =============================================================================

TOOL_ALIASES: Dict[str, str] = {
    # Old name -> New name
    "list_models": "models",
    "ask_specialist": "specialist",
    "web_search": "search",
    "crawl_url": "crawl",
    "tristar_memory_store": "memory_store",
    "tristar_memory_search": "memory_search",
    "cli-agents_list": "agents",
    "cli-agents_call": "agent_call",
    "cli-agents_broadcast": "agent_broadcast",
    "cli-agents_start": "agent_start",
    "cli-agents_stop": "agent_stop",
    # "queue_broadcast": "agent_broadcast",  # DEPRECATED: causes schema collision (message vs command)
    "codebase_file": "code_read",
    "codebase_search": "code_search",
    "codebase_edit": "code_edit",
    "codebase_structure": "code_tree",
    "code_scout": "code_tree",
    "ram_patch_apply": "code_patch",
    "ollama_generate": "ollama_run",
    "ollama_health": "ollama_status",
    "ollama_ps": "ollama_status",
    "triforce_logs_recent": "logs",
    "triforce_logs_errors": "logs_errors",
    "triforce_logs_stats": "logs_stats",
    "tristar_logs": "logs",
    "tristar_settings": "config",
    "tristar_settings_set": "config_set",
    "tristar_prompts_list": "prompts",
    "tristar_prompts_set": "prompt_set",
    "tristar_status": "status",
    "tristar_shell_exec": "shell",
    "restart_backend": "restart",
    "restart_agent": "restart",
    "vault_list_keys": "vault_keys",
    "vault_add_key": "vault_add",
    "remote_host_list": "remote_hosts",
    "remote_task_submit": "remote_task",
    "remote_task_status": "remote_status",
    # NOTE: Reverse mappings are auto-generated below as TOOL_ALIASES_REVERSE.
    # Do NOT add reversed entries here — they shadow the canonical names
    # (e.g. "remote_status": "remote_task_status" makes resolve_alias("remote_status")
    # point to a non-existent handler). Bug fixed 2026-04-27.
    "evolve_analyze": "evolve",
    "bootstrap_agents": "bootstrap",
    "gemini_code_exec": "gemini_exec",
    "mesh_get_status": "mesh_status",
    "mesh_submit_task": "mesh_task",
    "mesh_list_agents": "mesh_agents",
}


def resolve_alias(tool_name: str) -> str:
    """Resolve old tool name to new name, or return as-is."""
    return TOOL_ALIASES.get(tool_name, tool_name)


logger.info(f"MCP Tool Registry v4.0 loaded: {get_tool_count()} tools (optimized from 134)")
# Reverse alias mapping. Canonical names must never be rewritten when a
# canonical handler is registered. Several legacy names can map to the same
# canonical tool (notably restart_backend/restart_agent -> restart), so a
# simple dict inversion would silently select the last legacy alias.
TOOL_ALIASES_REVERSE = {v: k for k, v in TOOL_ALIASES.items()}

def resolve_alias_reverse(tool_name):
    if tool_name in _TOOL_HANDLERS:
        return tool_name
    matches = [old for old, new in TOOL_ALIASES.items() if new == tool_name]
    return matches[0] if len(matches) == 1 else tool_name


# =============================================================================
# OPNSENSE - Proxied Tools from OPNsense Jail (Private Category)
# =============================================================================

OPNSENSE_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "opnsense_status",
        "description": "Get OPNsense firewall status and system info",
        "inputSchema": {"type": "object", "properties": {}},
        "private": True,
    },
    {
        "name": "opnsense_interfaces",
        "description": "List network interfaces with status and IPs",
        "inputSchema": {"type": "object", "properties": {}},
        "private": True,
    },
    {
        "name": "opnsense_firewall_rules",
        "description": "List active firewall rules",
        "inputSchema": {
            "type": "object",
            "properties": {
                "interface": {"type": "string", "description": "Filter by interface (optional)"},
            },
        },
        "private": True,
    },
    {
        "name": "opnsense_logs",
        "description": "Get firewall/system logs",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["firewall", "system", "dhcp"], "description": "Log type"},
                "limit": {"type": "integer", "description": "Max entries (default: 50)"},
            },
        },
        "private": True,
    },
    {
        "name": "opnsense_services",
        "description": "List and manage OPNsense services",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "start", "stop", "restart"], "description": "Action"},
                "service": {"type": "string", "description": "Service name (for start/stop/restart)"},
            },
        },
        "private": True,
    },
    {
        "name": "opnsense_vpn_status",
        "description": "Get VPN tunnel status (WireGuard, OpenVPN)",
        "inputSchema": {"type": "object", "properties": {}},
        "private": True,
    },
    {
        "name": "opnsense_exec",
        "description": "Execute command in OPNsense jail (dangerous)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to execute"},
            },
            "required": ["command"],
        },
        "private": True,
    },
]


# =============================================================================
# VISIBILITY FILTER - Load blacklist and filter tools
# =============================================================================

import json
from pathlib import Path

_VISIBILITY_CONFIG: Optional[Dict] = None

def _load_visibility_config() -> Dict:
    """Load tool visibility configuration."""
    global _VISIBILITY_CONFIG
    if _VISIBILITY_CONFIG is not None:
        return _VISIBILITY_CONFIG
    
    from app.paths import CONFIG_DIR
    config_path = CONFIG_DIR / "mcp" / "tool_visibility.json"
    if config_path.exists():
        try:
            _VISIBILITY_CONFIG = json.loads(config_path.read_text())
        except Exception as e:
            logger.warning(f"Failed to load visibility config: {e}")
            _VISIBILITY_CONFIG = {"private_tools": [], "private_categories": []}
    else:
        _VISIBILITY_CONFIG = {"private_tools": [], "private_categories": []}
    
    return _VISIBILITY_CONFIG


def get_public_tools() -> List[Dict[str, Any]]:
    """Returns only PUBLIC tools (excludes private tools for external clients)."""
    config = _load_visibility_config()
    private_tools = set(config.get("private_tools", []))
    private_categories = set(config.get("private_categories", []))
    
    public_tools = []
    
    # Add tools from non-private categories
    categories = get_categories()
    for category, tool_names in categories.items():
        if category in private_categories:
            continue
        for tool in get_all_tools():
            if tool["name"] in tool_names and tool["name"] not in private_tools:
                # Also check tool-level private flag
                if not tool.get("private", False):
                    public_tools.append(tool)
    
    return public_tools


def is_tool_private(tool_name: str) -> bool:
    """Check if a tool is private (not for external clients)."""
    config = _load_visibility_config()
    if tool_name in config.get("private_tools", []):
        return True
    
    tool = get_tool_by_name(tool_name)
    if tool and tool.get("private", False):
        return True
    
    return False


# Update get_categories to include opnsense
def get_categories_v2() -> Dict[str, List[str]]:
    """Returns categories with their tool names (including opnsense)."""
    cats = get_categories()
    cats["opnsense"] = [t["name"] for t in OPNSENSE_TOOLS]
    return cats
