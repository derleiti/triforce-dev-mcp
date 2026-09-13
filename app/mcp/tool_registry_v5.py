"""
MCP Tool Registry v5.0 - Unified, AI-Optimized
================================================

Von 321 Tools auf 62 konsolidiert:
- 156 redundante/interne Tools entfernt
- 59 Duplikate in bestehende Tools gemergt
- 7 neue Dev-Tools hinzugefügt (dev_analyze, dev_lint, dev_debug,
  dev_summarize, dev_links, dev_refactor, git)

Kategorien:
  SYSTEM (8):     shell, status, health, logs, logs_errors, logs_stats, restart, hot_reload
  CONFIG (2):     config, config_set
  MEMORY (3):     memory_store, memory_search, memory_clear
  CODE (5):       code_read, code_tree, code_search, code_edit, code_patch
  DEV-TOOLS (6):  dev_analyze, dev_lint, dev_debug, dev_summarize, dev_links, dev_refactor
  GIT (1):        git
  AI-AGENTS (5):  chat, models, specialist, agent_call, agent_broadcast
  AGENTS (3):     agents, agent_start, agent_stop
  SEARCH (4):     search, crawl, image_search, current_time
  OLLAMA (5):     ollama_run, ollama_list, ollama_status, ollama_pull, ollama_delete
  INFRA (4):      mesh_status, mesh_task, remote_task, remote_hosts
  VAULT (3):      vault_status, vault_keys, vault_add
  PROMPTS (3):    init, prompts, prompt_set
  EVOLVE (2):     evolve, debug

Alle alten Tool-Namen funktionieren weiter via ALIASES.
"""

from typing import Dict, Any, List

# =============================================================================
# V5 TOOL DEFINITIONS — AI-Optimiert, ChatGPT/Claude/Codex kompatibel
# Jedes Tool: vollständige inputSchema, alle properties typed
# =============================================================================

V5_TOOLS: List[Dict[str, Any]] = [

    # =========================================================================
    # SYSTEM & ADMIN
    # =========================================================================
    {
        "name": "shell",
        "description": (
            "Execute any Linux shell command as root. Full terminal access: "
            "systemctl, docker, apt, pip, git, file I/O, network, processes. "
            "Use sudo=true for root operations. For long operations set timeout."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "sudo": {"type": "boolean", "description": "Run as root (default: false)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30, max: 300)"},
                "cwd": {"type": "string", "description": "Working directory (optional)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "status",
        "description": "Full system status: services (backend/ollama/redis/docker), directories, memory entries, agent states, federation nodes.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "health",
        "description": "Quick health check of all services with response times. Returns healthy/degraded/down per service.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "logs",
        "description": "Retrieve system logs. Filter by category (api/llm/mcp/error/agent) and level (debug/info/warning/error).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["api", "llm", "mcp", "error", "agent", "all"],
                    "description": "Log category (default: all)",
                },
                "level": {
                    "type": "string",
                    "enum": ["debug", "info", "warning", "error"],
                    "description": "Minimum log level (default: info)",
                },
                "limit": {"type": "integer", "description": "Max entries (default: 50, max: 500)"},
            },
        },
    },
    {
        "name": "logs_errors",
        "description": "Get recent error logs only. Faster than logs with level=error.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max entries (default: 50)"},
            },
        },
    },
    {
        "name": "logs_stats",
        "description": "Logging statistics: total entries, rates per category, buffer size, uptime.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "restart",
        "description": "Restart backend service or a specific CLI agent (claude-mcp, gemini-mcp, codex-mcp, opencode-mcp).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["backend", "claude-mcp", "gemini-mcp", "codex-mcp", "opencode-mcp"],
                    "description": "What to restart (default: backend)",
                },
            },
        },
    },
    {
        "name": "hot_reload",
        "description": "Hot-reload Python modules without restarting the backend. Scope: all/services/routes/single module.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["all", "services", "routes", "module"],
                    "description": "Reload scope (default: all)",
                },
                "module": {"type": "string", "description": "Module path when scope=module (e.g. app.routes.mcp)"},
            },
        },
    },

    # =========================================================================
    # CONFIG
    # =========================================================================
    {
        "name": "config",
        "description": "Get all current configuration settings as key-value pairs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "config_set",
        "description": "Set a configuration value by key. Changes persist to config store.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Canonical environment key (e.g. 'OLLAMA_TIMEOUT_MS')"},
                "value": {"type": "string", "description": "Setting value (serialized as string)"},
            },
            "required": ["key", "value"],
        },
    },

    # =========================================================================
    # MEMORY
    # =========================================================================
    {
        "name": "memory_store",
        "description": "Persist knowledge to long-term memory. Use for: decisions, discovered facts, code patterns, todos, summaries. Stored to disk, survives restarts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The knowledge to store"},
                "type": {
                    "type": "string",
                    "enum": ["fact", "decision", "code", "summary", "todo"],
                    "description": "Memory type (default: fact)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for later filtering (e.g. ['python', 'bug', 'triforce'])",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence 0.0-1.0 (default: 1.0)",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "memory_search",
        "description": "Search persistent memory by text query and/or tags. Returns matching entries with timestamps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search in memory content"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags (OR logic)",
                },
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
            },
        },
    },
    {
        "name": "memory_clear",
        "description": "Remove memory entries by tags or age. Without filters: clears all memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Delete entries with these tags",
                },
                "older_than_days": {
                    "type": "integer",
                    "description": "Delete entries older than N days",
                },
            },
        },
    },

    # =========================================================================
    # HIVEMIND — Semantische Textkomprimierung
    # =========================================================================
    {
        "name": "hive_compress",
        "description": "Komprimiert langen Text via Ollama auf ~25% der Originallänge. "
                       "Behält alle Fakten (Dateinamen, IPs, Hashes, Fehlermeldungen). "
                       "Original wird 7 Tage in Redis gespeichert und via hive_recall abrufbar. "
                       "Ideal für Agent-Outputs, Logs, lange Analysen.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Zu komprimierender Text (mind. 500 Zeichen)",
                },
                "context_key": {
                    "type": "string",
                    "description": "Optionaler Redis-Key (default: SHA256-Hash des Texts)",
                },
                "model": {
                    "type": "string",
                    "description": "Ollama-Modell (default: qwen3.5:cloud)",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "hive_recall",
        "description": "Holt den Original-Text zu einem via hive_compress komprimierten Kontext aus Redis.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "context_key": {
                    "type": "string",
                    "description": "Redis-Key aus hive_compress Ergebnis",
                },
            },
            "required": ["context_key"],
        },
    },
    {
        "name": "hive_stats",
        "description": "Zeigt Statistiken über gespeicherte HiveMind-Originals in Redis (Anzahl, Größe, Modell).",
        "inputSchema": {"type": "object", "properties": {}},
    },

    # =========================================================================
    # CODE ACCESS
    # =========================================================================
    {
        "name": "code_read",
        "description": "Read any file from the codebase. Returns content with line numbers. Supports text/source/config files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (absolute or relative to project root)"},
                "root": {"type": "string", "description": "Project root directory (default: /home/zombie/triforce). Set to work on other projects e.g. /home/zombie/ai-coder"},
                "start_line": {"type": "integer", "description": "First line to read (optional)"},
                "end_line": {"type": "integer", "description": "Last line to read (optional)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "code_tree",
        "description": "Get directory structure as tree. Shows files, types, sizes. Essential for project orientation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Root path to scan (default: project root)"},
                "root": {"type": "string", "description": "Project root directory (default: /home/zombie/triforce). Set to work on other projects e.g. /home/zombie/ai-coder"},
                "depth": {"type": "integer", "description": "Max depth (default: 3)"},
                "ignore": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Patterns to ignore (e.g. ['__pycache__', '*.pyc', 'node_modules'])",
                },
            },
        },
    },
    {
        "name": "code_search",
        "description": "Search text/regex in codebase files. Returns matching lines with file paths and line numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text or regex pattern"},
                "path": {"type": "string", "description": "Search scope path (default: project root)"},
                "root": {"type": "string", "description": "Project root directory (default: /home/zombie/triforce). Set to work on other projects e.g. /home/zombie/ai-coder"},
                "file_pattern": {"type": "string", "description": "File filter (e.g. '*.py', '*.ts')"},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive search (default: false)"},
                "max_results": {"type": "integer", "description": "Max results (default: 50)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "code_edit",
        "description": (
            "Edit a source file: replace text, insert at line, append to file, or delete lines. "
            "Auto-creates backup. Use dry_run=true to preview. Always reads file first with code_read."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "mode": {
                    "type": "string",
                    "enum": ["replace", "insert", "append", "delete"],
                    "description": "Edit mode",
                },
                "old_text": {"type": "string", "description": "Text to find and replace (mode=replace)"},
                "new_text": {"type": "string", "description": "Replacement text (mode=replace/insert/append)"},
                "line": {"type": "integer", "description": "Line number (mode=insert/delete)"},
                "dry_run": {"type": "boolean", "description": "Preview without saving (default: false)"},
            },
            "required": ["path", "mode"],
        },
    },
    {
        "name": "code_patch",
        "description": "Apply a unified diff patch (--- a/file +++ b/file format) to the codebase. Supports multi-file patches.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "patch": {"type": "string", "description": "Unified diff patch content"},
                "dry_run": {"type": "boolean", "description": "Preview without applying (default: false)"},
            },
            "required": ["patch"],
        },
    },

    # =========================================================================
    # DEV-TOOLS — Neue KI-optimierte Entwickler-Tools
    # =========================================================================
    {
        "name": "dev_analyze",
        "description": (
            "AI-powered code analysis. Detects: bugs, typos in variable/function names, "
            "dead code, unused imports, security issues, hardcoded secrets, complexity hotspots. "
            "Supports all languages: Python, JS/TS, Bash, Go, Rust, C/C++, Java, PHP, Ruby. "
            "Returns structured issue list with severity and line numbers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory to analyze"},
                "checks": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["bugs", "typos", "security", "dead_code", "complexity", "imports", "all"],
                    },
                    "description": "What to check (default: ['all'])",
                },
                "severity": {
                    "type": "string",
                    "enum": ["info", "warning", "error", "critical"],
                    "description": "Minimum severity to report (default: warning)",
                },
                "language": {"type": "string", "description": "Force language detection (optional)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "dev_lint",
        "description": (
            "Syntax and style check for any programming language. "
            "Python: ruff/pylint/mypy. JS/TS: eslint. Bash: shellcheck. "
            "Go: golint. Rust: clippy. Returns errors with file, line, column, message."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory to lint"},
                "language": {
                    "type": "string",
                    "enum": ["python", "javascript", "typescript", "bash", "go", "rust", "php", "ruby", "auto"],
                    "description": "Language (default: auto-detect)",
                },
                "fix": {"type": "boolean", "description": "Auto-fix fixable issues (default: false)"},
                "strict": {"type": "boolean", "description": "Strict mode: fail on warnings too (default: false)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "dev_debug",
        "description": (
            "Automatic debugger: paste an error traceback or describe a bug, get root cause analysis "
            "and concrete fix suggestions. Optionally provide the relevant source file for context. "
            "Works for any language and framework."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "error": {"type": "string", "description": "Error message, traceback, or bug description"},
                "file": {"type": "string", "description": "Source file path for additional context (optional)"},
                "context": {"type": "string", "description": "Additional context (what you were trying to do)"},
                "language": {"type": "string", "description": "Programming language (optional, auto-detected)"},
            },
            "required": ["error"],
        },
    },
    {
        "name": "dev_summarize",
        "description": (
            "Summarize a project, module, or file for AI context. "
            "Extracts: purpose, public API, dependencies, key patterns, entry points. "
            "Token-efficient output optimized for including in AI prompts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory to summarize"},
                "depth": {"type": "string", "enum": ["brief", "normal", "detailed"], "description": "Summary depth (default: normal)"},
                "focus": {
                    "type": "string",
                    "enum": ["api", "structure", "dependencies", "flow", "all"],
                    "description": "What to focus on (default: all)",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "dev_links",
        "description": (
            "Find and validate all internal code references: imports, requires, include paths, "
            "function calls, class references, config keys, file paths in strings. "
            "Reports broken/missing links, circular imports, and unreachable references."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory to scan"},
                "check_external": {"type": "boolean", "description": "Also validate external URLs in code (default: false)"},
                "language": {"type": "string", "description": "Language (optional, auto-detect)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "dev_refactor",
        "description": (
            "AI-powered refactoring suggestions. Analyzes code and suggests: "
            "function extraction, naming improvements, design patterns, "
            "performance optimizations, DRY violations, SOLID principle adherence. "
            "Returns diff-style suggestions ready to apply with code_patch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to refactor"},
                "focus": {
                    "type": "string",
                    "enum": ["naming", "structure", "performance", "patterns", "all"],
                    "description": "Refactoring focus (default: all)",
                },
                "apply": {"type": "boolean", "description": "Auto-apply suggestions (default: false, returns diff)"},
            },
            "required": ["path"],
        },
    },

    # =========================================================================
    # GIT — Unified
    # =========================================================================
    {
        "name": "git",
        "description": (
            "Unified git operations. mode: status, diff, commit, branch, log, push, pull, stash. "
            "Use shell for complex git workflows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["status", "diff", "commit", "branch", "log", "push", "pull", "stash", "add"],
                    "description": "Git operation",
                },
                "message": {"type": "string", "description": "Commit message (mode=commit)"},
                "branch": {"type": "string", "description": "Branch name (mode=branch)"},
                "path": {"type": "string", "description": "Repo path (default: project root)"},
                "args": {"type": "string", "description": "Additional git arguments (optional)"},
            },
            "required": ["mode"],
        },
    },

    # =========================================================================
    # AI & CHAT
    # =========================================================================
    {
        "name": "chat",
        "description": (
            "Send a message to any AI model. Supports all providers: "
            "Ollama (local), Gemini, Claude, GPT-4, Mistral, DeepSeek, Qwen, Groq. "
            "Use model='provider/model-id' format."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "User message"},
                "model": {"type": "string", "description": "Model ID (e.g. 'anthropic/claude-sonnet-4-6', 'ollama/qwen2.5:14b')"},
                "system_prompt": {"type": "string", "description": "System prompt (optional)"},
                "temperature": {"type": "number", "description": "Sampling temperature 0.0-2.0 (default: 0.7)"},
                "max_tokens": {"type": "integer", "description": "Max output tokens (default: 2048)"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "models",
        "description": "List all available AI models with provider, capabilities (chat/code/vision/embed), and status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "Filter by provider (e.g. 'ollama', 'anthropic')"},
                "capability": {
                    "type": "string",
                    "enum": ["chat", "code", "vision", "embed", "all"],
                    "description": "Filter by capability (default: all)",
                },
            },
        },
    },
    {
        "name": "specialist",
        "description": "Route task to the best specialist AI model. Task types: code, math, creative, analysis, research, vision.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Task description and content"},
                "task": {
                    "type": "string",
                    "enum": ["code", "math", "creative", "analysis", "research", "vision", "debug"],
                    "description": "Task type for model routing",
                },
                "context": {"type": "string", "description": "Additional context (optional)"},
            },
            "required": ["message", "task"],
        },
    },

    # =========================================================================
    # CLI AGENTS
    # =========================================================================
    {
        "name": "agents",
        "description": "List all standard agents (claude-mcp, codex-mcp, gemini-mcp, mistral-mcp, opencode-mcp) with status, last activity, and stats.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "agent_call",
        "description": "Send a message/task to a configured agent profile and get its response. Legacy CLI agents may need a running process; AICoder profiles are launched per call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9_.-]+$",
                    "description": "Configured agent/profile ID, for example claude-mcp or aicoder-review",
                },
                "message": {"type": "string", "description": "Message/task to send"},
                "timeout": {"type": "integer", "description": "Response timeout in seconds (default: 60)"},
                "execution_mode": {
                    "type": "string",
                    "enum": ["auto", "direct", "aicoder", "team"],
                    "description": "Execution routing. auto is direct-first and escalates only for complex tasks.",
                    "default": "auto",
                },
            },
            "required": ["agent_id", "message"],
        },
    },
    {
        "name": "agent_broadcast",
        "description": "Send a message to all running CLI agents simultaneously for parallel processing or consensus.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to broadcast to all agents"},
                "wait_all": {"type": "boolean", "description": "Wait for all responses (default: false)"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "agent_start",
        "description": "Start or restart a CLI agent. Initializes the agent process and loads its system prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "enum": ["claude-mcp", "codex-mcp", "gemini-mcp", "mistral-mcp", "opencode-mcp"],
                    "description": "Agent to start",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "agent_stop",
        "description": "Stop a running CLI agent gracefully.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "enum": ["claude-mcp", "codex-mcp", "gemini-mcp", "mistral-mcp", "opencode-mcp"],
                    "description": "Agent to stop",
                },
            },
            "required": ["agent_id"],
        },
    },

    # =========================================================================
    # SEARCH & WEB
    # =========================================================================
    {
        "name": "search",
        "description": (
            "Unified intent-aware search for websites, images, videos, files, downloads, "
            "documentation, code, news and science. mode=auto detects the intent and ranks "
            "official/original sources above SEO pages. Legacy fast/smart/deep values remain compatible."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results (default: 10)"},
                "mode": {
                    "type": "string",
                    "enum": ["auto", "web", "images", "videos", "files", "downloads", "docs", "code", "news", "science", "fast", "smart", "deep"],
                    "description": "Search intent (default: auto). fast/smart/deep are legacy aliases for auto.",
                },
                "lang": {"type": "string", "description": "Language code (default: de)"},
                "synthesize": {"type": "boolean", "description": "Generate a source-grounded answer (default: false)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "crawl",
        "description": "Crawl a URL and extract text content. Returns clean markdown. Respects robots.txt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to crawl"},
                "extract": {
                    "type": "string",
                    "enum": ["text", "links", "both"],
                    "description": "What to extract (default: text)",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "image_search",
        "description": "Search for images on the web. Returns image URLs with metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Image search query"},
                "max_results": {"type": "integer", "description": "Max results (default: 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "current_time",
        "description": "Get current date and time in any timezone. Also returns system uptime.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "Timezone (e.g. 'Europe/Berlin', default: system)"},
            },
        },
    },

    # =========================================================================
    # OLLAMA
    # =========================================================================
    {
        "name": "ollama_run",
        "description": "Run inference on a local Ollama model. Returns completion text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name (e.g. 'qwen2.5:14b', 'llama3.1:8b')"},
                "prompt": {"type": "string", "description": "Input prompt"},
                "system": {"type": "string", "description": "System prompt (optional)"},
                "temperature": {"type": "number", "description": "Temperature 0.0-2.0 (default: 0.7)"},
            },
            "required": ["model", "prompt"],
        },
    },
    {
        "name": "ollama_list",
        "description": "List all locally available Ollama models with size, quantization, and last-used info.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ollama_status",
        "description": "Ollama server health check. Shows running models, GPU usage, memory.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ollama_pull",
        "description": "Download a model from Ollama registry (ollama.com/library).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model to download (e.g. 'llama3.2:3b', 'phi4:latest')"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "ollama_delete",
        "description": "Delete a local Ollama model to free disk space.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name to delete"},
            },
            "required": ["model"],
        },
    },

    # =========================================================================
    # INFRASTRUCTURE
    # =========================================================================
    {
        "name": "mesh_status",
        "description": "Get mesh AI system status: active nodes, task queue, worker health.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mesh_task",
        "description": "Submit a task to the mesh AI system for distributed processing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task description"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "critical"],
                    "description": "Task priority (default: normal)",
                },
                "agents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific agents to use (optional, default: auto-select)",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "remote_hosts",
        "description": "List registered remote federation hosts with connection status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "remote_task",
        "description": "Execute a shell command on a remote federation host via SSH.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Remote host ID or IP"},
                "command": {"type": "string", "description": "Command to execute remotely"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
            },
            "required": ["host", "command"],
        },
    },

    # =========================================================================
    # VAULT
    # =========================================================================
    {
        "name": "vault_status",
        "description": "Check API key vault status: locked/unlocked, number of stored keys.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "vault_keys",
        "description": "List stored API key names (names only, values never exposed).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "vault_add",
        "description": "Add or update an API key in the vault.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Key name (e.g. 'OPENAI_API_KEY')"},
                "value": {"type": "string", "description": "API key value"},
            },
            "required": ["name", "value"],
        },
    },

    # =========================================================================
    # PROMPTS & INIT
    # =========================================================================
    {
        "name": "init",
        "description": "Initialize agent session. Returns system capabilities, shortcode protocol, tool list, and API documentation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent ID for customized system prompt (optional)"},
                "compact": {"type": "boolean", "description": "Token-efficient mode for large context (default: false)"},
            },
        },
    },
    {
        "name": "prompts",
        "description": "List all available system prompts with names and descriptions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "prompt_set",
        "description": "Create or update a system prompt. Used to configure agent behavior.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Prompt name/ID"},
                "content": {"type": "string", "description": "Prompt content"},
                "description": {"type": "string", "description": "Description of what this prompt does"},
            },
            "required": ["name", "content"],
        },
    },

    # =========================================================================
    # EVOLVE & DEBUG
    # =========================================================================
    {
        "name": "evolve",
        "description": (
            "Auto-evolution: activates all CLI agents to analyze codebase, "
            "find improvement opportunities, and propose concrete changes. "
            "Returns structured report with priorities."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "focus": {"type": "string", "description": "Focus area (e.g. 'performance', 'security', 'tests', default: all)"},
                "apply": {"type": "boolean", "description": "Auto-apply safe improvements (default: false)"},
            },
        },
    },
    {
        "name": "debug",
        "description": "Trace and debug an MCP request. Shows full request/response chain, timing, and handler resolution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "MCP tool name to trace"},
                "params": {"type": "object", "description": "Tool parameters to test"},
            },
            "required": ["tool_name"],
        },
    },
    # =========================================================================
    # MAIL — Nova IMAP/SMTP (nova@ailinux.me) — Added 2026-03-11
    # =========================================================================
    {
        "name": "mail_inbox",
        "description": "List recent emails from Nova inbox (nova@ailinux.me). Returns subject, from, date, seen-flag per message.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max messages to return (default: 20)"},
                "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
            },
        },
    },
    {
        "name": "mail_read",
        "description": "Read full email body by UID from Nova inbox. Returns headers + plain-text body (max 4000 chars).",
        "inputSchema": {
            "type": "object",
            "required": ["uid"],
            "properties": {
                "uid": {"type": "string", "description": "IMAP message UID from mail_inbox"},
                "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
            },
        },
    },
    {
        "name": "mail_send",
        "description": "Send email from nova@ailinux.me via SMTP. Requires to, subject, body.",
        "inputSchema": {
            "type": "object",
            "required": ["to", "subject", "body"],
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Plain text email body"},
                "cc": {"type": "string", "description": "CC recipient (optional)"},
                "reply_to": {"type": "string", "description": "Reply-To address (optional)"},
                "in_reply_to": {"type": "string", "description": "Original Message-ID for RFC reply threading (optional)"},
                "references": {"type": "string", "description": "References header for RFC reply threading (optional)"},
            },
        },
    },
    {
        "name": "mail_mark_seen",
        "description": "Mark an email as read/seen by UID in Nova inbox.",
        "inputSchema": {
            "type": "object",
            "required": ["uid"],
            "properties": {
                "uid": {"type": "string", "description": "IMAP message UID"},
                "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
            },
        },
    },

    # =========================================================================
    # WORDPRESS — Nova Admin via Application Password — Added 2026-03-11
    # =========================================================================
    {
        "name": "wp_list_drafts",
        "description": "List WordPress draft posts on ailinux.me. Returns post ID, title, date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "description": "Max posts to return (default: 20)"},
            },
        },
    },
    {
        "name": "wp_create_draft",
        "description": "Create a new WordPress draft post on ailinux.me. Returns post ID and link.",
        "inputSchema": {
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string", "description": "Post title"},
                "content": {"type": "string", "description": "Post body (HTML or plain text)"},
                "categories": {"type": "array", "items": {"type": "integer"}, "description": "Category IDs (optional)"},
            },
        },
    },
    {
        "name": "wp_update_post",
        "description": "Update an existing WordPress post on ailinux.me. Can update title, content, status.",
        "inputSchema": {
            "type": "object",
            "required": ["post_id"],
            "properties": {
                "post_id": {"type": "integer", "description": "WordPress post ID"},
                "title": {"type": "string", "description": "New title (optional)"},
                "content": {"type": "string", "description": "New content (optional)"},
                "status": {"type": "string", "enum": ["draft", "publish", "private"], "description": "Post status (optional)"},
            },
        },
    },

    # =========================================================================
    # FLARUM — Nova Forum Interface — Added 2026-03-11
    # =========================================================================
    {
        "name": "flarum_discussions",
        "description": "List or search discussions on the AILinux Community Forum. Supports search, tag filter, sorting and pagination.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q":     {"type": "string",  "description": "Search query (optional)"},
                "sort":  {"type": "string",  "description": "Sort: -lastPostedAt (default), -createdAt, top"},
                "tag":   {"type": "string",  "description": "Filter by tag slug (optional)"},
                "limit": {"type": "integer", "description": "Max results (default 20, max 50)"},
                "page":  {"type": "integer", "description": "Page number (default 0)"},
            },
        },
    },
    {
        "name": "flarum_discussion_get",
        "description": "Read a forum discussion including all posts. Returns title, metadata and post content.",
        "inputSchema": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id":            {"type": "string",  "description": "Discussion ID"},
                "include_posts": {"type": "boolean", "description": "Include posts (default true)"},
            },
        },
    },
    {
        "name": "flarum_post_get",
        "description": "Read a single forum post by ID.",
        "inputSchema": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "Post ID"},
            },
        },
    },
    {
        "name": "flarum_post_create",
        "description": "Post a reply in a forum discussion as Nova (ailinux-nova-ai).",
        "inputSchema": {
            "type": "object",
            "required": ["discussion_id", "content"],
            "properties": {
                "discussion_id": {"type": "string", "description": "Discussion ID to reply to"},
                "content":       {"type": "string", "description": "Post content (plain text or Markdown)"},
            },
        },
    },
    {
        "name": "flarum_post_edit",
        "description": "Edit an existing post by Nova. Only works for posts authored by ailinux-nova-ai.",
        "inputSchema": {
            "type": "object",
            "required": ["post_id", "content"],
            "properties": {
                "post_id": {"type": "string", "description": "Post ID to edit"},
                "content": {"type": "string", "description": "New post content"},
            },
        },
    },
    {
        "name": "flarum_discussion_create",
        "description": "Create a new forum discussion as Nova. Optionally assign tags.",
        "inputSchema": {
            "type": "object",
            "required": ["title", "content"],
            "properties": {
                "title":   {"type": "string", "description": "Discussion title"},
                "content": {"type": "string", "description": "Opening post content"},
                "tags":    {"type": "array", "items": {"type": "integer"}, "description": "Tag IDs (get via flarum_tags)"},
            },
        },
    },
    {
        "name": "flarum_tags",
        "description": "List all available tags in the AILinux Community Forum.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "flarum_users",
        "description": "List or search forum users.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q":     {"type": "string",  "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
        },
    },

    # =========================================================================
    # DOC-BROWSER — Rekursiver Dokumentations-Browser — Added 2026-03-11
    # =========================================================================
    {
        "name": "doc_scan",
        "description": (
            "Rekursiv alle Dokumentationsdateien im TriForce-Projekt finden. "
            "Unterstützt *.md, *.txt, *.sh, *.json, *.yml, *.toml u.v.m. "
            "Gibt Dateiliste mit Metadaten (Pfad, Größe, Datum, Zeilenzahl, Typ) zurück. "
            "category-Shortcuts: docs | scripts | config | all"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path":       {"type": "string",  "description": "Startpfad (default: project root)"},
                "extensions": {"type": "array",   "items": {"type": "string"}, "description": "Dateierweiterungen z.B. [.md, .sh]"},
                "category":   {"type": "string",  "enum": ["docs", "scripts", "config", "all"], "description": "Kategorie-Shortcut"},
                "max_depth":  {"type": "integer", "description": "Max. Verzeichnistiefe (default: unbegrenzt)"},
                "sort_by":    {"type": "string",  "enum": ["rel_path", "modified", "size", "kind", "name"], "description": "Sortierung"},
            },
        },
    },
    {
        "name": "doc_read",
        "description": (
            "Dokumentationsdatei lesen mit vollständigen Metadaten (Pfad, Größe, Zeilen, Datum, Typ). "
            "Unterstützt Zeilenfenster (start_line/end_line) für große Dateien."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path":       {"type": "string",  "description": "Dateipfad (absolut oder relativ zu project root)"},
                "start_line": {"type": "integer", "description": "Erste Zeile (default: 1)"},
                "end_line":   {"type": "integer", "description": "Letzte Zeile (default: EOF)"},
                "show_meta":  {"type": "boolean", "description": "Metadaten mitgeben (default: true)"},
                "max_chars":  {"type": "integer", "description": "Max. Zeichen im Output (default: 50000)"},
            },
        },
    },
    {
        "name": "doc_search",
        "description": (
            "Volltext-Suche über alle Dokumentationsdateien. Grep-style mit Kontext-Zeilen. "
            "Unterstützt Regex, case-sensitive, Dateifilter."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query":          {"type": "string",  "description": "Suchbegriff oder Regex"},
                "path":           {"type": "string",  "description": "Startpfad (default: project root)"},
                "extensions":     {"type": "array",   "items": {"type": "string"}, "description": "Nur diese Erweiterungen"},
                "max_results":    {"type": "integer", "description": "Max. Treffer-Dateien (default: 50)"},
                "context_lines":  {"type": "integer", "description": "Kontext-Zeilen um Treffer (default: 2)"},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive (default: false)"},
                "regex":          {"type": "boolean", "description": "Query als Regex (default: false)"},
            },
        },
    },
    {
        "name": "doc_tree",
        "description": (
            "Verzeichnis-Tree nur für Dokumentationsdateien mit Icons, Größe und Datum."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path":       {"type": "string",  "description": "Startpfad (default: project root)"},
                "extensions": {"type": "array",   "items": {"type": "string"}, "description": "Zu zeigende Erweiterungen"},
                "max_depth":  {"type": "integer", "description": "Max. Tiefe (default: 4)"},
            },
        },
    },
    {
        "name": "doc_stats",
        "description": (
            "Statistik über die gesamte Dokumentationsbasis: Dateizahl, Gesamtgröße, Zeilenzahl, "
            "Verteilung nach Typ, neueste/älteste Datei, Top-10 größte Dateien."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Startpfad (default: project root)"},
            },
        },
    },

]

# =============================================================================
# ALIAS TABLE — Alle alten Namen → neue v5 Namen
# Ermöglicht 100% Backward-Compatibility
# =============================================================================
V5_ALIASES: Dict[str, str] = {
    # shell variants
    "tristar_shell_exec": "shell",
    "execute_mcp_tool": "shell",
    # hot_reload variants
    "hot_reload_all": "hot_reload",
    "hot_reload_module": "hot_reload",
    "hot_reload_services": "hot_reload",
    "reinit_service": "hot_reload",
    "list_reloadable_modules": "hot_reload",
    "reload_history": "hot_reload",
    # memory variants
    "tristar_memory_store": "memory_store",
    "tristar_memory_search": "memory_search",
    "tristar.memory.store": "memory_store",
    "tristar.memory.search": "memory_search",
    "memory_index_add": "memory_store",
    "memory_index_search": "memory_search",
    "memory_index_get": "memory_search",
    "memory_index_compact": "memory_search",
    "memory_index_stats": "memory_search",
    "memory_recall": "memory_search",
    "memory_update": "memory_store",
    # code variants
    "codebase_file": "code_read",
    "codebase.file": "code_read",
    "file_read": "code_read",
    "triforce_read": "code_read",
    "code_probe": "code_read",
    "code_probe_v4": "code_read",
    "codebase_structure": "code_tree",
    "codebase.structure": "code_tree",
    "code_scout": "code_tree",
    "code_scout_v4": "code_tree",
    "codebase_search": "code_search",
    "codebase.search": "code_search",
    "ram_search": "code_search",
    "ram_search_v4": "code_search",
    "codebase_edit": "code_edit",
    "codebase.edit": "code_edit",
    "file_write": "code_edit",
    "triforce_write": "code_edit",
    # delta_sync_v4 hat eigenen Handler — kein Alias auf code_edit (Bug-Fix 2026-03-16)
    "ram_patch_apply": "code_patch",
    "codebase_create": "code_edit",
    "codebase.create": "code_edit",
    # git variants
    "git_status": "git",
    "git_diff": "git",
    "git_commit": "git",
    "git_branch": "git",
    # search variants
    "web_search": "search",
    "multi_search": "search",
    "smart_search": "search",
    "quick_smart_search": "search",
    "google_deep_search": "search",
    "ailinux_search": "search",
    "grokipedia_search": "search",
    "search_health": "health",
    # crawl variants
    "crawl_url": "crawl",
    # chat variants
    "ask_specialist": "specialist",
    "chat_smart": "chat",
    # agent variants (dot-notation)
    "cli-agents.list": "agents",
    "cli-agents.call": "agent_call",
    "cli-agents.broadcast": "agent_broadcast",
    "cli-agents.start": "agent_start",
    "cli-agents.stop": "agent_stop",
    "cli-agents.get": "agents",
    "cli-agents.restart": "agent_start",
    "cli-agents.output": "agent_call",
    "cli-agents.stats": "agents",
    "cli-agents_list": "agents",
    "cli-agents_call": "agent_call",
    "cli-agents_broadcast": "agent_broadcast",
    "cli-agents_start": "agent_start",
    "cli-agents_stop": "agent_stop",
    "cli-agents_get": "agents",
    "cli-agents_restart": "agent_start",
    "cli-agents_output": "agent_call",
    "cli-agents_stats": "agents",
    # models
    "list_models": "models",
    "chat_list_models": "models",
    # restart variants
    "restart_backend": "restart",
    "restart_agent": "restart",
    # logs variants
    "triforce_logs_recent": "logs",
    "triforce_logs_errors": "logs_errors",
    "triforce_logs_api": "logs",
    "triforce_logs_trace": "logs",
    "triforce_logs_stats": "logs_stats",
    "tristar_logs": "logs",
    # mesh variants
    "mesh_submit_task": "mesh_task",
    "mesh_queue_command": "mesh_task",
    "mesh_get_status": "mesh_status",
    "mesh_list_agents": "agents",
    # vault variants
    "vault_add_key": "vault_add",
    "vault_list_keys": "vault_keys",
    # prompts variants
    "tristar_prompts_list": "prompts",
    "tristar_prompts_get": "prompts",
    "tristar_prompts_set": "prompt_set",
    # config variants
    "tristar_settings": "config",
    "tristar_settings_get": "config",
    "tristar_settings_set": "config_set",
    "admin.crawler.control": "admin_crawler_control",
    "admin.crawler.config.get": "admin_crawler_config_get",
    "admin.crawler.config.set": "admin_crawler_config_set",
    # debug/compat
    "check_compatibility": "debug",
    "debug_mcp_request": "debug",
    # evolve
    "evolve_analyze": "evolve",
    "evolve_broadcast": "evolve",
    "evolve_history": "evolve",
    # init
    "compact_init": "init",
    "acknowledge_policy": "init",
    # status
    "tristar_status": "status",
    "triforce_status": "status",
    # remote
    "remote_host_list": "remote_hosts",
    "remote_task_submit": "remote_task",
    "remote_task_status": "remote_task",
}


def get_all_tools() -> List[Dict[str, Any]]:
    """Return all v5 tool definitions."""
    return V5_TOOLS


def get_tool(name: str) -> Dict[str, Any]:
    """Get a tool definition by name (supports aliases)."""
    resolved = V5_ALIASES.get(name, name)
    for tool in V5_TOOLS:
        if tool["name"] == resolved:
            return tool
    return None


def resolve_alias(name: str) -> str:
    """Resolve old tool name to v5 canonical name."""
    return V5_ALIASES.get(name, name)


def get_tool_count() -> int:
    return len(V5_TOOLS)

# =============================================================================
# FLARUM — Community Forum Tools
# =============================================================================
V5_TOOLS += [
    {
        "name": "telegram_mcp_agent",
        "description": "Internal Telegram identity-aware AI agent. Runs Nemotron or another configured model with a fixed TriForce MCP capability profile.",
        "inputSchema": {
            "type": "object",
            "required": ["message", "telegram_user_id"],
            "properties": {
                "message": {"type": "string", "description": "Telegram user message"},
                "model": {"type": "string", "description": "Model ID"},
                "profile": {"type": "string", "enum": ["owner", "group"]},
                "telegram_user_id": {"type": "string"},
                "telegram_username": {"type": "string"},
                "display_name": {"type": "string"},
                "chat_id": {"type": "string"},
                "chat_title": {"type": "string"}
            }
        },
        "x_inventory": "agent",
    },
    {
        "name": "flarum_refresh",
        "description": "Prüft Flarum Forum-Verbindung und gibt Status zurück. Nützlich als Verbindungstest.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "flarum_admin_request",
        "description": "Privileged Flarum REST API request as the configured forum administrator. Internal use only.",
        "inputSchema": {
            "type": "object",
            "required": ["method", "path"],
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PATCH", "DELETE"]},
                "path": {"type": "string", "description": "API-relative path, e.g. /users/1"},
                "query": {"type": "object", "description": "Optional query parameters"},
                "payload": {"type": "object", "description": "Optional JSON:API payload for POST/PATCH"},
            },
        },
    },
    {
        "name": "flarum_discussions",
        "description": (
            "Listet oder durchsucht Flarum-Discussions. "
            "Unterstützt Suche, Tag-Filter und Sortierung. "
            "Gibt Titel, ID, Post-Anzahl, URL und Zeitstempel zurück."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":  {"type": "string", "description": "Suchbegriff (optional)"},
                "tag":    {"type": "string", "description": "Tag-Slug Filter (optional)"},
                "sort":   {"type": "string", "enum": ["newest", "top", "latest"], "description": "Sortierung (default: latest)"},
                "limit":  {"type": "integer", "description": "Max Einträge (default: 20, max: 50)"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "flarum_discussion",
        "description": "Liest eine einzelne Discussion mit allen Posts. Gibt vollständigen Thread-Inhalt zurück.",
        "inputSchema": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id":    {"type": "string", "description": "Discussion ID"},
                "limit": {"type": "integer", "description": "Max Posts (default: 20)"},
            },
        },
    },
    {
        "name": "flarum_posts",
        "description": "Listet neueste Posts über alle Discussions. Optional gefiltert nach Discussion oder Author.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit":         {"type": "integer", "description": "Max Einträge (default: 20)"},
                "discussion_id": {"type": "string", "description": "Nur Posts dieser Discussion"},
                "author_id":     {"type": "string", "description": "Nur Posts dieses Users"},
            },
        },
    },
    {
        "name": "flarum_post_create",
        "description": "Schreibt einen neuen Post in eine Discussion (als Nova/ailinux-nova-ai). Inhalt in Markdown.",
        "inputSchema": {
            "type": "object",
            "required": ["discussion_id", "content"],
            "properties": {
                "discussion_id": {"type": "string", "description": "Discussion ID"},
                "content":       {"type": "string", "description": "Post-Inhalt (Markdown)"},
            },
        },
    },
    {
        "name": "flarum_post_edit",
        "description": "Bearbeitet einen bestehenden eigenen Post. Nur für Posts von Nova/ailinux-nova-ai.",
        "inputSchema": {
            "type": "object",
            "required": ["post_id", "content"],
            "properties": {
                "post_id": {"type": "string", "description": "Post ID"},
                "content": {"type": "string", "description": "Neuer Inhalt (Markdown)"},
            },
        },
    },
    {
        "name": "flarum_discussion_create",
        "description": "Erstellt eine neue Discussion im Forum (als Nova). Titel, Inhalt und optionale Tags.",
        "inputSchema": {
            "type": "object",
            "required": ["title", "content"],
            "properties": {
                "title":   {"type": "string", "description": "Discussion-Titel"},
                "content": {"type": "string", "description": "Erster Post-Inhalt (Markdown)"},
                "tag_ids": {"type": "array", "items": {"type": "integer"}, "description": "Tag-IDs (optional)"},
            },
        },
    },
    {
        "name": "flarum_users",
        "description": "Listet Forum-User mit Stats (Discussions, Posts, Admin-Status).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max Einträge (default: 20)"},
                "query": {"type": "string", "description": "Username-Filter (optional)"},
            },
        },
    },
    {
        "name": "flarum_tags",
        "description": "Listet alle verfügbaren Forum-Tags mit ID, Slug, Beschreibung und Discussion-Anzahl.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ── Notification Manager ──────────────────────────────────────────────────
    {
        "name": "notify_list",
        "description": "Listet alle Notifications (System, Agent, Forum, Mail, MCP). Standardmäßig nur ungelesene.",
        "inputSchema": {"type": "object", "properties": {
            "unread_only": {"type": "boolean", "description": "Nur ungelesene (default: true)"},
            "source": {"type": "string", "description": "Filter: system|agent|forum|mail|mcp|manual"},
            "priority": {"type": "string", "description": "Filter: low|normal|high|critical"},
            "limit": {"type": "integer", "description": "Max Einträge (default: 50)"},
        }},
    },
    {
        "name": "notify_read",
        "description": "Markiert eine Notification als gelesen oder erledigt (resolve=true löscht sie aus der Liste).",
        "inputSchema": {"type": "object", "required": ["id"], "properties": {
            "id": {"type": "string", "description": "Notification-ID"},
            "resolve": {"type": "boolean", "description": "Als erledigt markieren (default: false)"},
        }},
    },
    {
        "name": "notify_clear",
        "description": "Löscht erledigte Notifications. Mit all=true werden alle gelöscht.",
        "inputSchema": {"type": "object", "properties": {
            "all": {"type": "boolean", "description": "Alle löschen inkl. ungelesene (default: false)"},
        }},
    },
    {
        "name": "notify_send",
        "description": "Erstellt eine neue Notification — z.B. von Agents, System-Events oder manuell.",
        "inputSchema": {"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string", "description": "Titel (required)"},
            "body": {"type": "string", "description": "Nachrichtentext"},
            "source": {"type": "string", "description": "system|agent|forum|mail|mcp|manual"},
            "priority": {"type": "string", "description": "low|normal|high|critical"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"},
            "action_url": {"type": "string", "description": "Link zur Quelle"},
            "auto_resolve": {"type": "boolean", "description": "Sofort als erledigt markieren"},
            "target": {"type": "string", "description": "Optional target: global Shared Notify @handle, agent:<id>, aicoder:<profile>, account:<provider>/<model>, model:<id>, api:<id>. @handles require AILinux JWT auth."},
            "kind": {"type": "string", "enum": ["human_chat", "task", "review", "coordination", "ai_optimization", "brainstorm", "handoff"], "default": "human_chat"},
            "sender_endpoint_id": {"type": "string", "description": "Optional stable Shared Notify sender endpoint. Ownership is server-verified."},
            "thread_id": {"type": "string"},
            "correlation_id": {"type": "string"},
            "hop_count": {"type": "integer", "minimum": 0, "maximum": 8, "default": 0},
            "ttl_seconds": {"type": "integer", "minimum": 30, "maximum": 604800, "default": 86400},
            "metadata": {"type": "object"},
            "dedup_key": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 10, "maximum": 300, "description": "Optional synchronous AI call timeout in seconds (default 120)"},
        }},
    },
    {
        "name": "idle_assign",
        "description": "Queue a bounded read-only idle-analysis assignment. Operator assignments take priority over self-selected catalogue work.",
        "inputSchema": {"type": "object", "required": ["assignment"], "properties": {
            "assignment": {"type": "string", "description": "Exact analysis assignment; idle workers cannot implement changes"},
            "work_type": {"type": "string", "enum": ["bug-hunt", "test-gap", "security-review", "concurrency-review", "dead-code", "docs-drift"], "default": "bug-hunt"},
            "profile_id": {"type": "string", "description": "Optional configured AICoder agent profile"}
        }},
    },
    {
        "name": "notify_status",
        "description": "Gibt Status und Statistiken des Notification Managers zurück.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# =============================================================================
# GROUP CHAT — Multi-AI Collaboration Tools (2026-03-15)
# =============================================================================
V5_TOOLS += [
    {
        "name": "group_chat_create",
        "description": "Erstelle eine neue Multi-AI Group Chat Session. Startet eine Gruppendiskussion zwischen Gemini (Lead), Claude-Web, ChatGPT-Web und Coding-Agents.",
        "inputSchema": {"type": "object", "required": ["topic"], "properties": {
            "topic": {"type": "string", "description": "Thema/Aufgabe für die Diskussion"},
            "participants": {"type": "array", "items": {"type": "string"}, "description": "Optional: Teilnehmer-IDs"},
        }},
    },
    {
        "name": "group_chat_ask",
        "description": "Stelle eine Frage an die AI-Gruppe. Gemini Lead analysiert und erstellt Sub-Tasks für Claude-Web und ChatGPT-Web.",
        "inputSchema": {"type": "object", "required": ["session_id"], "properties": {
            "session_id": {"type": "string", "description": "Group Chat Session ID"},
            "question": {"type": "string", "description": "Optional: Zusätzliche Frage"},
        }},
    },
    {
        "name": "group_chat_enqueue",
        "description": "Queue a trusted external user message for a specific participant in an existing group chat session. Internal callers only.",
        "inputSchema": {"type": "object", "required": ["session_id", "target", "content"], "properties": {
            "session_id": {"type": "string"},
            "target": {"type": "string"},
            "content": {"type": "string"},
            "source": {"type": "string"},
            "metadata": {"type": "object"},
        }},
        "x_inventory": "group_chat",
    },
    {
        "name": "group_chat_message",
        "description": "Poste eine Nachricht in den Group Chat. Wird von Claude-Web und ChatGPT-Web genutzt um auf Sub-Tasks zu antworten.",
        "inputSchema": {"type": "object", "required": ["session_id", "sender", "content"], "properties": {
            "session_id": {"type": "string", "description": "Group Chat Session ID"},
            "sender": {"type": "string", "description": "Deine ID (z.B. 'claude-web', 'chatgpt-web')"},
            "content": {"type": "string", "description": "Deine Antwort/Analyse"},
            "type": {"type": "string", "enum": ["response", "code_result", "review"], "description": "Nachrichtentyp"},
        }},
    },
    {
        "name": "group_chat_read",
        "description": "Lese Nachrichten aus dem Group Chat. Zeigt Sub-Tasks, Antworten und Status.",
        "inputSchema": {"type": "object", "required": ["session_id"], "properties": {
            "session_id": {"type": "string", "description": "Group Chat Session ID"},
            "since": {"type": "string", "description": "Optional: Nur Nachrichten seit ISO-Timestamp"},
            "for_participant": {"type": "string", "description": "Optional: Nur Nachrichten für diesen Teilnehmer"},
            "limit": {"type": "integer", "description": "Max Nachrichten (default: 50)"},
        }},
    },
    {
        "name": "group_chat_status",
        "description": "Zeige den Status einer Group Chat Session.",
        "inputSchema": {"type": "object", "required": ["session_id"], "properties": {
            "session_id": {"type": "string", "description": "Group Chat Session ID"},
        }},
    },
    {
        "name": "agent_chat_list",
        "description": "Listet alle aktiven verschlüsselten Agent-Chat-Log-Sessions mit Alter und Größe.",
        "inputSchema": {"type": "object", "properties": {}},
        "x_inventory": "group_chat",
    },
    {
        "name": "agent_chat_read",
        "description": "Liest eine Agent-Chat-Session als formatiertes Markdown. session_id='latest' für neueste Session. last_n=N für letzte N Einträge.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session-ID oder 'latest'"},
                "last_n": {"type": "integer", "description": "Letzte N Einträge (0=alle)", "default": 0},
            },
            "required": ["session_id"],
        },
        "x_inventory": "group_chat",
    },
    {
        "name": "agent_chat_stream",
        "description": "Polling-Stream: Neue Agent-Chat-Einträge seit offset abrufen. offset=0 beim ersten Aufruf, dann neuen offset weiterverwenden.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session-ID oder 'latest'"},
                "offset": {"type": "integer", "description": "Letzter bekannter Offset", "default": 0},
            },
            "required": ["session_id"],
        },
        "x_inventory": "group_chat",
    },
    {
        "name": "agent_chat_summary",
        "description": "Erstellt eine KI-Zusammenfassung (via Groq) einer abgeschlossenen Agent-Chat-Session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session-ID"},
            },
            "required": ["session_id"],
        },
        "x_inventory": "group_chat",
    },
    {
        "name": "agent_chat_cleanup",
        "description": "Löscht alle Agent-Chat-Logs älter als 2 Stunden.",
        "inputSchema": {"type": "object", "properties": {}},
        "x_inventory": "group_chat",
    },
    {
        "name": "group_chat_list",
        "description": "Liste aller aktiven Group Chat Sessions.",
        "inputSchema": {"type": "object", "properties": {
            "active_only": {"type": "boolean", "description": "Nur aktive Sessions (default: true)"},
        }},
    },
    {
        "name": "group_chat_consolidate",
        "description": "Gemini Lead konsolidiert alle Antworten der Web-AIs zu einer Zusammenfassung und einem Coding-Prompt.",
        "inputSchema": {"type": "object", "required": ["session_id"], "properties": {
            "session_id": {"type": "string", "description": "Group Chat Session ID"},
        }},
    },
    {
        "name": "group_chat_assign",
        "description": "Weise den konsolidierten Coding-Task einem Agent zu. CLI-Agents führen sofort aus, Web-Agents lesen via group_chat_read.",
        "inputSchema": {"type": "object", "required": ["session_id"], "properties": {
            "session_id": {"type": "string", "description": "Group Chat Session ID"},
            "coder": {"type": "string", "description": "Coding-Agent ID (default: auto). Optionen: claude-mcp, codex-mcp, gemini-mcp, claude-web, chatgpt-web"},
            "context": {"type": "string", "description": "Optional: Zusätzlicher Kontext"},
        }},
    },
]


# =============================================================================
# DEDUPLICATION GUARD — Bug-Fix 2026-04-28 (Code Review)
# Removes accidental duplicate tool definitions (e.g. flarum_* re-definitions
# in the V5_TOOLS += [...] block). First occurrence wins — preserves the
# canonical schemas defined in the initial V5_TOOLS block.
# =============================================================================
def _v5_dedup_tools() -> int:
    """Remove duplicate tool definitions (first-occurrence wins). Returns count removed."""
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for _t in V5_TOOLS:
        _name = _t.get("name") if isinstance(_t, dict) else None
        if not _name:
            continue
        if _name in seen:
            continue
        seen.add(_name)
        deduped.append(_t)
    removed = len(V5_TOOLS) - len(deduped)
    V5_TOOLS.clear()
    V5_TOOLS.extend(deduped)
    return removed


_REMOVED = _v5_dedup_tools()
if _REMOVED:
    import logging as _logging
    _logging.getLogger("ailinux.mcp.registry").info(
        f"V5 registry: removed {_REMOVED} duplicate tool definition(s)"
    )
del _REMOVED
