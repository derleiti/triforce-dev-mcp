"""
Mesh AI Orchestration Routes v1.0
=================================

API endpoints für das Mesh AI System:
- /mesh/submit - Neue Aufgabe einreichen
- /mesh/tasks - Tasks auflisten/verwalten
- /mesh/agents - Agents verwalten
- /mesh/queue - MCP Command Queue
- /mesh/status - System Status

Author: TriForce System
Version: 1.0.0
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.paths import TRISTAR_DIR

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..services.mesh_coordinator import (
    mesh_coordinator,
    submit_mesh_task,
    queue_mcp_command,
    get_mesh_status,
    AgentRole,
    TaskPhase,
    MeshAgent,
)

router = APIRouter(prefix="/mesh", tags=["Mesh AI"])


# ============================================================================
# Request/Response Models
# ============================================================================

class TaskSubmitRequest(BaseModel):
    """Request für Task-Einreichung"""
    title: str = Field(..., description="Titel der Aufgabe")
    description: str = Field(..., description="Beschreibung der Aufgabe")


class TaskResponse(BaseModel):
    """Response für Task-Operationen"""
    id: str
    title: str
    phase: str
    created_at: str


class MCPCommandRequest(BaseModel):
    """Request für MCP Command Queue"""
    source_agent: str = Field(..., description="ID des sendenden Agents")
    command: str = Field(..., description="MCP Command Name")
    params: Dict[str, Any] = Field(default_factory=dict, description="Command Parameter")
    priority: int = Field(2, ge=0, le=4, description="Priorität (0=kritisch, 4=niedrig)")


class AgentRegisterRequest(BaseModel):
    """Request für Agent-Registrierung"""
    id: str = Field(..., description="Eindeutige Agent ID")
    name: str = Field(..., description="Agent Name")
    role: str = Field(..., description="lead|worker|reviewer|researcher")
    model: str = Field(..., description="Model ID (z.B. gemini/gemini-2.0-flash)")
    capabilities: List[str] = Field(default_factory=list, description="Fähigkeiten")
    mcp_filtered: bool = Field(True, description="MCP Commands gefiltert")


# ============================================================================
# Task Management Endpoints
# ============================================================================

@router.post("/submit", summary="Submit a new mesh task")
async def submit_task(request: TaskSubmitRequest) -> Dict[str, Any]:
    """
    Reicht eine neue Aufgabe beim Mesh AI System ein.

    Workflow:
    1. Task erstellen
    2. Automatisch Research starten (Web Search)
    3. KI-Umfrage bei allen Agents durchführen
    4. Gemini Lead erstellt Implementierungsplan
    5. Worker führen aus
    6. Reviewer prüfen

    Returns:
        Task-Objekt mit ID und Status
    """
    try:
        task = await submit_mesh_task(request.title, request.description)
        return {
            "status": "submitted",
            "task": task.to_dict(),
            "message": f"Task {task.id} submitted. Processing started.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks", summary="List all mesh tasks")
async def list_tasks(
    phase: Optional[str] = Query(None, description="Filter by phase"),
    limit: int = Query(20, le=100, description="Maximum tasks to return"),
) -> Dict[str, Any]:
    """Listet alle Tasks im Mesh System"""
    tasks = list(mesh_coordinator._tasks.values())

    if phase:
        try:
            phase_enum = TaskPhase(phase)
            tasks = [t for t in tasks if t.phase == phase_enum]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid phase: {phase}")

    # Sort by created_at descending
    tasks.sort(key=lambda t: t.created_at, reverse=True)
    tasks = tasks[:limit]

    return {
        "tasks": [t.to_dict() for t in tasks],
        "count": len(tasks),
        "phases": [p.value for p in TaskPhase],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/tasks/{task_id}", summary="Get task details")
async def get_task(task_id: str) -> Dict[str, Any]:
    """Holt Details zu einem bestimmten Task"""
    task = mesh_coordinator.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    return {
        "task": task.to_dict(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================================
# Agent Management Endpoints
# ============================================================================

@router.get("/agents", summary="List all mesh agents")
async def list_agents(
    role: Optional[str] = Query(None, description="Filter by role"),
    available: Optional[bool] = Query(None, description="Filter by availability"),
) -> Dict[str, Any]:
    """Listet alle registrierten Mesh Agents"""
    agents = list(mesh_coordinator._agents.values())

    if role:
        try:
            role_enum = AgentRole(role)
            agents = [a for a in agents if a.role == role_enum]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid role: {role}")

    if available is not None:
        agents = [a for a in agents if a.available == available]

    return {
        "agents": [a.to_dict() for a in agents],
        "count": len(agents),
        "roles": [r.value for r in AgentRole],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/agents/{agent_id}", summary="Get agent details")
async def get_agent(agent_id: str) -> Dict[str, Any]:
    """Holt Details zu einem bestimmten Agent"""
    agent = mesh_coordinator.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    return {
        "agent": agent.to_dict(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/agents/register", summary="Register a new mesh agent")
async def register_agent(request: AgentRegisterRequest) -> Dict[str, Any]:
    """
    Registriert einen neuen Agent im Mesh System.

    Rollen:
    - lead: Koordinator (nur Gemini)
    - worker: Ausführende (Claude, Codex, DeepSeek)
    - reviewer: Code-Reviewer (Mistral, Cogito)
    - researcher: Recherche-Spezialisten
    """
    try:
        role_enum = AgentRole(request.role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {request.role}")

    if request.id in mesh_coordinator._agents:
        raise HTTPException(status_code=409, detail=f"Agent already exists: {request.id}")

    agent = MeshAgent(
        id=request.id,
        name=request.name,
        role=role_enum,
        model=request.model,
        capabilities=set(request.capabilities),
        mcp_filtered=request.mcp_filtered,
    )

    mesh_coordinator.register_agent(agent)

    return {
        "status": "registered",
        "agent": agent.to_dict(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================================
# MCP Command Queue Endpoints
# ============================================================================

@router.post("/queue/enqueue", summary="Enqueue an MCP command")
async def enqueue_command(request: MCPCommandRequest) -> Dict[str, Any]:
    """
    Fügt einen MCP Command zur Queue hinzu.

    Mesh AI Workers können keine MCP Commands direkt ausführen.
    Stattdessen werden sie hier gequeued und vom TriForce Server
    sequenziell ausgeführt.

    Prioritäten:
    - 0: Kritisch (sofort)
    - 1: Hoch
    - 2: Normal (Standard)
    - 3: Niedrig
    - 4: Idle
    """
    try:
        cmd = await queue_mcp_command(
            source=request.source_agent,
            command=request.command,
            params=request.params,
        )

        return {
            "status": "queued",
            "command": cmd.to_dict(),
            "queue_position": len(mesh_coordinator._mcp_queue),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/queue", summary="Get MCP command queue status")
async def get_queue() -> Dict[str, Any]:
    """Zeigt die aktuelle MCP Command Queue"""
    return {
        "queue": [cmd.to_dict() for cmd in mesh_coordinator._mcp_queue],
        "size": len(mesh_coordinator._mcp_queue),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/queue/{command_id}", summary="Get MCP command status")
async def get_command_status(command_id: str) -> Dict[str, Any]:
    """Holt den Status eines MCP Commands"""
    # Check in queue
    for cmd in mesh_coordinator._mcp_queue:
        if cmd.id == command_id:
            return {
                "command": cmd.to_dict(),
                "in_queue": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    # Check persisted files
    queue_file = TRISTAR_DIR / "queue" / f"{command_id}.json"
    if queue_file.exists():
        import json
        data = json.loads(queue_file.read_text())
        return {
            "command": data,
            "in_queue": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    raise HTTPException(status_code=404, detail=f"Command not found: {command_id}")


# ============================================================================
# Status Endpoints
# ============================================================================

@router.get("/status", summary="Get mesh system status")
async def mesh_status() -> Dict[str, Any]:
    """
    Gibt den vollständigen Status des Mesh AI Systems zurück.

    Beinhaltet:
    - Aktive Agents
    - Laufende Tasks
    - MCP Queue Status
    - System Health
    """
    status = get_mesh_status()
    status["timestamp"] = datetime.now(timezone.utc).isoformat()
    status["version"] = "1.0.0"
    return status


@router.get("/health", summary="Health check for mesh system")
async def mesh_health() -> Dict[str, Any]:
    """Quick health check"""
    return {
        "status": "healthy" if mesh_coordinator._running else "stopped",
        "agents_count": len(mesh_coordinator._agents),
        "active_tasks": len([
            t for t in mesh_coordinator._tasks.values()
            if t.phase not in [TaskPhase.COMPLETED, TaskPhase.FAILED]
        ]),
        "queue_size": len(mesh_coordinator._mcp_queue),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/start", summary="Start the mesh coordinator")
async def start_coordinator() -> Dict[str, Any]:
    """Startet den Mesh Coordinator Background Service"""
    if mesh_coordinator._running:
        return {
            "status": "already_running",
            "message": "Mesh Coordinator is already running",
        }

    await mesh_coordinator.start()
    return {
        "status": "started",
        "message": "Mesh Coordinator started successfully",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/stop", summary="Stop the mesh coordinator")
async def stop_coordinator() -> Dict[str, Any]:
    """Stoppt den Mesh Coordinator Background Service"""
    if not mesh_coordinator._running:
        return {
            "status": "already_stopped",
            "message": "Mesh Coordinator is not running",
        }

    await mesh_coordinator.stop()
    return {
        "status": "stopped",
        "message": "Mesh Coordinator stopped successfully",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================================
# MCP Tool Definitions (for tool listing)
# ============================================================================

MESH_TOOLS = [
    {
        "name": "mesh_submit_task",
        "description": "Reicht eine neue Aufgabe beim Mesh AI System ein",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titel der Aufgabe"},
                "description": {"type": "string", "description": "Beschreibung der Aufgabe"},
            },
            "required": ["title", "description"],
        },
    },
    {
        "name": "mesh_queue_command",
        "description": "Queued einen MCP Command für Mesh AI Workers (gefiltert)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_agent": {"type": "string", "description": "Agent ID"},
                "command": {"type": "string", "description": "MCP Command"},
                "params": {"type": "object", "description": "Command Parameter"},
                "priority": {"type": "integer", "minimum": 0, "maximum": 4, "default": 2},
            },
            "required": ["source_agent", "command"],
        },
    },
    {
        "name": "mesh_get_status",
        "description": "Holt den Status des Mesh AI Systems",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "mesh_list_agents",
        "description": "Listet alle registrierten Mesh Agents",
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "enum": ["lead", "worker", "reviewer", "researcher"]},
            },
        },
    },
    {
        "name": "mesh_get_task",
        "description": "Holt Details zu einem Mesh Task",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID"},
            },
            "required": ["task_id"],
        },
    },
]


# ============================================================================
# MCP Handlers (for integration with MCP endpoint)
# ============================================================================

async def handle_mesh_submit_task(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle mesh_submit_task tool"""
    title = params.get("title")
    description = params.get("description")

    if not title or not description:
        raise ValueError("'title' and 'description' are required")

    task = await submit_mesh_task(title, description)
    return {
        "status": "submitted",
        "task": task.to_dict(),
    }


async def handle_mesh_queue_command(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle mesh_queue_command tool"""
    source = params.get("source_agent")
    command = params.get("command")
    cmd_params = params.get("params", {})
    priority = params.get("priority", 2)

    if not source or not command:
        raise ValueError("'source_agent' and 'command' are required")

    cmd = await queue_mcp_command(source, command, cmd_params, priority=priority)
    return {
        "status": "queued",
        "command": cmd.to_dict(),
    }


async def handle_mesh_get_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle mesh_get_status tool"""
    return get_mesh_status()


async def handle_mesh_list_agents(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle mesh_list_agents tool"""
    agents = list(mesh_coordinator._agents.values())

    role = params.get("role")
    if role:
        try:
            role_enum = AgentRole(role)
            agents = [a for a in agents if a.role == role_enum]
        except ValueError:
            pass

    return {
        "agents": [a.to_dict() for a in agents],
        "count": len(agents),
    }


async def handle_mesh_get_task(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle mesh_get_task tool"""
    task_id = params.get("task_id")
    if not task_id:
        raise ValueError("'task_id' is required")

    task = mesh_coordinator.get_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    return {"task": task.to_dict()}

# ============================================================================
# MESH RESOURCES - Live Hardware Status
# ============================================================================

import time as _time

FEDERATION_NODES = {
    "hetzner": {"ip": "10.10.0.1", "port": 9000, "cores": 20, "ram": 62, "gpu": None, "role": "master", "name": "Hetzner EX63"},
    "backup": {"ip": "10.10.0.3", "port": 9100, "cores": 28, "ram": 64, "gpu": None, "role": "hub", "name": "Backup VPS"},
    "zombie-pc": {"ip": "10.10.0.2", "port": 9000, "cores": 16, "ram": 30, "gpu": "RX 6800 XT", "role": "hub", "name": "Zombie PC"}
}

CLOUD_PROVIDERS = {"gemini": 15, "anthropic": 5, "groq": 12, "cerebras": 4, "mistral": 8, "openrouter": 200, "github": 10, "cloudflare": 15}

async def _check_node(session, nid, cfg):
    import aiohttp
    result = {"id": nid, "name": cfg["name"], "role": cfg["role"], "online": False, "latency_ms": None, "cores": cfg["cores"], "ram_gb": cfg["ram"], "gpu": cfg["gpu"]}
    try:
        t = _time.time()
        async with session.get(f"http://{cfg['ip']}:{cfg['port']}/health", timeout=aiohttp.ClientTimeout(total=3)) as r:
            if r.status == 200: result["online"], result["latency_ms"] = True, round((_time.time() - t) * 1000)
    except: pass
    return result

async def _get_ollama(session, ip):
    import aiohttp
    try:
        async with session.get(f"http://{ip}:11434/api/tags", timeout=aiohttp.ClientTimeout(total=2)) as r:
            return [m["name"] for m in (await r.json()).get("models", [])] if r.status == 200 else []
    except: return []

async def get_mesh_resources(fmt="summary"):
    import aiohttp, asyncio
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as s:
        nodes = await asyncio.gather(*[_check_node(s, n, c) for n, c in FEDERATION_NODES.items()])
        ollama = await asyncio.gather(*[_get_ollama(s, c["ip"]) for c in FEDERATION_NODES.values()])
    for i, n in enumerate(nodes): n["ollama_models"] = ollama[i]
    online = [n for n in nodes if n["online"]]
    local = list(set(m for n in nodes for m in n.get("ollama_models", [])))
    cloud = sum(CLOUD_PROVIDERS.values())
    base = {"timestamp": datetime.now(timezone.utc).isoformat(), "status": "healthy" if len(online) >= 2 else "degraded"}
    if fmt == "summary":
        return {**base, "mesh": {"nodes": f"{len(online)}/{len(nodes)} online", "compute": f"{sum(n['cores'] for n in online)} Cores", "memory": f"{sum(n['ram_gb'] for n in online)} GB RAM", "gpus": [n["gpu"] for n in nodes if n.get("gpu")], "latency": f"{round(sum(n.get('latency_ms') or 0 for n in online) / max(1, len(online)))}ms avg"}, "intelligence": {"providers": len(CLOUD_PROVIDERS), "models": f"{cloud + len(local)}+", "local": len(local)}}
    elif fmt == "nodes": return {**base, "nodes": [{k: v for k, v in n.items() if k != "ollama_models"} for n in nodes]}
    else: return {**base, "federation": {"nodes": nodes, "totals": {"online": len(online), "cores": sum(n["cores"] for n in online), "ram_gb": sum(n["ram_gb"] for n in online), "gpus": [n["gpu"] for n in nodes if n.get("gpu")]}}, "intelligence": {"cloud": CLOUD_PROVIDERS, "local": local, "total": cloud + len(local)}}

@router.get("/resources", summary="Live Mesh Hardware Resources")
async def mesh_resources_endpoint(format: str = Query("summary", pattern="^(summary|nodes|full)$")):
    return await get_mesh_resources(format)

async def handle_mesh_resources(params: Dict[str, Any]) -> Dict[str, Any]:
    return await get_mesh_resources(params.get("format", "summary"))



MESH_HANDLERS = {
    "mesh_submit_task": handle_mesh_submit_task,
    "mesh_queue_command": handle_mesh_queue_command,
    "mesh_get_status": handle_mesh_get_status,
    "mesh_list_agents": handle_mesh_list_agents,
    "mesh_get_task": handle_mesh_get_task,
    "mesh_resources": handle_mesh_resources,
}
