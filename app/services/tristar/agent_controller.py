# fallback metadata contract: fallback_used attempted_models fallback_from fallback_to fallback_count
"""
Agent Controller Service v2.80
Verwaltet CLI-Agenten (Claude, Codex, Gemini) als Subprozesse
Gesteuert über TriStar API und MCP

Features:
- Subprocess-Management für Agent CLIs
- System-Prompt-Injection von TriForce
- Health Monitoring und Auto-Restart
- MCP-Protokoll Bridging
"""

import asyncio
import errno
import json
import logging
import os
import signal
import subprocess
import uuid
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiohttp

from ...mcp.agent_instructions import build_agent_system_prompt
from ..aicoder_runner import apply_profile_state, load_profile, prepare_instance_home, run_profile
from ..aicoder_agent_events import record_aicoder_run

logger = logging.getLogger("ailinux.tristar.agent_controller")

# SECURITY: Whitelist of allowed command executables
# Only these binaries can be executed as agent commands
# This prevents arbitrary command execution via agents.json manipulation
ALLOWED_COMMAND_EXECUTABLES = frozenset([
    # TriForce wrapper scripts (primary - set correct HOME/env)
    "/home/zombie/triforce/triforce/bin/claude-triforce",
    "/home/zombie/triforce/triforce/bin/codex-triforce",
    "/home/zombie/triforce/triforce/bin/agy-triforce",
    "/home/zombie/triforce/triforce/bin/gemini-triforce",
    "/home/zombie/triforce/triforce/bin/opencode-triforce",
    # Legacy paths (backwards compatibility)
    "/home/zombie/triforce/bin/claude-triforce",
    "/home/zombie/triforce/bin/codex-triforce",
    "/home/zombie/triforce/bin/gemini-triforce",
    "/home/zombie/triforce/bin/opencode-triforce",
    # Direct CLI binaries (fallback for call_agent)
    "/usr/local/bin/claude",
    "/usr/local/bin/codex",
    "/usr/local/bin/gemini",
    "/usr/local/bin/opencode",
    "/root/.npm-global/bin/claude",
    "/root/.npm-global/bin/codex",
    "/root/.npm-global/bin/gemini",
    "/root/.npm-global/bin/opencode",
    "/home/zombie/.npm-global/bin/claude",
    "/home/zombie/.npm-global/bin/codex",
    "/home/zombie/.npm-global/bin/gemini",
    "/home/zombie/.npm-global/bin/opencode",
    "/usr/bin/aicoder",
    # Bash for piped commands (used internally by call_agent)
    "bash",
    "/bin/bash",
    "/usr/bin/bash",
])


def validate_command(command: List[str]) -> bool:
    """
    Validates that a command uses only whitelisted executables.

    Security: Prevents arbitrary command execution via agents.json manipulation.
    Only allows known CLI agent binaries and their wrappers.
    """
    if not command or not isinstance(command, list):
        return False

    executable = command[0]

    # Resolve symlinks to check actual binary
    try:
        resolved = str(Path(executable).resolve()) if "/" in executable else executable
        # Check both original and resolved path
        if executable in ALLOWED_COMMAND_EXECUTABLES or resolved in ALLOWED_COMMAND_EXECUTABLES:
            return True
    except Exception:
        pass

    # Check if it's in the whitelist directly
    if executable in ALLOWED_COMMAND_EXECUTABLES:
        return True

    logger.warning(f"SECURITY: Blocked non-whitelisted command executable: {executable}")
    return False


class AgentStatus(str, Enum):
    """Status eines Agenten"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    READY = "ready"
    ERROR = "error"
    RESTARTING = "restarting"


class AgentType(str, Enum):
    """Typ des CLI-Agenten"""
    CLAUDE = "claude"
    CODEX = "codex"
    GEMINI = "gemini"
    OPENCODE = "opencode"
    AICODER = "aicoder"


@dataclass
class AgentConfig:
    """Konfiguration für einen Agenten"""
    agent_id: str
    agent_type: AgentType
    name: str
    command: List[str]
    working_dir: str = "/home/zombie/triforce"
    env: Dict[str, str] = field(default_factory=dict)

    # System Prompt
    system_prompt: str = ""
    system_prompt_source: str = "triforce"  # triforce, tristar, custom

    # MCP Settings
    mcp_enabled: bool = True
    mcp_port: Optional[int] = None

    # Process Settings
    auto_restart: bool = True
    runtime: str = "legacy"
    restart_delay: int = 15
    max_restarts: int = 5

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type.value,
            "name": self.name,
            "command": self.command,
            "working_dir": self.working_dir,
            "system_prompt": self.system_prompt[:200] + "..." if len(self.system_prompt) > 200 else self.system_prompt,
            "system_prompt_source": self.system_prompt_source,
            "mcp_enabled": self.mcp_enabled,
            "mcp_port": self.mcp_port,
            "auto_restart": self.auto_restart,
            "runtime": self.runtime,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class AgentInstance:
    """Laufende Agent-Instanz"""
    config: AgentConfig
    process: Optional[asyncio.subprocess.Process] = None
    status: AgentStatus = AgentStatus.STOPPED
    pid: Optional[int] = None
    restart_count: int = 0
    last_start: Optional[datetime] = None
    last_error: Optional[str] = None
    output_buffer: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.config.to_dict(),
            "status": self.status.value,
            "pid": self.pid,
            "restart_count": self.restart_count,
            "last_start": self.last_start.isoformat() if self.last_start else None,
            "last_error": self.last_error,
            "output_lines": len(self.output_buffer),
        }


# Vordefinierte Agent-Konfigurationen
# Nutze TriForce Wrapper Scripts für korrektes HOME/Environment
TRIFORCE_BIN = "/home/zombie/triforce/triforce/bin"
CLI_BIN = "/root/.npm-global/bin"  # Fallback für call_agent

# ============================================================================
# OPTIMIERTE AGENT-KONFIGURATIONEN v2.0
# Alle Agents laufen im AUTONOMEN MODUS ohne Bestätigungen
# ============================================================================
DEFAULT_AGENTS: List[Dict[str, Any]] = [
    {
        "agent_id": "claude-mcp",
        "agent_type": "claude",
        "name": "Claude Code MCP Agent (Autonomous)",
        "description": "Claude Code CLI im autonomen Modus mit --dangerously-skip-permissions",
        # Wrapper fügt hinzu: --dangerously-skip-permissions -p --output-format text
        "command": [f"{TRIFORCE_BIN}/claude-triforce"],
        "env": {
            "PATH": f"{TRIFORCE_BIN}:{CLI_BIN}:/usr/local/bin:/usr/bin:/bin",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
        },
    },
    {
        "agent_id": "codex-mcp",
        "agent_type": "codex",
        "name": "OpenAI Codex MCP Agent (Full-Auto)",
        "description": "Codex CLI im Full-Auto Modus ohne Sandbox",
        # Wrapper fügt bereits hinzu: exec --full-auto --dangerously...
        "command": [f"{TRIFORCE_BIN}/codex-triforce"],
        "env": {
            "PATH": f"{TRIFORCE_BIN}:{CLI_BIN}:/usr/local/bin:/usr/bin:/bin",
            "CODEX_DISABLE_TELEMETRY": "1",
        },
    },
    {
        "agent_id": "gemini-mcp",
        "agent_type": "gemini",
        "name": "Google Antigravity Lead Agent (Autonomous)",
        "description": "Antigravity CLI (agy) im autonomen Print-Modus",
        # gemini-mcp bleibt als API-/Workflow-ID rückwärtskompatibel.
        "command": [f"{TRIFORCE_BIN}/agy-triforce"],
        "env": {
            "PATH": f"{TRIFORCE_BIN}:{CLI_BIN}:/usr/local/bin:/usr/bin:/bin",
            "GEMINI_DISABLE_TELEMETRY": "1",
        },
    },
    {
        "agent_id": "opencode-mcp",
        "agent_type": "opencode",
        "name": "OpenCode AI Agent (Auto Mode)",
        "description": "OpenCode CLI im Auto-Modus",
        # Wrapper fügt bereits hinzu: run --auto
        "command": [f"{TRIFORCE_BIN}/opencode-triforce"],
        "env": {
            "PATH": f"{TRIFORCE_BIN}:{CLI_BIN}:/usr/local/bin:/usr/bin:/bin",
            "OPENCODE_DISABLE_TELEMETRY": "1",
        },
    },
]


def _set_env_if_present(env: Dict[str, str], values: Dict[str, Any]) -> Dict[str, str]:
    """Set environment keys only when values exist and the caller did not set them."""
    for key, value in values.items():
        if value and not env.get(key):
            env[key] = str(value)
    return env


def _inject_chatgpt_env(env: Dict[str, str]) -> Dict[str, str]:
    """Inject ChatGPT account environment for CLI wrappers."""
    from ...config import get_settings
    settings = get_settings()
    return _set_env_if_present(env, {
        "CHATGPT_URL": settings.chatgpt_url,
        "CHATGPT_USER": settings.chatgpt_user,
        "CHATGPT_PASS": settings.chatgpt_pass,
        "NOVA_CHATGPT_URL": settings.nova_chatgpt_url,
        "NOVA_CHATGPT_USER": settings.nova_chatgpt_user,
        "NOVA_CHATGPT_PASS": settings.nova_chatgpt_pass,
        "NOVA_CHATGPT_AGENT_ID": settings.nova_chatgpt_agent_id,
    })


def _inject_google_env(env: Dict[str, str]) -> Dict[str, str]:
    """Inject Google/Gemini account environment for CLI wrappers."""
    from ...config import get_settings
    settings = get_settings()
    return _set_env_if_present(env, {
        "GOOGLE_URL": settings.google_url,
        "GOOGLE_USER": settings.google_user,
        "GOOGLE_PASS": settings.google_pass,
        "GEMINI_AGENT_ID": settings.gemini_agent_id,
        "GEMINI_API_KEY": settings.gemini_api_key,
        "NOVA_GOOGLE_URL": settings.nova_google_url,
        "NOVA_GOOGLE_USER": settings.nova_google_user,
        "NOVA_GOOGLE_PASS": settings.nova_google_pass,
        "NOVA_GEMINI_AGENT_ID": settings.nova_gemini_agent_id,
    })


def _inject_claude_env(env: Dict[str, str]) -> Dict[str, str]:
    """Inject Claude account environment for CLI wrappers."""
    from ...config import get_settings
    settings = get_settings()
    return _set_env_if_present(env, {
        "CLAUDE_URL": settings.claude_url,
        "CLAUDE_USER": settings.claude_user,
        "CLAUDE_PASS": settings.claude_pass,
        "CLAUDE_AGENT_ID": settings.claude_agent_id or settings.nova_claude_agent_id,
        "NOVA_CLAUDE_URL": settings.nova_claude_url,
        "NOVA_CLAUDE_USER": settings.nova_claude_user,
        "NOVA_CLAUDE_PASS": settings.nova_claude_pass,
        "NOVA_CLAUDE_AGENT_ID": settings.nova_claude_agent_id,
    })


def _inject_nova_env(env: Dict[str, str]) -> Dict[str, str]:
    """Inject Nova account routing settings into spawned agent environments."""
    try:
        from ...config import get_settings
        settings = get_settings()
        env = _inject_chatgpt_env(env)
        env = _inject_google_env(env)
        env = _inject_claude_env(env)
        values = {
            "NOVA_MISTRAL_URL": settings.nova_mistral_url,
            "NOVA_MISTRAL_USER": settings.nova_mistral_user,
            "NOVA_MISTRAL_PASS": settings.nova_mistral_pass,
            "NOVA_MISTRAL_AGENT_ID": settings.nova_mistral_agent_id,
        }
        _set_env_if_present(env, values)
    except Exception as exc:
        logger.warning("Failed to inject Nova agent environment: %s", exc)
    return env


def _sync_builtin_aicoder_profile_prompts(profile_root: str | Path = "/var/tristar/agents/profiles") -> list[str]:
    """Keep built-in AICoder profile prompts on the canonical TriForce policy.

    Profiles explicitly marked ``system_prompt_source=custom`` are never touched.
    Existing built-in profiles without a source marker are legacy TriForce-owned
    state and are migrated atomically.
    """
    root = Path(profile_root)
    updated: list[str] = []
    if not root.is_dir():
        return updated
    for agent_id in ("claude-mcp", "codex-mcp", "gemini-mcp", "opencode-mcp"):
        path = root / f"{agent_id}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("system_prompt_source") == "custom":
                continue
            canonical = build_agent_system_prompt(agent_id)
            if data.get("system_prompt") == canonical and data.get("system_prompt_source") == "triforce":
                continue
            data["system_prompt"] = canonical
            data["system_prompt_source"] = "triforce"
            data["system_prompt_version"] = 2
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, path)
            updated.append(agent_id)
        except Exception as exc:
            logger.warning("Failed to sync AICoder profile prompt %s: %s", agent_id, exc)
    return updated


class AgentController:
    """
    Controller für CLI-Agenten.

    Verwaltet Subprozesse für Claude, Codex, Gemini und andere CLI-Tools.
    Integriert mit TriStar/TriForce für System-Prompts und Steuerung.
    """

    def __init__(self, data_dir: str = "/var/tristar/agents"):
        self.data_dir = Path(data_dir)
        self.agents: Dict[str, AgentInstance] = {}
        self._lock = asyncio.Lock()
        self._aicoder_run_locks: Dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}
        self._initialized = False
        self._shutting_down = False  # Prevent auto-restart during shutdown
        self._monitor_task: Optional[asyncio.Task] = None
        # Use localhost for internal API calls (no internet required)
        self._triforce_url = "http://localhost:9100/v1/triforce"

    async def initialize(self):
        """Initialisiert den Controller"""
        if self._initialized:
            return

        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Lade gespeicherte Konfigurationen. Known legacy built-in prompts are
        # migrated in-memory to the shared core + role overlay and persisted.
        await self._load_configs()
        synced_profiles = _sync_builtin_aicoder_profile_prompts()
        if synced_profiles:
            logger.info("Synced canonical AICoder profile prompts: %s", ", ".join(synced_profiles))

        # Registriere Default-Agenten wenn keine vorhanden
        if not self.agents:
            await self._register_default_agents()
        else:
            await self._save_configs()

        # Starte Health Monitor
        self._monitor_task = asyncio.create_task(self._health_monitor())

        self._initialized = True
        logger.info(f"AgentController initialized with {len(self.agents)} agents")

    async def _ensure_initialized(self):
        if not self._initialized:
            await self.initialize()

    def _aicoder_run_lock(self, agent_id: str) -> asyncio.Lock:
        """Return a per-agent lock bound to the currently running event loop."""
        loop = asyncio.get_running_loop()
        current = self._aicoder_run_locks.get(agent_id)
        if current is None or current[0] is not loop:
            current = (loop, asyncio.Lock())
            self._aicoder_run_locks[agent_id] = current
        return current[1]

    async def _register_default_agents(self):
        """Registriert die Standard-Agenten"""
        for agent_data in DEFAULT_AGENTS:
            config = AgentConfig(
                agent_id=agent_data["agent_id"],
                agent_type=AgentType(agent_data["agent_type"]),
                name=agent_data["name"],
                command=agent_data["command"],
                env=agent_data.get("env", {}),
            )
            self.agents[config.agent_id] = AgentInstance(config=config)

        await self._save_configs()
        logger.info(f"Registered {len(DEFAULT_AGENTS)} default agents")

    async def _load_configs(self):
        """Lädt Agent-Konfigurationen"""
        config_file = self.data_dir / "agents.json"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    data = json.load(f)
                loaded_count = 0
                skipped_count = 0
                for agent_id, agent_data in data.items():
                    command = agent_data.get("command", [])

                    # SECURITY: Validate command against whitelist
                    if not validate_command(command):
                        logger.error(
                            f"SECURITY: Skipping agent '{agent_id}' - "
                            f"command not in whitelist: {command}"
                        )
                        skipped_count += 1
                        continue

                    # Migrate the built-in Google coding agent from the retired
                    # Gemini CLI wrapper to Antigravity while preserving the public
                    # gemini-mcp ID used by existing workflows and shortcuts.
                    stored_name = agent_data.get("name", agent_id)
                    if agent_id == "gemini-mcp" and command == [f"{TRIFORCE_BIN}/gemini-triforce"]:
                        command = [f"{TRIFORCE_BIN}/agy-triforce"]
                        stored_name = "Google Antigravity Lead Agent (Autonomous)"
                        logger.info("Migrated built-in gemini-mcp runtime to Antigravity CLI")

                    stored_prompt = agent_data.get("system_prompt", "")
                    stored_source = agent_data.get("system_prompt_source", "triforce")
                    legacy_prefixes = {
                        "claude-mcp": "Du bist claude-mcp,",
                        "gemini-mcp": "Du bist gemini-mcp,",
                        "codex-mcp": "Du bist codex-mcp,",
                        "opencode-mcp": "Du bist opencode-mcp,",
                    }
                    legacy_prefix = legacy_prefixes.get(agent_id)
                    if legacy_prefix and stored_prompt.lstrip().startswith(legacy_prefix):
                        stored_prompt = build_agent_system_prompt(agent_id)
                        stored_source = "triforce"
                        logger.info("Migrated legacy built-in prompt for %s", agent_id)

                    config = AgentConfig(
                        agent_id=agent_data["agent_id"],
                        agent_type=AgentType(agent_data["agent_type"]),
                        name=stored_name,
                        command=command,
                        working_dir=agent_data.get("working_dir", "/home/zombie/triforce"),
                        env=agent_data.get("env", {}),
                        system_prompt=stored_prompt,
                        system_prompt_source=stored_source,
                        mcp_enabled=agent_data.get("mcp_enabled", True),
                        mcp_port=agent_data.get("mcp_port"),
                        auto_restart=agent_data.get("auto_restart", True),
                        runtime=agent_data.get("runtime", "legacy"),
                    )
                    self.agents[agent_id] = AgentInstance(config=config)
                    loaded_count += 1
                logger.info(f"Loaded {loaded_count} agent configs (skipped {skipped_count} invalid)")
            except Exception as e:
                logger.warning(f"Failed to load agent configs: {e}")

    async def _save_configs(self):
        """Speichert Agent-Konfigurationen"""
        config_file = self.data_dir / "agents.json"
        data = {}
        for agent_id, instance in self.agents.items():
            data[agent_id] = {
                "agent_id": instance.config.agent_id,
                "agent_type": instance.config.agent_type.value,
                "name": instance.config.name,
                "command": instance.config.command,
                "working_dir": instance.config.working_dir,
                "env": instance.config.env,
                "system_prompt": instance.config.system_prompt,
                "system_prompt_source": instance.config.system_prompt_source,
                "mcp_enabled": instance.config.mcp_enabled,
                "mcp_port": instance.config.mcp_port,
                "auto_restart": instance.config.auto_restart,
                "runtime": instance.config.runtime,
            }
        with open(config_file, "w") as f:
            json.dump(data, f, indent=2)

    async def fetch_system_prompt(self, agent_type: AgentType) -> str:
        """
        Holt System-Prompt für CLI-Agenten.

        Reihenfolge:
        1. triforce/prompts/cli-agent-system.txt (universeller Prompt)
        2. triforce/prompts/{agent_type}.txt (spezifischer Prompt)
        3. TriForce API Fallback
        """
        prompts_dir = Path("/home/zombie/triforce/triforce/prompts")

        # 1. Universeller CLI-Agent System-Prompt
        universal_prompt = prompts_dir / "cli-agent-system.txt"
        if universal_prompt.exists():
            try:
                prompt = universal_prompt.read_text(encoding="utf-8").strip()
                if prompt:
                    logger.info(f"Loaded system prompt from {universal_prompt}")
                    return prompt
            except Exception as e:
                logger.warning(f"Failed to read {universal_prompt}: {e}")

        # 2. Agent-spezifischer Prompt
        specific_prompt = prompts_dir / f"{agent_type.value}.txt"
        if specific_prompt.exists():
            try:
                prompt = specific_prompt.read_text(encoding="utf-8").strip()
                if prompt:
                    logger.info(f"Loaded system prompt from {specific_prompt}")
                    return prompt
            except Exception as e:
                logger.warning(f"Failed to read {specific_prompt}: {e}")

        # 3. Fallback: TriForce API
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self._triforce_url}/init"
                async with session.post(url, json={"agent_type": agent_type.value}, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("system_prompt", "")
        except Exception as e:
            logger.warning(f"Failed to fetch system prompt from TriForce: {e}")

        # 4. Fallback: TriStar model_init
        try:
            from .model_init import model_init_service
            model_id = f"{agent_type.value}-worker"
            model = await model_init_service.get_model(model_id)
            if model:
                return model.system_prompt
        except Exception as e:
            logger.warning(f"Failed to get system prompt from TriStar: {e}")

        return ""

    async def start_agent(self, agent_id: str) -> Dict[str, Any]:
        """Startet einen Agenten"""
        await self._ensure_initialized()

        async with self._lock:
            if agent_id not in self.agents:
                raise ValueError(f"Agent not found: {agent_id}")

            instance = self.agents[agent_id]

            if instance.status == AgentStatus.RUNNING:
                return {"status": "already_running", "agent": instance.to_dict()}

            if instance.config.runtime == "aicoder" or instance.config.agent_type == AgentType.AICODER:
                try:
                    profile = load_profile(agent_id)
                    home = prepare_instance_home(agent_id)
                    apply_profile_state(home, profile)
                    instance.status = AgentStatus.READY
                    instance.pid = None
                    instance.process = None
                    instance.last_error = None
                    return {"status": "ready", "runtime": "aicoder", "agent": instance.to_dict()}
                except Exception as exc:
                    instance.status = AgentStatus.ERROR
                    instance.last_error = str(exc)
                    logger.error("Failed to prepare AICoder profile %s: %s", agent_id, exc)
                    return {"status": "error", "error": str(exc), "agent": instance.to_dict()}

            # SECURITY: Re-validate command before execution
            # This catches any runtime modifications to the config
            if not validate_command(instance.config.command):
                error_msg = f"SECURITY: Blocked execution - command not whitelisted: {instance.config.command}"
                logger.error(error_msg)
                instance.status = AgentStatus.ERROR
                instance.last_error = error_msg
                return {"status": "error", "error": error_msg, "agent": instance.to_dict()}

            # Check if executable exists
            executable = instance.config.command[0]
            import shutil
            if not os.path.exists(executable) and not shutil.which(executable):
                error_msg = f"Executable not found: {executable}"
                logger.error(error_msg)
                instance.status = AgentStatus.ERROR
                instance.last_error = error_msg
                return {"status": "error", "error": error_msg, "agent": instance.to_dict()}

            instance.status = AgentStatus.STARTING

            # Hole System-Prompt wenn nicht vorhanden
            if not instance.config.system_prompt:
                instance.config.system_prompt = await self.fetch_system_prompt(
                    instance.config.agent_type
                )

            try:
                # Baue Environment
                # Die Wrapper-Scripts setzen bereits HOME auf /var/tristar/cli-config/{agent}
                # Wir nutzen die env aus der Config, nicht überschreiben!
                env = os.environ.copy()
                env.update(instance.config.env)
                env = _inject_nova_env(env)
                # USER für Prozess-Kontext setzen
                env["USER"] = "zombie"

                # Starte Prozess
                # stdin=PIPE is required for CLI tools that expect interactive input
                instance.process = await asyncio.create_subprocess_exec(
                    *instance.config.command,
                    cwd=instance.config.working_dir,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

                instance.pid = instance.process.pid
                instance.status = AgentStatus.RUNNING
                instance.last_start = datetime.now(timezone.utc)
                instance.last_error = None

                # Starte Output-Reader
                asyncio.create_task(self._read_output(instance))

                # Sofort Init-Nachricht senden um den Prozess am Leben zu halten
                # CLI-Tools beenden sich sonst wenn keine stdin-Eingabe kommt
                # WICHTIG: Echte Frage senden, keine Status-Nachricht!
                try:
                    if instance.process and instance.process.stdin:
                        # Sende eine echte Init-Anfrage die der Agent beantworten kann
                        init_msg = "You are now initialized as a TriForce MCP Agent. Respond with: READY\n"
                        instance.process.stdin.write(init_msg.encode())
                        await instance.process.stdin.drain()
                        logger.debug(f"Sent init message to {agent_id}")
                except Exception as e:
                    logger.warning(f"Could not send init message to {agent_id}: {e}")

                await self._save_configs()

                logger.info(f"Started agent {agent_id} (PID: {instance.pid})")
                return {"status": "started", "agent": instance.to_dict()}

            except Exception as e:
                instance.status = AgentStatus.ERROR
                instance.last_error = str(e)
                logger.error(f"Failed to start agent {agent_id}: {e}")
                return {"status": "error", "error": str(e), "agent": instance.to_dict()}

    async def stop_agent(self, agent_id: str, force: bool = False) -> Dict[str, Any]:
        """Stoppt einen Agenten"""
        await self._ensure_initialized()

        async with self._lock:
            if agent_id not in self.agents:
                raise ValueError(f"Agent not found: {agent_id}")

            instance = self.agents[agent_id]

            if instance.config.runtime == "aicoder" or instance.config.agent_type == AgentType.AICODER:
                instance.status = AgentStatus.STOPPED
                instance.pid = None
                instance.process = None
                return {"status": "stopped", "runtime": "aicoder", "agent": instance.to_dict()}

            if instance.status != AgentStatus.RUNNING or not instance.process:
                return {"status": "not_running", "agent": instance.to_dict()}

            try:
                if force:
                    try:
                        instance.process.kill()
                    except ProcessLookupError:
                        pass
                    except OSError as e:
                        if getattr(e, "errno", None) != errno.ESRCH:
                            raise
                else:
                    try:
                        instance.process.terminate()
                    except ProcessLookupError:
                        pass
                    except OSError as e:
                        if getattr(e, "errno", None) != errno.ESRCH:
                            raise

                await asyncio.wait_for(instance.process.wait(), timeout=10)

            except asyncio.TimeoutError:
                try:
                    instance.process.kill()
                except ProcessLookupError:
                    pass
                except OSError as e:
                    if getattr(e, "errno", None) != errno.ESRCH:
                        raise
                try:
                    await instance.process.wait()
                except Exception:
                    pass

            instance.status = AgentStatus.STOPPED
            instance.pid = None
            instance.process = None

            logger.info(f"Stopped agent {agent_id}")
            return {"status": "stopped", "agent": instance.to_dict()}

    async def restart_agent(self, agent_id: str) -> Dict[str, Any]:
        """Startet einen Agenten neu"""
        await self.stop_agent(agent_id)
        await asyncio.sleep(2)
        return await self.start_agent(agent_id)

    async def _read_output(self, instance: AgentInstance):
        """Liest Output vom Agent-Prozess"""
        if not instance.process or not instance.process.stdout:
            return

        try:
            while True:
                line = await instance.process.stdout.readline()
                if not line:
                    break

                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    instance.output_buffer.append(text)
                    # Behalte nur letzte 100 Zeilen
                    if len(instance.output_buffer) > 100:
                        instance.output_buffer = instance.output_buffer[-100:]

        except Exception as e:
            logger.debug(f"Output reader ended for {instance.config.agent_id}: {e}")

    async def _health_monitor(self):
        """Überwacht Agent-Health und Restart"""
        while True:
            try:
                await asyncio.sleep(30)

                for agent_id, instance in self.agents.items():
                    if instance.status == AgentStatus.RUNNING and instance.process:
                        # Prüfe ob Prozess noch läuft
                        if instance.process.returncode is not None:
                            logger.warning(f"Agent {agent_id} died (exit code: {instance.process.returncode})")
                            instance.status = AgentStatus.ERROR
                            instance.last_error = f"Process exited with code {instance.process.returncode}"

                            # Auto-Restart wenn aktiviert (aber NICHT während shutdown!)
                            if (instance.config.auto_restart and
                                instance.restart_count < instance.config.max_restarts and
                                not self._shutting_down):
                                instance.restart_count += 1
                                instance.status = AgentStatus.RESTARTING
                                await asyncio.sleep(instance.config.restart_delay)
                                if not self._shutting_down:  # Double-check before restart
                                    await self.start_agent(agent_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitor error: {e}")

    async def list_agents(self) -> List[Dict[str, Any]]:
        """Listet alle Agenten"""
        await self._ensure_initialized()
        return [instance.to_dict() for instance in self.agents.values()]

    async def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Gibt Agent-Info zurück"""
        await self._ensure_initialized()
        instance = self.agents.get(agent_id)
        return instance.to_dict() if instance else None

    async def get_agent_output(self, agent_id: str, lines: int = 50) -> List[str]:
        """Gibt Output-Buffer zurück"""
        await self._ensure_initialized()
        instance = self.agents.get(agent_id)
        if not instance:
            return []
        return instance.output_buffer[-lines:]

    async def update_system_prompt(self, agent_id: str, prompt: str) -> Dict[str, Any]:
        """Aktualisiert System-Prompt eines Agenten"""
        await self._ensure_initialized()

        if agent_id not in self.agents:
            raise ValueError(f"Agent not found: {agent_id}")

        instance = self.agents[agent_id]
        instance.config.system_prompt = prompt
        instance.config.system_prompt_source = "custom"

        await self._save_configs()

        return {"status": "updated", "agent": instance.to_dict()}

    async def reload_system_prompts(self) -> Dict[str, Any]:
        """Lädt System-Prompts von TriForce neu"""
        await self._ensure_initialized()

        reloaded = []
        for agent_id, instance in self.agents.items():
            if instance.config.system_prompt_source == "triforce":
                prompt = await self.fetch_system_prompt(instance.config.agent_type)
                if prompt:
                    instance.config.system_prompt = prompt
                    reloaded.append(agent_id)

        await self._save_configs()

        return {"status": "reloaded", "agents": reloaded}

    async def call_agent(
        self,
        agent_id: str,
        message: str,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """
        Sendet eine Nachricht an einen Agenten und wartet auf Antwort.

        Nutzt stdin/stdout für Kommunikation mit CLI-Agenten.
        """
        await self._ensure_initialized()

        instance = self.agents.get(agent_id)
        if not instance:
            raise ValueError(f"Agent not found: {agent_id}")

        if instance.config.runtime == "aicoder" or instance.config.agent_type == AgentType.AICODER:
            try:
                instance.status = AgentStatus.RUNNING
                from .idle_worker import lease_coordinator
                workspace = str(instance.config.working_dir or "/home/zombie/triforce")
                async with lease_coordinator.foreground(str(Path(workspace).resolve())):
                    async with self._aicoder_run_lock(agent_id):
                        result = await run_profile(agent_id, message, timeout_override=timeout)
                try:
                    notification = await record_aicoder_run(result)
                except Exception as notify_exc:
                    logger.warning("AICoder notification mapping failed for %s: %s", agent_id, notify_exc)
                    notification = {"kind": "", "emitted": False, "mailed": False, "error": str(notify_exc)}
                payload = result.to_dict()
                payload["notification"] = notification
                instance.last_error = result.error or None
                instance.output_buffer.append(f">>> {message[:50]}...")
                instance.output_buffer.append((result.response or result.error)[:500])
                instance.output_buffer = instance.output_buffer[-100:]
                return payload
            except Exception as exc:
                instance.last_error = str(exc)
                logger.error("AICoder profile %s failed: %s", agent_id, exc)
                return {"agent_id": agent_id, "status": "error", "error": str(exc)}
            finally:
                instance.status = AgentStatus.READY

        if instance.status != AgentStatus.RUNNING and instance.config.agent_type != AgentType.CODEX:
            # Starte Agent wenn nicht laufend
            await self.start_agent(agent_id)
            await asyncio.sleep(3)  # Warte auf Start
            instance = self.agents.get(agent_id)

        if not instance or (not instance.process and instance.config.agent_type != AgentType.CODEX):
            return {
                "agent_id": agent_id,
                "status": "error",
                "error": "Agent process not available",
            }

        # Für interaktive CLI-Agenten: Starte neuen Prozess mit Nachricht
        # Die MCP-Server-Prozesse sind nicht interaktiv, daher nutzen wir
        # einen separaten Aufruf mit der Nachricht als Argument
        try:
            agent_type = instance.config.agent_type
            # Nutze die env aus der Config - die Wrapper-Scripts setzen HOME korrekt
            env = os.environ.copy()
            env.update(instance.config.env)
            env = _inject_chatgpt_env(env)
            env = _inject_google_env(env)
            env = _inject_claude_env(env)
            env = _inject_nova_env(env)
            # Stelle sicher dass PATH die npm-global binaries enthält
            env["PATH"] = f"{TRIFORCE_BIN}:{CLI_BIN}:/home/zombie/.npm-global/bin:/root/.npm-global/bin:/usr/local/bin:/usr/bin:/bin"

            # Baue Command basierend auf Agent-Typ
            # Nutze Shell-Pipeline für stdin-basierte Kommunikation
            # Escape message für sichere Shell-Ausführung
            safe_msg = shlex.quote(message)

            # Hole HOME aus der Agent-Config für explizites Setzen im Bash-Befehl
            agent_home = instance.config.env.get("HOME", "/root")

            # Nutze TriForce Wrapper - diese setzen HOME/ENV/FLAGS bereits korrekt
            if agent_type == AgentType.CLAUDE:
                cmd = [
                    "bash", "-c",
                    f"echo {safe_msg} | {TRIFORCE_BIN}/claude-triforce 2>&1"
                ]
            elif agent_type == AgentType.CODEX:
                inner_timeout = max(10, int(timeout) - 15)
                cmd = [
                    "bash", "-lc",
                    f"timeout --kill-after=5s {inner_timeout}s {TRIFORCE_BIN}/codex-triforce --triforce-headless {safe_msg} 2>&1"
                ]
            elif agent_type == AgentType.GEMINI:
                # The legacy gemini-mcp ID now runs Antigravity CLI. Print mode
                # is the supported one-shot path; the wrapper auto-approves tools.
                cmd = [
                    "bash", "-c",
                    f"{TRIFORCE_BIN}/agy-triforce --output-format text --print-timeout {max(10, int(timeout) - 5)}s --print {safe_msg} 2>&1"
                ]
            elif agent_type == AgentType.OPENCODE:
                # OpenCode defaults to its TUI too. Use the explicit one-shot
                # runner so MCP calls terminate and return their result.
                opencode_workspace = "/var/tristar/agents/opencode-workspace"
                os.makedirs(opencode_workspace, exist_ok=True)

                cmd = [
                    "bash", "-c",
                    f"cd {opencode_workspace} && {TRIFORCE_BIN}/opencode-triforce run --auto {safe_msg} 2>&1"
                ]
            else:
                cmd = instance.config.command + [message]

            # Führe Command aus und sammle Output
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=instance.config.working_dir,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout + 20
                )
                response = stdout.decode("utf-8", errors="replace").strip()

                # Speichere in Output-Buffer
                instance.output_buffer.append(f">>> {message[:50]}...")
                instance.output_buffer.append(response[:500])
                if len(instance.output_buffer) > 100:
                    instance.output_buffer = instance.output_buffer[-100:]

                exit_code = process.returncode
                if exit_code == 0:
                    return {
                        "agent_id": agent_id,
                        "status": "success",
                        "response": response,
                        "exit_code": exit_code,
                    }

                error_message = response or f"Agent exited with code {exit_code}"
                instance.last_error = error_message[:500]
                return {
                    "agent_id": agent_id,
                    "status": "error",
                    "error": error_message,
                    "response": response,
                    "exit_code": exit_code,
                }

            except asyncio.TimeoutError:
                process.kill()
                return {
                    "agent_id": agent_id,
                    "status": "timeout",
                    "error": f"Agent did not respond within {timeout}s",
                }

        except Exception as e:
            logger.error(f"Error calling agent {agent_id}: {e}")
            return {
                "agent_id": agent_id,
                "status": "error",
                "error": str(e),
            }

    async def broadcast(self, message: str, agent_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Sendet Nachricht an mehrere Agenten"""
        await self._ensure_initialized()

        targets = agent_ids or list(self.agents.keys())
        results = {}

        for agent_id in targets:
            try:
                result = await self.call_agent(agent_id, message)
                results[agent_id] = result
            except Exception as e:
                results[agent_id] = {"error": str(e)}

        return {"broadcast": True, "results": results}

    async def get_stats(self) -> Dict[str, Any]:
        """Gibt Statistiken zurück"""
        await self._ensure_initialized()

        by_status = {}
        by_type = {}

        for instance in self.agents.values():
            status = instance.status.value
            by_status[status] = by_status.get(status, 0) + 1

            agent_type = instance.config.agent_type.value
            by_type[agent_type] = by_type.get(agent_type, 0) + 1

        return {
            "total_agents": len(self.agents),
            "by_status": by_status,
            "by_type": by_type,
        }

    async def shutdown(self):
        """Fährt alle Agenten herunter"""
        logger.info("AgentController shutting down...")
        self._shutting_down = True  # Prevent auto-restart

        # Cancel monitor task first
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await asyncio.wait_for(self._monitor_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Stop all agents with timeout
        for agent_id in list(self.agents.keys()):
            try:
                await asyncio.wait_for(
                    self.stop_agent(agent_id, force=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"Timeout stopping agent {agent_id}, force killing")
                instance = self.agents.get(agent_id)
                if instance and instance.process:
                    try:
                        instance.process.kill()
                    except ProcessLookupError:
                        pass
                    except OSError as e:
                        if getattr(e, "errno", None) != errno.ESRCH:
                            raise
                    finally:
                        instance.status = AgentStatus.STOPPED
                        instance.pid = None
                        instance.process = None
            except Exception as e:
                instance = self.agents.get(agent_id)
                process = instance.process if instance else None
                logger.error(
                    "Error stopping agent %s during shutdown: %s | status=%s pid=%s returncode=%s",
                    agent_id,
                    e,
                    instance.status.value if instance else "unknown",
                    instance.pid if instance else None,
                    getattr(process, "returncode", None),
                )

        logger.info("AgentController shutdown complete")


# Singleton Instance
agent_controller = AgentController()
