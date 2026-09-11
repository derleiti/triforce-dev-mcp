"""
Agent Bootstrap Service
========================

Automatisches Initialisieren und Aufwecken von CLI Agents beim Server-Start.

Features:
- Bootstrap-Sequenz: Lead zuerst, dann Worker parallel
- Agent Wakeup via MCP Request
- Chat-Filter für Shortcode-Extraktion
- Command Execution mit Rate Limiting und Whitelist

Basierend auf KI-Umfrage Ergebnissen:
- Gemini Lead: Parallel-Start, HTTP Init, Regex-Filter
- Mistral Reviewer: Whitelist, Rate-Limiting, Logging

Version: 1.0.0
"""

import asyncio
import logging
import os
import re
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("ailinux.agent_bootstrap")


# ============================================================================
# CONFIGURATION
# ============================================================================

# Bootstrap-Reihenfolge (Lead zuerst)
BOOTSTRAP_ORDER = [
    "gemini-mcp",   # Lead - koordiniert andere
    "claude-mcp",   # Worker - Code
    "codex-mcp",    # Worker - Code-Spezialist
    "mistral-mcp",  # Worker - Mistral reasoning/coding via AICoder
    "opencode-mcp", # Worker - Alternative
]

# Ein Agent gilt als erfolgreich gebootet, wenn er gestartet ODER (im
# on-demand-Modus) als 'initialized' markiert wurde. FIX 2026-07-11:
# _start_and_init_agent liefert "initialized", der Zaehler pruefte aber nur
# "success" -> meldete faelschlich 0/4 + Phantom "Lead agent failed".
BOOTSTRAP_OK_STATES = {"success", "initialized", "already_initialized"}

# Rate Limiting Konfiguration
RATE_LIMIT_CONFIG = {
    "per_minute": 60,
    "per_hour": 1000,
    "burst": 10,
    "burst_window_seconds": 5,
}

# Command Whitelist (ohne Bestätigung ausführbar)
SAFE_COMMANDS = {
    "code", "gen", "generate", "query", "search", "review",
    "analyze", "explain", "summarize", "sum", "test", "init",
    "status", "memory", "mem",
}

# Commands die Bestätigung brauchen
CONFIRM_COMMANDS = {
    "exec", "execute", "delete", "rm", "config", "shutdown",
    "restart", "deploy", "push",
}

# Verbotene Commands
BLOCKED_COMMANDS = {
    "rm -rf", "sudo", "eval", "curl |", "wget |",
}


# ============================================================================
# SHORTCODE FILTER
# ============================================================================

@dataclass
class ExtractedCommand:
    """Extrahierter Command aus Agent-Output"""
    raw: str
    source_agent: Optional[str] = None
    target_agent: Optional[str] = None
    action: Optional[str] = None
    content: str = ""
    flow: str = "send"  # send, return, chain, final
    priority: str = "normal"
    tags: List[str] = field(default_factory=list)
    variables: Dict[str, str] = field(default_factory=dict)
    requires_confirmation: bool = False
    is_blocked: bool = False
    block_reason: Optional[str] = None


class ShortcodeFilter:
    """
    Filtert Agent-Output nach Shortcodes und extrahiert Commands.

    Regex-basiert für Performance (Empfehlung Gemini Lead).
    Mit Validierung gegen Injection (Empfehlung Mistral Reviewer).
    """

    # Agent Aliase
    AGENT_ALIASES = {
        "@c": "claude-mcp",
        "@g": "gemini-mcp",
        "@x": "codex-mcp",
        "@m": "mistral-mcp",
        "@d": "deepseek-mcp",
        "@n": "nova-mcp",
        "@o": "opencode-mcp",
        "@mcp": "mcp-server",
        "@*": "broadcast",
    }

    # Action Aliase
    ACTION_ALIASES = {
        "!g": "generate", "!gen": "generate",
        "!c": "code", "!code": "code",
        "!r": "review", "!review": "review",
        "!s": "search", "!search": "search",
        "!f": "fix", "!fix": "fix",
        "!a": "analyze", "!analyze": "analyze",
        "!x": "execute", "!exec": "execute",
        "!t": "test", "!test": "test",
        "!e": "explain", "!explain": "explain",
        "!m": "memory", "!mem": "memory",
        "!q": "query", "!query": "query",
        "!sum": "summarize",
        "!init": "init",
        "!d": "delegate", "!delegate": "delegate",
    }

    # Regex Patterns
    # Hauptpattern: @agent>@agent !action "content" =[var] #tag !!
    SHORTCODE_PATTERN = re.compile(
        r'(@[a-z*]+)'                    # Source/Target Agent
        r'([><]+|<<|>>|\|)?'             # Flow Symbol
        r'(@[a-z*]+)?'                   # Optional Target Agent
        r'\s*'
        r'(![\w]+)?'                     # Action
        r'\s*'
        r'(?:"([^"]*)")?'                # Quoted Content
        r'\s*'
        r'(=[a-zA-Z_]\w*)?'              # Output Variable
        r'\s*'
        r'(@\[[a-zA-Z_]\w*\])?'          # Input Variable
        r'\s*'
        r'((?:#\w+\s*)*)?'               # Tags
        r'(!!!|!!|~)?'                   # Priority
        r'\s*'
        r'(\[outputtoken\]|\[result\]|\[prompt\])?',  # Capture
        re.IGNORECASE
    )

    # Einfacheres Pattern für schnelle Erkennung
    QUICK_PATTERN = re.compile(r'@[a-z*]+[><]+@?[a-z*]*\s*!?\w*', re.IGNORECASE)

    # Injection Pattern (verbotene Zeichen)
    INJECTION_PATTERN = re.compile(r'[;&|`$()]')

    def __init__(self):
        self._cache: Dict[str, List[ExtractedCommand]] = {}

    def has_shortcode(self, text: str) -> bool:
        """Schnelle Prüfung ob Text Shortcodes enthält"""
        return bool(self.QUICK_PATTERN.search(text))

    def extract_commands(self, text: str, source_context: Optional[str] = None) -> List[ExtractedCommand]:
        """
        Extrahiert alle Shortcode Commands aus Text.

        Args:
            text: Agent-Output Text
            source_context: Optional Kontext (Agent ID)

        Returns:
            Liste von ExtractedCommand
        """
        if not text or not self.has_shortcode(text):
            return []

        # Cache Check
        cache_key = f"{source_context}:{hash(text)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        commands = []

        # Finde alle Shortcode Matches
        for match in self.SHORTCODE_PATTERN.finditer(text):
            cmd = self._parse_match(match, source_context)
            if cmd:
                # Sicherheitsvalidierung
                cmd = self._validate_command(cmd)
                commands.append(cmd)

        # Cache speichern (max 1000 Einträge)
        if len(self._cache) > 1000:
            self._cache.clear()
        self._cache[cache_key] = commands

        return commands

    def _parse_match(self, match: re.Match, source_context: Optional[str]) -> Optional[ExtractedCommand]:
        """Parst einen Regex Match zu ExtractedCommand"""
        groups = match.groups()

        raw = match.group(0)
        agent1 = groups[0]  # @c, @g, etc.
        flow_symbol = groups[1] or ">"
        agent2 = groups[2]  # Optional second agent
        action_raw = groups[3]  # !code, !gen, etc.
        content = groups[4] or ""
        output_var = groups[5]
        input_var = groups[6]
        tags_raw = groups[7] or ""
        priority_raw = groups[8]
        capture = groups[9]

        # Resolve Agent Aliase
        source = self.AGENT_ALIASES.get(agent1.lower())
        target = self.AGENT_ALIASES.get(agent2.lower()) if agent2 else None

        # Flow bestimmen
        flow_map = {
            ">": "send",
            ">>": "chain",
            "<": "return",
            "<<": "final",
            "|": "pipe",
        }
        flow = flow_map.get(flow_symbol, "send")

        # Bei Return-Flow: Source und Target tauschen
        if flow in ("return", "final"):
            source, target = target, source

        # Action resolven
        action = None
        if action_raw:
            action = self.ACTION_ALIASES.get(action_raw.lower(), action_raw.lstrip("!").lower())

        # Priority
        priority_map = {"!!!": "critical", "!!": "high", "~": "low"}
        priority = priority_map.get(priority_raw, "normal")

        # Tags parsen
        tags = re.findall(r'#(\w+)', tags_raw) if tags_raw else []

        # Variables
        variables = {}
        if output_var:
            variables["output"] = output_var.lstrip("=")
        if input_var:
            variables["input"] = input_var.strip("@[]")
        if capture:
            variables["capture"] = capture.strip("[]")

        return ExtractedCommand(
            raw=raw,
            source_agent=source or source_context,
            target_agent=target,
            action=action,
            content=content,
            flow=flow,
            priority=priority,
            tags=tags,
            variables=variables,
        )

    def _validate_command(self, cmd: ExtractedCommand) -> ExtractedCommand:
        """Validiert Command gegen Sicherheitsregeln"""

        # Check für Injection
        if self.INJECTION_PATTERN.search(cmd.content):
            cmd.is_blocked = True
            cmd.block_reason = "Potential command injection detected"
            logger.warning(f"Blocked command injection: {cmd.raw}")
            return cmd

        # Check für verbotene Commands
        for blocked in BLOCKED_COMMANDS:
            if blocked.lower() in cmd.content.lower():
                cmd.is_blocked = True
                cmd.block_reason = f"Blocked command pattern: {blocked}"
                logger.warning(f"Blocked dangerous command: {cmd.raw}")
                return cmd

        # Check ob Bestätigung erforderlich
        if cmd.action and cmd.action.lower() in CONFIRM_COMMANDS:
            cmd.requires_confirmation = True

        return cmd


# Singleton
shortcode_filter = ShortcodeFilter()


# ============================================================================
# RATE LIMITER
# ============================================================================

class AgentRateLimiter:
    """
    Token-Bucket Rate Limiter für Agent Commands.

    Empfehlung von Mistral Reviewer:
    - Per-Agent Limits
    - Burst Protection
    - Priorisierung
    """

    def __init__(self, config: Dict[str, int] = None):
        self._config = config or RATE_LIMIT_CONFIG
        self._buckets: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "tokens": self._config["burst"],
            "last_update": time.time(),
            "minute_count": 0,
            "hour_count": 0,
            "minute_reset": time.time(),
            "hour_reset": time.time(),
            "violations": 0,
        })
        self._lock = asyncio.Lock()

    async def check_rate_limit(self, agent_id: str, priority: str = "normal") -> Tuple[bool, Optional[str]]:
        """
        Prüft ob Agent noch Commands ausführen darf.

        Args:
            agent_id: Agent ID
            priority: Command Priorität (critical bypasses limits)

        Returns:
            (allowed: bool, reason: Optional[str])
        """
        # Critical Priority bypasses Rate Limits
        if priority == "critical":
            return True, None

        async with self._lock:
            bucket = self._buckets[agent_id]
            now = time.time()

            # Minute Counter Reset
            if now - bucket["minute_reset"] > 60:
                bucket["minute_count"] = 0
                bucket["minute_reset"] = now

            # Hour Counter Reset
            if now - bucket["hour_reset"] > 3600:
                bucket["hour_count"] = 0
                bucket["hour_reset"] = now

            # Token Bucket Refill
            elapsed = now - bucket["last_update"]
            refill = elapsed / self._config["burst_window_seconds"] * self._config["burst"]
            bucket["tokens"] = min(self._config["burst"], bucket["tokens"] + refill)
            bucket["last_update"] = now

            # Check Limits
            if bucket["minute_count"] >= self._config["per_minute"]:
                bucket["violations"] += 1
                return False, f"Rate limit exceeded: {self._config['per_minute']}/minute"

            if bucket["hour_count"] >= self._config["per_hour"]:
                bucket["violations"] += 1
                return False, f"Rate limit exceeded: {self._config['per_hour']}/hour"

            if bucket["tokens"] < 1:
                return False, "Burst limit exceeded, please slow down"

            # Consume Token
            bucket["tokens"] -= 1
            bucket["minute_count"] += 1
            bucket["hour_count"] += 1

            return True, None

    def get_stats(self, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """Gibt Rate Limit Stats zurück"""
        if agent_id:
            bucket = self._buckets.get(agent_id, {})
            return {
                "agent_id": agent_id,
                "tokens_remaining": bucket.get("tokens", 0),
                "minute_count": bucket.get("minute_count", 0),
                "hour_count": bucket.get("hour_count", 0),
                "violations": bucket.get("violations", 0),
            }

        return {
            "agents": {
                aid: {
                    "tokens": b["tokens"],
                    "minute_count": b["minute_count"],
                    "violations": b["violations"],
                }
                for aid, b in self._buckets.items()
            },
            "config": self._config,
        }


# Singleton
rate_limiter = AgentRateLimiter()


# ============================================================================
# COMMAND EXECUTOR
# ============================================================================

class CommandExecutor:
    """
    Führt extrahierte Commands aus.

    Features:
    - Whitelist-Validierung
    - Rate Limiting
    - Audit Logging
    - Callback für Ergebnisse
    """

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._pending_confirmations: Dict[str, ExtractedCommand] = {}
        self._execution_log: List[Dict[str, Any]] = []

    def register_handler(self, action: str, handler: Callable):
        """Registriert einen Handler für eine Action"""
        self._handlers[action.lower()] = handler

    async def execute(
        self,
        command: ExtractedCommand,
        callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        Führt einen Command aus.

        Args:
            command: Der auszuführende Command
            callback: Optional Callback für Ergebnis

        Returns:
            Execution Result
        """
        result = {
            "command": command.raw,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": command.source_agent,
            "target": command.target_agent,
            "action": command.action,
            "status": "pending",
        }

        # Blocked Check
        if command.is_blocked:
            result["status"] = "blocked"
            result["error"] = command.block_reason
            self._log_execution(result)
            return result

        # Rate Limit Check
        allowed, reason = await rate_limiter.check_rate_limit(
            command.source_agent or "unknown",
            command.priority
        )
        if not allowed:
            result["status"] = "rate_limited"
            result["error"] = reason
            self._log_execution(result)
            return result

        # Confirmation Check
        if command.requires_confirmation:
            confirm_id = f"confirm_{int(time.time() * 1000)}"
            self._pending_confirmations[confirm_id] = command
            result["status"] = "awaiting_confirmation"
            result["confirmation_id"] = confirm_id
            result["message"] = f"Command requires confirmation. Call confirm_command('{confirm_id}')"
            self._log_execution(result)
            return result

        # Whitelist Check für Action
        if command.action and command.action.lower() not in SAFE_COMMANDS:
            if command.action.lower() not in self._handlers:
                result["status"] = "rejected"
                result["error"] = f"Action '{command.action}' not in whitelist"
                self._log_execution(result)
                return result

        # Execute
        try:
            handler = self._handlers.get(command.action.lower()) if command.action else None

            if handler:
                exec_result = await handler(command)
                result["status"] = "success"
                result["result"] = exec_result
            else:
                # Default: Queue für MCP Processing
                result["status"] = "queued"
                result["message"] = "Command queued for MCP processing"

            # Callback
            if callback:
                await callback(result)

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            logger.error(f"Command execution error: {e}")

        self._log_execution(result)
        return result

    async def confirm_command(self, confirm_id: str) -> Dict[str, Any]:
        """Bestätigt einen ausstehenden Command"""
        command = self._pending_confirmations.pop(confirm_id, None)
        if not command:
            return {"status": "error", "error": "Confirmation not found or expired"}

        # Entferne Confirmation Flag und führe aus
        command.requires_confirmation = False
        return await self.execute(command)

    def _log_execution(self, result: Dict[str, Any]):
        """Loggt Execution für Audit"""
        self._execution_log.append(result)

        # Max 10000 Einträge behalten
        if len(self._execution_log) > 10000:
            self._execution_log = self._execution_log[-5000:]

        # Logger Output
        status = result.get("status", "unknown")
        if status in ("blocked", "error", "rejected"):
            logger.warning(f"Command {status}: {result}")
        else:
            logger.debug(f"Command {status}: {result.get('command', '')[:50]}")

    def get_execution_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Gibt Execution Log zurück"""
        return self._execution_log[-limit:]


# Singleton
command_executor = CommandExecutor()


# ============================================================================
# AGENT BOOTSTRAP SERVICE
# ============================================================================

class AgentBootstrapService:
    """
    Bootstrap-Service für CLI Agents.

    Startet Agents beim Server-Start und pusht /init.
    """

    def __init__(self):
        self._initialized_agents: Set[str] = set()
        self._boot_start: Optional[float] = None
        self._boot_complete: bool = False
        self._init_results: Dict[str, Dict[str, Any]] = {}

    async def bootstrap_all(self, sequential_lead: bool = True) -> Dict[str, Any]:
        """
        Startet alle Agents.

        Args:
            sequential_lead: Lead (Gemini) zuerst starten

        Returns:
            Bootstrap Result
        """
        self._boot_start = time.time()
        results = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "agents": {},
            "errors": [],
        }

        logger.info("Starting Agent Bootstrap...")

        try:
            from .tristar.agent_controller import agent_controller

            if sequential_lead:
                # Lead zuerst
                lead_agent = BOOTSTRAP_ORDER[0]
                logger.info(f"Starting Lead Agent: {lead_agent}")
                lead_result = await self._start_and_init_agent(lead_agent)
                results["agents"][lead_agent] = lead_result

                if lead_result.get("status") not in BOOTSTRAP_OK_STATES:
                    results["errors"].append(f"Lead agent failed: {lead_result.get('error')}")

                # Dann Worker parallel
                worker_tasks = [
                    self._start_and_init_agent(agent_id)
                    for agent_id in BOOTSTRAP_ORDER[1:]
                ]
                worker_results = await asyncio.gather(*worker_tasks, return_exceptions=True)

                for agent_id, result in zip(BOOTSTRAP_ORDER[1:], worker_results):
                    if isinstance(result, Exception):
                        results["agents"][agent_id] = {"status": "error", "error": str(result)}
                        results["errors"].append(f"{agent_id}: {result}")
                    else:
                        results["agents"][agent_id] = result
            else:
                # Alle parallel
                tasks = [
                    self._start_and_init_agent(agent_id)
                    for agent_id in BOOTSTRAP_ORDER
                ]
                all_results = await asyncio.gather(*tasks, return_exceptions=True)

                for agent_id, result in zip(BOOTSTRAP_ORDER, all_results):
                    if isinstance(result, Exception):
                        results["agents"][agent_id] = {"status": "error", "error": str(result)}
                    else:
                        results["agents"][agent_id] = result

            self._boot_complete = True
            results["completed_at"] = datetime.now(timezone.utc).isoformat()
            results["duration_seconds"] = time.time() - self._boot_start
            results["success_count"] = sum(
                1 for r in results["agents"].values()
                if r.get("status") in BOOTSTRAP_OK_STATES
            )

            logger.info(f"Bootstrap complete: {results['success_count']}/{len(BOOTSTRAP_ORDER)} agents")

        except Exception as e:
            logger.error(f"Bootstrap failed: {e}")
            results["errors"].append(str(e))

        return results

    async def _start_and_init_agent(self, agent_id: str) -> Dict[str, Any]:
        """Prüft ob ein Agent verfügbar ist und markiert ihn als ready.
        
        CLI Tools wie claude/codex/gemini sind nicht für persistente Prozesse designed.
        Sie werden on-demand gestartet wenn ein Call kommt.
        Der Bootstrap prüft nur die Verfügbarkeit.
        """
        result = {
            "agent_id": agent_id,
            "status": "pending",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            from .tristar.agent_controller import agent_controller

            # Prüfe ob der Agent konfiguriert ist
            agent_info = await agent_controller.get_agent(agent_id)
            if not agent_info:
                result["status"] = "not_configured"
                result["error"] = f"Agent {agent_id} not found in configuration"
                return result

            # Prüfe ob das Binary/Command existiert
            config = agent_info.get("config", {})
            command = config.get("command", [])
            if command:
                binary = command[0]
                import shutil
                if not shutil.which(binary) and not os.path.exists(binary):
                    result["status"] = "binary_not_found"
                    result["error"] = f"Binary not found: {binary}"
                    return result

            # Native AICoder profiles do not need a persistent subprocess, but
            # they do need their isolated HOME/profile prepared once. Marking
            # them READY makes agents() reflect actual callability after boot.
            if str(agent_info.get("runtime") or "") == "aicoder":
                prepared = await agent_controller.start_agent(agent_id)
                if prepared.get("status") not in {"ready", "already_running"}:
                    result["status"] = "error"
                    result["error"] = prepared.get("error") or "AICoder profile preparation failed"
                    return result
                result["status"] = "initialized"
                result["runtime_status"] = "ready"
                result["note"] = "AICoder profile ready for on-demand calls"
            else:
                result["status"] = "initialized"
                result["note"] = "CLI agent ready for on-demand calls"
            self._initialized_agents.add(agent_id)
            result["init_pushed"] = True
            
            self._init_results[agent_id] = result
            logger.info(f"Agent {agent_id} marked as ready (on-demand mode)")

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            logger.error(f"Failed to check agent {agent_id}: {e}")

        return result

    async def _push_init(self, agent_id: str) -> Dict[str, Any]:
        """Pusht /init an einen Agent via MCP Call"""
        try:
            from .tristar.agent_controller import agent_controller

            # Shortcode für Init
            init_shortcode = f"@mcp>@{agent_id.split('-')[0][0]} !init"

            # Nachricht an Agent senden
            call_result = await agent_controller.call_agent(
                agent_id=agent_id,
                message=f"GET /mcp/init?agent_id={agent_id}&include_docs=true\n\n{init_shortcode}",
                timeout=30,
            )

            return {
                "success": call_result.get("success", False),
                "response_preview": str(call_result.get("output", ""))[:200],
            }

        except Exception as e:
            logger.error(f"Failed to push init to {agent_id}: {e}")
            return {"success": False, "error": str(e)}

    async def wakeup_agent(self, agent_id: str) -> Dict[str, Any]:
        """Weckt einen einzelnen Agent auf"""
        if agent_id in self._initialized_agents:
            return {"status": "already_initialized", "agent_id": agent_id}

        return await self._start_and_init_agent(agent_id)

    def get_status(self) -> Dict[str, Any]:
        """Gibt Bootstrap-Status zurück"""
        return {
            "boot_complete": self._boot_complete,
            "boot_duration": time.time() - self._boot_start if self._boot_start else None,
            "initialized_agents": list(self._initialized_agents),
            "pending_agents": [
                a for a in BOOTSTRAP_ORDER if a not in self._initialized_agents
            ],
            "results": self._init_results,
        }


# Singleton
bootstrap_service = AgentBootstrapService()


# ============================================================================
# CHAT OUTPUT PROCESSOR
# ============================================================================

class AgentChatProcessor:
    """
    Verarbeitet Agent Chat Output.

    - Filtert nach Shortcodes
    - Führt Commands aus
    - Sendet Ergebnisse zurück
    """

    def __init__(self):
        self._filter = shortcode_filter
        self._executor = command_executor
        self._processed_count = 0
        self._command_count = 0

    async def process_output(
        self,
        agent_id: str,
        output: str,
        send_result_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        Verarbeitet Agent Output und führt Commands aus.

        Args:
            agent_id: ID des Agents
            output: Agent Output Text
            send_result_callback: Callback um Ergebnisse zurückzusenden

        Returns:
            Processing Result
        """
        self._processed_count += 1

        result = {
            "agent_id": agent_id,
            "output_length": len(output),
            "commands_found": 0,
            "commands_executed": 0,
            "commands_blocked": 0,
        }

        # Shortcodes extrahieren
        commands = self._filter.extract_commands(output, agent_id)
        result["commands_found"] = len(commands)

        if not commands:
            return result

        self._command_count += len(commands)

        # Commands ausführen
        execution_results = []
        for cmd in commands:
            if cmd.is_blocked:
                result["commands_blocked"] += 1
                continue

            exec_result = await self._executor.execute(
                cmd,
                callback=send_result_callback,
            )
            execution_results.append(exec_result)

            if exec_result.get("status") == "success":
                result["commands_executed"] += 1

        result["execution_results"] = execution_results

        return result

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Processing Stats zurück"""
        return {
            "outputs_processed": self._processed_count,
            "commands_extracted": self._command_count,
            "rate_limiter": rate_limiter.get_stats(),
        }


# Singleton
chat_processor = AgentChatProcessor()


# ============================================================================
# MCP TOOLS
# ============================================================================

BOOTSTRAP_TOOLS = [
    {
        "name": "bootstrap_agents",
        "description": "Startet alle CLI Agents und pusht /init (Lead zuerst)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequential_lead": {
                    "type": "boolean",
                    "default": True,
                    "description": "Lead Agent zuerst starten",
                },
            },
        },
    },
    {
        "name": "wakeup_agent",
        "description": "Weckt einen einzelnen Agent auf",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID (z.B. claude-mcp)",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "bootstrap_status",
        "description": "Gibt Bootstrap-Status zurück",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "process_agent_output",
        "description": "Verarbeitet Agent Output und führt Shortcode Commands aus",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "output": {"type": "string"},
            },
            "required": ["agent_id", "output"],
        },
    },
    {
        "name": "rate_limit_stats",
        "description": "Gibt Rate Limit Statistiken zurück",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Optional: Nur für diesen Agent"},
            },
        },
    },
    {
        "name": "execution_log",
        "description": "Gibt Command Execution Log zurück",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 100},
            },
        },
    },
]


async def handle_bootstrap_agents(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle bootstrap_agents tool"""
    return await bootstrap_service.bootstrap_all(
        sequential_lead=params.get("sequential_lead", True)
    )


async def handle_wakeup_agent(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle wakeup_agent tool"""
    agent_id = params.get("agent_id")
    if not agent_id:
        raise ValueError("'agent_id' is required")
    return await bootstrap_service.wakeup_agent(agent_id)


async def handle_bootstrap_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle bootstrap_status tool"""
    return bootstrap_service.get_status()


async def handle_process_agent_output(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle process_agent_output tool"""
    agent_id = params.get("agent_id")
    output = params.get("output")
    if not agent_id or not output:
        raise ValueError("'agent_id' and 'output' are required")
    return await chat_processor.process_output(agent_id, output)


async def handle_rate_limit_stats(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle rate_limit_stats tool"""
    return rate_limiter.get_stats(params.get("agent_id"))


async def handle_execution_log(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle execution_log tool"""
    return {"log": command_executor.get_execution_log(params.get("limit", 100))}


BOOTSTRAP_HANDLERS = {
    "bootstrap_agents": handle_bootstrap_agents,
    "wakeup_agent": handle_wakeup_agent,
    "bootstrap_status": handle_bootstrap_status,
    "process_agent_output": handle_process_agent_output,
    "rate_limit_stats": handle_rate_limit_stats,
    "execution_log": handle_execution_log,
}
