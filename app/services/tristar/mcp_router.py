"""
TriStar MCP Router v2.80 - Zentraler Multi-LLM Agent Router

Ersetzt separate systemd-Dienste durch einen zentralen Router mit:
- Dynamischen System-Prompts (editierbar via API)
- Integration mit TriForce (llm_mesh, rbac, circuit_breaker)
- Prompt-Management über /var/tristar/prompts/

Architektur:
    Client -> MCP Router -> PromptManager -> TriForce llm_mesh -> LLM Provider
"""

import os
import json
import asyncio
import logging
from pathlib import Path

from app.paths import TRISTAR_DIR
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

logger = logging.getLogger("ailinux.tristar.mcp_router")

# ============================================================================
# Data Transfer Objects
# ============================================================================

@dataclass
class AgentConfig:
    """Konfiguration für einen LLM-Agenten"""
    agent_id: str
    name: str
    description: str
    role: str  # admin, lead, worker, reviewer
    llm_model: str  # z.B. "gemini/gemini-2.5-flash"
    system_prompt: str
    specializations: List[str] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 4096
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "role": self.role,
            "llm_model": self.llm_model,
            "system_prompt": self.system_prompt[:200] + "..." if len(self.system_prompt) > 200 else self.system_prompt,
            "specializations": self.specializations,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class RouterRequest:
    """Eingehende Anfrage an den MCP Router"""
    target_agent: str
    user_message: str
    caller_id: str = "user"
    context: Dict[str, Any] = field(default_factory=dict)
    trace_id: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


@dataclass
class RouterResponse:
    """Antwort vom MCP Router"""
    agent_id: str
    llm_model: str
    response: str
    success: bool
    execution_time_ms: float = 0.0
    tokens_used: int = 0
    error: Optional[str] = None
    trace_id: Optional[str] = None


# ============================================================================
# Prompt Manager - Verwaltet Agent System-Prompts
# ============================================================================

class PromptManager:
    """
    Verwaltet System-Prompts für LLM-Agenten.
    Speichert Prompts in /var/tristar/prompts/ als JSON-Dateien.
    """

    def __init__(self, prompt_dir: str | Path = TRISTAR_DIR / "prompts"):
        self.prompt_dir = Path(prompt_dir)
        self.agents_dir = self.prompt_dir / "agents"
        self._agents: Dict[str, AgentConfig] = {}
        self._lock = asyncio.Lock()

        # Verzeichnisse erstellen
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self.agents_dir.mkdir(parents=True, exist_ok=True)

        # Initial laden
        self._load_agents_sync()
        logger.info(f"PromptManager initialisiert mit {len(self._agents)} Agenten")

    def _load_agents_sync(self):
        """Lädt alle Agenten synchron beim Start"""
        for filepath in self.agents_dir.glob("*.json"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    agent = AgentConfig(**data)
                    self._agents[agent.agent_id] = agent
            except Exception as e:
                logger.warning(f"Fehler beim Laden von {filepath}: {e}")

    async def reload_agents(self):
        """Lädt alle Agenten neu von der Festplatte"""
        async with self._lock:
            self._agents.clear()
            for filepath in self.agents_dir.glob("*.json"):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        agent = AgentConfig(**data)
                        self._agents[agent.agent_id] = agent
                except Exception as e:
                    logger.warning(f"Fehler beim Laden von {filepath}: {e}")
        logger.info(f"Agenten neu geladen: {len(self._agents)}")

    async def get_agent(self, agent_id: str) -> Optional[AgentConfig]:
        """Ruft einen Agenten ab"""
        return self._agents.get(agent_id)

    async def list_agents(self) -> List[AgentConfig]:
        """Listet alle Agenten"""
        return list(self._agents.values())

    async def create_agent(self, config: AgentConfig) -> AgentConfig:
        """Erstellt einen neuen Agenten"""
        async with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            config.created_at = now
            config.updated_at = now

            # Speichern
            filepath = self.agents_dir / f"{config.agent_id}.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump({
                    "agent_id": config.agent_id,
                    "name": config.name,
                    "description": config.description,
                    "role": config.role,
                    "llm_model": config.llm_model,
                    "system_prompt": config.system_prompt,
                    "specializations": config.specializations,
                    "temperature": config.temperature,
                    "max_tokens": config.max_tokens,
                    "enabled": config.enabled,
                    "created_at": config.created_at,
                    "updated_at": config.updated_at,
                }, f, indent=2, ensure_ascii=False)

            self._agents[config.agent_id] = config
            logger.info(f"Agent erstellt: {config.agent_id}")
            return config

    async def update_agent(self, agent_id: str, updates: Dict[str, Any]) -> Optional[AgentConfig]:
        """Aktualisiert einen Agenten"""
        async with self._lock:
            if agent_id not in self._agents:
                return None

            agent = self._agents[agent_id]

            # Updates anwenden
            for key, value in updates.items():
                if hasattr(agent, key) and key not in ("agent_id", "created_at"):
                    setattr(agent, key, value)

            agent.updated_at = datetime.now(timezone.utc).isoformat()

            # Speichern
            filepath = self.agents_dir / f"{agent_id}.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump({
                    "agent_id": agent.agent_id,
                    "name": agent.name,
                    "description": agent.description,
                    "role": agent.role,
                    "llm_model": agent.llm_model,
                    "system_prompt": agent.system_prompt,
                    "specializations": agent.specializations,
                    "temperature": agent.temperature,
                    "max_tokens": agent.max_tokens,
                    "enabled": agent.enabled,
                    "created_at": agent.created_at,
                    "updated_at": agent.updated_at,
                }, f, indent=2, ensure_ascii=False)

            logger.info(f"Agent aktualisiert: {agent_id}")
            return agent

    async def delete_agent(self, agent_id: str) -> bool:
        """Löscht einen Agenten"""
        async with self._lock:
            if agent_id not in self._agents:
                return False

            filepath = self.agents_dir / f"{agent_id}.json"
            if filepath.exists():
                filepath.unlink()

            del self._agents[agent_id]
            logger.info(f"Agent gelöscht: {agent_id}")
            return True

    async def get_system_prompt(self, agent_id: str) -> Optional[str]:
        """Ruft nur den System-Prompt eines Agenten ab"""
        agent = await self.get_agent(agent_id)
        return agent.system_prompt if agent else None

    async def update_system_prompt(self, agent_id: str, prompt: str) -> bool:
        """Aktualisiert nur den System-Prompt eines Agenten"""
        result = await self.update_agent(agent_id, {"system_prompt": prompt})
        return result is not None


# ============================================================================
# MCP Router - Zentrales Routing für LLM-Anfragen
# ============================================================================

class MCPRouter:
    """
    Zentraler Router für Multi-LLM Agent Anfragen.

    Features:
    - Dynamische Agent-Konfiguration via PromptManager
    - Integration mit TriForce (llm_mesh, rbac, circuit_breaker)
    - Automatisches Fallback bei Agent-Fehlern
    """

    def __init__(self, prompt_manager: PromptManager):
        self.prompt_manager = prompt_manager
        self._request_count = 0
        logger.info("MCPRouter initialisiert")

    async def route_request(self, request: RouterRequest) -> RouterResponse:
        """
        Routet eine Anfrage an den entsprechenden LLM-Agenten.

        1. Agent-Konfiguration laden
        2. RBAC-Check via TriForce
        3. System-Prompt mit User-Message kombinieren
        4. LLM-Aufruf via TriForce llm_mesh
        5. Antwort zurückgeben
        """
        import time
        import uuid
        from ..triforce.rbac import rbac_service
        from ..triforce.llm_mesh import llm_call

        start_time = time.time()
        trace_id = request.trace_id or str(uuid.uuid4())[:8]

        # 1. Agent-Konfiguration laden
        agent = await self.prompt_manager.get_agent(request.target_agent)
        if not agent:
            return RouterResponse(
                agent_id=request.target_agent,
                llm_model="unknown",
                response="",
                success=False,
                error=f"Agent '{request.target_agent}' nicht gefunden",
                trace_id=trace_id,
            )

        if not agent.enabled:
            return RouterResponse(
                agent_id=request.target_agent,
                llm_model=agent.llm_model,
                response="",
                success=False,
                error=f"Agent '{request.target_agent}' ist deaktiviert",
                trace_id=trace_id,
            )

        # 2. RBAC-Check
        if not rbac_service.can_call_llm(request.caller_id, request.target_agent):
            return RouterResponse(
                agent_id=request.target_agent,
                llm_model=agent.llm_model,
                response="",
                success=False,
                error=f"RBAC: '{request.caller_id}' darf '{request.target_agent}' nicht aufrufen",
                trace_id=trace_id,
            )

        # 3. Vollständigen Prompt erstellen
        full_prompt = self._build_full_prompt(agent, request)

        # 4. LLM-Aufruf via TriForce
        try:
            # Extrahiere den LLM-Namen aus dem Model-ID (z.B. "gemini" aus "gemini/gemini-2.5-flash")
            llm_target = agent.llm_model.split("/")[0] if "/" in agent.llm_model else agent.llm_model

            result = await llm_call(
                target=llm_target,
                prompt=full_prompt,
                caller_llm=request.caller_id,
                context=request.context,
                trace_id=trace_id,
                timeout=120,
            )

            execution_time_ms = (time.time() - start_time) * 1000

            if result.get("success"):
                return RouterResponse(
                    agent_id=request.target_agent,
                    llm_model=agent.llm_model,
                    response=result.get("response", ""),
                    success=True,
                    execution_time_ms=execution_time_ms,
                    tokens_used=len(result.get("response", "")) // 4,
                    trace_id=trace_id,
                )
            else:
                return RouterResponse(
                    agent_id=request.target_agent,
                    llm_model=agent.llm_model,
                    response="",
                    success=False,
                    execution_time_ms=execution_time_ms,
                    error=result.get("error", "Unbekannter Fehler"),
                    trace_id=trace_id,
                )

        except Exception as e:
            execution_time_ms = (time.time() - start_time) * 1000
            logger.error(f"LLM-Aufruf fehlgeschlagen: {e}")
            return RouterResponse(
                agent_id=request.target_agent,
                llm_model=agent.llm_model,
                response="",
                success=False,
                execution_time_ms=execution_time_ms,
                error=str(e),
                trace_id=trace_id,
            )

    def _build_full_prompt(self, agent: AgentConfig, request: RouterRequest) -> str:
        """Erstellt den vollständigen Prompt mit System-Prompt und User-Message"""
        parts = [agent.system_prompt]

        # Kontext hinzufügen falls vorhanden
        if request.context:
            context_str = "\n".join([f"{k}: {v}" for k, v in request.context.items()])
            parts.append(f"\n\nKONTEXT:\n{context_str}")

        # User-Message
        parts.append(f"\n\nANFRAGE:\n{request.user_message}")

        return "".join(parts)

    async def broadcast_to_agents(
        self,
        agents: List[str],
        message: str,
        caller_id: str = "system",
        trace_id: Optional[str] = None,
    ) -> Dict[str, RouterResponse]:
        """Sendet eine Nachricht an mehrere Agenten parallel"""
        tasks = []
        for agent_id in agents:
            request = RouterRequest(
                target_agent=agent_id,
                user_message=message,
                caller_id=caller_id,
                trace_id=trace_id,
            )
            tasks.append(self.route_request(request))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        return {
            agents[i]: (
                results[i] if isinstance(results[i], RouterResponse)
                else RouterResponse(
                    agent_id=agents[i],
                    llm_model="unknown",
                    response="",
                    success=False,
                    error=str(results[i]),
                )
            )
            for i in range(len(agents))
        }


# ============================================================================
# Default Agents - Werden beim ersten Start erstellt
# ============================================================================

DEFAULT_AGENTS = [
    AgentConfig(
        agent_id="gemini-lead",
        name="Gemini Lead",
        description="Haupt-Koordinator für Chain-Orchestration",
        role="lead",
        llm_model="gemini/gemini-2.5-flash",
        system_prompt="""Du bist Gemini Lead, der Haupt-Koordinator im TriStar Multi-LLM System.

DEINE ROLLE:
- Analysiere eingehende Aufgaben
- Erstelle Ausführungspläne
- Delegiere Teilaufgaben an spezialisierte Agenten
- Konsolidiere Ergebnisse

VERFÜGBARE AGENTEN:
- claude-worker: Coding, Analyse, Dokumentation
- deepseek-worker: Heavy Coding, Algorithmen
- qwen-worker: Multilingual, Vision
- mistral-reviewer: Code Review, Security
- cogito-reviewer: Logik, Debugging
- nova-admin: Deutsch, Dokumentation

Antworte strukturiert und präzise.""",
        specializations=["coordination", "planning", "research"],
        temperature=0.7,
    ),
    AgentConfig(
        agent_id="claude-worker",
        name="Claude Worker",
        description="Spezialist für Coding und Analyse",
        role="worker",
        llm_model="anthropic/claude-sonnet-4",
        system_prompt="""Du bist Claude Worker im TriStar System.

DEINE SPEZIALISIERUNG:
- Code-Generierung und Refactoring
- Komplexe Analyse-Aufgaben
- Dokumentation erstellen
- Architektur-Design

Liefere hochwertigen, gut dokumentierten Code.""",
        specializations=["coding", "analysis", "documentation"],
        temperature=0.5,
    ),
    AgentConfig(
        agent_id="deepseek-worker",
        name="DeepSeek Worker",
        description="Spezialist für komplexe Algorithmen",
        role="worker",
        llm_model="deepseek-v3.1:671b-cloud",
        system_prompt="""Du bist DeepSeek Worker im TriStar System.

DEINE SPEZIALISIERUNG:
- Komplexe Algorithmen
- Performance-Optimierung
- Mathematische Berechnungen
- Low-Level Code

Fokussiere auf Effizienz und Korrektheit.""",
        specializations=["algorithms", "optimization", "math"],
        temperature=0.3,
    ),
    AgentConfig(
        agent_id="qwen-worker",
        name="Qwen Worker",
        description="Spezialist für Multilingual und Vision",
        role="worker",
        llm_model="qwen3-vl:235b-cloud",
        system_prompt="""Du bist Qwen Worker im TriStar System.

DEINE SPEZIALISIERUNG:
- Mehrsprachige Inhalte
- Bild-Analyse (Vision)
- Übersetzungen
- Content-Lokalisierung

Unterstütze alle gängigen Sprachen.""",
        specializations=["multilingual", "vision", "translation"],
        temperature=0.6,
    ),
    AgentConfig(
        agent_id="mistral-reviewer",
        name="Mistral Reviewer",
        description="Code Review und Security Spezialist",
        role="reviewer",
        llm_model="mistral/mistral-medium-latest",
        system_prompt="""Du bist Mistral Reviewer im TriStar System.

DEINE SPEZIALISIERUNG:
- Code Review
- Security-Analyse
- Best Practices prüfen
- Vulnerability Detection

Sei kritisch aber konstruktiv.""",
        specializations=["review", "security", "best_practices"],
        temperature=0.4,
    ),
    AgentConfig(
        agent_id="cogito-reviewer",
        name="Cogito Reviewer",
        description="Logik und Debugging Spezialist",
        role="reviewer",
        llm_model="cogito-2.1:671b-cloud",
        system_prompt="""Du bist Cogito Reviewer im TriStar System.

DEINE SPEZIALISIERUNG:
- Logische Analyse
- Debugging
- Fehlersuche
- Konsistenz-Prüfung

Denke systematisch und gründlich.""",
        specializations=["logic", "debugging", "consistency"],
        temperature=0.3,
    ),
    AgentConfig(
        agent_id="kimi-lead",
        name="Kimi Lead",
        description="Long Context und Research Spezialist",
        role="lead",
        llm_model="kimi-k2:1t-cloud",
        system_prompt="""Du bist Kimi Lead im TriStar System.

DEINE SPEZIALISIERUNG:
- Lange Dokumente analysieren
- Research und Recherche
- Zusammenfassungen
- Deep Thinking

Nutze dein großes Kontextfenster optimal.""",
        specializations=["long_context", "research", "summarization"],
        temperature=0.6,
    ),
    AgentConfig(
        agent_id="nova-admin",
        name="Nova Admin",
        description="Deutscher Content und Admin",
        role="admin",
        llm_model="gpt-oss:20b-cloud",
        system_prompt="""Du bist Nova Admin im TriStar System.

DEINE SPEZIALISIERUNG:
- Deutsche Inhalte erstellen
- Dokumentation auf Deutsch
- Admin-Aufgaben
- Content Management

Schreibe professionelles Deutsch.""",
        specializations=["german", "documentation", "admin"],
        temperature=0.7,
    ),
]


async def initialize_default_agents(prompt_manager: PromptManager):
    """Erstellt die Standard-Agenten falls nicht vorhanden"""
    existing = await prompt_manager.list_agents()
    existing_ids = {a.agent_id for a in existing}

    for agent in DEFAULT_AGENTS:
        if agent.agent_id not in existing_ids:
            await prompt_manager.create_agent(agent)
            logger.info(f"Default-Agent erstellt: {agent.agent_id}")


# ============================================================================
# Singleton Instanzen
# ============================================================================

prompt_manager = PromptManager()
mcp_router = MCPRouter(prompt_manager)

# Default-Agenten werden lazy initialisiert beim ersten Zugriff
_default_agents_initialized = False


async def ensure_default_agents():
    """Initialize default agents if not already done (lazy initialization)."""
    global _default_agents_initialized
    if not _default_agents_initialized:
        await initialize_default_agents(prompt_manager)
        _default_agents_initialized = True
