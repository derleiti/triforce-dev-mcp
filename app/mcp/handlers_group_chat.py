"""
Group Chat MCP Handlers
========================

MCP-Tool-Handler für den Multi-AI Group Chat.
Diese Tools werden von Claude-Web, ChatGPT-Web und anderen via MCP aufgerufen.

Tools:
- group_chat_create    — Neue Session starten
- group_chat_ask       — Frage an die Gruppe (startet Gemini Lead)
- group_chat_message   — Nachricht posten (Web-AIs antworten hier)
- group_chat_read      — Nachrichten lesen
- group_chat_status    — Session-Status
- group_chat_list      — Alle aktiven Sessions
- group_chat_consolidate — Gemini konsolidiert
- group_chat_assign    — Coding-Task zuweisen

Version: 1.0.0
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("ailinux.mcp.group_chat")


# =============================================================================
# Tool Definitions (für tool_registry)
# =============================================================================

GROUP_CHAT_TOOLS = [
    {
        "name": "group_chat_create",
        "description": "Erstelle eine neue Multi-AI Group Chat Session. "
                      "Startet eine Gruppendiskussion zwischen Gemini (Lead), "
                      "Claude-Web, ChatGPT-Web und Coding-Agents.",
        "inputSchema": {
            "type": "object",
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "description": "Thema/Aufgabe für die Diskussion"},
                "participants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: Teilnehmer-IDs. Default: alle (gemini-lead, claude-web, chatgpt-web, claude-mcp, codex-mcp, gemini-mcp)",
                },
            },
        },
    },
    {
        "name": "group_chat_ask",
        "description": "Stelle eine Frage an die AI-Gruppe. Gemini Lead analysiert "
                      "und erstellt Sub-Tasks für Claude-Web und ChatGPT-Web. "
                      "Die Web-AIs antworten dann via group_chat_message.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Group Chat Session ID"},
                "question": {"type": "string", "description": "Optional: Zusätzliche Frage (sonst wird das Topic verwendet)"},
            },
        },
    },
    {
        "name": "group_chat_message",
        "description": "Poste eine Nachricht in den Group Chat. "
                      "Wird von Claude-Web und ChatGPT-Web genutzt um auf Sub-Tasks zu antworten.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id", "sender", "content"],
            "properties": {
                "session_id": {"type": "string", "description": "Group Chat Session ID"},
                "sender": {"type": "string", "description": "Deine ID (z.B. 'claude-web', 'chatgpt-web')"},
                "content": {"type": "string", "description": "Deine Antwort/Analyse"},
                "type": {
                    "type": "string",
                    "enum": ["response", "code_result", "review"],
                    "description": "Nachrichtentyp (default: response)",
                },
            },
        },
    },
    {
        "name": "group_chat_read",
        "description": "Lese Nachrichten aus dem Group Chat. Zeigt den aktuellen Stand "
                      "der Diskussion inkl. Sub-Tasks, Antworten und Status.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Group Chat Session ID"},
                "since": {"type": "string", "description": "Optional: Nur Nachrichten seit ISO-Timestamp"},
                "for_participant": {"type": "string", "description": "Optional: Nur Nachrichten für diesen Teilnehmer"},
                "limit": {"type": "integer", "description": "Max Nachrichten (default: 50)"},
            },
        },
    },
    {
        "name": "group_chat_status",
        "description": "Zeige den Status einer Group Chat Session.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Group Chat Session ID"},
            },
        },
    },
    {
        "name": "group_chat_list",
        "description": "Liste aller aktiven Group Chat Sessions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "active_only": {"type": "boolean", "description": "Nur aktive Sessions (default: true)"},
            },
        },
    },
    {
        "name": "group_chat_consolidate",
        "description": "Gemini Lead konsolidiert alle Antworten der Web-AIs "
                      "zu einer Zusammenfassung und einem Coding-Prompt.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Group Chat Session ID"},
            },
        },
    },
    {
        "name": "group_chat_assign",
        "description": "Weise den konsolidierten Coding-Task einem Agent zu. "
                      "CLI-Agents (claude-mcp, codex-mcp, gemini-mcp) führen sofort aus. "
                      "Web-Agents lesen den Task via group_chat_read.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Group Chat Session ID"},
                "coder": {
                    "type": "string",
                    "description": "Coding-Agent ID (default: auto). "
                                  "Optionen: claude-mcp, codex-mcp, gemini-mcp, claude-web, chatgpt-web",
                },
                "context": {"type": "string", "description": "Optional: Zusätzlicher Kontext für den Coder"},
            },
        },
    },
]


# =============================================================================
# Handler Functions
# =============================================================================

async def handle_group_chat_create(params: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.group_chat import group_chat
    topic = params.get("topic", "")
    if not topic:
        return {"error": "topic is required"}
    participants = params.get("participants")
    session = group_chat.create_session(topic, participants)
    return {
        "ok": True,
        "session": session.to_dict(),
        "hint": f"Session {session.id} erstellt. Nutze group_chat_ask um Gemini die Analyse starten zu lassen.",
    }


async def handle_group_chat_ask(params: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.group_chat import group_chat
    session_id = params.get("session_id", "")
    question = params.get("question", "")

    session = group_chat.get_session(session_id)
    if not session:
        return {"error": f"Session {session_id} not found"}

    # Optional: Extra-Frage hinzufügen
    if question:
        from app.services.group_chat import MessageType
        session.add_message("system", MessageType.QUESTION, question)

    return await group_chat.start_discussion(session_id)


async def handle_group_chat_message(params: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.group_chat import group_chat
    session_id = params.get("session_id", "")
    sender = params.get("sender", "")
    content = params.get("content", "")
    msg_type = params.get("type", "response")

    if not all([session_id, sender, content]):
        return {"error": "session_id, sender, and content are required"}

    return group_chat.post_message(session_id, sender, content, msg_type, params.get("metadata"))


async def handle_group_chat_enqueue(params: Dict[str, Any], request=None) -> Dict[str, Any]:
    """Queue an external user message for an existing MCP web-agent session.

    This does not invoke Gemini or another model. It is intended as the trusted
    reverse handoff primitive for local bridges such as Telegram/Desktop.
    """
    from app.services.group_chat import MessageType, group_chat
    from app.utils.mcp_security import is_internal_full_request

    if request is not None and not is_internal_full_request(request):
        return {
            "ok": False,
            "error": "group_chat_enqueue is restricted to trusted internal callers",
            "code": "INTERNAL_ONLY",
        }

    session_id = str(params.get("session_id", "")).strip()
    target = str(params.get("target", "")).strip()
    content = str(params.get("content", "")).strip()
    source = str(params.get("source", "external-bridge")).strip() or "external-bridge"
    metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}

    if not session_id or not target or not content:
        return {"ok": False, "error": "session_id, target, and content are required"}

    session = group_chat.get_session(session_id)
    if not session:
        return {"ok": False, "error": f"Session {session_id} not found"}
    if target not in session.participants:
        return {"ok": False, "error": f"Target {target} is not a participant of {session_id}"}

    msg = session.add_message(
        sender=source,
        msg_type=MessageType.QUESTION,
        content=content,
        metadata=metadata,
        addressed_to=target,
    )
    session.pending_responses.add(target)
    group_chat._save_session(session)
    logger.info("Group Chat %s: queued external message %s -> %s", session_id, source, target)

    return {
        "ok": True,
        "session_id": session_id,
        "message_id": msg.id,
        "target": target,
        "pending_responses": list(session.pending_responses),
    }


async def handle_group_chat_read(params: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.group_chat import group_chat
    session_id = params.get("session_id", "")
    try:
        limit = max(1, min(int(params.get("limit", 50)), 500))
    except (TypeError, ValueError):
        limit = 50
    return group_chat.read_messages(
        session_id,
        since=params.get("since"),
        for_participant=params.get("for_participant"),
        limit=limit,
    )


async def handle_group_chat_status(params: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.group_chat import group_chat
    session_id = params.get("session_id", "")
    session = group_chat.get_session(session_id)
    if not session:
        return {"error": f"Session {session_id} not found"}
    return {"ok": True, "session": session.to_dict()}


async def handle_group_chat_list(params: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.group_chat import group_chat
    active_only = params.get("active_only", True)
    sessions = group_chat.list_sessions(active_only)
    return {"ok": True, "count": len(sessions), "sessions": sessions}


async def handle_group_chat_consolidate(params: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.group_chat import group_chat
    session_id = params.get("session_id", "")
    return await group_chat.consolidate(session_id)


async def handle_group_chat_assign(params: Dict[str, Any], request=None) -> Dict[str, Any]:
    from app.services.group_chat import group_chat, DEFAULT_PARTICIPANTS
    from app.utils.mcp_security import is_internal_full_request

    session_id = params.get("session_id", "")
    coder = str(params.get("coder", "auto")).strip()
    context = params.get("context", "")
    ALLOWED_CODERS = {"auto", "claude-mcp", "codex-mcp", "gemini-mcp", "claude-web", "chatgpt-web"}
    CLI_CODERS = {"claude-mcp", "codex-mcp", "gemini-mcp"}

    if coder not in ALLOWED_CODERS and coder not in DEFAULT_PARTICIPANTS:
        return {"error": f"Invalid coder: {coder}. Allowed: {', '.join(sorted(ALLOWED_CODERS))}"}

    if request is not None and coder in CLI_CODERS and not is_internal_full_request(request):
        return {
            "ok": False,
            "error": "External callers may not assign CLI coders",
            "code": "EXTERNAL_CLI_BLOCKED",
        }

    return await group_chat.assign_coding_task(session_id, coder, context)


# =============================================================================
# Handler Registry
# =============================================================================

GROUP_CHAT_HANDLERS = {
    "group_chat_create": handle_group_chat_create,
    "group_chat_ask": handle_group_chat_ask,
    "group_chat_enqueue": handle_group_chat_enqueue,
    "group_chat_message": handle_group_chat_message,
    "group_chat_read": handle_group_chat_read,
    "group_chat_status": handle_group_chat_status,
    "group_chat_list": handle_group_chat_list,
    "group_chat_consolidate": handle_group_chat_consolidate,
    "group_chat_assign": handle_group_chat_assign,
}
