"""
Remote Task Service - CLI Agents führen Tasks auf Remote-Hosts aus
Ermöglicht: Client schickt Task → Backend spawnt Agent → Agent arbeitet per SSH
"""

import asyncio
import json
import subprocess
import os
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    SYSTEM_OPTIMIZE = "system_optimize"
    GAMING_OPTIMIZE = "gaming_optimize"
    ANALYZE = "analyze"
    INSTALL = "install"
    CONFIGURE = "configure"
    DEBUG = "debug"
    CUSTOM = "custom"


@dataclass
class RemoteHost:
    """SSH-Verbindungsdaten für einen Remote-Host"""
    host_id: str
    hostname: str
    username: str
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    port: int = 22
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> dict:
        d = asdict(self)
        if self.password:
            d["password"] = "***HIDDEN***"
        return d


@dataclass
class RemoteTask:
    """Ein Task der auf einem Remote-Host ausgeführt wird"""
    task_id: str
    host_id: str
    task_type: TaskType
    description: str
    agent_id: str = "claude-mcp"  # Default Agent
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    output: List[str] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class RemoteTaskService:
    """Service für Remote Task Execution via CLI Agents"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        self.hosts: Dict[str, RemoteHost] = {}
        self.tasks: Dict[str, RemoteTask] = {}
        self.running_processes: Dict[str, asyncio.subprocess.Process] = {}
        
        # Task Templates für verschiedene Task-Typen
        self.task_templates = {
            TaskType.GAMING_OPTIMIZE: {
                "agent": "claude-mcp",
                "prompt_template": """Du bist ein Linux-Systemadministrator. Verbinde dich per SSH zu {hostname} und optimiere das System für Gaming.

SSH-Verbindung: sshpass -p '{password}' ssh -o StrictHostKeyChecking=accept-new {username}@{hostname}

Führe folgende Optimierungen durch:
1. Prüfe CPU Governor (sollte 'performance' sein)
2. Prüfe GPU Power Profile (sollte 'high' sein für AMD)
3. Optimiere Kernel-Parameter für Gaming (vm.swappiness, dirty_ratio)
4. Prüfe ZRAM/Swap-Konfiguration
5. Installiere gamemode falls nicht vorhanden
6. Erstelle Gaming-Preset

Berichte jeden Schritt und zeige vorher/nachher Werte."""
            },
            TaskType.SYSTEM_OPTIMIZE: {
                "agent": "claude-mcp", 
                "prompt_template": """Du bist ein Linux-Systemadministrator. Verbinde dich per SSH zu {hostname} und analysiere/optimiere das System.

SSH-Verbindung: sshpass -p '{password}' ssh -o StrictHostKeyChecking=accept-new {username}@{hostname}

Aufgaben:
1. System-Info sammeln (CPU, RAM, Disk, Kernel)
2. Aktuelle Performance-Einstellungen prüfen
3. Optimierungen für die Hardware vorschlagen
4. Auf Wunsch Optimierungen anwenden

Sei vorsichtig und erstelle Backups vor Änderungen."""
            },
            TaskType.ANALYZE: {
                "agent": "gemini-mcp",
                "prompt_template": """Analysiere das Remote-System {hostname} per SSH.

SSH: sshpass -p '{password}' ssh {username}@{hostname}

Sammle:
- Hardware-Info (lscpu, free, df, lspci)
- Software-Status (uname, lsb_release)
- Laufende Services (systemctl)
- Performance-Metriken

Erstelle einen übersichtlichen Bericht."""
            },
            TaskType.DEBUG: {
                "agent": "codex-mcp",
                "prompt_template": """Debug-Session auf {hostname}.

SSH: sshpass -p '{password}' ssh {username}@{hostname}

Problem: {description}

Analysiere Logs, prüfe Services, finde die Ursache."""
            },
            TaskType.CUSTOM: {
                "agent": "claude-mcp",
                "prompt_template": """Führe folgende Aufgabe auf {hostname} aus:

SSH: sshpass -p '{password}' ssh {username}@{hostname}

Aufgabe: {description}"""
            }
        }
        
        logger.info("RemoteTaskService initialized")
    
    # === Host Management ===
    
    def register_host(self, hostname: str, username: str, password: str = None,
                     ssh_key: str = None, port: int = 22, description: str = "") -> RemoteHost:
        """Registriert einen neuen Remote-Host"""
        import hashlib
        host_id = hashlib.md5(f"{username}@{hostname}:{port}".encode()).hexdigest()[:12]
        
        host = RemoteHost(
            host_id=host_id,
            hostname=hostname,
            username=username,
            password=password,
            ssh_key=ssh_key,
            port=port,
            description=description
        )
        self.hosts[host_id] = host
        logger.info(f"Host registered: {host_id} ({username}@{hostname})")
        return host
    
    def get_host(self, host_id: str) -> Optional[RemoteHost]:
        return self.hosts.get(host_id)
    
    def list_hosts(self) -> List[dict]:
        return [h.to_dict() for h in self.hosts.values()]
    
    def remove_host(self, host_id: str) -> bool:
        if host_id in self.hosts:
            del self.hosts[host_id]
            return True
        return False
    
    # === Task Management ===
    
    async def submit_task(self, host_id: str, task_type: TaskType, 
                          description: str = "", agent_id: str = None) -> RemoteTask:
        """Reicht einen neuen Task ein"""
        import uuid
        
        host = self.get_host(host_id)
        if not host:
            raise ValueError(f"Host {host_id} nicht gefunden")
        
        # Agent aus Template oder Override
        template = self.task_templates.get(task_type, self.task_templates[TaskType.CUSTOM])
        if agent_id is None:
            agent_id = template["agent"]
        
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task = RemoteTask(
            task_id=task_id,
            host_id=host_id,
            task_type=task_type,
            description=description,
            agent_id=agent_id
        )
        self.tasks[task_id] = task
        
        # Task starten
        asyncio.create_task(self._execute_task(task, host, template))
        
        logger.info(f"Task submitted: {task_id} ({task_type}) on {host.hostname} via {agent_id}")
        return task
    
    async def _execute_task(self, task: RemoteTask, host: RemoteHost, template: dict):
        """Führt einen Task aus indem ein CLI-Agent gestartet wird"""
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now().isoformat()
        
        try:
            # Prompt aus Template erstellen
            prompt = template["prompt_template"].format(
                hostname=host.hostname,
                username=host.username,
                password=host.password or "",
                port=host.port,
                description=task.description
            )
            
            # CLI Agent Command bauen
            agent_commands = {
                "claude-mcp": ["claude", "-p", prompt, "--allowedTools", "Bash,Edit,Write"],
                "codex-mcp": ["codex", "exec", "--full-auto", "-m", "o4-mini", prompt],
                "gemini-mcp": [
                    "/home/zombie/workspace/triforce/triforce/bin/agy-triforce",
                    "--output-format", "text", "--print", prompt
                ],
                "opencode-mcp": ["opencode", "run", prompt]
            }
            
            cmd = agent_commands.get(task.agent_id)
            if not cmd:
                raise ValueError(f"Unknown agent: {task.agent_id}")
            
            # Agent starten
            logger.info(f"Starting agent {task.agent_id} for task {task.task_id}")
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd="/home/zombie",
                start_new_session=True,
            )
            self.running_processes[task.task_id] = process
            
            # Output sammeln
            output_lines = []
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                decoded = line.decode().strip()
                output_lines.append(decoded)
                task.output.append(decoded)
                logger.debug(f"[{task.task_id}] {decoded}")
            
            await process.wait()

            task.completed_at = datetime.now().isoformat()
            task.result = {
                "exit_code": process.returncode,
                "output_lines": len(output_lines),
            }
            if task.status == TaskStatus.CANCELLED:
                logger.info(f"Task {task.task_id} cancelled with exit code {process.returncode}")
            elif process.returncode == 0:
                task.status = TaskStatus.COMPLETED
                logger.info(f"Task {task.task_id} completed with exit code 0")
            else:
                task.status = TaskStatus.FAILED
                task.error = f"Agent exited with code {process.returncode}"
                logger.warning(f"Task {task.task_id} failed with exit code {process.returncode}")
            
        except Exception as e:
            if task.status != TaskStatus.CANCELLED:
                task.status = TaskStatus.FAILED
                task.error = str(e)
            task.completed_at = datetime.now().isoformat()
            logger.error(f"Task {task.task_id} failed: {e}")
        finally:
            self.running_processes.pop(task.task_id, None)
    
    def get_task(self, task_id: str) -> Optional[RemoteTask]:
        return self.tasks.get(task_id)
    
    def get_task_output(self, task_id: str, last_n: int = 50) -> List[str]:
        task = self.get_task(task_id)
        if task:
            return task.output[-last_n:]
        return []
    
    def list_tasks(self, host_id: str = None, status: TaskStatus = None) -> List[dict]:
        tasks = list(self.tasks.values())
        if host_id:
            tasks = [t for t in tasks if t.host_id == host_id]
        if status:
            tasks = [t for t in tasks if t.status == status]
        return [asdict(t) for t in tasks]
    
    def cancel_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return False
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now().isoformat()
        process = self.running_processes.get(task_id)
        if process is not None and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        return True


# Singleton Instance
remote_task_service = RemoteTaskService()
