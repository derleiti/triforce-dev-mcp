# app/services/task_spawner.py
"""
Task Spawner - Spawnt CLI Agents mit temporären API Keys
Agent arbeitet autonom bis Task abgeschlossen

Implementierung für TriForce Backend
Stand: 2025-12-13
"""

import asyncio
import os
import uuid
import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    SPAWNING = "spawning"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentType(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    GEMINI = "gemini"
    OPENCODE = "opencode"


@dataclass
class SpawnedTask:
    """Ein gespawnter Task mit Agent"""
    task_id: str
    agent_type: AgentType
    client_id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    
    # Process Info
    pid: Optional[int] = None
    process: Optional[asyncio.subprocess.Process] = None
    
    # Timing
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    # Result
    output_buffer: List[str] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    # API Keys die verwendet wurden (nur Provider-Namen, nicht Keys!)
    providers_used: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent_type": self.agent_type.value,
            "client_id": self.client_id,
            "description": self.description[:200],
            "status": self.status.value,
            "pid": self.pid,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "providers_used": self.providers_used,
            "output_lines": len(self.output_buffer),
            "error": self.error
        }


class TaskSpawner:
    """
    Spawnt autonome CLI Agents für Tasks
    
    1. Client sendet Task-Request
    2. Server entschlüsselt benötigte API Keys
    3. Server spawnt Agent als Subprocess mit Keys im Environment
    4. Agent arbeitet autonom
    5. Keys werden nach Task aus RAM gelöscht
    """
    
    def __init__(self):
        self.tasks: Dict[str, SpawnedTask] = {}
        self.max_concurrent = 5
        self.running_count = 0
        
        # Agent Commands
        self.agent_commands = {
            AgentType.CLAUDE: {
                "cmd": ["claude", "--print"],
                "providers": ["anthropic"],
                "workdir": "/home/zombie"
            },
            AgentType.CODEX: {
                "cmd": ["codex", "exec", "--full-auto", "-m", "o4-mini"],
                "providers": ["openai"],
                "workdir": "/home/zombie"
            },
            AgentType.GEMINI: {
                # Legacy enum name retained; runtime is Antigravity CLI.
                "cmd": [
                    "/home/zombie/workspace/triforce/triforce/bin/agy-triforce",
                    "--output-format", "text", "--print"
                ],
                "providers": [],  # agy uses the zombie account/keyring session
                "workdir": "/home/zombie"
            },
            AgentType.OPENCODE: {
                "cmd": ["opencode", "run"],
                "providers": ["openai", "anthropic"],
                "workdir": "/home/zombie"
            }
        }
    
    async def spawn_task(
        self,
        client_id: str,
        description: str,
        agent_type: AgentType = AgentType.CLAUDE,
        target_host: Optional[str] = None,
        additional_context: Dict[str, Any] = None
    ) -> SpawnedTask:
        """
        Spawnt einen neuen Task
        
        Args:
            client_id: ID des anfragenden Clients
            description: Was soll der Agent tun?
            agent_type: Welcher Agent?
            target_host: Optional: Remote-Host für SSH-Tasks
            additional_context: Zusätzlicher Kontext für Agent
        """
        if self.running_count >= self.max_concurrent:
            raise RuntimeError(f"Max concurrent tasks ({self.max_concurrent}) reached")
        
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        
        task = SpawnedTask(
            task_id=task_id,
            agent_type=agent_type,
            client_id=client_id,
            description=description
        )
        self.tasks[task_id] = task
        
        # Task async starten
        asyncio.create_task(self._run_task(task, target_host, additional_context))
        
        return task
    
    async def _run_task(
        self, 
        task: SpawnedTask, 
        target_host: Optional[str],
        additional_context: Optional[Dict[str, Any]]
    ):
        """Führt den Task aus"""
        # Import hier um zirkuläre Imports zu vermeiden
        from .api_vault import api_vault
        
        task.status = TaskStatus.SPAWNING
        task.started_at = datetime.now().isoformat()
        self.running_count += 1
        
        try:
            agent_config = self.agent_commands[task.agent_type]
            
            # === API Keys aus Vault holen ===
            task.providers_used = agent_config["providers"]
            temp_env = api_vault.get_temp_env(task.providers_used)
            
            if task.providers_used and not temp_env:
                raise RuntimeError(f"No API keys available for {task.providers_used}")
            
            # === Environment bauen ===
            env = os.environ.copy()
            env.update(temp_env)
            env["AILINUX_TASK_ID"] = task.task_id
            env["AILINUX_CLIENT_ID"] = task.client_id
            
            # === Prompt bauen ===
            prompt = self._build_prompt(task.description, target_host, additional_context)
            
            # === Agent Command ===
            cmd = agent_config["cmd"] + [prompt]
            
            logger.info(f"Spawning {task.agent_type.value} for task {task.task_id}")
            
            # === Subprocess starten ===
            task.status = TaskStatus.RUNNING
            task.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                cwd=agent_config["workdir"]
            )
            task.pid = task.process.pid
            
            # === Output sammeln ===
            while True:
                line = await task.process.stdout.readline()
                if not line:
                    break
                
                decoded = line.decode().strip()
                task.output_buffer.append(decoded)
                
                # Buffer limitieren
                if len(task.output_buffer) > 1000:
                    task.output_buffer = task.output_buffer[-500:]
                
                logger.debug(f"[{task.task_id}] {decoded[:100]}")
            
            # === Warten auf Ende ===
            await task.process.wait()
            
            # === Result verarbeiten ===
            exit_code = task.process.returncode
            
            if exit_code == 0:
                task.status = TaskStatus.COMPLETED
                task.result = {
                    "success": True,
                    "exit_code": exit_code,
                    "output_lines": len(task.output_buffer)
                }
            else:
                task.status = TaskStatus.FAILED
                task.error = f"Agent exited with code {exit_code}"
                task.result = {
                    "success": False,
                    "exit_code": exit_code,
                    "last_output": task.output_buffer[-20:]
                }
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            logger.error(f"Task {task.task_id} failed: {e}")
        
        finally:
            task.completed_at = datetime.now().isoformat()
            task.process = None  # Cleanup
            self.running_count -= 1
            
            # Keys sind automatisch aus dem Environment
            # des Subprocess gelöscht wenn er endet
            logger.info(f"Task {task.task_id} finished: {task.status.value}")
    
    def _build_prompt(
        self, 
        description: str, 
        target_host: Optional[str],
        context: Optional[Dict[str, Any]]
    ) -> str:
        """Baut den Prompt für den Agent"""
        
        parts = [description]
        
        if target_host:
            parts.append(f"\n\nZiel-Host: {target_host}")
            parts.append("Verbinde dich per SSH und führe die Aufgabe dort aus.")
        
        if context:
            if "client_system" in context:
                parts.append(f"\n\nClient-System: {json.dumps(context['client_system'], indent=2)}")
            if "logs" in context:
                parts.append(f"\n\nRelevante Logs:\n{context['logs']}")
        
        parts.append("\n\nArbeite autonom bis die Aufgabe erledigt ist.")
        parts.append("Berichte Fortschritt und Ergebnisse.")
        
        return "\n".join(parts)
    
    # =========================================================================
    # Task Management
    # =========================================================================
    
    def get_task(self, task_id: str) -> Optional[SpawnedTask]:
        return self.tasks.get(task_id)
    
    def get_task_output(self, task_id: str, last_n: int = 50) -> List[str]:
        task = self.get_task(task_id)
        if task:
            return task.output_buffer[-last_n:]
        return []
    
    def list_tasks(self, client_id: str = None, status: TaskStatus = None) -> List[dict]:
        tasks = list(self.tasks.values())
        
        if client_id:
            tasks = [t for t in tasks if t.client_id == client_id]
        if status:
            tasks = [t for t in tasks if t.status == status]
        
        return [t.to_dict() for t in tasks]
    
    async def cancel_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task:
            return False
        
        if task.status == TaskStatus.RUNNING and task.process:
            task.process.terminate()
            await asyncio.sleep(2)
            if task.process.returncode is None:
                task.process.kill()
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now().isoformat()
            return True
        
        return False
    
    def get_stats(self) -> dict:
        """Statistiken"""
        status_counts = {}
        for task in self.tasks.values():
            status_counts[task.status.value] = status_counts.get(task.status.value, 0) + 1
        
        return {
            "total_tasks": len(self.tasks),
            "running": self.running_count,
            "max_concurrent": self.max_concurrent,
            "by_status": status_counts
        }


# Singleton
task_spawner = TaskSpawner()


# =============================================================================
# MCP Tool Handlers
# =============================================================================

async def handle_client_request_task(params: dict) -> dict:
    """Task für Client einreichen"""
    client_id = params.get("client_id", "unknown")
    description = params.get("description")
    agent_type = params.get("agent_type", "claude")
    target_host = params.get("target_host")
    context = params.get("context", {})
    
    if not description:
        return {"error": "description required"}
    
    try:
        agent = AgentType(agent_type)
    except ValueError:
        return {"error": f"Invalid agent_type: {agent_type}. Use: claude, codex, gemini, opencode"}
    
    try:
        task = await task_spawner.spawn_task(
            client_id=client_id,
            description=description,
            agent_type=agent,
            target_host=target_host,
            additional_context=context
        )
        return {
            "success": True,
            "task_id": task.task_id,
            "status": task.status.value,
            "message": f"Task spawned with {agent_type} agent"
        }
    except RuntimeError as e:
        return {"error": str(e)}


async def handle_client_task_status(params: dict) -> dict:
    """Task-Status abfragen"""
    task_id = params.get("task_id")
    if not task_id:
        return {"error": "task_id required"}
    
    task = task_spawner.get_task(task_id)
    if not task:
        return {"error": f"Task not found: {task_id}"}
    
    return task.to_dict()


async def handle_client_task_output(params: dict) -> dict:
    """Task-Output holen"""
    task_id = params.get("task_id")
    last_n = params.get("last_n", 50)
    
    if not task_id:
        return {"error": "task_id required"}
    
    output = task_spawner.get_task_output(task_id, last_n)
    task = task_spawner.get_task(task_id)
    
    return {
        "task_id": task_id,
        "status": task.status.value if task else "unknown",
        "output": output,
        "total_lines": len(task.output_buffer) if task else 0
    }


async def handle_client_list_tasks(params: dict) -> dict:
    """Tasks auflisten"""
    client_id = params.get("client_id")
    status = params.get("status")
    
    status_enum = TaskStatus(status) if status else None
    tasks = task_spawner.list_tasks(client_id=client_id, status=status_enum)
    
    return {
        "tasks": tasks,
        "count": len(tasks),
        "stats": task_spawner.get_stats()
    }


async def handle_client_cancel_task(params: dict) -> dict:
    """Task abbrechen"""
    task_id = params.get("task_id")
    if not task_id:
        return {"error": "task_id required"}
    
    success = await task_spawner.cancel_task(task_id)
    return {
        "success": success,
        "task_id": task_id,
        "message": "Task cancelled" if success else "Could not cancel (not running or not found)"
    }


# Tool-Definitionen für MCP
TASK_SPAWNER_TOOLS = [
    {
        "name": "client_request_task",
        "description": "Task an Server senden - Server spawnt Agent mit API Keys",
        "inputSchema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "Client-ID"},
                "description": {"type": "string", "description": "Was soll gemacht werden?"},
                "agent_type": {
                    "type": "string",
                    "enum": ["claude", "codex", "gemini", "opencode"],
                    "default": "claude"
                },
                "target_host": {"type": "string", "description": "Optional: Remote-Host für SSH"},
                "context": {"type": "object", "description": "Zusätzlicher Kontext (system_info, logs)"}
            },
            "required": ["description"]
        }
    },
    {
        "name": "client_task_status",
        "description": "Status eines Tasks abfragen",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task-ID"}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "client_task_output",
        "description": "Live-Output eines Tasks holen",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task-ID"},
                "last_n": {"type": "integer", "default": 50, "description": "Letzte N Zeilen"}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "client_list_tasks",
        "description": "Tasks auflisten (optional gefiltert)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "Filter nach Client"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "spawning", "running", "completed", "failed", "cancelled"]
                }
            }
        }
    },
    {
        "name": "client_cancel_task",
        "description": "Laufenden Task abbrechen",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task-ID"}
            },
            "required": ["task_id"]
        }
    }
]

TASK_SPAWNER_HANDLERS = {
    "client_request_task": handle_client_request_task,
    "client_task_status": handle_client_task_status,
    "client_task_output": handle_client_task_output,
    "client_list_tasks": handle_client_list_tasks,
    "client_cancel_task": handle_client_cancel_task,
}
