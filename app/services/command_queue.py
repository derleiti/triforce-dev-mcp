"""
Command Queue Service
=====================

Zentrale Queue für Agenten und Mesh-AIs mit:
- Prioritätsbasierte Aufgabenverteilung
- Load-Balancing (freie/geringste Queue)
- Internet-Recherche-Distribution
- Asynchrone Ausführung

Version: 2.80
"""

import asyncio
import heapq
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from pathlib import Path

from app.paths import TRISTAR_DIR

logger = logging.getLogger("ailinux.command_queue")


class CommandPriority(int, Enum):
    """Prioritätsstufen für Kommandos"""
    CRITICAL = 0  # Sofort ausführen
    HIGH = 1      # Bevorzugt
    NORMAL = 2    # Standard
    LOW = 3       # Hintergrund
    IDLE = 4      # Wenn nichts anderes


class CommandStatus(str, Enum):
    """Status eines Kommandos"""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CommandType(str, Enum):
    """Typen von Kommandos"""
    CHAT = "chat"
    RESEARCH = "research"
    CODE = "code"
    REVIEW = "review"
    SEARCH = "search"
    COORDINATE = "coordinate"
    MEMORY = "memory"
    SYSTEM = "system"


@dataclass(order=True)
class Command:
    """Ein Kommando in der Queue"""
    priority: int
    timestamp: float = field(compare=True)
    id: str = field(compare=False, default_factory=lambda: f"cmd_{uuid.uuid4().hex[:12]}")
    type: CommandType = field(compare=False, default=CommandType.CHAT)
    payload: Dict[str, Any] = field(compare=False, default_factory=dict)
    target_agent: Optional[str] = field(compare=False, default=None)
    status: CommandStatus = field(compare=False, default=CommandStatus.PENDING)
    result: Optional[Dict[str, Any]] = field(compare=False, default=None)
    error: Optional[str] = field(compare=False, default=None)
    created_at: str = field(compare=False, default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = field(compare=False, default=None)
    completed_at: Optional[str] = field(compare=False, default=None)
    assigned_to: Optional[str] = field(compare=False, default=None)
    retries: int = field(compare=False, default=0)
    max_retries: int = field(compare=False, default=3)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "priority": self.priority,
            "type": self.type.value if isinstance(self.type, CommandType) else self.type,
            "status": self.status.value if isinstance(self.status, CommandStatus) else self.status,
            "payload": self.payload,
            "target_agent": self.target_agent,
            "assigned_to": self.assigned_to,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "retries": self.retries,
        }


@dataclass
class AgentStatus:
    """Status eines Agenten"""
    id: str
    name: str
    type: str  # ollama, gemini, claude, etc.
    available: bool = True
    current_command: Optional[str] = None
    queue_size: int = 0
    completed_count: int = 0
    failed_count: int = 0
    avg_response_time: float = 0.0
    capabilities: Set[str] = field(default_factory=set)
    last_active: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "available": self.available,
            "current_command": self.current_command,
            "queue_size": self.queue_size,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "avg_response_time": self.avg_response_time,
            "capabilities": list(self.capabilities),
            "last_active": self.last_active,
        }


class CommandQueue:
    """
    Zentrale Command Queue für alle Agenten.

    Features:
    - Priority Queue (heapq)
    - Load Balancing (least busy agent)
    - Capability-based Routing
    - Automatic Retry
    - Research Distribution
    """

    def __init__(self, max_queue_size: int = 1000, persistence_file: str | Path = TRISTAR_DIR / "queue/state.json"):
        self._queue: List[Command] = []  # Priority heap
        self._commands: Dict[str, Command] = {}  # ID -> Command
        self._agents: Dict[str, AgentStatus] = {}
        self._agent_queues: Dict[str, List[Command]] = defaultdict(list)
        self._max_queue_size = max_queue_size
        self._persistence_file = str(persistence_file)
        self._lock = asyncio.Lock()
        self._workers: Dict[str, asyncio.Task] = {}
        self._running = False

        # Ensure directory exists
        Path(self._persistence_file).parent.mkdir(parents=True, exist_ok=True)

        # Capabilities mapping
        self._capability_map = {
            "research": ["gemini", "kimi", "nova", "claude"],
            "code": ["deepseek", "qwen-coder", "claude", "codex"],
            "review": ["claude", "mistral", "cogito", "codex"],
            "search": ["gemini", "kimi", "nova"],  # Web search
            "chat": ["*"],  # Alle
            "coordinate": ["gemini"],  # Nur Gemini koordiniert
        }
        
        # Load state
        self._load_state()

    def _load_state(self):
        """Load queue state from file"""
        try:
            from pathlib import Path
            path = Path(self._persistence_file)
            if path.exists():
                data = json.loads(path.read_text())
                
                # Restore commands
                for cmd_data in data.get("commands", []):
                    cmd = Command(
                        priority=cmd_data["priority"],
                        timestamp=time.time(), # Reset timestamp to now to keep order relative
                        id=cmd_data["id"],
                        type=CommandType(cmd_data["type"]),
                        payload=cmd_data["payload"],
                        target_agent=cmd_data["target_agent"],
                        status=CommandStatus(cmd_data["status"]),
                        result=cmd_data.get("result"),
                        error=cmd_data.get("error"),
                        created_at=cmd_data["created_at"],
                        started_at=cmd_data.get("started_at"),
                        completed_at=cmd_data.get("completed_at"),
                        assigned_to=cmd_data.get("assigned_to"),
                        retries=cmd_data.get("retries", 0)
                    )
                    self._commands[cmd.id] = cmd
                    
                    # Re-queue pending/queued items
                    if cmd.status in (CommandStatus.PENDING, CommandStatus.QUEUED):
                        heapq.heappush(self._queue, cmd)
                    # Handle running items (likely crashed during run)
                    elif cmd.status == CommandStatus.RUNNING:
                        cmd.status = CommandStatus.QUEUED # Reset to queued
                        heapq.heappush(self._queue, cmd)
                        logger.warning(f"Recovered running command {cmd.id} to queue")

                logger.info(f"Loaded {len(self._commands)} commands from persistence")
        except Exception as e:
            logger.error(f"Failed to load queue state: {e}")

    def _save_state(self):
        """Save queue state to file"""
        try:
            data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "commands": [cmd.to_dict() for cmd in self._commands.values()]
            }
            with open(self._persistence_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save queue state: {e}")

    async def start(self):
        """Start queue processing"""
        self._running = True
        logger.info("Command Queue started")

    async def stop(self):
        """Stop queue processing"""
        self._running = False
        for task in self._workers.values():
            task.cancel()
        logger.info("Command Queue stopped")

    def register_agent(
        self,
        agent_id: str,
        name: str,
        agent_type: str,
        capabilities: Optional[Set[str]] = None,
    ) -> AgentStatus:
        """Registriert einen Agenten in der Queue"""
        if capabilities is None:
            # Default capabilities basierend auf Typ
            capabilities = self._get_default_capabilities(agent_type)

        status = AgentStatus(
            id=agent_id,
            name=name,
            type=agent_type,
            capabilities=capabilities,
        )
        self._agents[agent_id] = status
        logger.info(f"Agent registered: {agent_id} ({agent_type})")
        return status

    def _get_default_capabilities(self, agent_type: str) -> Set[str]:
        """Ermittelt Standard-Capabilities für einen Agent-Typ"""
        caps = {"chat"}
        type_lower = agent_type.lower()

        if "gemini" in type_lower:
            caps.update(["research", "search", "coordinate"])
        elif "claude" in type_lower:
            caps.update(["code", "review", "research"])
        elif "deepseek" in type_lower or "qwen" in type_lower:
            caps.update(["code"])
        elif "kimi" in type_lower or "nova" in type_lower:
            caps.update(["research", "search"])
        elif "mistral" in type_lower or "cogito" in type_lower:
            caps.update(["review"])
        elif "codex" in type_lower:
            caps.update(["code", "review"])

        return caps

    async def enqueue(
        self,
        payload: Dict[str, Any],
        command_type: CommandType = CommandType.CHAT,
        priority: CommandPriority = CommandPriority.NORMAL,
        target_agent: Optional[str] = None,
    ) -> Command:
        """
        Fügt ein Kommando zur Queue hinzu.

        Args:
            payload: Das Kommando-Payload
            command_type: Typ des Kommandos
            priority: Priorität
            target_agent: Spezifischer Agent (optional)

        Returns:
            Das erstellte Command-Objekt
        """
        async with self._lock:
            if len(self._queue) >= self._max_queue_size:
                raise ValueError("Queue is full")

            cmd = Command(
                priority=priority.value,
                timestamp=time.time(),
                type=command_type,
                payload=payload,
                target_agent=target_agent,
                status=CommandStatus.QUEUED,
            )

            heapq.heappush(self._queue, cmd)
            self._commands[cmd.id] = cmd
            
            self._save_state()

            logger.debug(f"Command enqueued: {cmd.id} ({command_type.value})")

            return cmd

    async def dequeue(self, agent_id: Optional[str] = None) -> Optional[Command]:
        """
        Holt das nächste Kommando für einen Agenten.

        Args:
            agent_id: Optional - nur Kommandos für diesen Agenten

        Returns:
            Das nächste Command oder None
        """
        async with self._lock:
            if not self._queue:
                return None

            if agent_id:
                agent = self._agents.get(agent_id)
                if not agent:
                    return None

                # Suche passendes Kommando für Agent
                for i, cmd in enumerate(self._queue):
                    if cmd.target_agent and cmd.target_agent != agent_id:
                        continue
                    if not self._can_handle(agent, cmd):
                        continue

                    # Gefunden - entfernen und zurückgeben
                    self._queue.pop(i)
                    heapq.heapify(self._queue)
                    cmd.status = CommandStatus.RUNNING
                    cmd.assigned_to = agent_id
                    cmd.started_at = datetime.now(timezone.utc).isoformat()
                    agent.current_command = cmd.id
                    agent.available = False
                    self._save_state()
                    return cmd

                return None

            else:
                # Erstes verfügbares Kommando
                cmd = heapq.heappop(self._queue)
                cmd.status = CommandStatus.RUNNING
                cmd.started_at = datetime.now(timezone.utc).isoformat()
                self._save_state()
                return cmd

    def _can_handle(self, agent: AgentStatus, cmd: Command) -> bool:
        """Prüft ob Agent das Kommando bearbeiten kann"""
        if not agent.available:
            return False

        cmd_type = cmd.type.value if isinstance(cmd.type, CommandType) else cmd.type
        required_caps = self._capability_map.get(cmd_type, ["*"])

        if "*" in required_caps:
            return True

        return bool(agent.capabilities & set(required_caps))

    async def complete(
        self,
        command_id: str,
        result: Dict[str, Any],
        success: bool = True,
    ):
        """Markiert ein Kommando als abgeschlossen"""
        async with self._lock:
            cmd = self._commands.get(command_id)
            if not cmd:
                return

            cmd.completed_at = datetime.now(timezone.utc).isoformat()

            if success:
                cmd.status = CommandStatus.COMPLETED
                cmd.result = result
            else:
                cmd.status = CommandStatus.FAILED
                cmd.error = result.get("error", "Unknown error")

                # Retry?
                if cmd.retries < cmd.max_retries:
                    cmd.retries += 1
                    cmd.status = CommandStatus.QUEUED
                    heapq.heappush(self._queue, cmd)
                    logger.warning(f"Command {command_id} failed, retrying ({cmd.retries}/{cmd.max_retries})")

            # Agent freigeben
            if cmd.assigned_to:
                agent = self._agents.get(cmd.assigned_to)
                if agent:
                    agent.current_command = None
                    agent.available = True
                    agent.last_active = datetime.now(timezone.utc).isoformat()
                    if success:
                        agent.completed_count += 1
                    else:
                        agent.failed_count += 1
            
            self._save_state()

    async def get_command(self, command_id: str) -> Optional[Command]:
        """Holt ein Kommando nach ID"""
        return self._commands.get(command_id)

    def get_least_busy_agent(
        self,
        capability: Optional[str] = None,
        exclude: Optional[Set[str]] = None,
    ) -> Optional[AgentStatus]:
        """
        Findet den Agenten mit der geringsten Queue.

        Args:
            capability: Benötigte Capability
            exclude: Agenten ausschließen

        Returns:
            Agent mit geringster Last oder None
        """
        exclude = exclude or set()
        candidates = []

        for agent_id, agent in self._agents.items():
            if agent_id in exclude:
                continue
            if not agent.available:
                continue
            if capability and capability not in agent.capabilities:
                continue

            candidates.append(agent)

        if not candidates:
            return None

        # Sortiere nach Queue-Größe, dann nach avg_response_time
        candidates.sort(key=lambda a: (a.queue_size, a.avg_response_time))
        return candidates[0]

    async def distribute_research(
        self,
        query: str,
        priority: CommandPriority = CommandPriority.NORMAL,
    ) -> List[Command]:
        """
        Verteilt eine Recherche-Aufgabe an verfügbare Agenten.

        Wählt den Agenten mit der geringsten Queue.
        """
        # Finde Agenten mit search/research Capability
        agent = self.get_least_busy_agent(capability="search")

        if not agent:
            agent = self.get_least_busy_agent(capability="research")

        if not agent:
            # Fallback: Nimm irgendeinen verfügbaren
            agent = self.get_least_busy_agent()

        if not agent:
            raise ValueError("No agents available for research")

        cmd = await self.enqueue(
            payload={"query": query, "type": "web_search"},
            command_type=CommandType.SEARCH,
            priority=priority,
            target_agent=agent.id,
        )

        return [cmd]

    async def broadcast_command(
        self,
        payload: Dict[str, Any],
        command_type: CommandType = CommandType.CHAT,
        targets: Optional[List[str]] = None,
    ) -> List[Command]:
        """Sendet Kommando an mehrere Agenten"""
        commands = []

        if targets:
            agent_ids = targets
        else:
            agent_ids = list(self._agents.keys())

        for agent_id in agent_ids:
            cmd = await self.enqueue(
                payload=payload,
                command_type=command_type,
                priority=CommandPriority.NORMAL,
                target_agent=agent_id,
            )
            commands.append(cmd)

        return commands

    def get_queue_stats(self) -> Dict[str, Any]:
        """Gibt Queue-Statistiken zurück"""
        by_status = defaultdict(int)
        by_type = defaultdict(int)
        by_priority = defaultdict(int)

        for cmd in self._commands.values():
            status = cmd.status.value if isinstance(cmd.status, CommandStatus) else cmd.status
            cmd_type = cmd.type.value if isinstance(cmd.type, CommandType) else cmd.type
            by_status[status] += 1
            by_type[cmd_type] += 1
            by_priority[cmd.priority] += 1

        return {
            "total_commands": len(self._commands),
            "queue_size": len(self._queue),
            "by_status": dict(by_status),
            "by_type": dict(by_type),
            "by_priority": dict(by_priority),
            "agents": {
                agent_id: agent.to_dict()
                for agent_id, agent in self._agents.items()
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_agent_stats(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Gibt Statistiken für einen Agenten zurück"""
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        # Zähle Kommandos für diesen Agenten
        pending = sum(1 for cmd in self._queue if cmd.target_agent == agent_id)
        completed = sum(
            1 for cmd in self._commands.values()
            if cmd.assigned_to == agent_id and cmd.status == CommandStatus.COMPLETED
        )

        return {
            **agent.to_dict(),
            "pending_commands": pending,
            "total_completed": completed,
        }


# Singleton instance
command_queue = CommandQueue()


# ============================================================================
# Auto-Register Default Agents
# ============================================================================

def _register_default_agents():
    """Registriert Standard-Agenten"""
    default_agents = [
        ("gemini-lead", "Gemini Lead", "gemini", {"research", "search", "coordinate", "chat"}),
        ("claude-mcp", "Claude MCP", "claude", {"code", "review", "research", "chat"}),
        ("codex-mcp", "Codex MCP", "codex", {"code", "review", "chat"}),
        ("deepseek-worker", "DeepSeek Worker", "deepseek", {"code", "chat"}),
        ("qwen-coder", "Qwen Coder", "qwen", {"code", "chat"}),
        ("kimi-research", "Kimi Research", "kimi", {"research", "search", "chat"}),
        ("mistral-reviewer", "Mistral Reviewer", "mistral", {"review", "chat"}),
        ("nova-research", "Nova Research", "nova", {"research", "search", "chat"}),
        ("cogito-reviewer", "Cogito Reviewer", "cogito", {"review", "chat"}),
    ]

    for agent_id, name, agent_type, caps in default_agents:
        command_queue.register_agent(agent_id, name, agent_type, caps)


# Register on import
_register_default_agents()


# ============================================================================
# MCP Tool Definitions
# ============================================================================

QUEUE_TOOLS = [
    {
        "name": "queue_enqueue",
        "description": "Fügt ein Kommando zur zentralen Queue hinzu",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Das auszuführende Kommando"},
                "type": {
                    "type": "string",
                    "enum": ["chat", "research", "code", "review", "search", "coordinate"],
                    "default": "chat",
                },
                "priority": {
                    "type": "string",
                    "enum": ["critical", "high", "normal", "low", "idle"],
                    "default": "normal",
                },
                "target_agent": {"type": "string", "description": "Ziel-Agent (optional)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "queue_research",
        "description": "Verteilt eine Internet-Recherche an den freien/geringsten-Queue Agent",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchanfrage"},
                "priority": {
                    "type": "string",
                    "enum": ["critical", "high", "normal", "low"],
                    "default": "normal",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "queue_status",
        "description": "Zeigt Queue-Statistiken und Agent-Status",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "queue_get",
        "description": "Holt Status eines Kommandos",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command_id": {"type": "string", "description": "Kommando-ID"},
            },
            "required": ["command_id"],
        },
    },
    {
        "name": "queue_agents",
        "description": "Listet alle registrierten Agenten mit Status",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "queue_broadcast",
        "description": "Sendet Kommando an mehrere Agenten",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Das Kommando"},
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ziel-Agenten (leer = alle)",
                },
            },
            "required": ["command"],
        },
    },
]


# ============================================================================
# MCP Tool Handlers
# ============================================================================

async def handle_queue_enqueue(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle queue.enqueue tool."""
    command = arguments.get("command")
    if not command:
        raise ValueError("'command' is required")

    cmd_type = CommandType(arguments.get("type", "chat"))
    priority_str = arguments.get("priority", "normal")
    priority = CommandPriority[priority_str.upper()]
    target = arguments.get("target_agent")

    cmd = await command_queue.enqueue(
        payload={"command": command},
        command_type=cmd_type,
        priority=priority,
        target_agent=target,
    )

    return {"queued": True, "command": cmd.to_dict()}


async def handle_queue_research(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle queue.research tool."""
    query = arguments.get("query")
    if not query:
        raise ValueError("'query' is required")

    priority_str = arguments.get("priority", "normal")
    priority = CommandPriority[priority_str.upper()]

    commands = await command_queue.distribute_research(query, priority)

    return {
        "distributed": True,
        "query": query,
        "commands": [cmd.to_dict() for cmd in commands],
    }


async def handle_queue_status(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle queue.status tool."""
    return command_queue.get_queue_stats()


async def handle_queue_get(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle queue.get tool."""
    command_id = arguments.get("command_id")
    if not command_id:
        raise ValueError("'command_id' is required")

    cmd = await command_queue.get_command(command_id)
    if not cmd:
        return {"error": f"Command {command_id} not found"}

    return cmd.to_dict()


async def handle_queue_agents(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle queue.agents tool."""
    return {
        "agents": [
            agent.to_dict() for agent in command_queue._agents.values()
        ],
        "count": len(command_queue._agents),
    }


async def handle_queue_broadcast(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle queue.broadcast tool."""
    command = arguments.get("command")
    if not command:
        raise ValueError("'command' is required")

    targets = arguments.get("targets")

    commands = await command_queue.broadcast_command(
        payload={"command": command},
        command_type=CommandType.CHAT,
        targets=targets,
    )

    return {
        "broadcast": True,
        "target_count": len(commands),
        "commands": [cmd.to_dict() for cmd in commands],
    }


QUEUE_HANDLERS = {
    "queue_enqueue": handle_queue_enqueue,
    "queue_research": handle_queue_research,
    "queue_status": handle_queue_status,
    "queue_get": handle_queue_get,
    "queue_agents": handle_queue_agents,
    "queue_broadcast": handle_queue_broadcast,
}
