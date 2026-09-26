"""
Nova Agent Router v1.0 — Spezialisierte Task-Verteilung
=========================================================
Konzept:
  - Jedes Modell (Ollama + API) kann Agent werden
  - Tasks bekommen eine ID + Komplex-Score
  - Router wählt optimalen Agent nach Skill-Ranking
  - Kleine Tasks → kleine schnelle Modelle
  - Große Tasks → spezialisierte Modelle
  
Skill-Kategorien:
  code, debug, math, creative, reasoning, search, 
  system, planning, summarize, translate

MCP Tools:
  agent_task_create  - Neuen Task erstellen + Agent assignen
  agent_task_status  - Task-Status abfragen
  agent_skill_list   - Verfügbare Agents + Rankings anzeigen
  agent_skill_update - Skill-Score eines Agents updaten (nach Task)
"""

import asyncio
import json
import os
import tempfile
import logging
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("ailinux.mcp.agent_router")

# Modell-ID → CLI-Agent-ID Mapping
# Nur Modelle mit einem laufenden CLI-Agent werden direkt executiert.
# Alle anderen (Ollama, Mistral etc.) gehen via /v1/chat (HTTP).
MODEL_TO_CLI_AGENT: Dict[str, str] = {
    "gemini-2.0-flash":         "gemini-mcp",
    "gemini-2.5-pro":           "gemini-mcp",
    "claude-sonnet-4-20250514": "claude-mcp",
    "gpt-4o":                   "codex-mcp",
    "nova-account/chatgpt":     "codex-mcp",
    "nova-account/claude":      "claude-mcp",
    "nova-account/google":      "gemini-mcp",
}

async def _execute_task_background(task_id: str, model: str, prompt: str) -> None:
    """Führt Task asynchron aus und speichert Ergebnis im Store."""
    start_ms = time.time()

    # State → running
    data = _load_tasks()
    if task_id in data["tasks"]:
        data["tasks"][task_id]["state"] = "running"
        data["tasks"][task_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save_tasks(data)

    result_text = None
    error_text  = None

    try:
        cli_agent = MODEL_TO_CLI_AGENT.get(model)

        if cli_agent:
            # Execution via CLI-Agent (claude-mcp / gemini-mcp / codex-mcp)
            from ..services.tristar.agent_controller import agent_controller
            res = await agent_controller.call_agent(cli_agent, prompt, timeout=180)
            if isinstance(res, dict):
                result_text = res.get("response") or res.get("output") or str(res)
            else:
                result_text = str(res)
        else:
            # Fallback: direkt via provider-spezifischen Endpoint
            import aiohttp
            skills_data = _load_skills()
            provider = skills_data.get(model, DEFAULT_SKILLS.get(model, {})).get("provider", "")

            if provider == "ollama":
                # Direct Ollama API with infrastructure-node failover.
                from app.services.ollama_node_router import ollama_candidates
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                }
                async with aiohttp.ClientSession() as session:
                    for endpoint in ollama_candidates(model):
                        try:
                            async with session.post(
                                f"{endpoint.base_url}/api/chat", json=payload,
                                timeout=aiohttp.ClientTimeout(total=180),
                            ) as resp:
                                if resp.status == 200:
                                    body = await resp.json()
                                    result_text = body.get("message", {}).get("content") or str(body)
                                    break
                                error_text = f"Ollama HTTP {resp.status}: {await resp.text()}"
                        except (aiohttp.ClientError, asyncio.TimeoutError):
                            continue
                    if result_text is None and error_text is None:
                        error_text = "Ollama nodes unavailable"
            else:
                # API-Provider: via /v1/chat mit Provider-Prefix
                if provider == "mistral" and not model.startswith("mistral/"):
                    chat_model = f"mistral/{model}"
                elif provider == "groq" and not model.startswith("groq/"):
                    chat_model = f"groq/{model}"
                elif provider == "openai" and not model.startswith("openai/"):
                    chat_model = f"openai/{model}"
                else:
                    chat_model = model
                payload = {
                    "model": chat_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "http://127.0.0.1:9000/v1/chat",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=180),
                    ) as resp:
                        if resp.status == 200:
                            body = await resp.json()
                            result_text = body.get("text") or body.get("content") or str(body)
                        else:
                            error_text = f"HTTP {resp.status}: {await resp.text()}"

    except Exception as exc:
        error_text = str(exc)
        log.exception("Task %s execution failed: %s", task_id, exc)

    duration_ms = int((time.time() - start_ms) * 1000)

    # State → done / failed
    data = _load_tasks()
    if task_id in data["tasks"]:
        data["tasks"][task_id].update({
            "state":       "failed" if error_text else "done",
            "result":      result_text,
            "error":       error_text,
            "duration_ms": duration_ms,
            "updated_at":  datetime.now(timezone.utc).isoformat(),
        })
        _save_tasks(data)

    log.info("Task %s finished: state=%s duration=%dms",
             task_id, "done" if not error_text else "failed", duration_ms)

STORE_FILE   = Path("/var/lib/triforce/agent_tasks.json")
SKILLS_FILE  = Path("/var/lib/triforce/agent_skills.json")

# ── Standard Skill-Rankings (Startwerte) ─────────────────────────────────────
# Score 0-100 pro Skill-Kategorie. Wird durch tatsächliche Task-Ergebnisse angepasst.

DEFAULT_SKILLS: Dict[str, Dict] = {
    # ── Gemini Models ──
    "gemini-2.0-flash": {
        "provider": "gemini", "type": "api",
        "speed": 90, "context": 100,
        "skills": {"code":75,"debug":70,"math":80,"creative":80,"reasoning":85,
                   "search":90,"system":70,"planning":85,"summarize":90,"translate":85},
        "tags": ["fast","large-context","multimodal"],
    },
    "gemini-2.5-pro": {
        "provider": "gemini", "type": "api",
        "speed": 60, "context": 100,
        "skills": {"code":90,"debug":90,"math":95,"creative":85,"reasoning":95,
                   "search":85,"system":80,"planning":95,"summarize":85,"translate":85},
        "tags": ["best","slow","expensive"],
    },
    # ── Claude ──
    "claude-sonnet-4-20250514": {
        "provider": "anthropic", "type": "api",
        "speed": 70, "context": 95,
        "skills": {"code":90,"debug":92,"math":88,"creative":90,"reasoning":95,
                   "search":75,"system":82,"planning":90,"summarize":88,"translate":85},
        "tags": ["reliable","reasoning","code"],
    },
    # ── Codex / GPT ──
    "gpt-4o": {
        "provider": "openai", "type": "api",
        "speed": 70, "context": 85,
        "skills": {"code":88,"debug":85,"math":88,"creative":82,"reasoning":88,
                   "search":80,"system":78,"planning":85,"summarize":82,"translate":80},
        "tags": ["reliable","versatile"],
    },
    "nova-account/chatgpt": {
        "provider": "openai", "type": "account",
        "speed": 78, "context": 85,
        "skills": {"code":92,"debug":90,"math":86,"creative":80,"reasoning":89,
                   "search":72,"system":84,"planning":90,"summarize":80,"translate":78},
        "tags": ["account","specialized","generalist","execution"],
    },
    "nova-account/claude": {
        "provider": "anthropic", "type": "account",
        "speed": 72, "context": 98,
        "skills": {"code":90,"debug":91,"math":88,"creative":92,"reasoning":96,
                   "search":74,"system":84,"planning":91,"summarize":89,"translate":86},
        "tags": ["account","specialized","long-context","review"],
    },
    "nova-account/google": {
        "provider": "gemini", "type": "account",
        "speed": 90, "context": 100,
        "skills": {"code":78,"debug":74,"math":84,"creative":78,"reasoning":87,
                   "search":95,"system":72,"planning":86,"summarize":92,"translate":84},
        "tags": ["account","specialized","research","multimodal","fast"],
    },
    # ── Ollama lokal (schnell, klein) ──
    "qwen2.5:7b": {
        "provider": "ollama", "type": "local",
        "speed": 95, "context": 40,
        "skills": {"code":72,"debug":65,"math":70,"creative":60,"reasoning":68,
                   "search":55,"system":70,"planning":62,"summarize":70,"translate":75},
        "tags": ["fast","local","small"],
    },
    "qwen2.5:14b": {
        "provider": "ollama", "type": "local",
        "speed": 80, "context": 50,
        "skills": {"code":80,"debug":75,"math":78,"creative":68,"reasoning":76,
                   "search":65,"system":75,"planning":72,"summarize":76,"translate":78},
        "tags": ["local","balanced"],
    },
    "deepseek-coder:6.7b": {
        "provider": "ollama", "type": "local",
        "speed": 95, "context": 30,
        "skills": {"code":88,"debug":85,"math":75,"creative":40,"reasoning":65,
                   "search":40,"system":70,"planning":55,"summarize":55,"translate":40},
        "tags": ["fast","local","code-specialist"],
    },
    "llama3.1:8b": {
        "provider": "ollama", "type": "local",
        "speed": 92, "context": 40,
        "skills": {"code":65,"debug":60,"math":60,"creative":72,"reasoning":68,
                   "search":62,"system":65,"planning":65,"summarize":72,"translate":70},
        "tags": ["fast","local","general"],
    },
    "gemma2:2b": {
        "provider": "ollama", "type": "local",
        "speed": 99, "context": 20,
        "skills": {"code":50,"debug":45,"math":50,"creative":55,"reasoning":52,
                   "search":50,"system":55,"planning":48,"summarize":60,"translate":58},
        "tags": ["ultra-fast","local","tiny","simple-tasks"],
    },
    # ── Mistral ──
    "mistral-small-latest": {
        "provider": "mistral", "type": "api",
        "speed": 88, "context": 60,
        "skills": {"code":78,"debug":72,"math":75,"creative":75,"reasoning":76,
                   "search":72,"system":70,"planning":72,"summarize":80,"translate":82},
        "tags": ["fast","cheap","versatile"],
    },
}

# Task-Komplexität → Mindest-Score + bevorzugte Tags
COMPLEXITY_RULES = {
    "tiny":    {"min_skill": 0,  "prefer_tags": ["ultra-fast","tiny"], "max_context": 10},
    "small":   {"min_skill": 50, "prefer_tags": ["fast","local"],      "max_context": 30},
    "medium":  {"min_skill": 70, "prefer_tags": ["fast","balanced"],   "max_context": 60},
    "large":   {"min_skill": 80, "prefer_tags": ["reliable"],          "max_context": 80},
    "complex": {"min_skill": 88, "prefer_tags": ["best","reasoning"],  "max_context": 100},
}

# ── Storage ───────────────────────────────────────────────────────────────────

def _load_tasks() -> Dict:
    try:
        STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        return json.loads(STORE_FILE.read_text()) if STORE_FILE.exists() else {"tasks": {}}
    except: return {"tasks": {}}

def _save_tasks(d: Dict):
    try:
        STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write — verhindert korrupte JSON bei Crash
        _tmp = STORE_FILE.with_suffix(".tmp")
        _tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        os.replace(_tmp, STORE_FILE)
    except Exception as e: log.error(f"Task save: {e}")

def _load_skills() -> Dict:
    try:
        SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if SKILLS_FILE.exists():
            stored = json.loads(SKILLS_FILE.read_text())
            # Merge mit Defaults (neue Modelle ergänzen)
            merged = dict(DEFAULT_SKILLS)
            merged.update(stored)
            return merged
        return dict(DEFAULT_SKILLS)
    except: return dict(DEFAULT_SKILLS)

def _save_skills(d: Dict):
    try:
        SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKILLS_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    except Exception as e: log.error(f"Skill save: {e}")


# ── Routing-Logik ─────────────────────────────────────────────────────────────

def _score_agent(agent: Dict, skill: str, complexity: str, speed_weight: float = 0.3) -> float:
    """Berechnet kombinierten Score für Agent + Task."""
    skill_score = agent["skills"].get(skill, 50)
    rule = COMPLEXITY_RULES.get(complexity, COMPLEXITY_RULES["medium"])
    
    if skill_score < rule["min_skill"]:
        return 0.0  # Nicht qualifiziert
    
    # Bonus für bevorzugte Tags
    tag_bonus = sum(5 for tag in rule["prefer_tags"] if tag in agent.get("tags", []))
    
    # Speed-Gewichtung: kleine Tasks → Speed wichtiger
    speed = agent.get("speed", 70)
    combined = (skill_score * (1 - speed_weight)) + (speed * speed_weight) + tag_bonus
    return combined


def route_task(skill: str, complexity: str, exclude: List[str] = None,
               require_local: bool = False) -> Optional[str]:
    """Wählt besten Agent für den Task."""
    skills = _load_skills()
    exclude = exclude or []
    
    scored = []
    for model, agent in skills.items():
        if model in exclude:
            continue
        if require_local and agent.get("type") != "local":
            continue
        s = _score_agent(agent, skill, complexity)
        if s > 0:
            scored.append((model, s))
    
    if not scored:
        return None
    
    # Speed-Gewichtung abhängig von Komplexität
    speed_w = {"tiny": 0.7, "small": 0.5, "medium": 0.3, "large": 0.1, "complex": 0.0}
    sw = speed_w.get(complexity, 0.3)
    scored_final = [(m, _score_agent(skills[m], skill, complexity, sw)) for m, _ in scored]
    scored_final.sort(key=lambda x: -x[1])
    
    return scored_final[0][0] if scored_final else None


# ── MCP Tool Handlers ─────────────────────────────────────────────────────────

async def handle_agent_task_create(params: Dict[str, Any]) -> Dict:
    """
    Erstellt neuen Task und weist automatisch den besten Agent zu.
    
    params:
      title      (str)  - Aufgabenbeschreibung
      skill      (str)  - Skill-Kategorie: code|debug|math|creative|reasoning|
                          search|system|planning|summarize|translate
      complexity (str)  - tiny|small|medium|large|complex
      prompt     (str)  - Vollständiger Prompt für den Agent
      model      (str)  - Modell explizit überschreiben (optional)
      local_only (bool) - Nur lokale Ollama-Modelle (default: false)
    """
    try:
        skill = params.get("skill", "reasoning")
        complexity = params.get("complexity", "medium")
        model = params.get("model")
        
        if not model:
            model = route_task(skill, complexity,
                               require_local=params.get("local_only", False))
        
        if not model:
            return {"error": f"Kein qualifizierter Agent für skill={skill} complexity={complexity}"}
        
        task_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        
        task = {
            "id": task_id,
            "title": params.get("title", "Unnamed task"),
            "skill": skill,
            "complexity": complexity,
            "model": model,
            "prompt": params.get("prompt", ""),
            "state": "pending",
            "created_at": now,
            "updated_at": now,
            "result": None,
            "duration_ms": None,
        }
        
        data = _load_tasks()
        data["tasks"][task_id] = task
        _save_tasks(data)
        
        skills = _load_skills()
        agent_info = skills.get(model, {})
        prompt = params.get("prompt", "")

        # Auto-Execute: wenn Prompt vorhanden, Task direkt starten
        execution_mode = "queued"
        if prompt:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_execute_task_background(task_id, model, prompt))
                execution_mode = "running"
            except RuntimeError:
                execution_mode = "manual"

        cli_agent = MODEL_TO_CLI_AGENT.get(model)
        return {
            "task_id":        task_id,
            "assigned_model": model,
            "cli_agent":      cli_agent or "http:/v1/chat",
            "model_tags":     agent_info.get("tags", []),
            "skill_score":    agent_info.get("skills", {}).get(skill, 0),
            "state":          "running" if execution_mode == "running" else "pending",
            "execution_mode": execution_mode,
        }
    except Exception as e:
        return {"error": str(e)}


async def handle_agent_task_status(params: Dict[str, Any]) -> Dict:
    """
    Gibt Status eines oder aller Tasks zurück.
    
    params:
      id     (str)  - Task-ID (optional)
      state  (str)  - Filter: pending|running|done|failed
      limit  (int)  - Max Einträge (default: 20)
    """
    try:
        data = _load_tasks()
        task_id = params.get("id")
        
        if task_id:
            t = data["tasks"].get(task_id)
            return t or {"error": f"Task {task_id} nicht gefunden"}
        
        tasks = list(reversed(list(data["tasks"].values())))
        state_filter = params.get("state")
        if state_filter:
            tasks = [t for t in tasks if t["state"] == state_filter]
        
        return {"count": len(tasks), "tasks": tasks[:params.get("limit", 20)]}
    except Exception as e:
        return {"error": str(e)}


async def handle_agent_skill_list(params: Dict[str, Any]) -> Dict:
    """
    Listet alle Agents mit ihren Skill-Rankings.
    
    params:
      skill    (str)  - Nach Skill sortieren (optional)
      type     (str)  - Filter: api|local
      top      (int)  - Nur Top-N anzeigen (default: alle)
    """
    try:
        skills = _load_skills()
        result = []
        
        skill_filter = params.get("skill")
        type_filter = params.get("type")
        
        for model, agent in skills.items():
            if type_filter and agent.get("type") != type_filter:
                continue
            
            entry = {
                "model": model,
                "provider": agent.get("provider"),
                "type": agent.get("type"),
                "speed": agent.get("speed"),
                "tags": agent.get("tags", []),
                "skills": agent.get("skills", {}),
            }
            if skill_filter:
                entry["rank_score"] = agent["skills"].get(skill_filter, 0)
            result.append(entry)
        
        if skill_filter:
            result.sort(key=lambda x: -x.get("rank_score", 0))
        
        top = params.get("top")
        if top:
            result = result[:top]
        
        return {"count": len(result), "agents": result}
    except Exception as e:
        return {"error": str(e)}


async def handle_agent_skill_update(params: Dict[str, Any]) -> Dict:
    """
    Aktualisiert Skill-Score eines Agents basierend auf Task-Ergebnis.
    
    params:
      model   (str)  - Modell-Name
      skill   (str)  - Skill-Kategorie
      score   (int)  - Neuer Score 0-100 (optional, wenn nicht angegeben: +/-5)
      success (bool) - War Task erfolgreich? (für Auto-Anpassung)
      delta   (int)  - Manuelle Score-Änderung (+/-)
    """
    try:
        model = params.get("model", "")
        skill = params.get("skill", "")
        if not model or not skill:
            return {"error": "model und skill erforderlich"}
        
        skills = _load_skills()
        if model not in skills:
            return {"error": f"Model {model} unbekannt"}
        
        current = skills[model]["skills"].get(skill, 50)
        
        if "score" in params:
            new_score = max(0, min(100, int(params["score"])))
        elif "delta" in params:
            new_score = max(0, min(100, current + int(params["delta"])))
        else:
            # Auto: success=+3, fail=-5
            delta = 3 if params.get("success", True) else -5
            new_score = max(0, min(100, current + delta))
        
        skills[model]["skills"][skill] = new_score
        _save_skills(skills)
        
        return {
            "model": model, "skill": skill,
            "old_score": current, "new_score": new_score,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Handler Registry ──────────────────────────────────────────────────────────

AGENT_ROUTER_HANDLERS = {
    "agent_task_create":  handle_agent_task_create,
    "agent_task_status":  handle_agent_task_status,
    "agent_skill_list":   handle_agent_skill_list,
    "agent_skill_update": handle_agent_skill_update,
}

AGENT_ROUTER_TOOL_NAMES = list(AGENT_ROUTER_HANDLERS.keys())
