"""
Group Chat Orchestrator — Multi-AI Collaboration Service
=========================================================

Koordiniert eine KI-Gruppendiskussion zwischen:
- Gemini (Lead, via API) — analysiert, plant, konsolidiert
- Claude-Web (via MCP von claude.ai) — Architektur, Reasoning
- ChatGPT-Web (via MCP von chatgpt.com) — Code, Debugging, Planning
- Coding Agents (CLI: claude-mcp, codex-mcp, gemini-mcp) — Ausführung

Flow:
1. Frage/Task kommt rein
2. Gemini Lead analysiert → erstellt Sub-Fragen
3. Claude-Web + ChatGPT-Web antworten via MCP
4. Gemini Lead konsolidiert → erstellt Coding-Prompt
5. Bester Coding-Agent führt aus

Version: 1.0.0
Author: Markus Leitermann (derleiti)
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from app.paths import TRISTAR_DIR
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("ailinux.group_chat")

# Persistence
def _configured_group_chat_dir() -> Path:
    explicit_dir = os.getenv("TRISTAR_GROUP_CHAT_DIR")
    if explicit_dir:
        return Path(explicit_dir)
    return TRISTAR_DIR / "group_chat"


GROUP_CHAT_DIR = _configured_group_chat_dir()


def _fallback_group_chat_dir() -> Path:
    return Path(os.getenv("TRISTAR_FALLBACK_STATE_DIR", "/tmp/tristar")) / "group_chat"


def _switch_to_fallback_group_chat_dir(exc: OSError) -> Path:
    global GROUP_CHAT_DIR
    fallback_dir = _fallback_group_chat_dir()
    fallback_dir.mkdir(parents=True, exist_ok=True)
    if GROUP_CHAT_DIR != fallback_dir:
        logger.warning(
            "Group chat state dir %s unavailable (%s); using %s",
            GROUP_CHAT_DIR,
            exc,
            fallback_dir,
        )
        GROUP_CHAT_DIR = fallback_dir
    return GROUP_CHAT_DIR


def _ensure_group_chat_dir() -> Path:
    """Return a writable group-chat state dir, falling back for tests/sandboxes."""
    try:
        GROUP_CHAT_DIR.mkdir(parents=True, exist_ok=True)
        return GROUP_CHAT_DIR
    except OSError as exc:
        return _switch_to_fallback_group_chat_dir(exc)


# =============================================================================
# Enums & Data Models
# =============================================================================

class ParticipantRole(str, Enum):
    LEAD = "lead"               # Gemini — koordiniert
    WEB_ANALYST = "web_analyst"  # Claude-Web, ChatGPT-Web — Analyse via MCP
    CODER = "coder"             # CLI Agents — Ausführung
    OBSERVER = "observer"       # Read-only Zugang


class SessionPhase(str, Enum):
    CREATED = "created"
    ANALYZING = "analyzing"          # Gemini analysiert die Frage
    DISCUSSING = "discussing"        # Web-AIs diskutieren
    WAITING_RESPONSES = "waiting"    # Wartet auf MCP-Antworten
    CONSOLIDATING = "consolidating"  # Gemini fasst zusammen
    CODING = "coding"                # Coding-Agent arbeitet
    REVIEWING = "reviewing"          # Review-Phase
    COMPLETED = "completed"
    FAILED = "failed"


class MessageType(str, Enum):
    SYSTEM = "system"       # Orchestrator-Nachrichten
    QUESTION = "question"   # Original-Frage
    ANALYSIS = "analysis"   # Gemini Lead Analyse
    SUB_TASK = "sub_task"   # Sub-Frage an Web-AIs
    RESPONSE = "response"   # Antwort von Web-AIs
    SUMMARY = "summary"     # Konsolidierte Zusammenfassung
    CODE_TASK = "code_task"  # Coding-Auftrag
    CODE_RESULT = "code_result"  # Coding-Ergebnis
    REVIEW = "review"       # Review-Kommentar


@dataclass
class Participant:
    id: str                  # z.B. "gemini-lead", "claude-web", "chatgpt-web"
    name: str
    role: ParticipantRole
    provider: str            # "gemini", "claude", "chatgpt", "codex"
    connection: str          # "api", "mcp", "cli"
    capabilities: Set[str] = field(default_factory=set)
    active: bool = True
    last_seen: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role.value,
            "provider": self.provider,
            "connection": self.connection,
            "capabilities": list(self.capabilities),
            "active": self.active,
            "last_seen": self.last_seen,
        }


@dataclass
class ChatMessage:
    id: str
    session_id: str
    sender: str              # Participant ID
    type: MessageType
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    addressed_to: Optional[str] = None  # None = broadcast, sonst gezielt

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "sender": self.sender,
            "type": self.type.value,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "addressed_to": self.addressed_to,
        }


@dataclass
class GroupChatSession:
    id: str
    topic: str
    phase: SessionPhase = SessionPhase.CREATED
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    messages: List[ChatMessage] = field(default_factory=list)
    participants: Dict[str, Participant] = field(default_factory=dict)
    pending_responses: Set[str] = field(default_factory=set)  # IDs wartend
    assigned_coder: Optional[str] = None
    coding_result: Optional[str] = None
    final_summary: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "phase": self.phase.value,
            "created_at": self.created_at,
            "message_count": len(self.messages),
            "participants": {k: v.to_dict() for k, v in self.participants.items()},
            "pending_responses": list(self.pending_responses),
            "assigned_coder": self.assigned_coder,
            "has_result": self.coding_result is not None,
            "final_summary": self.final_summary,
            "error": self.error,
        }

    def add_message(self, sender: str, msg_type: MessageType, content: str,
                    metadata: Optional[Dict] = None, addressed_to: Optional[str] = None) -> ChatMessage:
        msg = ChatMessage(
            id=f"msg-{uuid.uuid4().hex[:12]}",
            session_id=self.id,
            sender=sender,
            type=msg_type,
            content=content,
            metadata=metadata or {},
            addressed_to=addressed_to,
        )
        self.messages.append(msg)
        return msg


# =============================================================================
# Default Participants
# =============================================================================

DEFAULT_PARTICIPANTS = {
    "gemini-lead": Participant(
        id="gemini-lead",
        name="Gemini Lead Orchestrator (Swarm)",
        role=ParticipantRole.LEAD,
        provider="gemini",
        connection="api",
        capabilities={"analyze", "plan", "consolidate", "research", "delegate"},
    ),
    "claude-web": Participant(
        id="claude-web",
        name="Claude Web Agent (claude.ai)",
        role=ParticipantRole.WEB_ANALYST,
        provider="claude",
        connection="mcp",
        capabilities={"reasoning", "architecture", "code_review", "long_context", "writing"},
    ),
    "chatgpt-web": Participant(
        id="chatgpt-web",
        name="ChatGPT Web Agent (chatgpt.com)",
        role=ParticipantRole.WEB_ANALYST,
        provider="chatgpt",
        connection="mcp",
        capabilities={"code", "debug", "planning", "general_reasoning", "terminal"},
    ),
    "claude-mcp": Participant(
        id="claude-mcp",
        name="Claude Code CLI Agent",
        role=ParticipantRole.CODER,
        provider="claude",
        connection="cli",
        capabilities={"code", "review", "analysis", "file_ops"},
    ),
    "codex-mcp": Participant(
        id="codex-mcp",
        name="Codex CLI Agent",
        role=ParticipantRole.CODER,
        provider="codex",
        connection="cli",
        capabilities={"code", "optimize", "refactor", "exec"},
    ),
    "gemini-mcp": Participant(
        id="gemini-mcp",
        name="Gemini CLI Agent",
        role=ParticipantRole.CODER,
        provider="gemini",
        connection="cli",
        capabilities={"code", "research", "search", "yolo"},
    ),
    # === API Models (Cloud Providers) ===
    "mistral-api": Participant(
        id="mistral-api",
        name="Mistral API (Lead Coordinator)",
        role=ParticipantRole.LEAD,
        provider="mistral",
        connection="api",
        capabilities={"analyze", "plan", "consolidate", "research", "delegate", "code"},
    ),
    "groq-api": Participant(
        id="groq-api",
        name="Groq API (Fast Inference)",
        role=ParticipantRole.CODER,
        provider="groq",
        connection="api",
        capabilities={"code", "fast_response", "debug", "general_reasoning"},
    ),
    "cerebras-api": Participant(
        id="cerebras-api",
        name="Cerebras API (Fast Inference)",
        role=ParticipantRole.CODER,
        provider="cerebras",
        connection="api",
        capabilities={"code", "fast_response", "math", "reasoning"},
    ),
    "openrouter-api": Participant(
        id="openrouter-api",
        name="OpenRouter API (Multi-Model)",
        role=ParticipantRole.CODER,
        provider="openrouter",
        connection="api",
        capabilities={"code", "research", "multi_model", "reasoning"},
    ),
    # === Ollama Cloud Models (NO local) ===
    "ollama-kimi": Participant(
        id="ollama-kimi",
        name="Kimi K2 Thinking (1T Cloud via Ollama)",
        role=ParticipantRole.CODER,
        provider="ollama",
        connection="ollama",
        capabilities={"code", "thinking", "reasoning", "long_context"},
    ),
    "ollama-qwen": Participant(
        id="ollama-qwen",
        name="Qwen3 Coder 480B (Cloud via Ollama)",
        role=ParticipantRole.WEB_ANALYST,
        provider="ollama",
        connection="ollama",
        capabilities={"code", "research", "reasoning", "long_context"},
    ),
}


# =============================================================================
# Group Chat Orchestrator
# =============================================================================

class GroupChatOrchestrator:
    """
    Orchestriert Multi-AI Gruppendiskussionen.

    Regeln:
    1. Gemini ist IMMER Lead — analysiert, delegiert, konsolidiert
    2. Claude-Web + ChatGPT-Web antworten über MCP-Tools
    3. Coding-Agents werden nach Konsolidierung zugewiesen
    4. Alle Nachrichten persistent in /var/tristar/group_chat/
    """

    def __init__(self):
        self.sessions: Dict[str, GroupChatSession] = {}
        self._load_sessions()

    # -------------------------------------------------------------------------
    # Session Management
    # -------------------------------------------------------------------------

    def create_session(self, topic: str, participants: Optional[List[str]] = None) -> GroupChatSession:
        """Erstellt eine neue Group Chat Session."""
        session_id = f"gc-{uuid.uuid4().hex[:8]}"

        session = GroupChatSession(id=session_id, topic=topic)

        # Default: alle Standard-Teilnehmer
        if participants:
            for pid in participants:
                if pid in DEFAULT_PARTICIPANTS:
                    session.participants[pid] = copy.deepcopy(DEFAULT_PARTICIPANTS[pid])
            if not session.participants:
                raise ValueError(
                    f"No valid participants. Allowed: {', '.join(sorted(DEFAULT_PARTICIPANTS.keys()))}"
                )
        else:
            session.participants = copy.deepcopy(DEFAULT_PARTICIPANTS)

        # System-Nachricht
        session.add_message(
            sender="system",
            msg_type=MessageType.SYSTEM,
            content=f"Group Chat gestartet: {topic}",
            metadata={"participants": list(session.participants.keys())},
        )

        self.sessions[session_id] = session
        self._save_session(session)

        # ChatLog: Session-Start loggen
        try:
            from app.services.agent_chat_logger import get_chat_logger
            get_chat_logger().log_session_start(
                session_id=session_id,
                topic=topic,
                participants=list(session.participants.keys()),
            )
        except Exception as _log_err:
            logger.debug(f"ChatLog session_start skipped: {_log_err}")

        logger.info(f"Group Chat erstellt: {session_id} | Topic: {topic}")
        return session

    def get_session(self, session_id: str) -> Optional[GroupChatSession]:
        return self.sessions.get(session_id)

    def list_sessions(self, active_only: bool = True) -> List[Dict[str, Any]]:
        sessions = []
        for s in self.sessions.values():
            if active_only and s.phase in (SessionPhase.COMPLETED, SessionPhase.FAILED):
                continue
            sessions.append(s.to_dict())
        return sessions

    # -------------------------------------------------------------------------
    # Phase 1: Gemini Lead Analysis
    # -------------------------------------------------------------------------

    async def _chat_with_lead_model(self, messages, temperature: float = 0.3, max_tokens: int = 4096):
        """Call the lead model with fallback chain. Tests/adapters can override.

        Reihenfolge konfigurierbar via ENV GROUP_CHAT_LEAD_MODELS
        (comma-separated canonical model IDs). If unset, models are resolved from
        the shared live ModelRegistry. Default provider order: Gemini -> Mistral -> Groq -> Cerebras.
        Patched 2026-06-15: Gemini-Quota-Ausfall darf nicht den Lead blockieren.
        """
        from app.services.chat_router import api_proxy
        import os

        env_chain = os.environ.get("GROUP_CHAT_LEAD_MODELS", "").strip()
        if env_chain:
            chain = [m.strip() for m in env_chain.split(",") if m.strip()]
        else:
            from app.services.provider_model_resolver import resolve_provider_model
            chain = []
            for provider in ("gemini", "mistral", "groq", "cerebras"):
                try:
                    chain.append(await resolve_provider_model(provider))
                except Exception as exc:
                    logger.warning("No current %s lead model available: %s", provider, exc)

        last_err = None
        for model in chain:
            try:
                analysis = await api_proxy.chat(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if model != chain[0]:
                    logger.warning(f"Lead-Model Fallback aktiv: {model} (primary failed)")
                return analysis, model
            except Exception as e:
                last_err = e
                logger.warning(f"Lead-Model {model} failed: {e}")
                continue
        raise RuntimeError(f"All lead models failed. Last error: {last_err}")

    async def start_discussion(self, session_id: str) -> Dict[str, Any]:
        """
        Gemini Lead analysiert die Frage und erstellt Sub-Tasks für Web-AIs.
        """
        session = self.sessions.get(session_id)
        if not session:
            return {"error": f"Session {session_id} not found"}

        session.phase = SessionPhase.ANALYZING

        # Gemini analysiert die Frage
        analysis_prompt = self._build_analysis_prompt(session)

        try:
            analysis_result = await self._chat_with_lead_model(
                messages=[{"role": "user", "content": analysis_prompt}],
                temperature=0.3,
                max_tokens=4096,
            )
            if isinstance(analysis_result, tuple):
                analysis, lead_model = analysis_result
            else:
                analysis, lead_model = analysis_result, "gemini/gemini-2.5-flash"









            # Analyse als Nachricht posten
            session.add_message(
                sender="gemini-lead",
                msg_type=MessageType.ANALYSIS,
                content=analysis,
                metadata={"model": lead_model},
            )

            # Sub-Tasks für Web-AIs extrahieren und posten
            sub_tasks = self._extract_sub_tasks(analysis)
            for task in sub_tasks:
                target = task.get("target", "all")
                session.add_message(
                    sender="gemini-lead",
                    msg_type=MessageType.SUB_TASK,
                    content=task["question"],
                    metadata={"target": target, "priority": task.get("priority", "normal")},
                    addressed_to=target if target != "all" else None,
                )

            # Warte auf Antworten der Web-AIs
            # Only API/Ollama agents are auto-respondable.
            # MCP web-agents (claude-web, chatgpt-web) can post manually
            # but must NOT block auto-consolidation.
            respondable = [pid for pid, p in session.participants.items()
                          if p.connection in ("api", "ollama")
                          and pid != "gemini-lead"]
            session.pending_responses = set(respondable)
            session.phase = SessionPhase.WAITING_RESPONSES

            self._save_session(session)

            # Fire auto-response for API/Ollama agents as background task
            try:
                from app.services.group_chat_auto_response import auto_collect_api_responses
                asyncio.ensure_future(auto_collect_api_responses(session_id))
            except ImportError:
                asyncio.ensure_future(self.auto_respond_agents(session_id))

            return {
                "session_id": session_id,
                "phase": session.phase.value,
                "analysis": analysis,
                "sub_tasks": sub_tasks,
                "waiting_for": list(session.pending_responses),
                "auto_responding": True,
            }

        except Exception as e:
            session.phase = SessionPhase.FAILED
            session.error = str(e)
            self._save_session(session)
            logger.error(f"Gemini analysis failed: {e}")
            return {"error": str(e)}


    # -------------------------------------------------------------------------
    # Phase 1.5: Auto-Respond API/Ollama Agents (Background)
    # -------------------------------------------------------------------------

    async def auto_respond_agents(self, session_id: str) -> Dict[str, Any]:
        """
        Automatisch alle API- und Ollama-Agents abfragen.
        MCP-Agents (claude-web, chatgpt-web) bekommen Notifications.
        Wird als Background-Task nach start_discussion gefeuert.
        """
        session = self.sessions.get(session_id)
        if not session:
            return {"error": f"Session {session_id} not found"}

        # Sammle Sub-Tasks
        sub_tasks = [m for m in session.messages if m.type == MessageType.SUB_TASK]
        if not sub_tasks:
            # Fallback: nutze das Topic als Frage
            question = session.topic
        else:
            # Kombiniere alle Sub-Tasks zu einem Prompt
            question = "\n".join([f"- {m.content}" for m in sub_tasks])

        results = {}
        tasks = []

        for pid in list(session.pending_responses):
            participant = session.participants.get(pid)
            if not participant:
                continue

            # Build agent-specific prompt
            agent_prompt = self._build_agent_prompt(session, participant, question)

            if participant.connection == "api":
                tasks.append(self._auto_respond_single(session, pid, agent_prompt, "api"))
            elif participant.connection == "ollama":
                tasks.append(self._auto_respond_single(session, pid, agent_prompt, "ollama"))
            elif participant.connection == "mcp":
                # MCP agents (claude-web, chatgpt-web) — send notification
                try:
                    from app.services.group_chat import MessageType
                    session.add_message(
                        sender="system",
                        msg_type=MessageType.SYSTEM,
                        content=f"@{pid}: Bitte lies die Sub-Tasks oben und antworte via group_chat_message.",
                        addressed_to=pid,
                    )
                    logger.info(f"Notification sent to MCP agent {pid}")
                except Exception as e:
                    logger.warning(f"Failed to notify {pid}: {e}")

        # Execute API/Ollama agents in parallel
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Check if auto-consolidation possible
        if not session.pending_responses:
            logger.info(f"All agents responded in {session_id} — auto-consolidating")
            return await self.consolidate(session_id)

        self._save_session(session)
        return {
            "session_id": session_id,
            "auto_responded": len(tasks),
            "still_pending": list(session.pending_responses),
        }

    async def _auto_respond_single(self, session: 'GroupChatSession', agent_id: str,
                                    prompt: str, connection_type: str):
        """Einzelnen API/Ollama Agent abfragen und Antwort posten."""
        try:
            if connection_type == "api":
                result = await self._execute_via_api(agent_id, prompt)
            elif connection_type == "ollama":
                result = await self._execute_via_ollama(agent_id, prompt)
            else:
                return

            response_text = result.get("response", "")
            if result.get("status") == "success" and response_text:
                self.post_message(
                    session.id, agent_id, response_text,
                    msg_type="response",
                    metadata={"model": result.get("model", "unknown"), "auto": True},
                )
                logger.info(f"Auto-response from {agent_id}: {len(response_text)} chars")
            else:
                error = result.get("error", "unknown error")
                self.post_message(
                    session.id, agent_id,
                    f"[ERROR] {agent_id} konnte nicht antworten: {error}",
                    msg_type="response",
                    metadata={"error": error, "auto": True},
                )
                logger.warning(f"Auto-response failed for {agent_id}: {error}")

        except Exception as e:
            logger.error(f"Auto-respond error for {agent_id}: {e}")
            # Remove from pending even on error
            session.pending_responses.discard(agent_id)

    def _build_agent_prompt(self, session: 'GroupChatSession',
                            participant: 'Participant', sub_tasks: str) -> str:
        """Baut einen spezifischen Prompt für jeden Agent."""
        caps = ", ".join(participant.capabilities) if participant.capabilities else "general"
        return f"""Du bist {participant.name} ({participant.id}).
Deine Stärken: {caps}

TOPIC: {session.topic}

SUB-TASKS:
{sub_tasks}

Antworte kurz und konkret (max 500 Wörter). Fokus auf deine Stärken.
Format: Problem → Lösung → Code-Skizze (wenn relevant)."""


    # -------------------------------------------------------------------------
    # Phase 2: Web-AI Responses (via MCP)
    # -------------------------------------------------------------------------

    def post_message(self, session_id: str, sender: str, content: str,
                     msg_type: str = "response", metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Web-AIs posten ihre Antworten via MCP.
        Wird von Claude-Web oder ChatGPT-Web aufgerufen.
        """
        session = self.sessions.get(session_id)
        if not session:
            return {"error": f"Session {session_id} not found"}

        # Sender validieren
        if sender not in session.participants:
            return {"error": f"Unknown participant: {sender}"}

        try:
            mtype = MessageType(msg_type)
        except ValueError:
            mtype = MessageType.RESPONSE

        msg = session.add_message(
            sender=sender,
            msg_type=mtype,
            content=content,
            metadata=metadata or {},
        )

        # Aus pending entfernen
        session.pending_responses.discard(sender)

        # Update last_seen
        if sender in session.participants:
            session.participants[sender].last_seen = datetime.now(timezone.utc).isoformat()

        self._save_session(session)

        logger.info(f"Group Chat {session_id}: {sender} hat geantwortet ({len(content)} chars)")

        result = {
            "message_id": msg.id,
            "session_id": session_id,
            "sender": sender,
            "pending_responses": list(session.pending_responses),
        }

        # Auto-Konsolidierung wenn alle geantwortet haben
        if not session.pending_responses and session.phase == SessionPhase.WAITING_RESPONSES:
            result["all_responded"] = True
            result["hint"] = "Alle Web-AIs haben geantwortet. Starte Konsolidierung mit group_chat_consolidate."

        return result

    def read_messages(self, session_id: str, since: Optional[str] = None,
                      for_participant: Optional[str] = None,
                      limit: int = 50) -> Dict[str, Any]:
        """
        Liest Nachrichten aus dem Group Chat.
        Web-AIs rufen das via MCP auf um den Stand zu sehen.
        """
        session = self.sessions.get(session_id)
        if not session:
            return {"error": f"Session {session_id} not found"}

        messages = session.messages

        # Filter: nur Nachrichten seit Zeitpunkt
        if since:
            messages = [m for m in messages if m.timestamp > since]

        # Filter: nur an bestimmten Teilnehmer oder broadcast
        if for_participant:
            messages = [m for m in messages
                       if m.addressed_to is None or m.addressed_to == for_participant
                       or m.addressed_to == "all"]

        # Limit (safe coercion)
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit = 50
        messages = messages[-limit:]

        return {
            "session_id": session_id,
            "topic": session.topic,
            "phase": session.phase.value,
            "message_count": len(messages),
            "messages": [m.to_dict() for m in messages],
            "pending_responses": list(session.pending_responses),
            "participants": {k: v.to_dict() for k, v in session.participants.items()},
        }

    # -------------------------------------------------------------------------
    # Phase 3: Gemini Lead Consolidation
    # -------------------------------------------------------------------------

    async def consolidate(self, session_id: str) -> Dict[str, Any]:
        """
        Gemini Lead konsolidiert alle Antworten und erstellt Coding-Prompt.
        """
        session = self.sessions.get(session_id)
        if not session:
            return {"error": f"Session {session_id} not found"}

        session.phase = SessionPhase.CONSOLIDATING

        consolidation_prompt = self._build_consolidation_prompt(session)

        try:
            # Nutze dieselbe Lead-Fallback-Chain wie in der Analysephase
            summary_result = await self._chat_with_lead_model(
                messages=[{"role": "user", "content": consolidation_prompt}],
                temperature=0.2,
                max_tokens=6000,
            )
            if isinstance(summary_result, tuple):
                summary, lead_model = summary_result
            else:
                summary, lead_model = summary_result, "gemini/gemini-2.5-flash"









            session.add_message(
                sender="gemini-lead",
                msg_type=MessageType.SUMMARY,
                content=summary,
                metadata={"model": lead_model, "phase": "consolidation"},
            )

            session.final_summary = summary
            self._save_session(session)

            return {
                "session_id": session_id,
                "phase": session.phase.value,
                "summary": summary,
                "hint": "Zusammenfassung erstellt. Nutze group_chat_assign um einen Coding-Agent zuzuweisen.",
            }

        except Exception as e:
            session.error = str(e)
            self._save_session(session)
            return {"error": str(e)}

    # -------------------------------------------------------------------------
    # Phase 4: Coding Task Delegation
    # -------------------------------------------------------------------------

    async def assign_coding_task(self, session_id: str,
                                  coder_id: str = "auto",
                                  additional_context: str = "") -> Dict[str, Any]:
        """
        Weist den konsolidierten Task einem Coding-Agent zu.
        """
        session = self.sessions.get(session_id)
        if not session:
            return {"error": f"Session {session_id} not found"}

        if not session.final_summary:
            return {"error": "Keine Zusammenfassung vorhanden. Zuerst consolidate aufrufen."}

        # Auto-Select besten Coder
        if coder_id == "auto":
            coder_id = self._select_best_coder(session)

        if coder_id not in session.participants:
            return {"error": f"Unknown coder: {coder_id}"}

        session.phase = SessionPhase.CODING
        session.assigned_coder = coder_id

        # Coding-Prompt zusammenbauen
        coding_prompt = self._build_coding_prompt(session, additional_context)

        session.add_message(
            sender="gemini-lead",
            msg_type=MessageType.CODE_TASK,
            content=coding_prompt,
            metadata={"assigned_to": coder_id},
            addressed_to=coder_id,
        )

        self._save_session(session)

        # Wenn CLI-Agent: direkt ausführen via AgentController
        coder = session.participants[coder_id]
        if coder.connection == "cli":
            result = await self._execute_via_cli(coder_id, coding_prompt)
            session.coding_result = result.get("response", "")
            session.add_message(
                sender=coder_id,
                msg_type=MessageType.CODE_RESULT,
                content=session.coding_result,
                metadata=result,
            )
            session.phase = SessionPhase.COMPLETED
            self._save_session(session)

            return {
                "session_id": session_id,
                "coder": coder_id,
                "connection": "cli",
                "phase": session.phase.value,
                "result": session.coding_result[:2000],  # Preview
            }


        # Wenn API-Agent: direkt via Cloud API ausfuehren
        if coder.connection == "api":
            result = await self._execute_via_api(coder_id, coding_prompt)
            session.coding_result = result.get("response", "")
            session.add_message(
                sender=coder_id,
                msg_type=MessageType.CODE_RESULT,
                content=session.coding_result,
                metadata=result,
            )
            session.phase = SessionPhase.COMPLETED
            self._save_session(session)
            return {
                "session_id": session_id,
                "coder": coder_id,
                "connection": "api",
                "model": result.get("model", "unknown"),
                "phase": session.phase.value,
                "result": session.coding_result[:2000],
            }

        # Wenn Ollama-Agent: lokal oder cloud via Ollama
        if coder.connection == "ollama":
            result = await self._execute_via_ollama(coder_id, coding_prompt)
            session.coding_result = result.get("response", "")
            session.add_message(
                sender=coder_id,
                msg_type=MessageType.CODE_RESULT,
                content=session.coding_result,
                metadata=result,
            )
            session.phase = SessionPhase.COMPLETED
            self._save_session(session)
            return {
                "session_id": session_id,
                "coder": coder_id,
                "connection": "ollama",
                "model": result.get("model", "unknown"),
                "phase": session.phase.value,
                "result": session.coding_result[:2000],
            }
        # Wenn Web-Agent mit MCP: Task steht in der Queue, Agent liest via MCP
        return {
            "session_id": session_id,
            "coder": coder_id,
            "connection": "mcp",
            "phase": session.phase.value,
            "hint": f"Coding-Task an {coder_id} gepostet. Agent liest via group_chat_read.",
        }

    # -------------------------------------------------------------------------
    # Full Auto Pipeline
    # -------------------------------------------------------------------------

    async def auto_orchestrate(self, topic: str, question: str,
                                coder: str = "auto",
                                wait_timeout: int = 600) -> Dict[str, Any]:
        """
        Kompletter Auto-Workflow:
        1. Session erstellen
        2. Gemini analysiert
        3. Wartet auf Web-AI Antworten (oder timeout)
        4. Konsolidiert
        5. Delegiert an Coder
        """
        # 1. Session
        session = self.create_session(topic)
        session.add_message("system", MessageType.QUESTION, question)

        # 2. Gemini analysiert
        analysis_result = await self.start_discussion(session.id)
        if "error" in analysis_result:
            return analysis_result

        # 3. Warte auf Web-AIs (polling)
        start = time.time()
        while session.pending_responses and (time.time() - start) < wait_timeout:
            await asyncio.sleep(5)

        if session.pending_responses:
            logger.warning(f"Timeout: {session.pending_responses} haben nicht geantwortet")
            session.add_message(
                "system", MessageType.SYSTEM,
                f"Timeout: {list(session.pending_responses)} haben nicht geantwortet. Fahre fort.",
            )

        # 4. Konsolidierung
        consolidation = await self.consolidate(session.id)
        if "error" in consolidation:
            return consolidation

        # 5. Coding-Task
        result = await self.assign_coding_task(session.id, coder_id=coder)
        return {
            "session_id": session.id,
            "topic": topic,
            "phases_completed": ["analysis", "discussion", "consolidation", "coding"],
            "result": result,
        }

    # -------------------------------------------------------------------------
    # Prompt Builders
    # -------------------------------------------------------------------------

    def _build_analysis_prompt(self, session: GroupChatSession) -> str:
        questions = [m.content for m in session.messages if m.type == MessageType.QUESTION]
        question_text = "\n".join(questions) if questions else session.topic

        return f"""Du bist der Lead Coordinator in einer Multi-AI Gruppendiskussion.

AUFGABE: Analysiere die folgende Frage/Aufgabe und erstelle gezielte Sub-Fragen
für die anderen AI-Teilnehmer.

ORIGINAL-FRAGE:
{question_text}

TEILNEHMER:
- claude-web: Spezialist für Architektur, Reasoning, Code-Review, Long-Context
- chatgpt-web: Spezialist für Code, Debugging, Planning, Terminal-Workflows

DEINE AUFGABE:
1. Analysiere die Frage kurz (2-3 Sätze)
2. Erstelle 2-3 gezielte Sub-Fragen für die Spezialisten
3. Format STRIKT als JSON:

```json
{{
  "analysis": "Kurze Analyse der Aufgabe",
  "sub_tasks": [
    {{"target": "claude-web", "question": "...", "priority": "high"}},
    {{"target": "chatgpt-web", "question": "...", "priority": "high"}},
    {{"target": "all", "question": "...", "priority": "normal"}}
  ]
}}
```

Antworte NUR mit dem JSON. Keine Erklärungen drumherum."""

    def _build_consolidation_prompt(self, session: GroupChatSession) -> str:
        # Sammle alle relevanten Nachrichten
        question = ""
        analysis = ""
        responses = []

        for msg in session.messages:
            if msg.type == MessageType.QUESTION:
                question = msg.content
            elif msg.type == MessageType.ANALYSIS:
                analysis = msg.content
            elif msg.type == MessageType.RESPONSE:
                responses.append(f"[{msg.sender}]: {msg.content}")

        responses_text = "\n\n---\n\n".join(responses) if responses else "(Keine Antworten)"

        return f"""Du bist der Lead Coordinator. Konsolidiere die Ergebnisse der Gruppendiskussion.

ORIGINAL-FRAGE:
{question or session.topic}

DEINE ANALYSE:
{analysis}

ANTWORTEN DER SPEZIALISTEN:
{responses_text}

ERSTELLE:
1. Eine klare Zusammenfassung aller Erkenntnisse
2. Einen konkreten, ausführbaren Coding-Prompt mit:
   - Genaue Dateien und Pfade
   - Schritt-für-Schritt Anweisungen
   - Erwartetes Ergebnis
   - Tests/Validierung

Der Coding-Prompt muss so detailliert sein, dass ein CLI-Agent ihn direkt ausführen kann.

Format:
## Zusammenfassung
...

## Coding-Prompt
...

## Validierung
..."""

    def _build_coding_prompt(self, session: GroupChatSession, extra: str = "") -> str:
        parts = [
            f"# Coding Task: {session.topic}\n",
            f"## Konsolidierte Analyse\n{session.final_summary}\n",
        ]
        if extra:
            parts.append(f"## Zusätzlicher Kontext\n{extra}\n")

        parts.append(
            "## Regeln\n"
            "- Arbeite im TriForce Backend: /home/zombie/triforce\n"
            "- Teste jede Änderung\n"
            "- Erstelle Backups vor destruktiven Ops\n"
            "- Poste das Ergebnis via group_chat_message wenn fertig\n"
        )
        return "\n".join(parts)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _extract_sub_tasks(self, analysis: str) -> List[Dict[str, Any]]:
        """Extrahiert Sub-Tasks aus Gemini's JSON-Antwort."""
        try:
            # JSON aus Markdown Code-Block extrahieren
            if "```json" in analysis:
                json_str = analysis.split("```json")[1].split("```")[0].strip()
            elif "```" in analysis:
                json_str = analysis.split("```")[1].split("```")[0].strip()
            else:
                json_str = analysis.strip()

            data = json.loads(json_str)
            return data.get("sub_tasks", [])
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.warning(f"Sub-task extraction failed: {e}")
            # Fallback: eine generische Frage an alle
            return [{"target": "all", "question": analysis, "priority": "normal"}]

    def _select_best_coder(self, session: GroupChatSession) -> str:
        """Wählt den besten Coding-Agent basierend auf Task-Typ."""
        coders = {pid: p for pid, p in session.participants.items()
                  if p.role == ParticipantRole.CODER}

        # Einfache Heuristik: claude-mcp für Review/Architektur, codex-mcp für Code
        topic_lower = session.topic.lower()
        if any(w in topic_lower for w in ["review", "architektur", "analyse", "security"]):
            return "claude-mcp"
        elif any(w in topic_lower for w in ["code", "bug", "fix", "implement", "refactor"]):
            return "codex-mcp"
        else:
            return "claude-mcp"  # Default

    async def _execute_via_cli(self, agent_id: str, prompt: str) -> Dict[str, Any]:
        """Führt Task via CLI AgentController aus."""
        try:
            from app.services.tristar.agent_controller import agent_controller
            result = await agent_controller.call_agent(agent_id, prompt, timeout=600)
            return result
        except Exception as e:
            logger.error(f"CLI execution failed for {agent_id}: {e}")
            return {"status": "error", "error": str(e), "response": ""}


    # Model mappings for API/Ollama execution
    API_PROVIDER_MAP = {
        "mistral-api": "mistral",
        "groq-api": "groq",
        "cerebras-api": "cerebras",
        "openrouter-api": "openrouter",
        "gemini-lead": "gemini",
    }
    OLLAMA_MODEL_MAP = {
        "ollama-kimi": "kimi-k2-thinking:cloud",
        "ollama-qwen": "qwen3-coder:480b-cloud",
    }

    async def _execute_via_api(self, agent_id: str, prompt: str) -> Dict[str, Any]:
        """Fuehrt Task via Cloud API aus (Mistral, Groq, Cerebras, OpenRouter)."""
        try:
            from app.services.chat_router import api_proxy
            from app.services.provider_model_resolver import resolve_provider_model
            provider = self.API_PROVIDER_MAP.get(agent_id, "mistral")
            model = await resolve_provider_model(provider)
            messages = [{"role": "user", "content": prompt}]
            response = await api_proxy.chat(model=model, messages=messages, temperature=0.3, max_tokens=4096)
            return {"status": "success", "response": response, "model": model}
        except Exception as e:
            logger.error(f"API execution failed for {agent_id}: {e}")
            return {"status": "error", "error": str(e), "response": ""}

    async def _execute_via_ollama(self, agent_id: str, prompt: str) -> Dict[str, Any]:
        """Fuehrt Task via Ollama aus (lokal oder cloud Modelle)."""
        try:
            import aiohttp
            model = self.OLLAMA_MODEL_MAP.get(agent_id, "kimi-k2-thinking:cloud")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
                async with session.post(
                    "http://localhost:11434/api/chat",
                    json={"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return {"status": "error", "error": f"Ollama {resp.status}: {error}", "response": ""}
                    data = await resp.json()
                    content = data.get("message", {}).get("content", "")
                    return {"status": "success", "response": content, "model": model}
        except Exception as e:
            logger.error(f"Ollama execution failed for {agent_id}: {e}")
            return {"status": "error", "error": str(e), "response": ""}
    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def _save_session(self, session: GroupChatSession):
        """Speichert Session als JSON."""
        directory = _ensure_group_chat_dir()
        filepath = directory / f"{session.id}.json"
        data = {
            "id": session.id,
            "topic": session.topic,
            "phase": session.phase.value,
            "created_at": session.created_at,
            "messages": [m.to_dict() for m in session.messages],
            "participants": {k: v.to_dict() for k, v in session.participants.items()},
            "pending_responses": list(session.pending_responses),
            "assigned_coder": session.assigned_coder,
            "coding_result": session.coding_result,
            "final_summary": session.final_summary,
            "error": session.error,
        }
        serialized = json.dumps(data, indent=2, ensure_ascii=False)
        try:
            filepath.write_text(serialized)
        except OSError as exc:
            directory = _switch_to_fallback_group_chat_dir(exc)
            filepath = directory / f"{session.id}.json"
            filepath.write_text(serialized)

    def _load_sessions(self):
        """Lädt alle Sessions aus dem Dateisystem."""
        try:
            directory = _ensure_group_chat_dir()
        except OSError as exc:
            logger.warning("Group chat sessions not loaded; state dir unavailable: %s", exc)
            return
        for filepath in directory.glob("gc-*.json"):
            try:
                data = json.loads(filepath.read_text())
                session = GroupChatSession(
                    id=data["id"],
                    topic=data["topic"],
                    phase=SessionPhase(data.get("phase", "created")),
                    created_at=data.get("created_at", ""),
                    assigned_coder=data.get("assigned_coder"),
                    coding_result=data.get("coding_result"),
                    final_summary=data.get("final_summary"),
                    error=data.get("error"),
                )
                session.pending_responses = set(data.get("pending_responses", []))

                # Participants wiederherstellen
                for pid, pdata in data.get("participants", {}).items():
                    if pid in DEFAULT_PARTICIPANTS:
                        session.participants[pid] = DEFAULT_PARTICIPANTS[pid]

                # Messages wiederherstellen
                for mdata in data.get("messages", []):
                    msg = ChatMessage(
                        id=mdata["id"],
                        session_id=mdata["session_id"],
                        sender=mdata["sender"],
                        type=MessageType(mdata["type"]),
                        content=mdata["content"],
                        metadata=mdata.get("metadata", {}),
                        timestamp=mdata.get("timestamp", ""),
                        addressed_to=mdata.get("addressed_to"),
                    )
                    session.messages.append(msg)

                self.sessions[session.id] = session
            except Exception as e:
                logger.error(f"Failed to load session {filepath}: {e}")

    def _schedule_recovery(self):
        """Re-trigger auto-response for sessions that were waiting when backend restarted.
        Called by app startup event after event loop is running.
        """
        waiting = [s for s in self.sessions.values()
                   if s.phase == SessionPhase.WAITING_RESPONSES and s.pending_responses]
        if not waiting:
            logger.debug("Recovery: no waiting sessions found")
            return
        logger.info(f"Recovery: {len(waiting)} waiting sessions — scheduling auto-response")
        async def _recover_all():
            for session in waiting:
                api_pending = {pid for pid in session.pending_responses
                               if session.participants.get(pid) and
                               session.participants[pid].connection in ("api", "ollama")}
                if api_pending:
                    try:
                        from app.services.group_chat_auto_response import auto_collect_api_responses
                        await auto_collect_api_responses(session.id)
                        logger.info(f"Recovery completed for {session.id}")
                    except Exception as e:
                        logger.error(f"Recovery failed for {session.id}: {e}")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_recover_all())
        except RuntimeError:
            # No running loop yet — log for startup hook to call later
            logger.warning("Recovery deferred: no running event loop at import time")


# =============================================================================
# Singleton
# =============================================================================

group_chat = GroupChatOrchestrator()
group_chat._schedule_recovery()
