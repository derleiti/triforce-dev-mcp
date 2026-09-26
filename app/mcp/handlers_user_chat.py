"""
MCP Handler: User-in-Group-Chat + Model Picker
================================================

Tools:
- model_picker         — Zeigt auswählbare Modelle gruppiert nach Provider
- user_group_chat_start — Startet einen User-Interactive-Group-Chat
- user_group_chat_reply — User sendet Nachricht, alle KIs antworten sequenziell
- user_group_chat_status — Aktuellen Chat-Status abrufen

Konzept:
  - User wählt 1-5 Modelle via model_picker
  - user_group_chat_start erstellt Session mit diesen Modellen
  - Bei jedem user_group_chat_reply bekommt JEDES Modell den
    kompletten bisherigen Chat-Verlauf (User + alle KI-Antworten)
  - Responses werden sequenziell gestreamt und geloggt
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ailinux.user_chat")

# Limits
MAX_MODELS_INTERACTIVE = 5
MAX_MODELS_SWARM = 15
MAX_HISTORY_MESSAGES = 60     # after this, oldest entries get trimmed
MAX_CONCURRENT_QUERIES = 4    # semaphore for parallel model queries
_QUERY_SEM: Optional[asyncio.Semaphore] = None

def _get_sem() -> asyncio.Semaphore:
    global _QUERY_SEM
    if _QUERY_SEM is None:
        _QUERY_SEM = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)
    return _QUERY_SEM

# In-Memory Store für aktive User-Chat-Sessions
_USER_CHAT_SESSIONS: Dict[str, Dict] = {}


# =============================================================================
# Tool Definitions
# =============================================================================

USER_CHAT_TOOLS = [
    {
        "name": "model_picker",
        "description": (
            "Zeigt alle verfügbaren LLM-Modelle gruppiert nach Provider als auswählbare Liste. "
            "Für Swarm-Modus alle Modelle, für User-Group-Chat bis zu 5 empfohlen. "
            "filter: 'all' | 'ollama' | 'groq' | 'mistral' | 'cerebras' | 'openrouter' | 'gemini' | 'anthropic' | 'fast' | 'smart'"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Provider-Filter oder 'fast'/'smart' für vordefinierte Sets",
                    "default": "all",
                },
                "max_per_provider": {
                    "type": "integer",
                    "description": "Max Modelle pro Provider anzeigen (0=alle)",
                    "default": 5,
                },
            },
        },
    },
    {
        "name": "user_group_chat_start",
        "description": (
            "Startet einen User-Interactive-Group-Chat mit ausgewählten Modellen. "
            "model_ids: Liste der Modell-IDs aus model_picker. Max 5 für interaktiven Chat, "
            "beliebig viele für Swarm. "
            "topic: Thema/Aufgabe für den Chat."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Liste der gewählten Modell-IDs",
                },
                "topic": {
                    "type": "string",
                    "description": "Thema oder initiale Aufgabe",
                },
                "mode": {
                    "type": "string",
                    "enum": ["interactive", "swarm"],
                    "description": "interactive=User antwortet, swarm=alle parallel",
                    "default": "interactive",
                },
            },
            "required": ["model_ids", "topic"],
        },
    },
    {
        "name": "user_group_chat_reply",
        "description": (
            "Sendet eine User-Nachricht an alle KIs im aktiven Group-Chat. "
            "Alle KIs bekommen den kompletten Chat-Verlauf und antworten der Reihe nach. "
            "session_id: aus user_group_chat_start, oder 'latest'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session-ID oder 'latest'"},
                "message": {"type": "string", "description": "User-Nachricht an alle KIs"},
            },
            "required": ["session_id", "message"],
        },
    },
    {
        "name": "user_group_chat_status",
        "description": "Aktuellen Status und letzten Chat-Verlauf einer User-Group-Chat-Session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session-ID oder 'latest'"},
            },
            "required": ["session_id"],
        },
    },
]


# =============================================================================
# Helper
# =============================================================================

def _resolve_session(session_id: str) -> Optional[str]:
    if session_id == "latest":
        if not _USER_CHAT_SESSIONS:
            return None
        return max(_USER_CHAT_SESSIONS.keys(),
                   key=lambda k: _USER_CHAT_SESSIONS[k]["created_at"])
    return session_id if session_id in _USER_CHAT_SESSIONS else None


def _build_context_messages(session: Dict, new_user_msg: str) -> List[Dict]:
    """Baut den Chat-Verlauf als messages-Array auf (mit History-Trimming)."""
    messages = []
    # System-Prompt mit Topic
    messages.append({
        "role": "system",
        "content": (
            f"Du bist Teil eines Multi-KI-Group-Chats zum Thema: {session['topic']}\n"
            f"Andere teilnehmende Modelle: {', '.join(session['model_ids'])}\n"
            f"Antworte präzise. Du siehst den Chatverlauf aller Teilnehmer."
        )
    })
    # Bisheriger Verlauf — trimmed to last MAX_HISTORY_MESSAGES entries
    history = session["history"]
    if len(history) > MAX_HISTORY_MESSAGES:
        trimmed = len(history) - MAX_HISTORY_MESSAGES
        messages.append({
            "role": "system",
            "content": f"[{trimmed} ältere Nachrichten ausgeblendet]"
        })
        history = history[-MAX_HISTORY_MESSAGES:]

    for entry in history:
        if entry["role"] == "user":
            messages.append({"role": "user", "content": entry["content"]})
        else:
            messages.append({
                "role": "assistant",
                "content": f"[{entry['model_id']}]: {entry['content']}"
            })
    # Neue User-Nachricht
    messages.append({"role": "user", "content": new_user_msg})
    return messages


async def _query_model(model_id: str, messages: List[Dict], timeout: int = 30) -> str:
    """Einzelnes Modell abfragen — direkt via HTTP."""
    import httpx
    provider = model_id.split("/")[0] if "/" in model_id else "unknown"
    model_name = model_id.split("/", 1)[1] if "/" in model_id else model_id

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Ollama — direkt via /api/chat (Cloud-Stubs laufen intern über Ollama)
            if provider == "ollama":
                from app.services.ollama_node_router import ollama_candidates
                last_error = "[Ollama nicht erreichbar]"
                for endpoint in ollama_candidates(model_name):
                    try:
                        r = await client.post(
                            f"{endpoint.base_url}/api/chat",
                            json={"model": model_name, "messages": messages, "stream": False},
                        )
                    except (httpx.ConnectError, httpx.TimeoutException):
                        continue
                    if r.status_code == 200:
                        return r.json().get("message", {}).get("content", "") or "[leer]"
                    last_error = f"[Ollama {r.status_code}: {r.text[:100]}]"
                return last_error

            # Alle anderen Provider — über TriForce /v1/chat/completions
            r = await client.post(
                "http://localhost:9000/v1/chat/completions",
                json={"model": model_id, "messages": messages,
                      "max_tokens": 1024, "temperature": 0.7, "stream": False},
            )
            d = r.json()
            return (d.get("text") or
                    d.get("choices", [{}])[0].get("message", {}).get("content") or
                    d.get("content") or f"[Fehler: {str(d)[:100]}]")

    except asyncio.TimeoutError:
        return f"[TIMEOUT: {model_id}]"
    except Exception as e:
        return f"[FEHLER: {model_id} — {str(e)[:150]}]"


# =============================================================================
# Handlers
# =============================================================================

async def handle_user_chat_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:

    if name == "model_picker":
        filter_arg = args.get("filter", "all")
        max_per = int(args.get("max_per_provider", 5))

        # Modelle aus Registry laden
        try:
            from app.services.model_registry import registry
            await registry.ensure_loaded()
            all_models = list(registry._models.values())
        except Exception:
            # Fallback: /v1/models HTTP
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.get("http://localhost:9000/v1/models") as r:
                    data = await r.json()
            all_models_raw = data.get("data", [])
            all_models = [type("M", (), m)() for m in all_models_raw]

        # Predefined filter sets
        FAST_MODELS = ["groq/llama-3.1-8b-instant", "cerebras/llama3.1-8b",
                       "groq/llama-3.3-70b-versatile", "mistral/mistral-small-latest"]
        SMART_MODELS = ["mistral/mistral-large-latest", "openrouter/anthropic/claude-opus-4",
                        "gemini/gemini-2.5-pro", "openrouter/meta-llama/llama-4-maverick",
                        "ollama/kimi-k2-thinking:cloud"]

        # Nach Provider gruppieren
        by_provider: Dict[str, List] = {}
        for m in all_models:
            mid = m.id if hasattr(m, "id") else m.get("id", "")
            provider = m.provider if hasattr(m, "provider") else m.get("provider", mid.split("/")[0] if "/" in mid else "unknown")

            # Agents ausfiltern (cli-Agents sind keine LLMs)
            if any(x in mid.lower() for x in ["claude-mcp", "codex-mcp", "gemini-mcp", "opencode"]):
                continue

            if filter_arg == "fast":
                if mid not in FAST_MODELS:
                    continue
            elif filter_arg == "smart":
                if mid not in SMART_MODELS:
                    continue
            elif filter_arg != "all":
                if provider != filter_arg:
                    continue

            by_provider.setdefault(provider, []).append(mid)

        # Markdown-Ausgabe
        lines = ["## 🤖 Verfügbare Modelle\n"]
        lines.append("Wähle Modelle für deinen Group-Chat (max 5 für interaktiv, beliebig für Swarm):\n")

        all_filtered = []
        for provider in sorted(by_provider.keys()):
            models = by_provider[provider]
            if max_per:
                models = models[:max_per]
            lines.append(f"\n### {provider.upper()} ({len(by_provider[provider])} gesamt)")
            for mid in models:
                lines.append(f"- `{mid}`")
            all_filtered.extend(models)

        lines.append(f"\n---\n**{len(all_filtered)} Modelle angezeigt** "
                     f"({sum(len(v) for v in by_provider.values())} total)")
        lines.append("\n💡 *Tipp: Nutze `user_group_chat_start(model_ids=[...], topic='...')` um loszulegen.*")

        return {
            "markdown": "\n".join(lines),
            "models_by_provider": {k: v for k, v in by_provider.items()},
            "total": sum(len(v) for v in by_provider.values()),
            "fast_set": FAST_MODELS,
            "smart_set": SMART_MODELS,
        }

    elif name == "user_group_chat_start":
        raw_model_ids = args.get("model_ids", [])
        topic = args.get("topic", "Allgemeiner Chat")
        mode = args.get("mode", "interactive")

        if not raw_model_ids:
            return {"error": "Keine Modelle angegeben. Nutze model_picker um Modelle auszuwählen."}

        # Deduplicate while preserving order
        seen = set()
        model_ids = []
        for mid in raw_model_ids:
            mid = str(mid).strip()
            if mid and mid not in seen:
                seen.add(mid)
                model_ids.append(mid)

        if not model_ids:
            return {"error": "Keine gültigen Modelle nach Bereinigung."}

        # Enforce limits
        limit = MAX_MODELS_INTERACTIVE if mode == "interactive" else MAX_MODELS_SWARM
        if len(model_ids) > limit:
            return {"error": f"Maximal {limit} Modelle im {mode}-Modus erlaubt, {len(model_ids)} angegeben."}

        # Validate model IDs exist in registry
        try:
            from app.services.model_registry import get_model_registry
            registry = get_model_registry()
            invalid = []
            for mid in model_ids:
                info = await registry.get_model(mid)
                if not info:
                    invalid.append(mid)
            if invalid:
                return {"error": f"Unbekannte Modelle: {', '.join(invalid)}. Nutze model_picker für gültige IDs."}
        except Exception as e:
            logger.warning(f"Model validation skipped: {e}")

        session_id = f"uc-{uuid.uuid4().hex[:8]}"
        _USER_CHAT_SESSIONS[session_id] = {
            "session_id": session_id,
            "topic": topic,
            "model_ids": model_ids,
            "mode": mode,
            "history": [],
            "created_at": time.time(),
            "turn_count": 0,
        }

        model_list = "\n".join(f"  - `{m}`" for m in model_ids)
        markdown = (
            f"## ✅ Group-Chat gestartet\n\n"
            f"**Session:** `{session_id}`  \n"
            f"**Topic:** {topic}  \n"
            f"**Modus:** {mode}  \n"
            f"**Modelle ({len(model_ids)}):**\n{model_list}\n\n"
            f"---\nNutze `user_group_chat_reply(session_id='{session_id}', message='...')` "
            f"um die erste Nachricht zu senden."
        )

        # ChatLog starten
        try:
            from app.services.agent_chat_logger import get_chat_logger
            get_chat_logger().log_session_start(session_id, topic, model_ids)
        except Exception:
            pass

        return {
            "session_id": session_id,
            "model_ids": model_ids,
            "mode": mode,
            "markdown": markdown,
        }

    elif name == "user_group_chat_reply":
        raw_sid = args.get("session_id", "latest")
        session_id = _resolve_session(raw_sid)
        message = args.get("message", "")

        if not session_id:
            return {"error": f"Session '{raw_sid}' nicht gefunden. Starte mit user_group_chat_start."}
        if not message.strip():
            return {"error": "Leere Nachricht."}

        session = _USER_CHAT_SESSIONS[session_id]
        session["turn_count"] += 1
        turn = session["turn_count"]

        # User-Nachricht in History (wird NICHT geloggt per Design)
        session["history"].append({
            "role": "user",
            "content": message,
            "turn": turn,
        })

        # KIs parallel abfragen mit Semaphore-Limit
        context = _build_context_messages(session, message)
        responses = []
        parts = [f"## Turn {turn} — User\n> {message}\n"]

        sem = _get_sem()

        async def _bounded_query(mid: str) -> Dict:
            provider = mid.split("/")[0] if "/" in mid else "unknown"
            timeout = 120 if provider == "ollama" else 45
            async with sem:
                text = await _query_model(mid, context, timeout)
            return {"model_id": mid, "content": text}

        tasks = [_bounded_query(mid) for mid in session["model_ids"]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                result = {"model_id": "?", "content": f"[FEHLER: {result}]"}
            responses.append(result)
            session["history"].append({
                "role": "assistant",
                "model_id": result["model_id"],
                "content": result["content"],
                "turn": turn,
            })
            short_id = result["model_id"].split("/")[-1] if "/" in result["model_id"] else result["model_id"]
            parts.append(f"\n### 🤖 {short_id}\n{result['content']}")

            # ChatLog: nur AI-Output loggen
            try:
                from app.services.agent_chat_logger import get_chat_logger
                get_chat_logger().log_message(
                    session_id=session_id,
                    agent_id=short_id,
                    content=result["content"],
                    model=result["model_id"],
                    msg_type="response",
                )
            except Exception:
                pass

        parts.append(f"\n---\n*{len(responses)} Modelle haben geantwortet. "
                     f"Nutze `user_group_chat_reply` für die nächste Runde.*")

        return {
            "session_id": session_id,
            "turn": turn,
            "responses": responses,
            "markdown": "\n".join(parts),
            "history_length": len(session["history"]),
        }

    elif name == "user_group_chat_status":
        raw_sid = args.get("session_id", "latest")
        session_id = _resolve_session(raw_sid)

        if not session_id:
            return {
                "active_sessions": list(_USER_CHAT_SESSIONS.keys()),
                "error": f"Session '{raw_sid}' nicht gefunden.",
            }

        session = _USER_CHAT_SESSIONS[session_id]
        age_min = round((time.time() - session["created_at"]) / 60, 1)

        # Letzten 3 Turns als Markdown
        parts = [f"## Status: `{session_id}`\n"]
        parts.append(f"**Topic:** {session['topic']}  ")
        parts.append(f"**Modelle:** {', '.join(f'`{m}`' for m in session['model_ids'])}  ")
        parts.append(f"**Turns:** {session['turn_count']} | **Alter:** {age_min}min\n\n---")

        # Letzte 6 History-Einträge
        for entry in session["history"][-6:]:
            if entry["role"] == "user":
                parts.append(f"\n**User (Turn {entry['turn']}):** {entry['content'][:200]}")
            else:
                mid = entry.get("model_id", "?").split("/")[-1]
                parts.append(f"\n**{mid}:** {entry['content'][:300]}...")

        return {
            "session_id": session_id,
            "topic": session["topic"],
            "model_ids": session["model_ids"],
            "turn_count": session["turn_count"],
            "age_minutes": age_min,
            "markdown": "\n".join(parts),
        }

    return {"error": f"Unbekanntes Tool: {name}"}
