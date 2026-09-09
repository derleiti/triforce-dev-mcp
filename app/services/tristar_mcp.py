"""
TriStar MCP Service - System Management Tools
==============================================

Provides MCP tools for TriStar system management:
- Logs access and streaming
- Prompt management
- Settings configuration
- Chat/conversation history
- Agent status and control
- Memory operations

NEW in v2.80:
- Gemini Function Calling integration via gemini_access.py
- Gemini Code Execution (sandbox & local fallback)
- Hugging Face Inference API (text generation, embeddings, images, translation)

Related MCP Methods (see api_docs.py for full documentation):
- gemini_function_call: Autonomous tool execution via Gemini
- gemini_code_exec: Python code execution in sandbox
- hf_generate, hf_chat: Text generation via HuggingFace
- hf_embed: Embeddings via sentence-transformers
- hf_image: Text-to-image via FLUX/StableDiffusion
- hf_translate, hf_summarize: NLP tasks

Version: 2.80
"""

import asyncio
import json
import logging
import os
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path

from app.paths import LOG_DIR
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ailinux.tristar_mcp")

# TriStar directories
TRISTAR_BASE = Path("/var/tristar")
TRISTAR_PROMPTS = TRISTAR_BASE / "prompts"
TRISTAR_LOGS = TRISTAR_BASE / "logs"
TRISTAR_MEMORY = TRISTAR_BASE / "memory"
TRISTAR_AGENTS = TRISTAR_BASE / "agents"
TRISTAR_JOBS = TRISTAR_BASE / "jobs"
TRISTAR_PROJECTS = TRISTAR_BASE / "projects"


class TriStarMCPService:
    """Service for TriStar MCP tools."""

    def __init__(self):
        self._ensure_dirs()

    def _ensure_dirs(self):
        """Ensure all directories exist."""
        for dir_path in [TRISTAR_PROMPTS, TRISTAR_LOGS, TRISTAR_MEMORY, TRISTAR_AGENTS]:
            dir_path.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # Logs Management
    # =========================================================================

    async def get_logs(
        self,
        agent_id: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 100,
        since: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get system logs with filtering."""
        logs = []
        log_files = []

        # Find log files
        if agent_id:
            agent_log = TRISTAR_LOGS / f"{agent_id}.log"
            if agent_log.exists():
                log_files.append(agent_log)
        else:
            log_files = list(TRISTAR_LOGS.glob("*.log"))

        # Also check backend logs
        backend_log = LOG_DIR
        if backend_log.exists():
            log_files.extend(backend_log.glob("*.log"))

        # Parse logs
        for log_file in log_files[:10]:  # Limit files
            try:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[-limit:]
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        # Try JSON parse
                        try:
                            entry = json.loads(line)
                            if level and entry.get("level", "").lower() != level.lower():
                                continue
                            logs.append(entry)
                        except json.JSONDecodeError:
                            # Plain text log
                            if level and level.upper() not in line:
                                continue
                            logs.append({
                                "message": line,
                                "source": log_file.name,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
            except Exception as e:
                logger.warning(f"Failed to read log {log_file}: {e}")

        return {
            "logs": logs[-limit:],
            "count": len(logs),
            "files_scanned": len(log_files),
        }

    async def get_agent_logs(self, agent_id: str, lines: int = 50) -> Dict[str, Any]:
        """Get logs for a specific agent via journalctl."""
        import subprocess

        try:
            # Try systemd journal first
            result = subprocess.run(
                ["journalctl", "-u", f"{agent_id}.service", "-n", str(lines), "--no-pager", "-o", "json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                entries = []
                for line in result.stdout.strip().split("\n"):
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            entries.append({"MESSAGE": line})
                return {
                    "agent_id": agent_id,
                    "entries": entries,
                    "count": len(entries),
                    "source": "journald",
                }
        except Exception as e:
            logger.warning(f"journalctl failed for {agent_id}: {e}")

        # Fallback to file logs
        return await self.get_logs(agent_id=agent_id, limit=lines)

    async def clear_logs(self, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """Clear logs (truncate files)."""
        cleared = []
        if agent_id:
            log_file = TRISTAR_LOGS / f"{agent_id}.log"
            if log_file.exists():
                log_file.write_text("")
                cleared.append(str(log_file))
        else:
            for log_file in TRISTAR_LOGS.glob("*.log"):
                log_file.write_text("")
                cleared.append(str(log_file))

        return {"cleared": cleared, "count": len(cleared)}

    # =========================================================================
    # Prompt Management
    # =========================================================================

    async def list_prompts(self) -> Dict[str, Any]:
        """List all available prompts."""
        prompts = []
        for prompt_file in TRISTAR_PROMPTS.glob("*.txt"):
            stat = prompt_file.stat()
            prompts.append({
                "name": prompt_file.stem,
                "path": str(prompt_file),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            })
        # Also check agents subdir
        agents_prompts = TRISTAR_PROMPTS / "agents"
        if agents_prompts.exists():
            for prompt_file in agents_prompts.glob("*.txt"):
                stat = prompt_file.stat()
                prompts.append({
                    "name": f"agents/{prompt_file.stem}",
                    "path": str(prompt_file),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                })

        return {"prompts": prompts, "count": len(prompts)}

    async def get_prompt(self, name: str) -> Dict[str, Any]:
        """Get a specific prompt content."""
        # Try direct path first
        prompt_file = TRISTAR_PROMPTS / f"{name}.txt"
        if not prompt_file.exists():
            prompt_file = TRISTAR_PROMPTS / name
        if not prompt_file.exists():
            prompt_file = TRISTAR_PROMPTS / "agents" / f"{name}.txt"
        if not prompt_file.exists():
            return {"error": f"Prompt '{name}' not found"}

        try:
            content = prompt_file.read_text(encoding="utf-8")
            return {
                "name": name,
                "path": str(prompt_file),
                "content": content,
                "size": len(content),
                "lines": content.count("\n") + 1,
            }
        except Exception as e:
            return {"error": str(e)}

    async def set_prompt(self, name: str, content: str) -> Dict[str, Any]:
        """Create or update a prompt."""
        prompt_file = TRISTAR_PROMPTS / f"{name}.txt"
        try:
            prompt_file.write_text(content, encoding="utf-8")
            return {
                "name": name,
                "path": str(prompt_file),
                "size": len(content),
                "status": "saved",
            }
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    async def delete_prompt(self, name: str) -> Dict[str, Any]:
        """Delete a prompt."""
        prompt_file = TRISTAR_PROMPTS / f"{name}.txt"
        if not prompt_file.exists():
            return {"error": f"Prompt '{name}' not found"}
        try:
            prompt_file.unlink()
            return {"name": name, "status": "deleted"}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    # =========================================================================
    # Settings Management
    # =========================================================================

    async def get_settings(self) -> Dict[str, Any]:
        """Get all TriStar settings."""
        settings = {}

        # Read from various config files
        config_files = [
            TRISTAR_BASE / "config.json",
            TRISTAR_AGENTS / "agents.json",
            Path("/home/zombie/triforce/.env"),
        ]

        for config_file in config_files:
            if config_file.exists():
                try:
                    if config_file.suffix == ".json":
                        settings[config_file.stem] = json.loads(config_file.read_text())
                    elif config_file.name == ".env":
                        # Parse .env file
                        env_vars = {}
                        for line in config_file.read_text().split("\n"):
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                key, _, value = line.partition("=")
                                # Mask sensitive values
                                if any(s in key.upper() for s in ["KEY", "SECRET", "PASSWORD", "TOKEN"]):
                                    value = value[:4] + "****" if len(value) > 4 else "****"
                                env_vars[key] = value
                        settings["env"] = env_vars
                except Exception as e:
                    settings[config_file.stem] = {"error": str(e)}

        return {"settings": settings, "count": len(settings)}

    async def get_setting(self, key: str) -> Dict[str, Any]:
        """Get a specific setting value."""
        settings = await self.get_settings()

        # Search in all settings
        for section, values in settings.get("settings", {}).items():
            if isinstance(values, dict):
                if key in values:
                    return {"key": key, "value": values[key], "section": section}

        return {"error": f"Setting '{key}' not found"}

    async def set_setting(self, key: str, value: Any, section: str = "config") -> Dict[str, Any]:
        """Set a configuration value."""
        config_file = TRISTAR_BASE / "config.json"

        try:
            if config_file.exists():
                config = json.loads(config_file.read_text())
            else:
                config = {}

            config[key] = value
            config_file.write_text(json.dumps(config, indent=2))

            return {"key": key, "value": value, "status": "saved"}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    # =========================================================================
    # Chat/Conversation History
    # =========================================================================

    async def get_conversations(self, limit: int = 20) -> Dict[str, Any]:
        """Get list of conversation sessions."""
        conversations = []
        conv_dir = TRISTAR_BASE / "conversations"

        if conv_dir.exists():
            for conv_file in sorted(conv_dir.glob("*.json"), reverse=True)[:limit]:
                try:
                    data = json.loads(conv_file.read_text())
                    conversations.append({
                        "id": conv_file.stem,
                        "messages": len(data.get("messages", [])),
                        "model": data.get("model"),
                        "created": data.get("created"),
                        "updated": data.get("updated"),
                    })
                except Exception:
                    pass

        return {"conversations": conversations, "count": len(conversations)}

    async def get_conversation(self, session_id: str) -> Dict[str, Any]:
        """Get full conversation history."""
        conv_file = TRISTAR_BASE / "conversations" / f"{session_id}.json"

        if not conv_file.exists():
            return {"error": f"Conversation '{session_id}' not found"}

        try:
            data = json.loads(conv_file.read_text())
            return {
                "id": session_id,
                "messages": data.get("messages", []),
                "model": data.get("model"),
                "created": data.get("created"),
                "updated": data.get("updated"),
                "metadata": data.get("metadata", {}),
            }
        except Exception as e:
            return {"error": str(e)}

    async def save_conversation(
        self,
        session_id: str,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Save conversation to history."""
        conv_dir = TRISTAR_BASE / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        conv_file = conv_dir / f"{session_id}.json"

        now = datetime.now(timezone.utc).isoformat()
        data = {
            "messages": messages,
            "model": model,
            "updated": now,
            "metadata": metadata or {},
        }

        if conv_file.exists():
            old_data = json.loads(conv_file.read_text())
            data["created"] = old_data.get("created", now)
        else:
            data["created"] = now

        try:
            conv_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            return {"id": session_id, "status": "saved", "messages": len(messages)}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    async def delete_conversation(self, session_id: str) -> Dict[str, Any]:
        """Delete a conversation."""
        conv_file = TRISTAR_BASE / "conversations" / f"{session_id}.json"

        if not conv_file.exists():
            return {"error": f"Conversation '{session_id}' not found"}

        try:
            conv_file.unlink()
            return {"id": session_id, "status": "deleted"}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    # =========================================================================
    # Agent Management
    # =========================================================================

    async def list_agents(self) -> Dict[str, Any]:
        """List all configured agents."""
        agents = []
        agents_file = TRISTAR_AGENTS / "agents.json"

        if agents_file.exists():
            try:
                data = json.loads(agents_file.read_text())
                if isinstance(data, list):
                    agents = data
                elif isinstance(data, dict):
                    agents = list(data.values())
            except Exception as e:
                logger.warning(f"Failed to read agents.json: {e}")

        # Also scan agent directories
        for agent_dir in TRISTAR_AGENTS.iterdir():
            if agent_dir.is_dir():
                agent_id = agent_dir.name
                if not any(a.get("id") == agent_id for a in agents):
                    # Check for systemprompt.txt
                    prompt_file = agent_dir / "systemprompt.txt"
                    agents.append({
                        "id": agent_id,
                        "has_prompt": prompt_file.exists(),
                        "path": str(agent_dir),
                    })

        return {"agents": agents, "count": len(agents)}

    async def get_agent_config(self, agent_id: str) -> Dict[str, Any]:
        """Get agent configuration."""
        agent_dir = TRISTAR_AGENTS / agent_id
        config = {"id": agent_id}

        # Check for config files
        config_file = agent_dir / "config.json"
        if config_file.exists():
            try:
                config["config"] = json.loads(config_file.read_text())
            except Exception:
                pass

        # Check for system prompt
        prompt_file = agent_dir / "systemprompt.txt"
        if prompt_file.exists():
            config["systemprompt"] = prompt_file.read_text()[:1000]  # First 1000 chars
            config["systemprompt_length"] = prompt_file.stat().st_size

        return config

    async def set_agent_config(
        self,
        agent_id: str,
        config: Optional[Dict[str, Any]] = None,
        systemprompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update agent configuration."""
        agent_dir = TRISTAR_AGENTS / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)

        results = {"id": agent_id}

        if config is not None:
            config_file = agent_dir / "config.json"
            config_file.write_text(json.dumps(config, indent=2))
            results["config"] = "saved"

        if systemprompt is not None:
            prompt_file = agent_dir / "systemprompt.txt"
            prompt_file.write_text(systemprompt)
            results["systemprompt"] = "saved"

        return results

    # =========================================================================
    # System Status
    # =========================================================================

    async def get_status(self) -> Dict[str, Any]:
        """Get full TriStar system status."""
        import subprocess

        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "services": {},
            "directories": {},
            "memory": {},
        }

        # Check systemd services
        # systemd-services that actually exist on this host.
        # Renamed 2026-06-04: ailinux-backend → triforce; the CLI agents
        # (gemini-lead, claude-mcp, codex-mcp) are subprocesses of the backend,
        # NOT systemd units — they should be checked via agent_review, not here.
        services = [
            "triforce",
            "triforce-docker",
            "ollama",
            "redis-server",
            "nova-flarum-bot",
            "nova-log-monitor",
        ]
        for service in services:
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", f"{service}.service"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                status["services"][service] = result.stdout.strip()
            except Exception:
                status["services"][service] = "unknown"

        # Check directories
        for name, path in [
            ("prompts", TRISTAR_PROMPTS),
            ("logs", TRISTAR_LOGS),
            ("memory", TRISTAR_MEMORY),
            ("agents", TRISTAR_AGENTS),
        ]:
            if path.exists():
                files = list(path.glob("*"))
                status["directories"][name] = {
                    "path": str(path),
                    "files": len(files),
                    "exists": True,
                }
            else:
                status["directories"][name] = {"exists": False}

        # Memory stats
        memory_files = list(TRISTAR_MEMORY.glob("*.json"))
        status["memory"]["entries"] = len(memory_files)

        return status


# Singleton instance
tristar_mcp = TriStarMCPService()


# ============================================================================
# MCP Tool Definitions
# ============================================================================

TRISTAR_TOOLS = [
    # TriForce Central Logs (ALL system logs - API traffic, LLM calls, errors, etc.)
    {
        "name": "triforce_logs_recent",
        "description": "Get recent logs from the central logger. Includes ALL system logs: API traffic, LLM calls, tool calls, errors, security events",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 100, "maximum": 1000, "description": "Max entries to return"},
                "category": {
                    "type": "string",
                    "enum": ["api_request", "llm_call", "tool_call", "mcp_call", "error", "warning", "info", "debug", "security", "system", "agent", "memory", "chain"],
                    "description": "Filter by log category"
                },
            },
            "required": [],
        },
    },
    {
        "name": "triforce_logs_errors",
        "description": "Get recent error logs from the central logger",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 100, "maximum": 500},
            },
            "required": [],
        },
    },
    {
        "name": "triforce_logs_api",
        "description": "Get API traffic logs. Shows all HTTP requests/responses with latency, status codes, paths",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 100, "maximum": 1000},
            },
            "required": [],
        },
    },
    {
        "name": "triforce_logs_trace",
        "description": "Get all logs for a specific trace ID. Useful for debugging request chains",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trace_id": {"type": "string", "description": "Trace ID to search for"},
            },
            "required": ["trace_id"],
        },
    },
    {
        "name": "triforce_logs_stats",
        "description": "Get central logger statistics: total logged, buffer size, flush stats",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # TriStar Logs (agent-specific)
    {
        "name": "tristar_logs",
        "description": "Get system logs with filtering by agent, level, and time",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Filter by agent ID"},
                "level": {"type": "string", "enum": ["debug", "info", "warning", "error", "critical"]},
                "limit": {"type": "integer", "default": 100, "description": "Max entries to return"},
                "since": {"type": "string", "description": "ISO timestamp to filter from"},
            },
            "required": [],
        },
    },
    {
        "name": "tristar_logs_agent",
        "description": "Get logs for a specific agent from journald",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID (e.g., 'claude-mcp', 'gemini-lead')"},
                "lines": {"type": "integer", "default": 50},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "tristar_logs_clear",
        "description": "Clear/truncate log files",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID (omit for all)"},
            },
            "required": [],
        },
    },
    # Prompts
    {
        "name": "tristar_prompts_list",
        "description": "List all available system prompts",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tristar_prompts_get",
        "description": "Get content of a specific prompt",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Prompt name (without .txt)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "tristar_prompts_set",
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
    {
        "name": "tristar_prompts_delete",
        "description": "Delete a system prompt",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Prompt name to delete"},
            },
            "required": ["name"],
        },
    },
    # Settings
    {
        "name": "tristar_settings",
        "description": "Get all TriStar configuration settings",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tristar_settings_get",
        "description": "Get a specific setting value",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Setting key name"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "tristar_settings_set",
        "description": "Set a configuration value",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Setting key"},
                "value": {
                    "description": "The configuration value to set. Supports string, number, boolean or object.",
                    "type": ["string", "number", "boolean", "object"],
                    "oneOf": [
                        {"type": "string"},
                        {"type": "number"},
                        {"type": "boolean"},
                        {"type": "object", "additionalProperties": True},
                    ],
                },
            },
            "required": ["key", "value"],
            "additionalProperties": False,
        },
    },
    # Conversations
    {
        "name": "tristar_conversations",
        "description": "List all saved conversation sessions",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
            "required": [],
        },
    },
    {
        "name": "tristar_conversation_get",
        "description": "Get full conversation history by session ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Conversation session ID"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "tristar_conversation_save",
        "description": "Save conversation to history",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID"},
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "description": "Message role (system/user/assistant)"},
                            "content": {"type": "string", "description": "Message content"}
                        },
                        "required": ["role", "content"]
                    },
                    "description": "Chat messages",
                },
                "model": {"type": "string", "description": "Model used"},
                "metadata": {"type": "object", "description": "Additional metadata"},
            },
            "required": ["session_id", "messages"],
        },
    },
    {
        "name": "tristar_conversation_delete",
        "description": "Delete a saved conversation",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID to delete"},
            },
            "required": ["session_id"],
        },
    },
    # Agents
    {
        "name": "tristar_agents",
        "description": "List all configured TriStar agents",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tristar_agent_config",
        "description": "Get agent configuration and system prompt",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID"},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "tristar_agent_configure",
        "description": "Update agent configuration or system prompt",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID"},
                "config": {"type": "object", "description": "Configuration object"},
                "systemprompt": {"type": "string", "description": "System prompt content"},
            },
            "required": ["agent_id"],
        },
    },
    # System
    {
        "name": "tristar_status",
        "description": "Get full TriStar system status including services and directories",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tristar_shell_exec",
        "description": "Execute a shell command on the TriStar server (DEVOPS ONLY, DANGEROUS). Supports optional sudo mode for root access when user confirms.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute (e.g. 'ls -la /var/log')."
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory for the command."
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (1–300).",
                    "default": 30,
                    "minimum": 1,
                    "maximum": 300
                },
                "env": {
                    "type": "object",
                    "description": "Optional environment variables to add/override.",
                    "additionalProperties": {
                        "type": "string"
                    }
                },
                "sudo": {
                    "type": "boolean",
                    "description": "Execute command with sudo (root privileges). Only use when user explicitly confirms root access.",
                    "default": False
                }
            },
            "required": ["command"],
            "additionalProperties": False
        },
    },
]


# ============================================================================
# MCP Tool Handlers
# ============================================================================

async def handle_tristar_logs(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await tristar_mcp.get_logs(
        agent_id=arguments.get("agent_id"),
        level=arguments.get("level"),
        limit=arguments.get("limit", 100),
        since=arguments.get("since"),
    )


async def handle_tristar_logs_agent(arguments: Dict[str, Any]) -> Dict[str, Any]:
    agent_id = arguments.get("agent_id")
    if not agent_id:
        raise ValueError("'agent_id' is required")
    return await tristar_mcp.get_agent_logs(agent_id, arguments.get("lines", 50))


async def handle_tristar_logs_clear(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await tristar_mcp.clear_logs(arguments.get("agent_id"))


async def handle_tristar_prompts_list(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await tristar_mcp.list_prompts()


async def handle_tristar_prompts_get(arguments: Dict[str, Any]) -> Dict[str, Any]:
    name = arguments.get("name")
    if not name:
        raise ValueError("'name' is required")
    return await tristar_mcp.get_prompt(name)


async def handle_tristar_prompts_set(arguments: Dict[str, Any]) -> Dict[str, Any]:
    name = arguments.get("name")
    content = arguments.get("content")
    if not name or not content:
        raise ValueError("'name' and 'content' are required")
    return await tristar_mcp.set_prompt(name, content)


async def handle_tristar_prompts_delete(arguments: Dict[str, Any]) -> Dict[str, Any]:
    name = arguments.get("name")
    if not name:
        raise ValueError("'name' is required")
    return await tristar_mcp.delete_prompt(name)


async def handle_tristar_settings(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await tristar_mcp.get_settings()


async def handle_tristar_settings_get(arguments: Dict[str, Any]) -> Dict[str, Any]:
    key = arguments.get("key")
    if not key:
        raise ValueError("'key' is required")
    return await tristar_mcp.get_setting(key)


async def handle_tristar_settings_set(arguments: Dict[str, Any]) -> Dict[str, Any]:
    key = arguments.get("key")
    value = arguments.get("value")
    if not key:
        raise ValueError("'key' is required")
    return await tristar_mcp.set_setting(key, value)


async def handle_tristar_conversations(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await tristar_mcp.get_conversations(arguments.get("limit", 20))


async def handle_tristar_conversation_get(arguments: Dict[str, Any]) -> Dict[str, Any]:
    session_id = arguments.get("session_id")
    if not session_id:
        raise ValueError("'session_id' is required")
    return await tristar_mcp.get_conversation(session_id)


async def handle_tristar_conversation_save(arguments: Dict[str, Any]) -> Dict[str, Any]:
    session_id = arguments.get("session_id")
    messages = arguments.get("messages")
    if not session_id or not messages:
        raise ValueError("'session_id' and 'messages' are required")
    return await tristar_mcp.save_conversation(
        session_id,
        messages,
        model=arguments.get("model"),
        metadata=arguments.get("metadata"),
    )


async def handle_tristar_conversation_delete(arguments: Dict[str, Any]) -> Dict[str, Any]:
    session_id = arguments.get("session_id")
    if not session_id:
        raise ValueError("'session_id' is required")
    return await tristar_mcp.delete_conversation(session_id)


async def handle_tristar_agents(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await tristar_mcp.list_agents()


async def handle_tristar_agent_config(arguments: Dict[str, Any]) -> Dict[str, Any]:
    agent_id = arguments.get("agent_id")
    if not agent_id:
        raise ValueError("'agent_id' is required")
    return await tristar_mcp.get_agent_config(agent_id)


async def handle_tristar_agent_configure(arguments: Dict[str, Any]) -> Dict[str, Any]:
    agent_id = arguments.get("agent_id")
    if not agent_id:
        raise ValueError("'agent_id' is required")
    return await tristar_mcp.set_agent_config(
        agent_id,
        config=arguments.get("config"),
        systemprompt=arguments.get("systemprompt"),
    )


async def handle_tristar_status(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await tristar_mcp.get_status()


async def handle_tristar_shell_exec(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a shell command on the TriStar server.

    WARNING: Highly privileged. Guard via RBAC / MCP filter.
    
    Supports sudo mode for root access when explicitly requested.
    """
    cmd = arguments.get("command")
    if not cmd or not isinstance(cmd, str):
        raise ValueError("command (string) is required")

    cwd_arg = arguments.get("cwd")
    timeout = arguments.get("timeout", 30)
    env_arg = arguments.get("env") or {}
    use_sudo = arguments.get("sudo", False)

    # Basic safety: bound timeout
    if not isinstance(timeout, int) or timeout < 1 or timeout > 300:
        timeout = 30

    # Resolve working directory if provided
    cwd: Optional[str] = None
    if cwd_arg:
        p = Path(cwd_arg).expanduser().resolve()
        cwd = str(p)

    # Build environment – overlay on current env
    import os
    env = os.environ.copy()
    for k, v in env_arg.items():
        if isinstance(k, str) and isinstance(v, str):
            env[k] = v

    # SUDO MODE: Prefix command with sudo if requested
    # This allows root operations when user explicitly confirms
    actual_cmd = cmd
    if use_sudo:
        # Use sudo with non-interactive flag to avoid password prompts
        # Assumes passwordless sudo is configured for the user
        actual_cmd = f"sudo -n {cmd}"
        logger.info(f"Executing with sudo: {cmd[:100]}...")

    start_time = time.time()

    # IMPORTANT:
    # Use shell=True bewusst – du willst echte Shell semantics.
    # Wenn du es härter machen willst: shlex.split + shell=False.
    proc = await asyncio.create_subprocess_shell(
        actual_cmd,  # Use actual_cmd which may have sudo prefix
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
        timed_out = False
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        stdout_bytes = b""
        stderr_bytes = b"Command timed out"
        timed_out = True

    elapsed_ms = int((time.time() - start_time) * 1000)

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    result: Dict[str, Any] = {
        "command": cmd,
        "cwd": cwd,
        "exit_code": proc.returncode if not timed_out else None,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed_ms": elapsed_ms,
    }

    # Optional: Hier kannst du dein TriForce-Audit-Logging einhängen:
    # await audit_logger.log_system_shell_call(...)

    return result


# ============================================================================
# Central Logger Handlers (for TriStar access to ALL system logs)
# ============================================================================

try:
    from ..utils.triforce_logging import central_logger, LogCategory
    _HAS_CENTRAL_LOGGER = True
except ImportError:
    _HAS_CENTRAL_LOGGER = False


async def handle_triforce_logs_recent(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get recent logs from the central logger."""
    if not _HAS_CENTRAL_LOGGER:
        return {"error": "Central logger not available"}

    limit = arguments.get("limit", 100)
    category = arguments.get("category")

    cat = None
    if category:
        try:
            cat = LogCategory(category)
        except ValueError:
            pass

    entries = central_logger.get_recent(limit=limit, category=cat)
    return {
        "entries": entries,
        "count": len(entries),
    }


async def handle_triforce_logs_errors(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get recent errors from the central logger."""
    if not _HAS_CENTRAL_LOGGER:
        return {"error": "Central logger not available"}

    limit = arguments.get("limit", 100)
    entries = central_logger.get_errors(limit=limit)
    return {
        "entries": entries,
        "count": len(entries),
    }


async def handle_triforce_logs_api(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get API traffic logs."""
    if not _HAS_CENTRAL_LOGGER:
        return {"error": "Central logger not available"}

    limit = arguments.get("limit", 100)
    entries = central_logger.get_api_traffic(limit=limit)
    return {
        "entries": entries,
        "count": len(entries),
    }


async def handle_triforce_logs_trace(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get logs by trace ID."""
    if not _HAS_CENTRAL_LOGGER:
        return {"error": "Central logger not available"}

    trace_id = arguments.get("trace_id")
    if not trace_id:
        return {"error": "trace_id is required"}

    entries = central_logger.get_by_trace(trace_id)
    return {
        "trace_id": trace_id,
        "entries": entries,
        "count": len(entries),
    }


async def handle_triforce_logs_stats(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get central logger statistics."""
    if not _HAS_CENTRAL_LOGGER:
        return {"error": "Central logger not available"}

    return central_logger.get_stats()


# ============================================================================
# Auto-Evolution Handlers (Multi-Agent Backend Self-Improvement)
# ============================================================================

try:
    from .auto_evolve import AutoEvolveService, EvolutionMode
    _HAS_AUTO_EVOLVE = True
except ImportError:
    _HAS_AUTO_EVOLVE = False
    logger.warning("Auto-evolve service not available")


async def handle_evolve_analyze(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Run auto-evolution analysis phase."""
    if not _HAS_AUTO_EVOLVE:
        return {"error": "Auto-evolve service not available"}

    service = AutoEvolveService()

    # Log the evolution start
    if _HAS_CENTRAL_LOGGER:
        from ..utils.triforce_logging import TriForceLogEntry, LogCategory, LogLevel
        central_logger.queue_log(TriForceLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            trace_id="evolve",
            category=LogCategory.SYSTEM,
            level=LogLevel.INFO,
            source="auto_evolve",
            message=f"Starting evolution analysis mode={arguments.get('mode', 'analyze')}",
        ))

    result = await service.run_evolution(
        mode=EvolutionMode(arguments.get("mode", "analyze")),
        focus_areas=arguments.get("focus_areas"),
        max_findings=arguments.get("max_findings", 50),
    )

    # Log completion
    if _HAS_CENTRAL_LOGGER:
        central_logger.queue_log(TriForceLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            trace_id="evolve",
            category=LogCategory.SYSTEM,
            level=LogLevel.INFO,
            source="auto_evolve",
            message=f"Evolution completed: {len(result.findings)} findings",
            metadata={"evolution_id": result.evolution_id, "findings_count": len(result.findings)},
        ))

    return result.to_dict()


async def handle_evolve_history(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get evolution history."""
    if not _HAS_AUTO_EVOLVE:
        return {"error": "Auto-evolve service not available"}

    service = AutoEvolveService()
    history = await service.get_evolution_history(limit=arguments.get("limit", 10))
    return {"history": history, "count": len(history)}


async def handle_evolve_broadcast(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Broadcast a message to all CLI agents for collective analysis."""
    if not _HAS_AUTO_EVOLVE:
        return {"error": "Auto-evolve service not available"}

    message = arguments.get("message")
    if not message:
        return {"error": "message is required"}

    service = AutoEvolveService()
    results = await service._activate_agent_mesh()

    # Log the broadcast
    if _HAS_CENTRAL_LOGGER:
        from ..utils.triforce_logging import TriForceLogEntry, LogCategory, LogLevel
        central_logger.queue_log(TriForceLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            trace_id="evolve_broadcast",
            category=LogCategory.AGENT,
            level=LogLevel.INFO,
            source="auto_evolve",
            message=f"Agent mesh broadcast completed: {len(results)} agents",
            metadata={"agents": list(results.keys())},
        ))

    return {"agents": list(results.keys()), "results": results}


# Evolution Tools definitions
EVOLVE_TOOLS = [
    {
        "name": "evolve_analyze",
        "description": "Run auto-evolution analysis to find improvement opportunities. Activates all CLI agents (Claude, Codex, Gemini, OpenCode) to analyze codebase, search for issues, and propose improvements.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["analyze", "suggest", "implement", "full_auto"],
                    "default": "analyze",
                    "description": "Evolution mode: analyze=report only, suggest=with code proposals, implement=apply changes, full_auto=complete cycle",
                },
                "focus_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional focus areas: security, performance, quality, architecture, scalability",
                },
                "max_findings": {
                    "type": "integer",
                    "default": 50,
                    "maximum": 200,
                    "description": "Maximum number of findings to return",
                },
            },
            "required": [],
        },
    },
    {
        "name": "evolve_history",
        "description": "Get history of past evolution runs",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "maximum": 50},
            },
            "required": [],
        },
    },
    {
        "name": "evolve_broadcast",
        "description": "Broadcast a custom message to all CLI agents for collective analysis",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to broadcast to all agents"},
            },
            "required": ["message"],
        },
    },
]

# Add evolution tools to TRISTAR_TOOLS
TRISTAR_TOOLS.extend(EVOLVE_TOOLS)


# Handler mapping
TRISTAR_HANDLERS = {
    # Auto-Evolution
    "evolve_analyze": handle_evolve_analyze,
    "evolve_history": handle_evolve_history,
    "evolve_broadcast": handle_evolve_broadcast,
    # TriForce Central Logs (ALL system logs)
    "triforce_logs_recent": handle_triforce_logs_recent,
    "triforce_logs_errors": handle_triforce_logs_errors,
    "triforce_logs_api": handle_triforce_logs_api,
    "triforce_logs_trace": handle_triforce_logs_trace,
    "triforce_logs_stats": handle_triforce_logs_stats,
    # TriStar Logs
    "tristar_logs": handle_tristar_logs,
    "tristar_logs_agent": handle_tristar_logs_agent,
    "tristar_logs_clear": handle_tristar_logs_clear,
    "tristar_prompts_list": handle_tristar_prompts_list,
    "tristar_prompts_get": handle_tristar_prompts_get,
    "tristar_prompts_set": handle_tristar_prompts_set,
    "tristar_prompts_delete": handle_tristar_prompts_delete,
    "tristar_settings": handle_tristar_settings,
    "tristar_settings_get": handle_tristar_settings_get,
    "tristar_settings_set": handle_tristar_settings_set,
    "tristar_conversations": handle_tristar_conversations,
    "tristar_conversation_get": handle_tristar_conversation_get,
    "tristar_conversation_save": handle_tristar_conversation_save,
    "tristar_conversation_delete": handle_tristar_conversation_delete,
    "tristar_agents": handle_tristar_agents,
    "tristar_agent_config": handle_tristar_agent_config,
    "tristar_agent_configure": handle_tristar_agent_configure,
    "tristar_status": handle_tristar_status,
    "tristar_shell_exec": handle_tristar_shell_exec,
}
