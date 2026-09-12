"""
Mesh AI Coordinator Service
============================

Koordiniert mehrere AI Agents in einem Mesh-Netzwerk mit:
- Gemini als Lead Coordinator
- Mesh AI Workers (Claude, Codex, DeepSeek, etc.)
- Zentraler MCP Command Queue
- Pre-Implementation Research & KI-Umfrage
- Filtered MCP Execution (nur über TriForce Server)

Version: 1.0.0
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from app.paths import TRISTAR_DIR

logger = logging.getLogger("ailinux.mesh_coordinator")

# TriForce paths
TRIFORCE_BASE = TRISTAR_DIR
QUEUE_DIR = TRIFORCE_BASE / "queue"
MEMORY_DIR = TRIFORCE_BASE / "memory"
PROMPTS_DIR = TRIFORCE_BASE / "prompts"


class AgentRole(str, Enum):
    """Rollen im Mesh AI System"""
    LEAD = "lead"           # Koordinator (Gemini)
    WORKER = "worker"       # Ausführende (Claude, Codex, DeepSeek)
    REVIEWER = "reviewer"   # Code-Reviewer (Mistral, Cogito)
    RESEARCHER = "researcher"  # Recherche-Spezialisten


class TaskPhase(str, Enum):
    """Phasen einer Aufgabe"""
    RECEIVED = "received"
    RESEARCHING = "researching"
    POLLING = "polling"         # KI-Umfrage
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class MeshAgent:
    """Ein Agent im Mesh-Netzwerk"""
    id: str
    name: str
    role: AgentRole
    model: str  # z.B. "gemini/gemini-2.0-flash"
    capabilities: Set[str] = field(default_factory=set)
    available: bool = True
    current_task: Optional[str] = None
    mcp_filtered: bool = True  # MCP Commands werden gefiltert

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role.value,
            "model": self.model,
            "capabilities": list(self.capabilities),
            "available": self.available,
            "current_task": self.current_task,
            "mcp_filtered": self.mcp_filtered,
        }


@dataclass
class MeshTask:
    """Eine Aufgabe im Mesh-System"""
    id: str
    title: str
    description: str
    phase: TaskPhase = TaskPhase.RECEIVED
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    research_results: List[Dict[str, Any]] = field(default_factory=list)
    poll_responses: Dict[str, str] = field(default_factory=dict)
    implementation_plan: Optional[str] = None
    assigned_workers: List[str] = field(default_factory=list)
    review_status: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "phase": self.phase.value,
            "created_at": self.created_at,
            "research_results": self.research_results,
            "poll_responses": self.poll_responses,
            "implementation_plan": self.implementation_plan,
            "assigned_workers": self.assigned_workers,
            "review_status": self.review_status,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class MCPCommand:
    """Ein gefilterter MCP Command"""
    id: str
    source_agent: str
    command: str
    params: Dict[str, Any]
    priority: int = 2  # 0=critical, 1=high, 2=normal, 3=low
    status: str = "pending"  # pending, executing, completed, failed
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    result: Optional[Any] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_agent": self.source_agent,
            "command": self.command,
            "params": self.params,
            "priority": self.priority,
            "status": self.status,
            "created_at": self.created_at,
            "result": self.result,
            "error": self.error,
        }


class MeshCoordinator:
    """
    Zentraler Koordinator für das Mesh AI System.

    Architektur:
    - Gemini Lead: Koordiniert, recherchiert, entscheidet
    - Mesh Workers: Führen Tasks aus (MCP gefiltert)
    - TriForce Server: Führt MCP Commands aus
    """

    def __init__(self):
        self._agents: Dict[str, MeshAgent] = {}
        self._tasks: Dict[str, MeshTask] = {}
        self._mcp_queue: List[MCPCommand] = []
        self._lock = asyncio.Lock()
        self._running = False
        self._mcp_processor_task: Optional[asyncio.Task] = None

        # Initialize default agents
        self._init_default_agents()

        # Ensure directories exist
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    def _init_default_agents(self):
        """Initialisiere Standard-Agents"""
        # Gemini Lead
        self.register_agent(MeshAgent(
            id="gemini-lead",
            name="Gemini Mesh Lead",
            role=AgentRole.LEAD,
            model="gemini/gemini-3.5-flash",
            capabilities={"coordinate", "research", "search", "evaluate", "plan"},
            mcp_filtered=False,  # Lead darf MCP direkt nutzen
        ))

        # Claude Worker
        self.register_agent(MeshAgent(
            id="claude-worker",
            name="Claude Mesh Worker",
            role=AgentRole.WORKER,
            model="anthropic/claude-sonnet-4-6",
            capabilities={"code", "analyze", "document", "test"},
            mcp_filtered=True,
        ))

        # Codex Worker
        self.register_agent(MeshAgent(
            id="codex-worker",
            name="Codex Mesh Worker",
            role=AgentRole.WORKER,
            model="openai/gpt-5.4-mini",
            capabilities={"code", "optimize", "refactor"},
            mcp_filtered=True,
        ))

        # DeepSeek Worker
        self.register_agent(MeshAgent(
            id="deepseek-worker",
            name="DeepSeek Mesh Worker",
            role=AgentRole.WORKER,
            model="openrouter/deepseek/deepseek-chat-v3.1",
            capabilities={"code", "analyze", "debug"},
            mcp_filtered=True,
        ))

        # Mistral Reviewer
        self.register_agent(MeshAgent(
            id="mistral-reviewer",
            name="Mistral Code Reviewer",
            role=AgentRole.REVIEWER,
            model="mistral/mistral-medium-3.5",
            capabilities={"review", "security", "best-practices"},
            mcp_filtered=True,
        ))

    def register_agent(self, agent: MeshAgent):
        """Registriert einen Agent im Mesh"""
        self._agents[agent.id] = agent
        logger.info(f"Mesh Agent registered: {agent.id} ({agent.role.value})")

    async def start(self):
        """Startet den Mesh Coordinator"""
        await self._load_pending_commands()
        self._running = True
        self._mcp_processor_task = asyncio.create_task(self._process_mcp_queue())
        logger.info("Mesh Coordinator started")

    async def _load_pending_commands(self):
        """Lädt ausstehende Commands von Disk"""
        try:
            loaded_cmds = []
            if QUEUE_DIR.exists():
                for f in QUEUE_DIR.glob("*.json"):
                    try:
                        data = json.loads(f.read_text())
                        if data.get("status") == "pending":
                            cmd = MCPCommand(
                                id=data["id"],
                                source_agent=data["source_agent"],
                                command=data["command"],
                                params=data["params"],
                                priority=data.get("priority", 2),
                                status="pending",
                                created_at=data["created_at"],
                            )
                            loaded_cmds.append(cmd)
                    except Exception as e:
                        logger.warning(f"Failed to load queue file {f}: {e}")
            
            # Sort by priority
            loaded_cmds.sort(key=lambda x: x.priority)
            
            async with self._lock:
                self._mcp_queue.extend(loaded_cmds)
                
            logger.info(f"Loaded {len(loaded_cmds)} pending MCP commands from disk")
            
        except Exception as e:
            logger.error(f"Error loading pending commands: {e}")

    async def stop(self):
        """Stoppt den Mesh Coordinator"""
        self._running = False
        if self._mcp_processor_task:
            self._mcp_processor_task.cancel()
        logger.info("Mesh Coordinator stopped")

    # =========================================================================
    # Task Management
    # =========================================================================

    async def submit_task(self, title: str, description: str) -> MeshTask:
        """
        Reicht eine neue Aufgabe ein.

        Workflow:
        1. Task erstellen
        2. Automatisch Research starten
        3. KI-Umfrage durchführen
        4. Plan erstellen
        5. Worker zuweisen
        """
        import uuid
        task_id = f"task_{uuid.uuid4().hex[:12]}"

        task = MeshTask(
            id=task_id,
            title=title,
            description=description,
        )

        self._tasks[task_id] = task
        logger.info(f"Task submitted: {task_id} - {title}")

        # Start async processing
        asyncio.create_task(self._process_task(task))

        return task

    async def _process_task(self, task: MeshTask):
        """Verarbeitet eine Aufgabe durch alle Phasen"""
        try:
            # Phase 1: Research
            task.phase = TaskPhase.RESEARCHING
            await self._research_task(task)

            # Phase 2: KI-Umfrage
            task.phase = TaskPhase.POLLING
            await self._poll_agents(task)

            # Phase 3: Planning
            task.phase = TaskPhase.PLANNING
            await self._plan_implementation(task)

            # Phase 4: Implementation
            task.phase = TaskPhase.IMPLEMENTING
            await self._execute_implementation(task)

            # Phase 5: Review
            task.phase = TaskPhase.REVIEWING
            await self._review_implementation(task)

            task.phase = TaskPhase.COMPLETED
            logger.info(f"Task completed: {task.id}")

        except Exception as e:
            task.phase = TaskPhase.FAILED
            task.error = str(e)
            logger.error(f"Task failed: {task.id} - {e}")

    async def _research_task(self, task: MeshTask):
        """
        Führt Internet-Recherche vor Implementierung durch.
        Nutzt Gemini Lead für Web Search.
        """
        logger.info(f"Researching task: {task.id}")

        try:
            # Web Search über TriForce API
            from .gemini_access import gemini_access_point

            # Recherche-Query erstellen
            query = f"Best practices implementation: {task.title} - {task.description[:200]}"

            # Gemini Research durchführen
            research = await gemini_access_point.process_request(
                query=query,
                research=True,
                store_findings=True,
                include_context=True,
            )

            task.research_results.append({
                "type": "web_research",
                "query": query,
                "findings": research.get("response", ""),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            logger.info(f"Research completed for task: {task.id}")

        except Exception as e:
            logger.warning(f"Research failed for task {task.id}: {e}")
            task.research_results.append({
                "type": "error",
                "message": str(e),
            })

    async def _poll_agents(self, task: MeshTask):
        """
        Führt KI-Umfrage bei allen relevanten Agents durch.
        Sammelt Meinungen zur besten Implementierungsstrategie.
        """
        logger.info(f"Polling agents for task: {task.id}")

        poll_question = f"""
Aufgabe: {task.title}

Beschreibung: {task.description}

Recherche-Ergebnisse:
{json.dumps(task.research_results, indent=2, ensure_ascii=False)[:2000]}

Frage: Wie würdest du diese Aufgabe implementieren?
Gib eine kurze Empfehlung (max 200 Wörter).
"""

        # Poll all worker agents
        for agent_id, agent in self._agents.items():
            if agent.role in [AgentRole.WORKER, AgentRole.REVIEWER]:
                try:
                    response = await self._query_agent(agent, poll_question)
                    task.poll_responses[agent_id] = response
                except Exception as e:
                    task.poll_responses[agent_id] = f"[Error: {e}]"

        logger.info(f"Poll completed for task: {task.id} - {len(task.poll_responses)} responses")

    async def _query_agent(self, agent: MeshAgent, question: str) -> str:
        """Fragt einen Agent und erhält Antwort"""
        try:
            from . import chat as chat_service
            from .model_registry import registry

            model = await registry.get_model(agent.model)
            if not model:
                raise ValueError(f"Model not available in registry: {agent.model}")

            messages = [{"role": "user", "content": question}]
            chunks: List[str] = []
            async for chunk in chat_service.stream_chat(
                model,
                agent.model,
                iter(messages),
                stream=True,
                temperature=0.2,
            ):
                if isinstance(chunk, dict):
                    chunks.append(str(chunk.get("content") or chunk.get("text") or ""))
                else:
                    chunks.append(str(chunk))

            return "".join(chunks).strip()

        except Exception as e:
            logger.error(f"Failed to query agent {agent.id}: {e}")
            return f"[Error: {e}]"

    async def _plan_implementation(self, task: MeshTask):
        """
        Gemini Lead erstellt Implementierungsplan basierend auf
        Research und KI-Umfrage.
        """
        logger.info(f"Planning implementation for task: {task.id}")

        lead = self._agents.get("gemini-lead")
        if not lead:
            task.error = "No lead agent available"
            return

        plan_prompt = f"""
Als Lead Coordinator, erstelle einen Implementierungsplan.

## Aufgabe
{task.title}: {task.description}

## Recherche-Ergebnisse
{json.dumps(task.research_results, indent=2, ensure_ascii=False)[:2000]}

## KI-Umfrage Ergebnisse
{json.dumps(task.poll_responses, indent=2, ensure_ascii=False)[:2000]}

## Erstelle einen Plan mit:
1. Zusammenfassung der besten Ansätze aus der Umfrage
2. Konkrete Implementierungsschritte
3. Zuweisung an Worker (claude-worker, codex-worker, deepseek-worker)
4. Review-Anforderungen

Format: Strukturierter Plan als Text.
"""

        try:
            plan = await self._query_agent(lead, plan_prompt)
            task.implementation_plan = plan

            # Assign workers basierend auf Plan
            # Einfache Heuristik: Alle Worker zuweisen
            task.assigned_workers = ["claude-worker", "codex-worker"]

        except Exception as e:
            logger.error(f"Planning failed for task {task.id}: {e}")
            task.error = str(e)

    async def _execute_implementation(self, task: MeshTask):
        """
        Worker führen die Implementierung durch.
        MCP Commands werden gefiltert und über Queue ausgeführt.
        """
        logger.info(f"Executing implementation for task: {task.id}")

        for worker_id in task.assigned_workers:
            worker = self._agents.get(worker_id)
            if not worker or not worker.available:
                continue

            worker.current_task = task.id
            worker.available = False

            try:
                impl_prompt = f"""
## Implementiere folgende Aufgabe

{task.title}

## Plan
{task.implementation_plan}

## Wichtig
- Nutze die MCP Queue für externe Operationen
- Schreibe sauberen, dokumentierten Code
- Speichere Erkenntnisse in Memory
"""

                result = await self._query_agent(worker, impl_prompt)

                if task.result is None:
                    task.result = {}
                task.result[worker_id] = result

            finally:
                worker.current_task = None
                worker.available = True

    async def _review_implementation(self, task: MeshTask):
        """Reviewer prüfen die Implementierung"""
        logger.info(f"Reviewing implementation for task: {task.id}")

        reviewers = [a for a in self._agents.values() if a.role == AgentRole.REVIEWER]

        for reviewer in reviewers:
            if not reviewer.available:
                continue

            review_prompt = f"""
## Code Review für: {task.title}

## Implementierungsergebnisse
{json.dumps(task.result, indent=2, ensure_ascii=False)[:3000]}

## Prüfe:
1. Code-Qualität
2. Security Issues
3. Best Practices
4. Performance

Gib ein kurzes Review mit Status: APPROVED / CHANGES_REQUESTED / REJECTED
"""

            try:
                review = await self._query_agent(reviewer, review_prompt)
                task.review_status = review
                break  # Nur ein Review nötig
            except Exception as e:
                logger.error(f"Review failed: {e}")

    # =========================================================================
    # MCP Command Queue
    # =========================================================================

    async def enqueue_mcp_command(
        self,
        source_agent: str,
        command: str,
        params: Dict[str, Any],
        priority: int = 2,
    ) -> MCPCommand:
        """
        Fügt einen MCP Command zur Queue hinzu.

        Mesh AI Workers können keine MCP Commands direkt ausführen.
        Stattdessen werden sie hier gequeued und vom TriForce Server
        ausgeführt.
        """
        import uuid

        cmd = MCPCommand(
            id=f"mcp_{uuid.uuid4().hex[:12]}",
            source_agent=source_agent,
            command=command,
            params=params,
            priority=priority,
        )

        async with self._lock:
            self._mcp_queue.append(cmd)
            # Sort by priority
            self._mcp_queue.sort(key=lambda x: x.priority)

        logger.debug(f"MCP Command queued: {cmd.command} from {source_agent}")

        # Persist to disk
        await self._persist_mcp_command(cmd)

        return cmd

    async def _persist_mcp_command(self, cmd: MCPCommand):
        """Speichert MCP Command auf Disk für TriForce Server"""
        queue_file = QUEUE_DIR / f"{cmd.id}.json"
        queue_file.write_text(json.dumps(cmd.to_dict(), indent=2))

    async def _process_mcp_queue(self):
        """
        Background Task: Verarbeitet MCP Commands aus der Queue.
        Führt sie über den TriForce Server aus.
        """
        from ..routes.mcp_remote import TOOL_HANDLERS

        while self._running:
            try:
                async with self._lock:
                    if not self._mcp_queue:
                        await asyncio.sleep(0.5)
                        continue

                    cmd = self._mcp_queue.pop(0)

                cmd.status = "executing"
                logger.info(f"Executing MCP Command: {cmd.command}")

                try:
                    handler = TOOL_HANDLERS.get(cmd.command)
                    if handler:
                        result = await handler(cmd.params)
                        cmd.result = result
                        cmd.status = "completed"
                    else:
                        cmd.error = f"Unknown command: {cmd.command}"
                        cmd.status = "failed"

                except Exception as e:
                    cmd.error = str(e)
                    cmd.status = "failed"
                    logger.error(f"MCP Command failed: {cmd.command} - {e}")

                # Update persisted file
                await self._persist_mcp_command(cmd)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"MCP Queue processor error: {e}")
                await asyncio.sleep(1)

    # =========================================================================
    # Status & Info
    # =========================================================================

    def get_status(self) -> Dict[str, Any]:
        """Gibt den aktuellen Status des Mesh Systems zurück"""
        return {
            "running": self._running,
            "agents": {
                agent_id: agent.to_dict()
                for agent_id, agent in self._agents.items()
            },
            "active_tasks": len([t for t in self._tasks.values() if t.phase not in [TaskPhase.COMPLETED, TaskPhase.FAILED]]),
            "mcp_queue_size": len(self._mcp_queue),
            "tasks": {
                task_id: task.to_dict()
                for task_id, task in list(self._tasks.items())[-10:]  # Last 10
            },
        }

    def get_task(self, task_id: str) -> Optional[MeshTask]:
        """Holt einen Task by ID"""
        return self._tasks.get(task_id)

    def get_agent(self, agent_id: str) -> Optional[MeshAgent]:
        """Holt einen Agent by ID"""
        return self._agents.get(agent_id)


# Singleton instance
mesh_coordinator = MeshCoordinator()


# ============================================================================
# Convenience Functions
# ============================================================================

async def submit_mesh_task(title: str, description: str) -> MeshTask:
    """Reicht eine Aufgabe beim Mesh Coordinator ein"""
    return await mesh_coordinator.submit_task(title, description)


async def queue_mcp_command(
    source: str,
    command: str,
    params: Dict[str, Any],
    priority: int = 2,
) -> MCPCommand:
    """Queued einen MCP Command (für gefilterte Mesh AIs)"""
    return await mesh_coordinator.enqueue_mcp_command(
        source,
        command,
        params,
        priority=priority,
    )


def get_mesh_status() -> Dict[str, Any]:
    """Gibt Mesh System Status zurück"""
    return mesh_coordinator.get_status()
