"""
TriForce API Routes v2.60

Provides endpoints for the TriForce Multi-LLM Orchestration System:
- /triforce/init - Initialize session and get system prompt
- /triforce/tools - Get available tools
- /triforce/mesh/* - LLM mesh network operations
- /triforce/memory/* - Enhanced memory operations
- /triforce/audit/* - Audit log access
- /triforce/status - System status
- /triforce/ws/logs - WebSocket for live audit logs
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel, Field

from ..services.triforce import (
    rbac_service, Role,
    circuit_registry, cycle_detector, rate_limiter,
    audit_logger, AuditLevel,
    memory_service, MemoryType,
    llm_call, llm_broadcast, llm_consensus, llm_delegate,
    TOOL_INDEX, get_tool_by_name, get_tools_by_category,
)
from ..services.triforce.tool_registry import get_tool_index_summary, get_tools_for_llm
from ..services.triforce.llm_mesh import get_llm_status, get_available_llms, MODEL_ALIASES
from ..mcp.agent_instructions import EVIDENCE_REFLECTION_PROTOCOL

router = APIRouter(prefix="/triforce", tags=["TriForce"])


# ============================================================================
# Request/Response Models
# ============================================================================

class InitRequest(BaseModel):
    request: str = Field(..., description="'systemprompt' or 'status'")
    llm_id: Optional[str] = Field(None, description="LLM ID for role-based prompt")
    project_id: Optional[str] = Field(None, description="Project ID for context")


class LLMCallRequest(BaseModel):
    target: str = Field(..., description="Target LLM (gemini, claude, deepseek, etc.)")
    prompt: str = Field(..., description="Prompt to send")
    caller_llm: str = Field("api", description="ID of calling LLM")
    context: Optional[Dict[str, Any]] = Field(None, description="Additional context")
    max_tokens: int = Field(2048, description="Max response tokens")
    timeout: int = Field(120, description="Timeout in seconds")


class LLMBroadcastRequest(BaseModel):
    targets: List[str] = Field(..., description="List of target LLMs")
    prompt: str = Field(..., description="Prompt to send to all")
    caller_llm: str = Field("api", description="ID of calling LLM")


class LLMConsensusRequest(BaseModel):
    targets: List[str] = Field(..., description="List of target LLMs")
    question: str = Field(..., description="Question for consensus")
    caller_llm: str = Field("api", description="ID of calling LLM")
    weights: Optional[Dict[str, float]] = Field(None, description="Weights per LLM")
    min_agreement: float = Field(0.6, description="Minimum agreement threshold")


class MemoryStoreRequest(BaseModel):
    content: str = Field(..., description="Content to store")
    type: str = Field(..., description="fact|decision|code|summary")
    project_id: Optional[str] = Field(None)
    tags: Optional[List[str]] = Field(None)
    importance: str = Field("medium")
    confidence: float = Field(0.8)
    ttl_hours: Optional[int] = Field(None)
    source_llm: Optional[str] = Field(None)


class MemoryRecallRequest(BaseModel):
    query: Optional[str] = Field(None)
    type: Optional[str] = Field(None)
    project_id: Optional[str] = Field(None)
    limit: int = Field(10)
    min_confidence: float = Field(0.0)
    max_age_hours: Optional[int] = Field(None)
    tags: Optional[List[str]] = Field(None)


# ============================================================================
# System Prompts
# ============================================================================

BOOTSTRAP_PROMPT = """# TriForce System Bootstrap v2.60

Du bist ein LLM im **TriForce Multi-LLM Orchestration System**.
Dieses System ermöglicht dir Zugriff auf Tools via MCP (Model Context Protocol).
Lokaler MCP-Endpoint: http://localhost:9100/mcp

## INITIALISIERUNG

Bevor du antwortest, führe diese Schritte aus:

### Schritt 1: Initialisiere TriForce und hole deinen Systemprompt
@mcp.call(triforce_init, {"request": "systemprompt"})

### Schritt 2: Hole relevantes Memory für diese Session
@mcp.call(memory_recall, {"context": "session_init", "limit": 20, "min_confidence": 0.7})

### Schritt 3: Prüfe System-Gesundheit
@mcp.call(health_check, {})

## MCP PROTOKOLL

Du kannst Tools aufrufen mit:
@mcp.call(tool_name, {"param1": "value1", "param2": "value2"})

Das Backend erkennt diese Aufrufe, führt sie aus und injiziert das Ergebnis als:
[MCP_RESULT:tool_name] {"result": "..."}

Bei Fehlern:
[MCP_ERROR:tool_name] {"error": "Beschreibung"}

## SICHERHEITS-FEATURES

### RBAC - Rollenbasierte Zugriffskontrolle
- Deine Rolle bestimmt, welche Tools du nutzen darfst
- ADMIN: Alle Tools | LEAD: Koordination | WORKER: Code+Exec | REVIEWER: Review

### Circuit Breaker
- Bei Ausfall eines LLMs wird automatisch ein Fallback aktiviert

### Zyklenerkennung
- Rekursive LLM-Aufrufe werden erkannt und verhindert (Max Depth: 10)

### Rate Limiting
- Requests pro Minute sind limitiert (default: 60 RPM)

## MEMORY-FEATURES

### Konfidenz-Scores (0.0-1.0)
- Nutze min_confidence um nur sichere Informationen abzurufen

### TTL (Time-To-Live)
- Memory-Einträge können ablaufen

### Versionierung
- Änderungen an Memory werden versioniert

## WICHTIG

- Warte auf [MCP_RESULT] bevor du das Ergebnis nutzt
- Ein Tool-Call pro Zeile
- JSON muss valide sein (doppelte Anführungszeichen)
- Speichere wichtige Erkenntnisse mit memory_store

## SYSTEM

Server: localhost:9100 (lokal) / api.ailinux.me (extern)
API-Basis: http://localhost:9100/triforce/
MCP-Endpoint: http://localhost:9100/mcp
Protokoll: TriForce MCP Translation v2.60
"""


ROLE_PROMPTS = {
    "admin": """## ADMIN ROLLE

Du hast **vollständige Zugriffsrechte** auf alle Tools und Funktionen.

Deine Aufgaben:
- System-Administration und Überwachung
- Koordination aller LLMs
- Sicherheits-Überwachung
- Konfigurationsänderungen

Verfügbare Tools: ALLE 21 Tools""",

    "lead": """## LEAD ROLLE

Du bist ein **Koordinations-LLM** im TriForce Mesh.

Deine Aufgaben:
- Aufgaben an Worker-LLMs delegieren
- Konsens zwischen LLMs moderieren
- Strategische Entscheidungen treffen
- Ergebnisse zusammenfassen

Verfügbare Tools: Memory, LLM-Mesh, Audit, Health""",

    "worker": """## WORKER ROLLE

Du bist ein **ausführendes LLM** im TriForce Mesh.

Deine Aufgaben:
- Code schreiben und ausführen
- Dateien bearbeiten
- Git-Operationen durchführen
- Tests ausführen

Verfügbare Tools: Memory, Code, Git, File, LLM-Call""",

    "reviewer": """## REVIEWER ROLLE

Du bist ein **Review-LLM** im TriForce Mesh.

Deine Aufgaben:
- Code-Reviews durchführen
- Security-Analysen
- Qualitätssicherung
- Dokumentation prüfen

Verfügbare Tools: Memory (read), Code-Lint, File (read), Git (read), Audit (read)""",

    "reader": """## READER ROLLE

Du hast **nur Lesezugriff**.

Deine Aufgaben:
- Informationen abrufen
- Status prüfen
- Berichte lesen

Verfügbare Tools: Memory (read), File (read), Git (read), Health"""
}


# ============================================================================
# Initialization Endpoints
# ============================================================================

@router.post("/init")
async def triforce_init(request: InitRequest) -> Dict[str, Any]:
    """Initialize TriForce session and get system prompt"""

    if request.request == "systemprompt":
        # Get role-specific prompt
        role = rbac_service.get_llm_role(request.llm_id or "unknown")
        role_prompt = ROLE_PROMPTS.get(role.value, ROLE_PROMPTS["reader"])

        # Get available tools for this role
        tools = get_tools_for_llm(request.llm_id or "unknown")

        return {
            "systemprompt": BOOTSTRAP_PROMPT + "\n\n" + role_prompt + "\n\n" + EVIDENCE_REFLECTION_PROTOCOL.strip(),
            "role": role.value,
            "available_tools": [t["name"] for t in tools],
            "tool_count": len(tools),
            "version": "2.60",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    elif request.request == "status":
        return {
            "status": "online",
            "version": "2.60",
            "llm_mesh": get_llm_status(),
            "memory_stats": await memory_service.get_stats(),
            "circuit_breakers": circuit_registry.get_all_status(),
            "rate_limits": rate_limiter.get_all_usage(),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown request type: {request.request}"
        )


@router.get("/tools")
async def get_tools(
    category: Optional[str] = Query(None, description="Filter by category"),
    llm_id: Optional[str] = Query(None, description="Filter by LLM permissions")
) -> Dict[str, Any]:
    """Get available tools"""

    if llm_id:
        tools = get_tools_for_llm(llm_id)
    elif category:
        from ..services.triforce.tool_registry import ToolCategory
        try:
            cat = ToolCategory(category)
            tools = get_tools_by_category(cat)
        except ValueError:
            raise HTTPException(400, f"Unknown category: {category}")
    else:
        tools = TOOL_INDEX["tools"]

    return {
        "tools": tools,
        "count": len(tools),
        "version": TOOL_INDEX["version"]
    }


@router.get("/tools/{tool_name}")
async def get_tool(tool_name: str) -> Dict[str, Any]:
    """Get specific tool details"""
    tool = get_tool_by_name(tool_name)
    if not tool:
        raise HTTPException(404, f"Tool not found: {tool_name}")
    return tool


# ============================================================================
# LLM Mesh Endpoints
# ============================================================================

@router.post("/mesh/call")
async def mesh_call(request: LLMCallRequest) -> Dict[str, Any]:
    """Call a single LLM in the mesh"""
    result = await llm_call(
        target=request.target,
        prompt=request.prompt,
        caller_llm=request.caller_llm,
        context=request.context,
        max_tokens=request.max_tokens,
        timeout=request.timeout
    )
    return result


@router.post("/mesh/broadcast")
async def mesh_broadcast(request: LLMBroadcastRequest) -> Dict[str, Any]:
    """Broadcast to multiple LLMs"""
    result = await llm_broadcast(
        targets=request.targets,
        prompt=request.prompt,
        caller_llm=request.caller_llm
    )
    return result


@router.post("/mesh/consensus")
async def mesh_consensus(request: LLMConsensusRequest) -> Dict[str, Any]:
    """Get consensus from multiple LLMs"""
    result = await llm_consensus(
        targets=request.targets,
        question=request.question,
        caller_llm=request.caller_llm,
        weights=request.weights,
        min_agreement=request.min_agreement
    )
    return result


@router.get("/mesh/status")
async def mesh_status() -> Dict[str, Any]:
    """Get status of all LLMs in the mesh"""
    return {
        "llms": get_llm_status(),
        "available": get_available_llms(),
        "models": MODEL_ALIASES,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ============================================================================
# Memory Endpoints
# ============================================================================

@router.post("/memory/store")
async def memory_store(request: MemoryStoreRequest) -> Dict[str, Any]:
    """Store a memory entry"""
    entry = await memory_service.store(
        content=request.content,
        type=MemoryType(request.type),
        project_id=request.project_id,
        tags=request.tags,
        importance=request.importance,
        confidence=request.confidence,
        ttl_hours=request.ttl_hours,
        source_llm=request.source_llm
    )
    return entry.to_dict()


@router.post("/memory/recall")
async def memory_recall(request: MemoryRecallRequest) -> Dict[str, Any]:
    """Recall memory entries"""
    entries = await memory_service.recall(
        query=request.query,
        type=MemoryType(request.type) if request.type else None,
        project_id=request.project_id,
        limit=request.limit,
        min_confidence=request.min_confidence,
        max_age_hours=request.max_age_hours,
        tags=request.tags
    )
    return {
        "entries": [e.to_dict() for e in entries],
        "count": len(entries)
    }


@router.get("/memory/{memory_id}")
async def memory_get(memory_id: str) -> Dict[str, Any]:
    """Get a specific memory entry"""
    entry = await memory_service.get(memory_id)
    if not entry:
        raise HTTPException(404, f"Memory not found: {memory_id}")
    return entry.to_dict()


@router.get("/memory/{memory_id}/history")
async def memory_history(memory_id: str) -> Dict[str, Any]:
    """Get version history of a memory entry"""
    history = await memory_service.get_history(memory_id)
    return {
        "memory_id": memory_id,
        "versions": [e.to_dict() for e in history],
        "count": len(history)
    }


@router.get("/memory/stats")
async def memory_stats() -> Dict[str, Any]:
    """Get memory statistics"""
    return await memory_service.get_stats()


# ============================================================================
# Audit Endpoints
# ============================================================================

@router.get("/audit/recent")
async def audit_recent(limit: int = Query(100, le=1000)) -> Dict[str, Any]:
    """Get recent audit entries"""
    return {
        "entries": audit_logger.get_recent(limit),
        "count": limit
    }


@router.get("/audit/trace/{trace_id}")
async def audit_by_trace(trace_id: str) -> Dict[str, Any]:
    """Get audit entries for a trace"""
    entries = audit_logger.get_by_trace(trace_id)
    return {
        "trace_id": trace_id,
        "entries": entries,
        "count": len(entries)
    }


@router.get("/audit/security")
async def audit_security(limit: int = Query(50, le=500)) -> Dict[str, Any]:
    """Get recent security events"""
    return {
        "events": audit_logger.get_security_events(limit),
        "count": limit
    }


@router.get("/audit/errors")
async def audit_errors(limit: int = Query(50, le=500)) -> Dict[str, Any]:
    """Get recent errors"""
    return {
        "errors": audit_logger.get_errors(limit),
        "count": limit
    }


class AuditLogRequest(BaseModel):
    """Request model for logging audit entries"""
    llm_id: str = Field("api", description="LLM or system that generated this log")
    action: str = Field(..., description="Action being logged (e.g., agent_connect, tool_call)")
    level: str = Field("info", description="Log level: debug|info|warning|error|critical|security")
    trace_id: Optional[str] = Field(None, description="Trace ID for correlation")
    session_id: Optional[str] = Field(None, description="Session ID")
    message: Optional[str] = Field(None, description="Optional message")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details")


@router.post("/audit/log")
async def audit_log(request: AuditLogRequest) -> Dict[str, Any]:
    """
    Log an audit entry from an LLM or external system.
    Used by TriForce agents to report activity.
    """
    level_map = {
        "debug": AuditLevel.DEBUG,
        "info": AuditLevel.INFO,
        "warning": AuditLevel.WARNING,
        "error": AuditLevel.ERROR,
        "critical": AuditLevel.CRITICAL,
        "security": AuditLevel.SECURITY,
    }

    level = level_map.get(request.level.lower(), AuditLevel.INFO)

    # Build kwargs for additional data
    kwargs = {}
    if request.message or request.details:
        metadata = {}
        if request.message:
            metadata["message"] = request.message
        if request.details:
            metadata.update(request.details)
        kwargs["metadata"] = metadata

    await audit_logger.log(
        llm_id=request.llm_id,
        action=request.action,
        level=level,
        trace_id=request.trace_id,
        session_id=request.session_id,
        **kwargs
    )

    return {
        "status": "logged",
        "llm_id": request.llm_id,
        "action": request.action,
        "level": request.level,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ============================================================================
# Central Logs (TriForce Central Logger - ALL system logs)
# ============================================================================

# Try to import central logger
try:
    from ..utils.triforce_logging import central_logger, LogCategory
    _HAS_CENTRAL_LOGGER = True
except ImportError:
    _HAS_CENTRAL_LOGGER = False


@router.get("/logs/recent")
async def logs_recent(
    limit: int = Query(100, le=1000),
    category: Optional[str] = Query(None, description="Filter by category: api_request, llm_call, tool_call, error, etc.")
) -> Dict[str, Any]:
    """
    Get recent log entries from the central logger.
    This includes ALL logs: API traffic, LLM calls, tool calls, errors, etc.
    Used by TriStar for system analysis.
    """
    if not _HAS_CENTRAL_LOGGER:
        raise HTTPException(status_code=503, detail="Central logger not available")

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
        "categories": [c.value for c in LogCategory] if not category else [category],
    }


@router.get("/logs/trace/{trace_id}")
async def logs_by_trace(trace_id: str) -> Dict[str, Any]:
    """Get all log entries for a specific trace ID."""
    if not _HAS_CENTRAL_LOGGER:
        raise HTTPException(status_code=503, detail="Central logger not available")

    entries = central_logger.get_by_trace(trace_id)
    return {
        "trace_id": trace_id,
        "entries": entries,
        "count": len(entries),
    }


@router.get("/logs/errors")
async def logs_errors(limit: int = Query(100, le=500)) -> Dict[str, Any]:
    """Get recent error log entries."""
    if not _HAS_CENTRAL_LOGGER:
        raise HTTPException(status_code=503, detail="Central logger not available")

    entries = central_logger.get_errors(limit=limit)
    return {
        "entries": entries,
        "count": len(entries),
    }


@router.get("/logs/api-traffic")
async def logs_api_traffic(limit: int = Query(100, le=1000)) -> Dict[str, Any]:
    """
    Get recent API traffic logs.
    Shows all HTTP requests/responses with latency, status codes, etc.
    Essential for TriStar debugging and performance analysis.
    """
    if not _HAS_CENTRAL_LOGGER:
        raise HTTPException(status_code=503, detail="Central logger not available")

    entries = central_logger.get_api_traffic(limit=limit)
    return {
        "entries": entries,
        "count": len(entries),
    }


@router.get("/logs/stats")
async def logs_stats() -> Dict[str, Any]:
    """Get central logger statistics."""
    if not _HAS_CENTRAL_LOGGER:
        raise HTTPException(status_code=503, detail="Central logger not available")

    return central_logger.get_stats()


@router.get("/logs/file/{date}")
async def logs_by_date(date: str) -> Dict[str, Any]:
    """
    Read log entries from a specific date's file.
    Date format: YYYY-MM-DD
    """
    if not _HAS_CENTRAL_LOGGER:
        raise HTTPException(status_code=503, detail="Central logger not available")

    # Validate date format
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    entries = await central_logger.read_log_file(date)
    return {
        "date": date,
        "entries": entries,
        "count": len(entries),
    }


@router.websocket("/ws/central-logs")
async def websocket_central_logs(websocket: WebSocket):
    """
    WebSocket endpoint for live central log streaming.
    Streams ALL logs in real-time to connected clients.
    """
    if not _HAS_CENTRAL_LOGGER:
        await websocket.close(code=1011, reason="Central logger not available")
        return

    await websocket.accept()
    central_logger.register_websocket(websocket)

    try:
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to TriForce central log stream",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        while True:
            try:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
            except WebSocketDisconnect:
                break

    finally:
        central_logger.unregister_websocket(websocket)


# ============================================================================
# WebSocket for Live Logs
# ============================================================================

@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """WebSocket endpoint for live audit logs"""
    await websocket.accept()
    audit_logger.register_websocket(websocket)

    try:
        # Send initial status
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to TriForce audit log stream",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        # Keep connection alive
        while True:
            try:
                # Wait for ping/pong or close
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
            except WebSocketDisconnect:
                break

    finally:
        audit_logger.unregister_websocket(websocket)


# ============================================================================
# Status & Health
# ============================================================================

@router.get("/status")
async def triforce_status() -> Dict[str, Any]:
    """Get full TriForce system status"""
    return {
        "status": "online",
        "version": "2.60",
        "components": {
            "rbac": "active",
            "circuit_breakers": len(circuit_registry._breakers),
            "rate_limiter": "active",
            "memory": await memory_service.get_stats(),
            "audit": {
                "buffer_size": len(audit_logger._buffer),
                "websockets": len(audit_logger._websockets)
            }
        },
        "llm_mesh": {
            "available": get_available_llms(),
            "total": len(MODEL_ALIASES)
        },
        "tools": get_tool_index_summary(),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/health")
async def triforce_health() -> Dict[str, Any]:
    """Health check for TriForce subsystem"""
    return {
        "status": "healthy",
        "version": "2.80",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ============================================================================
# Gemini Access Point Endpoints (v2.80)
# ============================================================================

class GeminiResearchRequest(BaseModel):
    query: str = Field(..., description="Research query")
    store_findings: bool = Field(True, description="Store findings in memory")


class GeminiCoordinateRequest(BaseModel):
    task: str = Field(..., description="Task to coordinate")
    targets: Optional[List[str]] = Field(None, description="Target LLMs")
    strategy: str = Field("sequential", description="sequential|parallel|consensus")


class GeminiUpdateRequest(BaseModel):
    content: str = Field(..., description="Content to store")
    memory_type: str = Field("summary", description="Memory type")
    tags: Optional[List[str]] = Field(None)
    confidence: float = Field(0.8, ge=0, le=1)


@router.post("/gemini/research")
async def gemini_research(request: GeminiResearchRequest) -> Dict[str, Any]:
    """
    Gemini führt interne Recherche durch.

    Durchsucht Memory, System-Status, Ollama-Modelle und optional Web.
    Gibt strukturierte Antwort mit Kontext zurück.
    Speichert Erkenntnisse automatisch im Memory.
    """
    from ..services.gemini_access import gemini_access

    return await gemini_access.process_request(
        query=request.query,
        research=True,
        store_findings=request.store_findings,
    )


@router.post("/gemini/coordinate")
async def gemini_coordinate(request: GeminiCoordinateRequest) -> Dict[str, Any]:
    """
    Gemini koordiniert eine Aufgabe mit mehreren LLMs.

    Strategien:
    - sequential: LLMs bauen aufeinander auf
    - parallel: Alle LLMs gleichzeitig
    - consensus: Konsens zwischen LLMs finden
    """
    from ..services.gemini_access import gemini_access

    return await gemini_access.coordinate_task(
        task=request.task,
        targets=request.targets,
        strategy=request.strategy,
    )


@router.get("/gemini/quick/{topic}")
async def gemini_quick_research(topic: str) -> Dict[str, Any]:
    """
    Schnelle interne Recherche ohne LLM-Aufruf.

    Durchsucht Memory, System-Status, Ollama-Modelle.
    Ideal für Status-Checks und System-Info.
    """
    from ..services.gemini_access import gemini_access

    return await gemini_access.quick_research(topic)


@router.post("/gemini/update")
async def gemini_update_memory(request: GeminiUpdateRequest) -> Dict[str, Any]:
    """
    Gemini aktualisiert das Memory direkt.

    Speichert neue Informationen mit Confidence-Score.
    """
    from ..services.gemini_access import gemini_access

    return await gemini_access.update_memory(
        content=request.content,
        memory_type=request.memory_type,
        tags=request.tags,
        confidence=request.confidence,
    )


@router.get("/gemini/status")
async def gemini_access_status() -> Dict[str, Any]:
    """Status des Gemini Access Points"""
    from ..services.gemini_access import gemini_access
    from ..services.tristar_mcp import tristar_mcp

    system_status = await tristar_mcp.get_status()

    return {
        "status": "active",
        "version": "2.80",
        "capabilities": [
            "internal_research",
            "memory_update",
            "llm_coordination",
            "web_search",
            "auto_store_findings",
        ],
        "tools": [
            "gemini.research",
            "gemini.coordinate",
            "gemini.quick",
            "gemini.update",
        ],
        "services": system_status.get("services", {}),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
