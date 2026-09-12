"""
Group Chat Auto-Response Service
==================================

Automatisch API-basierte Agents in Group Chat Sessions antworten lassen.
Wird als Background-Task nach group_chat_ask gestartet.

Agents die automatisch antworten:
- mistral-api / groq-api / cerebras-api / openrouter-api resolve dynamically from ModelRegistry
- ollama-qwen / ollama-kimi keep their explicit Ollama Cloud tags

Web-Agents (claude-web, chatgpt-web) antworten via MCP — NICHT hier.

Version: 1.0.0
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ailinux.group_chat.auto_response")

# Bus für Live-Streaming an MCP-Clients
try:
    from app.mcp.group_chat_bus import gc_bus as _gc_bus
except Exception:
    _gc_bus = None

# Provider mapping for cloud agents. Concrete model IDs are resolved from the
# shared ModelRegistry at execution time so this service cannot drift stale.
API_AGENT_PROVIDERS: Dict[str, str] = {
    "mistral-api": "mistral",
    "groq-api": "groq",
    "cerebras-api": "cerebras",
    "openrouter-api": "openrouter",
}
OLLAMA_AGENT_MODELS: Dict[str, str] = {
    "ollama-qwen": "qwen3-coder:480b-cloud",
    "ollama-kimi": "kimi-k2-thinking:cloud",
}
API_AGENT_MODELS = frozenset((*API_AGENT_PROVIDERS, *OLLAMA_AGENT_MODELS))

# Which agents use Ollama directly (vs cloud API proxy)
OLLAMA_AGENTS = {"ollama-qwen", "ollama-kimi"}

# Timeout per provider (seconds)
PROVIDER_TIMEOUTS: Dict[str, int] = {
    "mistral-api": 60,
    "groq-api": 30,
    "cerebras-api": 30,
    "ollama-qwen": 120,
    "ollama-kimi": 180,
    "openrouter-api": 90,
}


def _build_prompt_for_agent(session, agent_id: str) -> str:
    """Baut einen fokussierten Prompt für den API-Agent."""
    # Sammle die relevanten Nachrichten
    topic = session.topic
    questions = []
    sub_tasks = []

    for msg in session.messages:
        if msg.type.value == "question":
            questions.append(msg.content)
        elif msg.type.value == "sub_task":
            # Nur Tasks die an diesen Agent oder an "all" gehen
            target = msg.metadata.get("target", "all")
            if target in (agent_id, "all"):
                sub_tasks.append(msg.content)
        elif msg.type.value == "analysis":
            questions.append(f"Gemini-Analyse: {msg.content[:500]}")

    question_text = "\n".join(questions) if questions else topic
    task_text = "\n---\n".join(sub_tasks) if sub_tasks else ""

    prompt = f"""Du bist {agent_id} im TriForce Multi-AI Group Chat.

THEMA: {topic}

FRAGE:
{question_text}
"""
    if task_text:
        prompt += f"""
DEIN SPEZIFISCHER TASK:
{task_text}
"""
    prompt += """
Antworte konkret und praxisnah. Max 500 Wörter. Wenn Code gefragt ist, gib Python-Code-Skizzen."""

    return prompt


async def _query_agent_model(agent_id: str, model: str, prompt: str, timeout: int) -> str:
    """Fragt ein Modell via chat_router oder Ollama direkt ab."""
    try:
        if agent_id in OLLAMA_AGENTS:
            # Ollama: direkt via aiohttp
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as http_session:
                async with http_session.post(
                    "http://localhost:11434/api/chat",
                    json={"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Ollama HTTP {resp.status}")
                    data = await resp.json()
                    return data.get("message", {}).get("content", "")
        else:
            # Cloud API via chat_router proxy
            from app.services.chat_router import api_proxy
            response = await asyncio.wait_for(
                api_proxy.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.4,
                    max_tokens=2048,
                ),
                timeout=timeout,
            )
            return response if isinstance(response, str) else str(response)
    except asyncio.TimeoutError:
        logger.warning(f"Auto-response timeout for {agent_id} ({model}) after {timeout}s")
        return f"[TIMEOUT nach {timeout}s - {agent_id}]"
    except Exception as e:
        logger.error(f"Auto-response error for {agent_id}: {e}")
        return f"[ERROR: {agent_id} - {str(e)[:200]}]"


async def auto_collect_api_responses(session_id: str) -> Dict[str, Any]:
    """
    Background-Task: Fragt alle API-Agents automatisch ab und postet ihre Antworten.
    
    Wird nach group_chat_ask als asyncio.create_task gestartet.
    """
    from app.services.group_chat import group_chat

    session = group_chat.get_session(session_id)
    if not session:
        return {"error": f"Session {session_id} not found"}

    # Nur API-Agents die noch pending sind
    api_pending = {
        pid for pid in session.pending_responses
        if pid in API_AGENT_MODELS
    }

    if not api_pending:
        logger.info(f"No API agents pending for {session_id}")
        return {"collected": 0}

    logger.info(f"Auto-collecting responses from {len(api_pending)} API agents for {session_id}")

    results = {}
    tasks = []

    async def _query_and_post(agent_id: str):
        if agent_id in OLLAMA_AGENT_MODELS:
            model = OLLAMA_AGENT_MODELS[agent_id]
        else:
            from app.services.provider_model_resolver import resolve_provider_model
            model = await resolve_provider_model(API_AGENT_PROVIDERS[agent_id])
        timeout = PROVIDER_TIMEOUTS.get(agent_id, 60)
        prompt = _build_prompt_for_agent(session, agent_id)

        response = await _query_agent_model(agent_id, model, prompt, timeout)
        
        # Antwort in den Group Chat posten
        result = group_chat.post_message(
            session_id=session_id,
            sender=agent_id,
            content=response,
            msg_type="response",
            metadata={"model": model, "auto_response": True},
        )
        # ChatLog: AI-Output loggen (kein User-Input)
        try:
            from app.services.agent_chat_logger import get_chat_logger
            get_chat_logger().log_message(
                session_id=session_id,
                agent_id=agent_id,
                content=response,
                model=model,
                msg_type="response",
            )
        except Exception as _log_err:
            logger.debug(f"ChatLog write skipped: {_log_err}")
        # Live-Push an alle SSE-Subscriber
        if _gc_bus:
            try:
                await _gc_bus.publish(
                    session_id=session_id,
                    sender=agent_id,
                    content=response,
                    msg_type="response",
                    metadata={"model": model, "auto_response": True},
                )
            except Exception as _bus_err:
                logger.debug(f"Bus publish failed (non-fatal): {_bus_err}")
        results[agent_id] = {
            "status": "ok" if not response.startswith("[") else "error",
            "model": model,
            "response_length": len(response),
        }
        logger.info(f"Auto-response from {agent_id}: {len(response)} chars")

    # Alle API-Agents parallel abfragen
    for agent_id in api_pending:
        tasks.append(_query_and_post(agent_id))

    await asyncio.gather(*tasks, return_exceptions=True)

    # Check ob alle pending beantwortet sind → Auto-Consolidation
    session = group_chat.get_session(session_id)  # Refresh
    remaining = session.pending_responses if session else set()
    
    collected = len(results)
    logger.info(f"Auto-collected {collected} responses for {session_id}. Remaining: {remaining}")

    return {
        "session_id": session_id,
        "collected": collected,
        "results": results,
        "remaining_pending": list(remaining),
    }


async def auto_respond_all_waiting() -> Dict[str, Any]:
    """
    Batch: Alle wartenden Sessions durchgehen und API-Agents antworten lassen.
    Nützlich für Sessions die vor dem Auto-Response-Service erstellt wurden.
    """
    from app.services.group_chat import group_chat

    waiting_sessions = [
        s for s in group_chat.sessions.values()
        if s.phase.value == "waiting" and s.pending_responses
    ]

    results = {}
    for session in waiting_sessions:
        api_pending = {pid for pid in session.pending_responses if pid in API_AGENT_MODELS}
        if api_pending:
            try:
                result = await auto_collect_api_responses(session.id)
                results[session.id] = result
            except Exception as e:
                results[session.id] = {"error": str(e)}

    return {
        "processed": len(results),
        "results": results,
    }
