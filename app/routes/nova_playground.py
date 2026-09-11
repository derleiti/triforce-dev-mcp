"""
Nova Playground v1.0
====================
Stateless Web-Endpoint für ailinux.me/playground (WordPress-Einbettung).
Kein Login, kein Session-Overhead.

POST /v1/nova/playground
  { "message": "...", "url": null, "mode": "auto" }

Modes:
  auto    → entscheidet anhand Message (URL drin? → fetch, sonst search+chat)
  search  → web_search + LLM-Antwort
  fetch   → URL crawlen + zusammenfassen
  chat    → nur LLM ohne Web (schnell)
"""
from __future__ import annotations

import logging
import re
import ipaddress
import socket
from urllib.parse import urljoin, urlparse
from typing import Any, Dict, Optional, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field


logger = logging.getLogger("ailinux.nova_playground")

router = APIRouter(prefix="/nova/playground", tags=["nova-playground"])

_URL_RE = re.compile(r"https?://[^\s]+")


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------

class PlaygroundRequest(BaseModel):
    message: str
    url: Optional[str] = None
    mode: Literal["auto", "search", "fetch", "chat"] = "auto"
    lang: Literal["de", "en"] = "de"
    max_results: int = Field(default=3, ge=1, le=8)
    model: Optional[str] = None
    history: list[dict[str, str]] = Field(default_factory=list)
    context: Optional[str] = None
    tools: list[str] = Field(default_factory=lambda: ["web_search", "crawl_url"])


class PlaygroundResponse(BaseModel):
    ok:      bool
    mode:    str
    answer:  str
    sources: list = Field(default_factory=list)
    activity: list[dict[str, Any]] = Field(default_factory=list)
    error:   Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _web_search(query: str, max_results: int = 3, lang: str = "de") -> list[dict]:
    """Use TriForce's canonical multi-source web search, with SearXNG as fallback."""
    normalized = []
    # If a concrete public hostname is part of the query, ground the result in
    # that site directly instead of accepting search-engine home pages.
    domain_match = re.search(r"(?<![@\w])([a-z0-9][a-z0-9.-]+\.[a-z]{2,})(?:/[^\s]*)?", query.lower())
    if domain_match:
        direct_url = "https://" + domain_match.group(1).rstrip(".") + "/"
        snippet = await _fetch_url(direct_url)
        if snippet:
            normalized.append({"title": domain_match.group(1), "url": direct_url, "snippet": snippet[:900], "source": "direct"})

    try:
        from app.services.web_search import search_web
        data = await search_web(query, num_results=max(max_results * 3, 10), per_page=max(max_results * 3, 10), lang=lang)
        results = data.get("results", []) if isinstance(data, dict) else []
        blocked_hosts = {"google.com", "google.de", "bing.com", "de.bing.com", "search.yahoo.com", "de.search.yahoo.com", "ipv4.google.com"}
        seen = {item["url"] for item in normalized}
        for r in results:
            url = r.get("url", "")
            if not url or url in seen:
                continue
            host = (urlparse(url).hostname or "").lower()
            if host in blocked_hosts:
                continue
            normalized.append({
                "title": r.get("title", ""),
                "url": url,
                "snippet": r.get("snippet", r.get("body", r.get("content", "")))[:600],
                "source": r.get("source", "web"),
            })
            seen.add(url)
            if len(normalized) >= max_results:
                break
        if normalized:
            return normalized[:max_results]
    except Exception as e:
        logger.warning("playground canonical search failed: %s", e)

    import aiohttp, urllib.parse
    try:
        params = urllib.parse.urlencode({"q": query, "format": "json", "language": lang})
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://localhost:8888/search?{params}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
                return [
                    {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:600], "source": "searxng"}
                    for r in data.get("results", [])[:max_results]
                    if r.get("url")
                ]
    except Exception as e:
        logger.warning("playground searxng fallback failed: %s", e)
        return []


async def _is_safe_public_url(url: str) -> bool:
    """Reject non-HTTP and private/local targets before public crawl requests."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False
        infos = await __import__("asyncio").to_thread(socket.getaddrinfo, host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                return False
        return True
    except Exception:
        return False


async def _fetch_url(url: str) -> str:
    """Fetch public text content while validating every redirect hop."""
    import aiohttp

    headers = {"User-Agent": "Mozilla/5.0 (Nova-Playground/1.1; +https://ailinux.me)"}
    current_url = url
    max_bytes = 2_000_000
    try:
        async with aiohttp.ClientSession() as session:
            for _hop in range(4):
                if not await _is_safe_public_url(current_url):
                    logger.warning("playground crawl rejected unsafe URL: %s", current_url)
                    return ""
                async with session.get(
                    current_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                    allow_redirects=False,
                ) as resp:
                    if resp.status in {301, 302, 303, 307, 308}:
                        location = resp.headers.get("Location", "").strip()
                        if not location:
                            return ""
                        next_url = urljoin(current_url, location)
                        if not await _is_safe_public_url(next_url):
                            logger.warning("playground crawl rejected unsafe redirect: %s -> %s", current_url, next_url)
                            return ""
                        current_url = next_url
                        continue
                    if resp.status != 200:
                        return ""
                    content_type = (resp.headers.get("Content-Type") or "").lower()
                    if content_type and not any(t in content_type for t in ("text/html", "text/plain", "application/xhtml+xml")):
                        return ""
                    if resp.content_length is not None and resp.content_length > max_bytes:
                        return ""
                    body = await resp.content.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        return ""
                    charset = resp.charset or "utf-8"
                    html = body.decode(charset, errors="replace")
                    break
            else:
                return ""

        text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()[:4000]
    except Exception as e:
        logger.debug("playground fetch: %s", e)
        return ""


async def _llm_answer(prompt: str, lang: str = "de", model: Optional[str] = None, history: Optional[list[dict[str, str]]] = None, context: Optional[str] = None) -> tuple[str, str]:
    """Schneller LLM-Call direkt via chat_service (Ollama/Groq Fallback)."""
    sys_prompt = (
        "Du bist Nova, ein hilfreicher KI-Assistent von AILinux. "
        "Antworte präzise und auf Deutsch." if lang == "de" else
        "You are Nova, a helpful AI assistant by AILinux. Be concise."
    )
    if context:
        sys_prompt += (
            "\n\nNutze den bereitgestellten Seiten-/Artikelkontext als Hintergrundwissen. "
            "Wiederhole oder fasse ihn nicht ungefragt komplett zusammen. Antworte strukturiert "
            "auf die konkrete Frage und trenne Kontextwissen von neu recherchierten Informationen."
            if lang == "de" else
            "\n\nUse the supplied page/article context as background. Do not repeat or summarize it unless asked. "
            "Answer the concrete question in a structured way and distinguish supplied context from newly researched information."
        )
        sys_prompt += "\n\nCONTEXT:\n" + context[:12000]

    messages = [{"role": "system", "content": sys_prompt}]
    for item in (history or [])[-12:]:
        role = item.get("role", "")
        content = item.get("content", "")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content[:6000]})
    messages.append({"role": "user", "content": prompt})
    # Provider failover: honor the selected model first, then known-good
    # server-backed general chat models. A stale frontend model must not make
    # the public playground return an empty answer.
    from app.services.chat_router import APIProxy
    proxy = APIProxy()
    candidates = []
    for candidate in [model, "mistral/mistral-small-latest", "mistral/mistral-medium-latest"]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        try:
            answer = await proxy.chat(
                model=candidate,
                messages=messages,
                max_tokens=1200,
            )
            if answer and answer.strip():
                if candidate != model and model:
                    logger.info("playground model fallback %s -> %s", model, candidate)
                return answer.strip(), candidate
        except Exception as e:
            logger.warning("playground provider failed model=%s error=%s", candidate, e)

    # Fallback: Ollama direkt
    try:
        import aiohttp
        payload = {
            "model": "qwen3:8b",
            "messages": messages,
            "stream": False,
            "options": {"num_predict": 800, "temperature": 0.4},
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "http://localhost:11434/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("message", {}).get("content", "").strip(), "ollama/qwen3:8b"
    except Exception as e:
        logger.warning(f"playground ollama fallback: {e}")

    return "", ""


def _choose_mode(message: str, url: Optional[str], requested: str, tools: list[str]) -> tuple[str, Optional[str]]:
    """Select the least-privileged mode. Web tools are used only when intent warrants it."""
    if requested != "auto":
        return requested, url

    match = _URL_RE.search(message)
    if match and "crawl_url" in tools:
        return "fetch", url or match.group(0)

    lowered = message.lower()
    search_markers = (
        "such im web", "suche im web", "recherchier", "google", "websuche",
        "search the web", "look up", "research", "latest", "aktuell", "heute",
        "news", "quelle", "quellen", "source", "sources",
    )
    if "web_search" in tools and any(marker in lowered for marker in search_markers):
        return "search", url
    return "chat", url


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post("", response_model=PlaygroundResponse)
async def nova_playground(req: PlaygroundRequest) -> Dict[str, Any]:
    message = req.message.strip()
    if not message:
        return {"ok": False, "mode": "error", "answer": "", "sources": [],
                "error": "message darf nicht leer sein"}

    allowed_tools = [t for t in req.tools if t in {"web_search", "crawl_url"}]
    mode, url = _choose_mode(message, req.url, req.mode, allowed_tools)

    # ── FETCH MODE ─────────────────────────────────────────────────────────
    if mode == "fetch":
        if not url:
            url_match = _URL_RE.search(message)
            url = url_match.group(0) if url_match else None
        if not url:
            return {"ok": False, "mode": "fetch", "answer": "",
                    "sources": [], "error": "Keine URL gefunden"}

        content = await _fetch_url(url)
        if not content:
            return {"ok": False, "mode": "fetch", "answer": "",
                    "sources": [], "error": f"URL konnte nicht geladen werden: {url}"}

        question = message.replace(url, "").strip() or "Fasse den Inhalt zusammen."
        prompt = (
            f"URL: {url}\n\nINHALT:\n{content}\n\n"
            f"AUFGABE: {question}"
        )
        answer, used_model = await _llm_answer(prompt, req.lang, req.model, req.history, req.context)
        if not answer:
            answer = content[:800] + "..."

        activity = [{
            "type": "tool", "name": "crawl_url", "status": "completed", "url": url,
        }]
        if used_model:
            activity.append({"type": "model", "name": used_model, "status": "completed"})
        return {"ok": True, "mode": "fetch", "answer": answer,
                "sources": [{"url": url}], "activity": activity}

    # ── SEARCH MODE ────────────────────────────────────────────────────────
    if mode == "search":
        results = await _web_search(message, req.max_results, req.lang)

        if not results:
            # Fallback: nur LLM ohne Web
            answer, used_model = await _llm_answer(message, req.lang, req.model, req.history, req.context)
            activity = [{"type": "tool", "name": "web_search", "status": "no_results", "result_count": 0}]
            if used_model:
                activity.append({"type": "model", "name": used_model, "status": "completed"})
            return {"ok": True, "mode": "chat_fallback", "answer": answer,
                    "sources": [], "activity": activity}

        # Snippets zusammenbauen
        context_parts = []
        sources = []
        for r in results[:req.max_results]:
            title   = r.get("title", "")
            snippet = r.get("snippet", r.get("body", r.get("content", "")))[:500]
            link    = r.get("url", r.get("href", r.get("link", "")))
            if snippet:
                context_parts.append(f"[{title}]\n{snippet}")
            if link:
                sources.append({"title": title, "url": link})

        context = "\n\n".join(context_parts)
        prompt = (
            f"SUCHANFRAGE: {message}\n\n"
            f"SUCHERGEBNISSE:\n{context}\n\n"
            "Beantworte die Anfrage basierend auf den Suchergebnissen. "
            "Nenne am Ende die wichtigsten Quellen."
        )
        answer, used_model = await _llm_answer(prompt, req.lang, req.model, req.history, req.context)
        if not answer:
            answer = context[:600]

        activity = [{
            "type": "tool", "name": "web_search", "status": "completed",
            "result_count": len(sources),
        }]
        if used_model:
            activity.append({"type": "model", "name": used_model, "status": "completed"})
        return {"ok": True, "mode": "search", "answer": answer,
                "sources": sources, "activity": activity}

    # ── CHAT MODE (kein Web) ───────────────────────────────────────────────
    answer, used_model = await _llm_answer(message, req.lang, req.model, req.history, req.context)
    activity = []
    if used_model:
        activity.append({"type": "model", "name": used_model, "status": "completed"})
    return {"ok": True, "mode": "chat", "answer": answer, "sources": [], "activity": activity}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health")
async def playground_health() -> Dict[str, Any]:
    return {"ok": True, "endpoint": "/v1/nova/playground", "modes": ["auto", "search", "fetch", "chat"]}


# ---------------------------------------------------------------------------
# Agent Mode — spawn_for_user() Integration (#6)
# ---------------------------------------------------------------------------

class AgentRequest(BaseModel):
    topic:      str
    prompt:     str = ""
    agent_id:   str = "claude-mcp"
    model_id:   str = ""


@router.post("/agent")
async def nova_playground_agent(req: AgentRequest) -> Dict[str, Any]:
    """
    Startet einen dedizierten Agent-Session via spawn_for_user().
    Gibt session_id zurück — Ergebnis via /nova/playground/agent/{session_id}/result abrufbar.
    """
    topic  = req.topic.strip()
    prompt = req.prompt.strip() or f"Analysiere und bearbeite: {topic}"
    if not topic:
        return {"ok": False, "error": "topic darf nicht leer sein"}
    try:
        from app.services.agent_spawner import get_agent_spawner
        spawner = get_agent_spawner()
        result  = await spawner.spawn_for_user(
            topic=topic,
            custom_prompt=prompt,
            agent_id=req.agent_id or "claude-mcp",
            model_id=req.model_id or None,
        )
        return {"ok": True, "mode": "agent", **result}
    except Exception as e:
        logger.error(f"playground agent spawn: {e}")
        return {"ok": False, "error": str(e)}


@router.get("/agent/{session_id}/result")
async def nova_playground_agent_result(session_id: str) -> Dict[str, Any]:
    """Gibt den aktuellen Status + letztes Ergebnis einer Agent-Session zurück."""
    try:
        from app.services.agent_spawner import get_agent_spawner
        spawner  = get_agent_spawner()
        sessions = spawner._sessions
        session  = sessions.get(session_id)
        if not session:
            return {"ok": False, "error": f"Session nicht gefunden: {session_id}"}
        return {
            "ok":           True,
            "session_id":   session_id,
            "status":       session.status,
            "agent_id":     session.agent_id,
            "last_response": session.last_response[:2000] if session.last_response else "",
            "message_count": len(session.messages),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
